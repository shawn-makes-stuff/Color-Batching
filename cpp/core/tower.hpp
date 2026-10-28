// Shrink Orca's prime tower to the swaps that are left (port of colorstack/tower.py).
//
// A tower layer is walls, one wipe block per tool change, and a sparse grid over the rest. Each remaining swap keeps
// a wipe: the n-th tool swapped in during a band (counting a swap at its start) wipes on the n-th block of the
// band's first layer. Unused blocks become grid lines (the grid's own spacing, width and flow). The tool that
// finishes the band prints the rest of the band's tower.
//
// When Orca sized each block by its colour pair's flush (tower_flush), the bands share one running tower instead
// (RunTower): a swap takes the wipe lines from the lowest unprinted tower layer on, in the slicer's order and up the
// layers, until they hold its own pair's flush (the next swap goes on from the line after, inside the same block if it
// must), finishing each layer it uses up, so it may wipe on tower layers above its band, though never more than the
// nozzle's clear height above the band's top (as high as any stack may stand); the band's last tool prints the tower up
// to the band's top. The tower stays the size the slicer made it: the swaps left need less than all of it. A swap that
// cannot get its whole flush that way is counted (short).
#pragma once

#include <cmath>
#include <map>
#include <optional>
#include <set>
#include <tuple>
#include <utility>
#include <vector>

#include "geometry.hpp"
#include "model.hpp"
#include "writer.hpp"

namespace cb {

struct GridTemplate {
    std::vector<double> xs;     // the grid's long Y lines, sorted
    double epm, width, feed;    // filament per mm, line width, feed: medians
};

// whether Orca sized each wipe block by its colour pair's flush rather than prime_volume for every pair:
// purge_in_prime_tower with single_extruder_multi_material on a Type 2 tower (Orca 2.3 ToolOrdering.cpp, Print.cpp:
// volume_to_wipe = flush_volumes_matrix x flush_multiplier)
bool tower_flush(const Program& program);
std::optional<GridTemplate> grid_template(const Program& program);
// grid lines over the area of wipe block p
std::vector<PathP> grid_over(const Path& p, const GridTemplate& tpl);
// rewrite each band's tower paths: (wipe blocks kept, wipe blocks turned into grid, swaps short of their flush)
std::tuple<int, int, int> compensate(const Program& program, std::vector<Band>& bands, const Nozzle& nozzle);

using TowerPaths = std::vector<std::pair<int, std::vector<PathP>>>;     // (tower layer, paths)

// the tower for staggered runs: a run that swaps tools in wipes on a free block of the lowest tower layer not
// printed yet; the run after which every unit is past some layers prints the rest of their tower, unused blocks as
// grid
class RunTower {
public:
    explicit RunTower(const Program& program, double clear = INFINITY);
    // for a swap from tool a to b, the model printed below layer top: one free wipe block on the lowest unprinted
    // tower layer, if it has one; with tower_flush, the pair's flush (see above)
    TowerPaths wipe(int a, int b, int top);
    TowerPaths complete(int upto);     // what is left of the tower layers below upto
    int kept = 0, replaced = 0, short_ = 0;

private:
    TowerPaths wipe_flush(double mm, int below);
    const Program& program;
    bool flush;
    double clear;
    std::optional<GridTemplate> tpl;
    std::vector<std::vector<PathP>> layers;
    int next = 0;                      // tower layers below are printed
    std::set<std::pair<int, int>> used;     // (layer, block) wiped on
    std::map<int, size_t> taken;            // tower_flush: layer -> wipe segments used, in the order of its wipe paths
};

}  // namespace cb
