// Shrink Orca's prime tower to the swaps that are left (port of colorstack/tower.py).
//
// A tower layer is walls, one wipe block per tool change, and a sparse grid over the rest. Each remaining swap keeps
// a wipe: the n-th tool swapped in during a band (counting a swap at its start) wipes on the n-th block of the
// band's first layer. Unused blocks become grid lines (the grid's own spacing, width and flow). The tool that
// finishes the band prints the rest of the band's tower.
#pragma once

#include <optional>
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

}  // namespace cb
