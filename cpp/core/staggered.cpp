// Port of colorstack/staggered.py.
#include "staggered.hpp"

#include <algorithm>
#include <map>
#include <numeric>
#include <stdexcept>
#include <tuple>

#include "tower.hpp"

namespace cb {

std::pair<std::vector<Band>, std::pair<int, int>> plan_staggered(const Program& program, const Nozzle& nozzle,
                                                                 const Options& opts,
                                                                 const std::function<void(double)>& progress) {
    Samples samples(opts.resolution * 2);
    const auto& layers = program.layers;
    const int n = int(layers.size());
    const int max_k = max_layers(program, nozzle, opts);
    Work own;
    for (const auto& L : layers) {
        own.emplace_back();
        for (const auto& p : L.paths)
            if (p->tool) own.back().emplace_back(unit_of(*p, opts.per_object), p);
    }
    std::vector<Unit> units;      // first seen order
    for (const auto& w : own)
        for (const auto& [o, p] : w)
            if (std::find(units.begin(), units.end(), o) == units.end()) units.push_back(o);
    std::map<Unit, int> front;
    for (const auto& u : units) front[u] = 0;
    const double reach = nozzle.reach(nozzle.clear_height) + 1.0;
    std::map<std::pair<int, Unit>, const KDTree*> trees;

    auto eff = [&](const Unit& u) {     // first layer with material u still has to print
        int j = front[u];
        while (j < n && std::none_of(own[j].begin(), own[j].end(), [&](const auto& w) { return w.first == u; })) j++;
        front[u] = j;
        return j;
    };
    auto tree = [&](int j, const Unit& u) {
        auto key = std::make_pair(j, u);
        auto it = trees.find(key);
        if (it == trees.end()) {
            std::vector<PathP> mine;
            for (const auto& p : layers[j].paths)
                if (p->tool && unit_of(*p, opts.per_object) == u) mine.push_back(p);
            it = trees.emplace(key, samples.tree(mine)).first;
        }
        return it->second;
    };
    auto touching = [&](const Unit& u, const Unit& v, int lo) {
        for (int j = lo; j < std::min(n, lo + max_k); j++) {
            const KDTree *a = tree(j, u), *b = tree(j, v);
            if (!a || !b) continue;
            for (const auto& q : a->points())
                if (b->nearest(q.x, q.y, reach) < reach) return true;
        }
        return false;
    };
    // u printing its layers up to b, the rest of `play` finishing the window after it in some order
    auto run = [&](const Unit& u, int lo, int b, const std::vector<Unit>& play,
                   const std::map<Unit, int>& effs) -> std::optional<Band> {
        std::vector<Unit> others;
        for (const auto& v : play)
            if (!(v == u)) others.push_back(v);
        std::stable_sort(others.begin(), others.end(), [&](const Unit& a, const Unit& c) {
            return std::make_pair(effs.at(a), a.tool) < std::make_pair(effs.at(c), c.tool);
        });
        std::vector<std::pair<int, std::vector<Unit>>> groups;      // per tool, in first seen order
        for (const auto& v : others) {
            auto it = std::find_if(groups.begin(), groups.end(), [&](const auto& g) { return g.first == v.tool; });
            if (it == groups.end()) groups.push_back({v.tool, {v}});
            else it->second.push_back(v);
        }
        std::map<Unit, int> starts;
        for (const auto& v : play) starts[v] = effs.at(v) - lo;
        std::vector<const Layer*> window;
        for (int j = lo; j < b; j++) window.push_back(&layers[j]);
        Work work(own.begin() + lo, own.begin() + b);
        std::vector<size_t> perm(groups.size());
        std::iota(perm.begin(), perm.end(), size_t(0));
        do {     // itertools.permutations(groups): the groups' positions in lexicographic order
            std::vector<Unit> order{u};
            for (size_t g : perm) order.insert(order.end(), groups[g].second.begin(), groups[g].second.end());
            auto band = plan_band(window, order, samples, nozzle, opts, &work, &starts);
            if (band) return band;
        } while (std::next_permutation(perm.begin(), perm.end()));
        return std::nullopt;
    };

    RunTower tower(program);
    std::vector<int> barriers;
    for (const auto& L : layers)
        if (L.barrier()) barriers.push_back(L.index);
    std::vector<double> done;
    if (progress) done = progress_at(layers);
    int loaded = program.first_tool;
    std::optional<Unit> last;
    int top = 0;      // layers below are printed by somebody
    std::vector<Band> bands;
    while (true) {
        std::map<Unit, int> effs;
        std::vector<std::pair<Unit, int>> active;      // in units order
        for (const auto& u : units) {
            effs[u] = eff(u);
            if (effs[u] < n) active.emplace_back(u, effs[u]);
        }
        if (active.empty()) break;
        int lo_all = n;
        for (const auto& [u, e] : active) lo_all = std::min(lo_all, e);
        if (progress) progress(done[size_t(lo_all)]);
        int cap = n;      // nobody prints past a barrier until all reach it
        for (int k : barriers)
            if (k > lo_all) {
                cap = k;
                break;
            }
        struct Cand {
            Unit u;
            int e, b, lo;
            Band band;
        };
        std::vector<Cand> cands;
        for (const auto& [u, e] : active) {
            std::optional<Cand> best;
            int b = std::max(top, e + 1);
            while (b <= cap) {
                std::vector<Unit> play;
                int lo = n;
                for (const auto& [v, ev] : active)
                    if (ev < b) {
                        play.push_back(v);
                        lo = std::min(lo, ev);
                    }
                if (b - lo > max_k) break;
                auto band = run(u, lo, b, play, effs);
                if (!band) break;
                best = Cand{u, e, b, lo, std::move(*band)};
                b++;
            }
            if (best) cands.push_back(std::move(*best));
        }
        if (cands.empty()) throw std::runtime_error("no valid run at layer " + std::to_string(layers[size_t(lo_all)].index));
        // max(): the first of the best
        auto key = [&](const Cand& c) {
            return std::make_tuple(c.u.tool == loaded, c.b - c.e, !last || !touching(c.u, *last, lo_all));
        };
        size_t pick = 0;
        auto best_key = key(cands[0]);
        for (size_t c = 1; c < cands.size(); c++) {
            auto k = key(cands[c]);
            if (best_key < k) {
                best_key = k;
                pick = c;
            }
        }
        Cand& c = cands[pick];
        const Unit u = c.u;
        const int e = c.e, b = c.b, lo = c.lo;
        // commit u's pieces: printed now (DONE) or handed to the unit that prints them later
        std::vector<std::vector<PathP>> mine(size_t(b - lo));
        for (int j = 0; j < b - lo; j++) {
            std::vector<std::pair<Unit, PathP>> keep;
            for (const auto& [o, p] : own[size_t(lo + j)])
                if (!(o == u)) keep.emplace_back(o, p);
            for (size_t q = 0; q < c.band.work[j].size(); q++) {
                const auto& [o, p] = c.band.work[j][q];
                if (c.band.origin[j][q] == u) {
                    keep.emplace_back(o == u ? DONE : o, p);
                    if (o == u) mine[j].push_back(p);
                }
            }
            own[size_t(lo + j)] = std::move(keep);
        }
        top = std::max(top, b);
        TowerPaths tower_paths;
        if (u.tool != loaded) tower_paths = tower.wipe();
        int upto = n;
        for (const auto& v : units) upto = std::min(upto, eff(v));
        for (auto& tp : tower.complete(upto)) tower_paths.push_back(std::move(tp));
        int start = e, end = b;      // u has nothing to print below e; above b: layers with nothing but tower
        for (const auto& [j, paths] : tower_paths) {
            start = std::min(start, j);
            end = std::max(end, j + 1);
        }
        Band out;
        for (int j = start; j < end; j++) out.layers.push_back(layers[size_t(j)].index);
        out.order = {u};
        out.work.resize(size_t(end - start));
        for (const auto& [j, paths] : tower_paths)
            for (const auto& p : paths) out.work[size_t(j - start)].emplace_back(u, p);
        for (int j = e; j < b; j++)
            for (const auto& p : mine[size_t(j - lo)]) out.work[size_t(j - start)].emplace_back(u, p);
        auto by = c.band.ceded_by.find(u);
        if (by != c.band.ceded_by.end()) {
            out.ceded_mm = by->second.first;
            out.visible_ceded_mm = by->second.second;
        }
        bands.push_back(std::move(out));
        loaded = u.tool;
        last = u;
        samples.clear_answers();
    }
    return {std::move(bands), {tower.kept, tower.replaced}};
}

}  // namespace cb
