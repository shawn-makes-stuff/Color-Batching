// Shrink Orca's prime tower to the swaps that are left (port of colorstack/tower.py).
//
// A tower layer is walls, one wipe block per tool change, and a sparse grid over the rest. Each remaining swap keeps
// a wipe: the n-th tool swapped in during a band (counting a swap at its start) wipes on the n-th block of the
// band's first layer. Unused blocks become grid lines (the grid's own spacing, width and flow). The tool that
// finishes the band prints the rest of the band's tower.
#pragma once

#include <optional>
#include <set>
#include <utility>
#include <vector>

#include "model.hpp"
#include "writer.hpp"

namespace cb {

struct GridTemplate {
    std::vector<double> xs;     // the grid's long Y lines, sorted
    double epm, width, feed;    // filament per mm, line width, feed: medians
};

std::optional<GridTemplate> grid_template(const Program& program);
// grid lines over the area of wipe block p
std::vector<PathP> grid_over(const Path& p, const GridTemplate& tpl);
// rewrite each band's tower paths: (wipe blocks kept, wipe blocks turned into grid)
std::pair<int, int> compensate(const Program& program, std::vector<Band>& bands);

using TowerPaths = std::vector<std::pair<int, std::vector<PathP>>>;     // (tower layer, paths)

// the tower for staggered runs: a run that swaps tools in wipes on a free block of the lowest tower layer not
// printed yet; the run after which every unit is past some layers prints the rest of their tower, unused blocks as
// grid
class RunTower {
public:
    explicit RunTower(const Program& program);
    TowerPaths wipe();                 // one free wipe block on the lowest unprinted tower layer, if it has one
    TowerPaths complete(int upto);     // what is left of the tower layers below upto
    int kept = 0, replaced = 0;

private:
    std::optional<GridTemplate> tpl;
    std::vector<std::vector<PathP>> layers;
    int next = 0;                      // tower layers below are printed
    std::set<std::pair<int, int>> used;     // (layer, block) wiped on
};

}  // namespace cb
