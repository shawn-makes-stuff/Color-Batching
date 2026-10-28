// Port of colorstack/tower.py.
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

std::pair<int, int> compensate(const Program& program, std::vector<Band>& bands) {
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
    return {kept, replaced};
}

RunTower::RunTower(const Program& program) : tpl(grid_template(program)) {
    for (const auto& L : program.layers) {
        layers.emplace_back();
        for (const auto& p : L.paths)
            if (!p->tool) layers.back().push_back(p);
    }
}

TowerPaths RunTower::wipe() {
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

TowerPaths RunTower::complete(int upto) {
    TowerPaths out;
    for (int j = next; j < std::min(upto, int(layers.size())); j++) {
        std::vector<PathP> paths;
        for (const auto& p : layers[j]) {
            if (p->tower == "wipe" && used.count({j, p->block})) continue;
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

}  // namespace cb
