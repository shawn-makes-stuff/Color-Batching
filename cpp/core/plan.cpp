// Port of color_batching/plan.py (and pipeline.materials).
#include "plan.hpp"

#include <algorithm>
#include <cmath>
#include <stdexcept>

#include "reader.hpp"

namespace cb {

const Unit DONE{-1, std::nullopt};

namespace {
const double PI = 3.141592653589793;     // math.pi
const double UNDER_TOL = 0.02;           // mm past Orca's line overlap before a line counts as under another
const double UNDER_SQUISH = 1 - 0.25 * PI;    // Orca's neighbouring lines overlap by height * this (Flow.cpp spacing)

}  // namespace

namespace {

// Python int(s) for the setting values it meets: optional sign and digits around whitespace
// ponytail: no underscores between digits, which int() also takes
bool py_int(std::string_view s, int& out) {
    s = py_strip(s);
    size_t k = (!s.empty() && (s[0] == '+' || s[0] == '-')) ? 1 : 0;
    if (k == s.size()) return false;
    for (size_t j = k; j < s.size(); j++)
        if (s[j] < '0' || s[j] > '9') return false;
    out = std::stoi(std::string(s));
    return true;
}

// prog.setting_list(key, int, [0])
std::vector<int> int_list(const Program& prog, const std::string& key) {
    auto it = prog.settings.find(key);
    if (it == prog.settings.end()) return {0};
    std::vector<int> out;
    std::string_view s = it->second;
    size_t start = 0;
    while (true) {
        size_t c = s.find(',', start);
        int v;
        if (!py_int(s.substr(start, c == std::string_view::npos ? std::string_view::npos : c - start), v)) return {0};
        out.push_back(v);
        if (c == std::string_view::npos) break;
        start = c + 1;
    }
    return out;
}

const Material* material(const Options& opts, int tool) {
    if (opts.materials.empty()) return nullptr;
    return &opts.materials[std::min<size_t>(size_t(tool), opts.materials.size() - 1)];
}

bool same_material(const Options& opts, int a, int b) {
    const Material *ma = material(opts, a), *mb = material(opts, b);
    return ma == mb || (ma && mb && *ma == *mb);
}

bool any(const std::vector<char>& v) { return std::find(v.begin(), v.end(), char(1)) != v.end(); }

// A scarf loop's end passes over its start, which runs up from below the layer. Where a handed-over piece of that ramp
// lies under a kept piece of the same path by more than Orca's neighbouring lines overlap, the kept piece goes too, or
// it would print first with the ramp under it. Neighbours along the path (a width and a sample either way) are beside
// it, not over it.
void over_ramp(const Path& p, const Samples1& s, std::vector<char>& mask, double step, double height) {
    const size_t n = s.x.size();
    const double w = *p.width;
    std::vector<double> z(n), arc(n), start(p.pts.size(), 0.0), lens(p.pts.size() - 1);
    double sum = 0.0;
    for (size_t i = 0; i + 1 < p.pts.size(); i++) {
        lens[i] = std::hypot(p.pts[i + 1].first - p.pts[i].first, p.pts[i + 1].second - p.pts[i].second);
        sum += lens[i];
        start[i] = sum - lens[i];       // numpy: cumsum - lens
    }
    std::vector<size_t> ramp, kept;
    for (size_t k = 0; k < n; k++) {
        size_t i = size_t(s.seg[k]);
        z[k] = p.zs[i] + (p.zs[i + 1] - p.zs[i]) * s.t[k];
        arc[k] = start[i] + lens[i] * s.t[k];
        if (!mask[k]) kept.push_back(k);
        else if (z[k] < p.z - 1e-6) ramp.push_back(k);
    }
    if (ramp.empty() || kept.empty()) return;
    const Pieces pc = pieces(p, s);
    std::vector<char> out = mask;
    for (size_t k : kept)
        for (size_t r : ramp)
            if (seg_dist(pc.l[k].first, pc.l[k].second, pc.r[k].first, pc.r[k].second, pc.l[r].first, pc.l[r].second,
                         pc.r[r].first, pc.r[r].second) - w + height * UNDER_SQUISH + UNDER_TOL < 0
                && z[k] > z[r] + 1e-6 && std::abs(arc[k] - arc[r]) > w + 2 * step) {
                out[k] = 1;
                break;
            }
    mask = out;
}

}  // namespace

std::vector<Material> materials(const Program& prog) {
    auto it = prog.settings.find("filament_type");
    std::string_view all = it == prog.settings.end() ? std::string_view() : std::string_view(it->second);
    std::vector<int> soluble = int_list(prog, "filament_soluble"), support = int_list(prog, "filament_is_support");
    std::vector<Material> out;
    size_t start = 0;
    for (size_t k = 0;; k++) {
        size_t c = all.find(';', start);
        std::string t = py_upper(py_strip(all.substr(start, c == std::string_view::npos ? std::string_view::npos : c - start)));
        t = t.substr(0, t.find('-'));
        out.emplace_back(t, soluble[std::min(k, soluble.size() - 1)], support[std::min(k, support.size() - 1)]);
        if (c == std::string_view::npos) break;
        start = c + 1;
    }
    return out;
}

Unit unit_of(const Path& p, bool per_object) {
    return {*p.tool, per_object ? p.obj : std::nullopt};
}

std::optional<Band> plan_band(const std::vector<const Layer*>& layers, const std::vector<Unit>& order, Samples& samples,
                              const Nozzle& nozzle, const Options& opts, const Work* given,
                              const std::map<Unit, int>* starts) {
    const Unit& first = order[0];
    Work work;
    if (given) {
        work = *given;
    } else {
        for (const Layer* L : layers) {
            work.emplace_back();
            for (const auto& p : L->paths) work.back().emplace_back(p->tool ? unit_of(*p, opts.per_object) : first, p);
        }
    }
    std::vector<std::vector<Unit>> origin;
    for (const auto& w : work) {
        origin.emplace_back();
        for (const auto& [o, p] : w) origin.back().push_back(o);
    }
    double ceded = 0.0, vis_ceded = 0.0;
    std::map<Unit, std::pair<double, double>> ceded_by;
    const double margin_mm = samples.step;      // samples lie up to step/2 off the true line
    double height = 0.0;
    for (const Layer* L : layers) height += L->height;
    const double max_need = nozzle.reach(height + 1.0) + margin_mm + 1.0;
    std::vector<const KDTree*> layer_trees;
    for (const Layer* L : layers) {
        std::vector<PathP> model;
        for (const auto& q : L->paths)
            if (q->tool) model.push_back(q);
        layer_trees.push_back(samples.tree(model));
    }
    bool has_done = false;
    for (const auto& w : work)
        for (const auto& [o, p] : w) has_done = has_done || o == DONE;

    auto owned = [&](size_t j, auto&& pred) {
        std::vector<PathP> out;
        for (const auto& [o, p] : work[j])
            if (pred(o)) out.push_back(p);
        return out;
    };
    for (size_t k = has_done ? 0 : 1; k < order.size(); k++) {
        const Unit T = order[k];
        auto is_earlier = [&](const Unit& u) { return u == DONE || std::find(order.begin(), order.begin() + k, u) != order.begin() + k; };
        auto is_T = [&](const Unit& u) { return u == T; };
        int start = 0;
        if (starts) {
            auto it = starts->find(T);
            if (it != starts->end()) start = it->second;
        }
        std::vector<const KDTree*> trees_T, trees_E;     // per band layer, after ceding
        std::vector<const Samples::Ramp*> ramps_T;       // per band layer, T's scarf seam ramps
        for (size_t j = 0; j < layers.size(); j++) {
            const Layer& L = *layers[j];
            if (int(j) < start) {     // T printed this layer already
                trees_T.push_back(nullptr);
                trees_E.push_back(nullptr);
                ramps_T.push_back(nullptr);
                continue;
            }
            std::vector<PathP> own = owned(j, is_T);
            const KDTree* own_tree = samples.tree(own);
            const Samples::Ramp* own_ramp = samples.ramp(own);
            // T's lines on this layer that run below its top (scarf ramps), by the path they came from: T's own, then
            // the ones handed to it here too, so the layer is planned again until no more are
            std::vector<std::pair<size_t, PathP>> ramps;
            for (size_t q = 0; q < work[j].size(); q++)
                if (work[j][q].first == T && !work[j][q].second->zs.empty()) ramps.emplace_back(q, work[j][q].second);
            const double ceded0 = ceded, vis0 = vis_ceded;
            const auto by0 = ceded_by;
            std::vector<std::pair<Unit, PathP>> new_work;
            std::vector<Unit> new_origin;
            while (true) {
                ceded = ceded0;
                vis_ceded = vis0;
                ceded_by = by0;
                new_work.clear();
                new_origin.clear();
                std::vector<size_t> new_src;
                for (size_t q = 0; q < work[j].size(); q++) {
                    const Unit owner = work[j][q].first;
                    const PathP& p = work[j][q].second;
                    const Unit& first_owner = origin[j][q];
                    auto keep = [&]() {
                        new_work.emplace_back(owner, p);
                        new_origin.push_back(first_owner);
                        new_src.push_back(q);
                    };
                    if (!is_earlier(owner)) {
                        keep();
                        continue;
                    }
                    const Samples1& s = samples.of(p);
                    const size_t n = s.x.size();
                    const double w = *p->width;
                    std::vector<double> margin(n, INFINITY);
                    std::vector<char> under(n, 0);
                    auto lower = [&](const std::vector<double>& d, double dz) {
                        double r = nozzle.reach(dz);
                        for (size_t m = 0; m < n; m++) margin[m] = std::min(margin[m], d[m] - r - margin_mm);
                    };
                    for (size_t i = 0; i <= j; i++) {
                        if (i < j && trees_T[i]) {
                            // layer Z, not path Z: path Z carries Orca's Z offset
                            double dz = nozzle.over(L.z - layers[i]->z);
                            if (dz > 1e-6) lower(samples.nearest(trees_T[i], p, max_need + w), dz);
                            if (L.z - layers[i]->z > 1e-6) {
                                // over T's line on a lower layer by more than Orca's neighbouring lines overlap: T would
                                // print under it. Any height, no contact allowance.
                                const auto& g = samples.gaps(trees_T[i], p);
                                for (size_t m = 0; m < n; m++)
                                    if (g[m] + layers[i]->height * UNDER_SQUISH + UNDER_TOL < 0) under[m] = 1;
                            }
                        }
                        // T's scarf seam ramps: its nozzle runs up to `drop` below that layer there (on this layer too)
                        const Samples::Ramp* ramp = i < j ? ramps_T[i] : own_ramp;
                        if (ramp) {
                            double dz = nozzle.over(L.z - layers[i]->z + ramp->drop);
                            if (dz > 1e-6) lower(samples.nearest(&ramp->tree, p, max_need + ramp->drop + w), dz);
                        }
                    }
                    std::vector<PathP> others;
                    for (const auto& [r, piece] : ramps)
                        if (r != q) others.push_back(piece);
                    if (const KDTree* tree = samples.tree(others)) {
                        // T's ramp on this layer runs under the line (Orca's lines overlap walls, e.g. infill): same rule
                        const auto& g = samples.gaps(tree, p);
                        for (size_t m = 0; m < n; m++)
                            if (g[m] + L.height * UNDER_SQUISH + UNDER_TOL < 0) under[m] = 1;
                    }
                    std::vector<char> mask(n);
                    for (size_t m = 0; m < n; m++) mask[m] = margin[m] < w / 2 || under[m];
                    if (owner == DONE) {
                        if (any(mask)) return std::nullopt;
                        keep();
                        continue;
                    }
                    if (j > 0) {
                        // resting only on T's previous layer (printed later): hand it to T, or it prints in mid-air
                        const auto& on_T = samples.nearest(trees_T[j - 1], p, w);
                        const auto& on_E = samples.nearest(trees_E[j - 1], p, w);
                        for (size_t m = 0; m < n; m++) mask[m] = mask[m] || (on_T[m] < w / 2 && !(on_E[m] < w / 2));
                    }
                    if (any(mask) && opts.min_piece > 0) mask = absorb_short_runs(mask, opts.min_piece / samples.step);
                    if (!p->zs.empty() && any(mask)) over_ramp(*p, s, mask, samples.step, L.height);
                    if (!any(mask)) {
                        keep();
                        continue;
                    }
                    if (owner.tool != T.tool && !same_material(opts, owner.tool, T.tool))
                        return std::nullopt;     // never hand plastic to a material it would not bond like (materials)
                    if (p->type.rfind("Support", 0) == 0 && owner.tool != T.tool && !opts.free_support.count({p->obj, p->type}))
                        return std::nullopt;     // support keeps the filament chosen for it (interface material is chosen on purpose)
                    std::vector<char> vis(n, p->visible());
                    if (p->visible()) {
                        std::vector<char> inner = interface_mask(*p, s, layer_trees[j]);
                        for (size_t m = 0; m < n; m++) vis[m] = vis[m] && !inner[m];
                    }
                    Samples1 shown;      // pts[vis & mask]
                    for (size_t m = 0; m < n; m++)
                        if (vis[m] && mask[m]) {
                            shown.x.push_back(s.x[m]);
                            shown.y.push_back(s.y[m]);
                        }
                    if (!shown.x.empty()) {
                        for (double d : nearest(own_tree, shown, opts.surface_tolerance + w + 1.0))
                            if (d - w / 2 > opts.surface_tolerance) return std::nullopt;
                    }
                    double v = double(shown.x.size()) * samples.step;
                    vis_ceded += v;
                    auto& by = ceded_by[first_owner];
                    by.second += v;
                    for (auto& [flag, piece] : split(*p, s, mask)) {
                        if (flag) {
                            double len = piece->length();
                            ceded += len;
                            by.first += len;
                        }
                        new_work.emplace_back(flag ? T : owner, piece);
                        new_origin.push_back(first_owner);
                        new_src.push_back(q);
                    }
                }
                std::vector<std::pair<size_t, PathP>> got;
                for (size_t m = 0; m < new_work.size(); m++)
                    if (new_work[m].first == T && !new_work[m].second->zs.empty()) got.emplace_back(new_src[m], new_work[m].second);
                bool same = got.size() == ramps.size();
                for (size_t m = 0; same && m < got.size(); m++)
                    same = got[m].first == ramps[m].first && got[m].second->pts == ramps[m].second->pts;
                if (same) break;
                ramps = std::move(got);
            }
            work[j] = std::move(new_work);
            origin[j] = std::move(new_origin);
            std::vector<PathP> mine = owned(j, is_T);
            trees_T.push_back(samples.tree(mine));
            trees_E.push_back(samples.tree(owned(j, is_earlier)));
            ramps_T.push_back(samples.ramp(mine));
        }
    }
    Band b;
    for (const Layer* L : layers) b.layers.push_back(L->index);
    b.order = order;
    b.work = std::move(work);
    b.ceded_mm = ceded;
    b.visible_ceded_mm = vis_ceded;
    b.origin = std::move(origin);
    b.ceded_by = std::move(ceded_by);
    return b;
}

namespace {

// each tool's units, objects in the order the slicer first prints them
std::map<int, std::vector<Unit>> units_by_tool(const std::vector<const Layer*>& group, bool per_object) {
    std::vector<std::pair<Unit, int>> first_seen;
    for (const Layer* L : group)
        for (const auto& p : L->paths) {
            if (!p->tool) continue;
            Unit u = unit_of(*p, per_object);
            if (std::none_of(first_seen.begin(), first_seen.end(), [&](const auto& f) { return f.first == u; }))
                first_seen.emplace_back(u, p->seq);
        }
    std::stable_sort(first_seen.begin(), first_seen.end(), [](const auto& a, const auto& b) { return a.second < b.second; });
    std::map<int, std::vector<Unit>> out;
    for (const auto& [u, seq] : first_seen) out[u.tool].push_back(u);
    return out;
}

std::optional<Band> best_band(const std::vector<const Layer*>& group, int loaded, Samples& samples, const Nozzle& nozzle,
                              const Options& opts) {
    auto units = units_by_tool(group, opts.per_object);
    if (units.empty()) return plan_band(group, {Unit{loaded, std::nullopt}}, samples, nozzle, opts);
    std::vector<int> tools;     // sorted
    for (const auto& [t, us] : units) tools.push_back(t);
    std::vector<int> starts = units.count(loaded) ? std::vector<int>{loaded} : tools;
    std::optional<Band> best;
    std::pair<double, double> best_key;
    for (int s : starts) {
        std::vector<int> rest;
        for (int t : tools)
            if (t != s) rest.push_back(t);
        do {     // itertools.permutations of a sorted list: lexicographic, as next_permutation
            std::vector<Unit> order = units[s];
            for (int t : rest) order.insert(order.end(), units[t].begin(), units[t].end());
            auto b = plan_band(group, order, samples, nozzle, opts);
            if (!b) continue;
            // ordering by purge volume was tried: no gain with 2 colours, more swaps with 4
            std::pair<double, double> key{b->visible_ceded_mm, b->ceded_mm};
            if (!best || key < best_key) {
                best = std::move(b);
                best_key = key;
            }
        } while (std::next_permutation(rest.begin(), rest.end()));
    }
    return best;
}

}  // namespace

std::vector<double> progress_at(const std::vector<Layer>& layers) {
    std::vector<long long> w{0};
    for (const auto& L : layers) {
        std::set<int> tools;
        for (const auto& p : L.paths)
            if (p->tool) tools.insert(*p->tool);
        long long c = std::max<long long>(1, (long long)tools.size());
        w.push_back(w.back() + c * c);
    }
    std::vector<double> out;
    for (long long v : w) out.push_back(double(v) / double(w.back()));
    return out;
}

int max_layers(const Program& program, const Nozzle& nozzle, const Options& opts) {
    double h = -INFINITY;
    for (const auto& L : program.layers) h = std::max(h, L.height);
    return std::max(1, std::min(opts.max_band_layers, int(nozzle.clear_height / h + 1e-9)));
}

std::vector<Band> plan(const Program& program, const Nozzle& nozzle, const Options& opts,
                       const std::function<void(double)>& progress) {
    Samples samples(opts.resolution * 2);
    const auto& layers = program.layers;
    std::vector<Band> bands;
    int loaded = program.first_tool;
    size_t i = 0;
    const size_t max_k = size_t(max_layers(program, nozzle, opts));
    std::vector<double> done;
    if (progress) done = progress_at(layers);
    auto group = [&](size_t a, size_t n) {
        std::vector<const Layer*> g;
        for (size_t k = a; k < a + n; k++) g.push_back(&layers[k]);
        return g;
    };
    while (i < layers.size()) {
        if (progress) progress(done[i]);
        auto best = best_band(group(i, 1), loaded, samples, nozzle, opts);
        if (!best) throw std::runtime_error("no valid plan for layer " + std::to_string(layers[i].index));
        size_t k = 1;
        while (k < max_k && i + k < layers.size() && !layers[i + k].barrier()) {
            auto b = best_band(group(i, k + 1), loaded, samples, nozzle, opts);
            if (!b) break;
            best = std::move(b);
            k++;
        }
        loaded = best->order.back().tool;
        i += best->layers.size();
        bands.push_back(std::move(*best));
        samples.clear_answers();
    }
    return bands;
}

}  // namespace cb
