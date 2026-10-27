// Parse multicolour G-code into layers of extrusion paths (port of colorstack/parse.py).
#pragma once

#include <map>
#include <string>
#include <vector>

#include "model.hpp"

namespace cb {

// the slicer's "; key = value" lines, the first of each key
std::map<std::string, std::string> parse_settings(const std::vector<std::string>& lines);

// throws std::runtime_error with the reason for a file ColorStack cannot process
Program parse(const std::string& path);

// round(x, n) as Python does it (correctly rounded to n decimals)
double py_round(double x, int n);

}  // namespace cb
