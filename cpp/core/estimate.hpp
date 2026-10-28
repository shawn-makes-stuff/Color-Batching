// Print-time estimate, the same for the original and the processed file (port of colorstack/estimate.py): Klipper's
// planner (trapezoids, lookahead, square-corner junctions) with the machine limits the slicer wrote into the file.
#pragma once

#include <map>
#include <optional>
#include <string>
#include <utility>
#include <vector>

namespace cb {

struct Limits {
    double v, a, scv, zv, za;
    double swap_s;      // the profile's load + unload time, only for files without a slicer total
};

struct PrinterFallback { double max_velocity = 500, max_accel = 20000, square_corner_velocity = 9, max_z_velocity = 20, max_z_accel = 500; };

Limits machine_limits(const std::map<std::string, std::string>& settings, const PrinterFallback& fallback);

struct Estimate {
    double motion_s = 0, dwell_s = 0;
    int swaps = 0;
    std::map<std::optional<int>, double> extruded;   // per tool, filament mm (no tool yet = none)
    std::map<int, int> swaps_into;
    // filled by anchor()
    double swap_s = 0, total_s = 0, swap_each_s = 0;
    std::string source;
};

// steps: the timeline, (line, seconds) per move and dwell, (line, none) per colour change
using Steps = std::vector<std::pair<size_t, std::optional<double>>>;
Estimate estimate(const std::vector<std::string>& lines, const Limits& lim, Steps* steps = nullptr);

double parse_duration(const std::string& text);         // '1d 2h 3m 4s' -> seconds
std::string fmt_duration(double seconds);                // seconds -> '1h 2m 3s'
std::optional<double> file_estimate(const std::vector<std::string>& lines);   // the slicer's total
std::pair<double, std::string> swap_each(const Estimate& o, std::optional<double> slicer, const Limits& lim);
void anchor(Estimate& o, Estimate& p, std::optional<double> slicer, const Limits& lim);

}  // namespace cb
