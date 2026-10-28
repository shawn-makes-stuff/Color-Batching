// Rewrite the file's own summary (times, colour changes, filament used) and progress lines (M73) (port of
// color_batching/summary.py).
#pragma once

#include <string>
#include <vector>

#include "estimate.hpp"

namespace cb {

void stamp_header(std::vector<std::string>& lines, const Estimate& o, const Estimate& p);
std::vector<std::string> stamp_progress(const std::vector<std::string>& lines, const Steps& steps, double each, double total);

}  // namespace cb
