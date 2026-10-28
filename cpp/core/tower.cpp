// Port of color_batching/tower.py.
#include "tower.hpp"

#include <algorithm>
#include <cmath>
#include <map>
#include <set>

#include "parse.hpp"

namespace cb {

namespace {

double median(std::vector<double> v) {      // statistics.median
    std::sort(v.begin(), v.end());
    size_t n = v.size(), i = n / 2;
    return n % 2 ? v[i] : (v[i - 1] + v[i]) / 2;
}

}  // namespace

bool tower_flush(const Program& program) {
    auto get = [&](const char* key) -> const std::string* {
        auto it = program.settings.find(key);
        return it == program.settings.end() ? nullptr : &it->second;
    };
    const std::string *purge = get("purge_in_prime_tower"), *semm = get("single_extruder_multi_material"),
                      *type = get("wipe_tower_type");
    return purge && *purge == "1" && semm && *semm == "1" && !(type && *type == "type1");
}

std::optional<GridTemplate> grid_template(const Program& program) {
    std::set<double> xs;
    std::vector<double> epm, widths, feeds;
    for (const auto& L : program.layers)
        for (const auto& p : L.paths) {
            if (p->tower != "grid") continue;
            for (size_t i = 0; i < p->e.size(); i++) {
                auto [x0, y0] = p->pts[i];
                auto [x1, y1] = p->pts[i + 1];
                if (std::fabs(x1 - x0) < 1e-3 && std::fabs(y1 - y0) > 1.0) {     // the grid's long Y lines
                    xs.insert(py_round(x0, 2));
                    epm.push_back(p->e[i] / std::fabs(y1 - y0));
                    feeds.push_back(p->f[i]);
                }
            }
            widths.push_back(*p->width);
        }
    if (xs.empty()) return std::nullopt;
    return GridTemplate{{xs.begin(), xs.end()}, median(epm), median(widths), median(feeds)};
}

std::vector<PathP> grid_over(const Path& p, const GridTemplate& tpl) {
    double x0 = INFINITY, x1 = -INFINITY, y0 = INFINITY, y1 = -INFINITY;
    for (auto [x, y] : p.pts) {
        x0 = std::min(x0, x);
        x1 = std::max(x1, x);
        y0 = std::min(y0, y);
        y1 = std::max(y1, y);
    }
    std::vector<double> cols;
    for (double x : tpl.xs)
        if (x0 - 1e-6 <= x && x <= x1 + 1e-6) cols.push_back(x);
    if (cols.empty() || y1 - y0 < 1e-3) return {};
    auto g = std::make_shared<Path>(p);
    g->pts.clear();
    g->e.clear();
    g->f.clear();
    bool up = true;
    for (double x : cols) {
        double a = up ? y0 : y1, b = up ? y1 : y0;
        if (!g->pts.empty()) {      // unextruded step along the block's edge
            g->e.push_back(0.0);
            g->f.push_back(tpl.feed);
        }
        g->pts.emplace_back(x, a);
        g->pts.emplace_back(x, b);
        g->e.push_back(tpl.epm * std::fabs(b - a));
        g->f.push_back(tpl.feed);
        up = !up;
    }
    g->width = tpl.width;
    g->tower = "grid";
    g->block = -1;
    return {g};
}

std::tuple<int, int, int> run_bands(const Program& program, std::vector<Band>& bands, const Nozzle& nozzle);

std::tuple<int, int, int> compensate(const Program& program, std::vector<Band>& bands, const Nozzle& nozzle) {
    if (tower_flush(program)) return run_bands(program, bands, nozzle);
    auto tpl = grid_template(program);
    int kept = 0, replaced = 0;
    int loaded = program.first_tool;
    for (auto& band : bands) {
        std::map<int, Unit> first_of;
        for (const auto& u : band.order) first_of.emplace(u.tool, u);
        std::vector<int> tool_seq = band.tools();
        const Unit last = first_of.at(tool_seq.back());
        std::set<int> blocks;
        for (const auto& [o, p] : band.work[0])
            if (p->tower == "wipe") blocks.insert(p->block);
        // a band may start with a swap
        std::vector<int> swapped_in(tool_seq.begin() + (tool_seq[0] != loaded ? 0 : 1), tool_seq.end());
        std::map<int, Unit> wiper;
        auto bl = blocks.begin();
        for (size_t k = 0; k < swapped_in.size() && bl != blocks.end(); k++, ++bl) wiper.emplace(*bl, first_of.at(swapped_in[k]));
        loaded = tool_seq.back();
        for (auto& items : band.work) {
            std::vector<std::pair<Unit, PathP>> out;
            for (auto& [owner, p] : items) {
                if (!p->tower) {
                    out.emplace_back(owner, p);
                } else if (p->tower == "wipe" && wiper.count(p->block)) {
                    kept++;
                    out.emplace_back(wiper.at(p->block), p);
                } else if (p->tower == "wipe" && tpl) {
                    for (auto& g : grid_over(*p, *tpl)) out.emplace_back(last, g);
                    replaced++;
                } else {
                    out.emplace_back(last, p);
                }
            }
            items = std::move(out);
        }
    }
    return {kept, replaced, 0};
}

RunTower::RunTower(const Program& program, double clear)
    : program(program), flush(tower_flush(program)), clear(clear), tpl(grid_template(program)) {
    for (const auto& L : program.layers) {
        layers.emplace_back();
        for (const auto& p : L.paths)
            if (!p->tool) layers.back().push_back(p);
    }
}

TowerPaths RunTower::wipe(int a, int b, int top) {
    if (flush) {
        const auto& ls = program.layers;
        double limit = ls[size_t(top - 1)].z + clear + 1e-6;
        int below = int(ls.size());
        for (int j = top; j < int(ls.size()); j++)
            if (ls[size_t(j)].z > limit) {
                below = j;
                break;
            }
        return wipe_flush(program.flush_length(a, b), below);
    }
    int j = next;
    if (j >= int(layers.size())) return {};
    std::optional<int> free;
    for (const auto& p : layers[j])
        if (p->tower == "wipe" && !used.count({j, p->block})) {
            free = p->block;
            break;
        }
    if (!free) return {};
    used.insert({j, *free});
    std::vector<PathP> block;
    for (const auto& p : layers[j])
        if (p->tower == "wipe" && p->block == *free) block.push_back(p);
    kept += int(block.size());
    return {{j, block}};
}

namespace {

PathP segments(const PathP& p, size_t i, size_t s) {     // segments i..s-1 of p
    auto q = std::make_shared<Path>(*p);
    q->pts.assign(p->pts.begin() + i, p->pts.begin() + s + 1);
    q->e.assign(p->e.begin() + i, p->e.begin() + s);
    q->f.assign(p->f.begin() + i, p->f.begin() + s);
    return q;
}

}  // namespace

TowerPaths RunTower::wipe_flush(double mm, int below) {
    TowerPaths out;
    double got = 0.0;
    while (next < below && got < mm) {
        int j = next;
        size_t k = 0, cut = taken.count(j) ? taken.at(j) : 0, used_now = 0;
        std::vector<PathP> mine;
        for (const auto& p : layers[size_t(j)]) {
            if (p->tower != "wipe") continue;
            size_t n = p->e.size();
            size_t i = std::min(cut > k ? cut - k : 0, n), s = i;
            while (s < n && got < mm) got += p->e[s++];
            if (s > i) {
                mine.push_back(i == 0 && s == n ? p : segments(p, i, s));
                used_now += s - i;
            }
            k += n;
        }
        taken[j] = cut + used_now;
        kept += int(mine.size());
        if (!mine.empty()) out.emplace_back(j, mine);
        if (got < mm)          // layer j's wipe lines used up: finish it, go on above
            for (auto& tp : complete(j + 1)) out.push_back(std::move(tp));
    }
    short_ += got < mm;
    return out;
}

TowerPaths RunTower::complete(int upto) {
    TowerPaths out;
    for (int j = next; j < std::min(upto, int(layers.size())); j++) {
        std::vector<PathP> paths;
        size_t k = 0, cut = taken.count(j) ? taken.at(j) : 0;
        for (PathP p : layers[j]) {
            if (p->tower == "wipe" && used.count({j, p->block})) continue;
            if (p->tower == "wipe") {     // the part not wiped on (tower_flush)
                size_t n = p->e.size(), i = std::min(cut > k ? cut - k : 0, n);
                k += n;
                if (i == n) continue;
                if (i) p = segments(p, i, n);
            }
            if (p->tower == "wipe" && tpl) {
                for (auto& g : grid_over(*p, *tpl)) paths.push_back(g);
                replaced++;
            } else {
                paths.push_back(p);
            }
        }
        out.emplace_back(j, paths);
    }
    next = std::max(next, upto);
    return out;
}

// compensate with one running tower over the bands (tower_flush)
std::tuple<int, int, int> run_bands(const Program& program, std::vector<Band>& bands, const Nozzle& nozzle) {
    RunTower tower(program, nozzle.clear_height);
    int loaded = program.first_tool;
    for (auto& band : bands) {
        std::map<int, Unit> first_of;
        for (const auto& u : band.order) first_of.emplace(u.tool, u);
        std::vector<int> chain = band.tools();
        if (chain[0] != loaded) chain.insert(chain.begin(), loaded);     // a band may start with a swap
        int lo = band.layers.front(), top = band.layers.back() + 1;
        std::vector<std::pair<Unit, TowerPaths>> got;
        for (size_t k = 0; k + 1 < chain.size(); k++)
            got.emplace_back(first_of.at(chain[k + 1]), tower.wipe(chain[k], chain[k + 1], top));
        got.emplace_back(first_of.at(chain.back()), tower.complete(top));
        loaded = chain.back();
        int end = top;          // above top: tower only
        for (const auto& [u, tp] : got)
            for (const auto& [j, paths] : tp) end = std::max(end, j + 1);
        std::vector<std::vector<std::pair<Unit, PathP>>> work;
        for (const auto& items : band.work) {
            work.emplace_back();
            for (const auto& [o, p] : items)
                if (!p->tower) work.back().emplace_back(o, p);
        }
        work.resize(work.size() + size_t(end - top));
        for (const auto& [u, tp] : got)
            for (const auto& [j, paths] : tp)      // j >= lo: the band before finished the tower up to its top
                for (const auto& p : paths) work[size_t(j - lo)].emplace_back(u, p);
        for (int j = top; j < end; j++) band.layers.push_back(program.layers[size_t(j)].index);
        band.work = std::move(work);
    }
    return {tower.kept, tower.replaced, tower.short_};
}

}  // namespace cb
