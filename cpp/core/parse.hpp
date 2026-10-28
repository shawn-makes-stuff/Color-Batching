// Parse multicolour G-code into layers of extrusion paths (port of colorstack/parse.py).
#pragma once

#include <map>
#include <string>
#include <vector>

#include "model.hpp"

namespace cb {

// the slicer's "; key = value" lines, the first of each key
std::map<std::string, std::string> parse_settings(const std::vector<std::string>& lines);

// written as a comment into every output; parse refuses a file that has it
inline constexpr const char* PROCESSED = "Processed by Color Batching (https://github.com/shawn-makes-stuff/Color-Batching)";
inline constexpr const char* PROCESSED_BEFORE = "Processed by ColorStack (https://github.com/shawn-makes-stuff/ColorStack)";  // 0.1.0

// throws std::runtime_error with the reason for a file Color Batching cannot process
Program parse(const std::string& path);

// round(x, n) as Python does it (correctly rounded to n decimals)
double py_round(double x, int n);

}  // namespace cb
