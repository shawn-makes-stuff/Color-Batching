// Staggered colours: each unit on its own schedule instead of shared bands (port of colorstack/staggered.py).
#pragma once

#include <functional>
#include <utility>
#include <vector>

#include "plan.hpp"

namespace cb {

// a run prints one unit's next layers as far as the units below could still finish the window after it as a band,
// and no further than the next barrier layer while any unit is below it. Next run: the most layers for no swap, else
// for one swap; ties go to a unit not touching the one just printed. Returns one single-unit band per run and
// (wipes kept, wipes to grid).
std::pair<std::vector<Band>, std::pair<int, int>> plan_staggered(const Program& program, const Nozzle& nozzle,
                                                                 const Options& opts,
                                                                 const std::function<void(double)>& progress = nullptr);

}  // namespace cb
