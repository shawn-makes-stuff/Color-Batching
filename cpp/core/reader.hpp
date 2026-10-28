// One G-code reader for the parser, the estimate and the checker (port of color_batching/reader.py).
// read() calls back with (line_index, event): a move (arcs linearised, de always relative), a tool change, an
// acceleration or square-corner-velocity setting, a layer marker, Z, line height, feature type (Orca names), width, any
// other command (comment stripped) or an unrecognised comment line.
#pragma once

#include <cstdint>
#include <functional>
#include <map>
#include <string>
#include <string_view>
#include <vector>

namespace cb {

struct Move {
    double x0, y0, z0, x1, y1, z1;
    double de;          // mm of filament, negative = retract
    double f;           // mm/min
    std::string word;   // G0 .. G3
    double xy() const;
};

enum class Kind { move, tool, accel, scv, layer, z, height, type, width, cmd, comment };

struct Event {
    Kind kind;
    Move move;          // kind == move
    int tool = 0;       // kind == tool
    double value = 0;   // accel, scv, z, height, width
    std::string text;   // tool (its line), type (Orca name), cmd (comment stripped), comment (the line)
};

// the file's lines as Python's str.splitlines() gives them for G-code (\n, \r\n, \r, \v, \f, \x1c-\x1e)
std::vector<std::string> load(const std::string& path);

void read(const std::vector<std::string>& lines, const std::function<void(size_t, const Event&)>& emit);

// letter -> value of a G-code line's words (WORD_RE findall on the upper-cased text, the last of a letter wins)
std::map<char, double> words(std::string_view code);

// Python str helpers with Python's whitespace set (str.isspace on ASCII)
bool py_space(char c);
std::string_view py_strip(std::string_view s);
std::string py_upper(std::string_view s);
// float(s) for the number forms G-code uses; false if s is not one
bool py_float(std::string_view s, double& out);

}  // namespace cb
