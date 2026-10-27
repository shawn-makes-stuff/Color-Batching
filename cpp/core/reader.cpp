// Port of colorstack/reader.py. Where Python's regular expressions are used, the matching rules are reproduced exactly
// (\s = Python's whitespace, \d*\.?\d+ with its backtracking), so the events are the same, value for value.
#include "reader.hpp"

#include <charconv>
#include <cmath>
#include <fstream>
#include <sstream>
#include <stdexcept>

#include "pymath.hpp"

namespace cb {

namespace {

const double PI = 3.141592653589793;   // math.pi

const std::map<std::string, std::string> CURA_TYPES = {
    {"WALL-OUTER", "Outer wall"}, {"WALL-INNER", "Inner wall"}, {"SKIN", "Top surface"}, {"FILL", "Sparse infill"},
    {"SUPPORT", "Support"}, {"SUPPORT-INTERFACE", "Support interface"}, {"SKIRT", "Skirt"},
    {"PRIME-TOWER", "Prime tower"}, {"TRAVEL", "Travel"}};
const std::map<std::string, std::string> OTHER_TYPES = {
    {"External perimeter", "Outer wall"}, {"Perimeter", "Inner wall"}, {"Overhang perimeter", "Overhang wall"},
    {"Internal infill", "Sparse infill"}, {"Solid infill", "Internal solid infill"},
    {"Top solid infill", "Top surface"}, {"Bridge infill", "Bridge"}, {"Wipe tower", "Prime tower"},
    {"Support material", "Support"}, {"Support material interface", "Support interface"}};

bool digit(char c) { return c >= '0' && c <= '9'; }

bool starts(std::string_view s, std::string_view p) { return s.substr(0, p.size()) == p; }

// length of a -?\d*\.?\d+ match at s[i] (Python's backtracking: "1." matches "1", "-." nothing), 0 if none
size_t number_at(std::string_view s, size_t i, bool minus) {
    size_t j = i;
    if (minus && j < s.size() && s[j] == '-') j++;
    size_t d = j;
    while (j < s.size() && digit(s[j])) j++;
    if (j + 1 < s.size() && s[j] == '.' && digit(s[j + 1])) {
        j++;
        while (j < s.size() && digit(s[j])) j++;
        return j - i;
    }
    return j > d ? j - i : 0;
}

char lower(char c) { return (c >= 'A' && c <= 'Z') ? char(c - 'A' + 'a') : c; }

// re.search(key + r"(\d*\.?\d+)", s, re.I): the first place where the key and a number both match
bool search_value(std::string_view s, std::string_view key, double& out) {
    for (size_t i = 0; i + key.size() <= s.size(); i++) {
        bool same = true;
        for (size_t k = 0; k < key.size() && same; k++) same = lower(s[i + k]) == lower(key[k]);
        if (!same) continue;
        size_t n = number_at(s, i + key.size(), false);
        if (n && py_float(s.substr(i + key.size(), n), out)) return true;
    }
    return false;
}

// re.search(r"Z\s*=\s*(-?\d*\.?\d+)", s)
bool search_z(std::string_view s, double& out) {
    for (size_t i = 0; i < s.size(); i++) {
        if (s[i] != 'Z') continue;
        size_t j = i + 1;
        while (j < s.size() && py_space(s[j])) j++;
        if (j >= s.size() || s[j] != '=') continue;
        j++;
        while (j < s.size() && py_space(s[j])) j++;
        size_t n = number_at(s, j, true);
        if (n && py_float(s.substr(j, n), out)) return true;
    }
    return false;
}

// ^;\s*LAYER:\s*-?\d+  and  ^;\s*layer \d+ (case-insensitive)
bool layer_marker(std::string_view line) {
    size_t j = 1;
    while (j < line.size() && py_space(line[j])) j++;
    std::string_view rest = line.substr(j);
    if (starts(rest, "LAYER:")) {
        size_t k = 6;
        while (k < rest.size() && py_space(rest[k])) k++;
        if (k < rest.size() && rest[k] == '-') k++;
        if (k < rest.size() && digit(rest[k])) return true;
    }
    if (rest.size() >= 7) {
        bool word = true;
        for (size_t k = 0; k < 5; k++) word = word && lower(rest[k]) == "layer"[k];
        if (word && rest[5] == ' ' && digit(rest[6])) return true;
    }
    return false;
}

double float_or_throw(std::string_view s) {
    double v;
    if (!py_float(s, v)) throw std::runtime_error("could not convert string to float: '" + std::string(s) + "'");
    return v;
}

void comment(std::string_view line, size_t idx, const std::function<void(size_t, const Event&)>& emit) {
    std::string_view body = py_strip(line.substr(1));
    Event e;
    std::string head5;
    for (size_t k = 0; k < 5 && k < body.size(); k++) head5 += lower(body[k]);
    if (starts(line, ";LAYER_CHANGE") || (head5 == "layer" && layer_marker(line))) {
        e.kind = Kind::layer;
        emit(idx, e);
        double z;
        if (search_z(line, z)) {
            Event ez;
            ez.kind = Kind::z;
            ez.value = z;
            emit(idx, ez);
        }
    } else if (starts(line, ";Z:")) {
        e.kind = Kind::z;
        e.value = float_or_throw(line.substr(3));
        emit(idx, e);
    } else if (starts(line, ";HEIGHT:")) {
        e.kind = Kind::height;
        e.value = float_or_throw(line.substr(8));
        emit(idx, e);
    } else if (starts(line, ";TYPE:")) {
        std::string name(py_strip(line.substr(6)));
        auto c = CURA_TYPES.find(name);
        auto o = OTHER_TYPES.find(name);
        e.kind = Kind::type;
        e.text = c != CURA_TYPES.end() ? c->second : o != OTHER_TYPES.end() ? o->second : name;
        emit(idx, e);
    } else if (starts(body, "FEATURE:")) {
        std::string name(py_strip(body.substr(8)));
        auto o = OTHER_TYPES.find(name);
        e.kind = Kind::type;
        e.text = o != OTHER_TYPES.end() ? o->second : name;
        emit(idx, e);
    } else if (starts(line, ";WIDTH:")) {
        e.kind = Kind::width;
        e.value = float_or_throw(line.substr(7));
        emit(idx, e);
    } else {
        e.kind = Kind::comment;
        e.text = std::string(line);
        emit(idx, e);
    }
}

// I/J for an R-form arc (negative R = the long way round); false when there is none
bool centre_from_r(double x0, double y0, double x1, double y1, double r, bool clockwise, double& i, double& j) {
    double dx = x1 - x0, dy = y1 - y0;
    double d = py_hypot({dx, dy});
    if (d < 1e-9 || std::fabs(r) < d / 2 - 1e-9) return false;
    double h = std::sqrt(std::fmax(0.0, r * r - d * d / 4));
    double mx = x0 + dx / 2, my = y0 + dy / 2;
    double nx = -dy / d, ny = dx / d;
    double side = double((clockwise ? 1 : -1) * (r > 0 ? 1 : -1));
    double cx = mx - side * nx * h, cy = my - side * ny * h;
    i = cx - x0;
    j = cy - y0;
    return true;
}

// points along an arc around (x0+i, y0+j), start excluded, chord error below tol
std::vector<std::pair<double, double>> arc_points(double x0, double y0, double x1, double y1, double i, double j,
                                                  bool clockwise, double tol = 0.01) {
    double cx = x0 + i, cy = y0 + j;
    double r = py_hypot({i, j});
    if (r < 1e-9) return {{x1, y1}};
    double a0 = std::atan2(y0 - cy, x0 - cx);
    double a1 = std::atan2(y1 - cy, x1 - cx);
    double sweep = a1 - a0;
    if (clockwise && sweep >= 0)
        sweep -= 2 * PI;
    else if (!clockwise && sweep <= 0)
        sweep += 2 * PI;
    double step = r > tol ? 2 * std::acos(std::fmax(-1.0, std::fmin(1.0, 1 - tol / r))) : PI / 2;
    long long n = std::max<long long>(1, (long long)std::ceil(std::fabs(sweep) / std::fmax(step, 1e-6)));
    std::vector<std::pair<double, double>> pts;
    for (long long k = 1; k < n; k++) {
        double a = a0 + sweep * double(k) / double(n);
        pts.emplace_back(cx + r * std::cos(a), cy + r * std::sin(a));
    }
    pts.emplace_back(x1, y1);
    return pts;
}

}  // namespace

double Move::xy() const { return py_hypot({x1 - x0, y1 - y0}); }

bool py_space(char c) {
    return c == ' ' || c == '\t' || c == '\n' || c == '\r' || c == '\x0b' || c == '\x0c' || (c >= '\x1c' && c <= '\x1f');
}

std::string_view py_strip(std::string_view s) {
    size_t a = 0, b = s.size();
    while (a < b && py_space(s[a])) a++;
    while (b > a && py_space(s[b - 1])) b--;
    return s.substr(a, b - a);
}

std::string py_upper(std::string_view s) {
    std::string out(s);
    for (char& c : out)
        if (c >= 'a' && c <= 'z') c = char(c - 'a' + 'A');
    return out;
}

bool py_float(std::string_view s, double& out) {
    s = py_strip(s);
    if (!s.empty() && s[0] == '+') s.remove_prefix(1);
    if (s.empty()) return false;
    auto [p, ec] = std::from_chars(s.data(), s.data() + s.size(), out);
    return ec == std::errc() && p == s.data() + s.size();
}

std::map<char, double> words(std::string_view code) {
    std::string up = py_upper(code);
    std::string_view s = up;
    std::map<char, double> out;
    size_t i = 0;
    while (i < s.size()) {
        if (s[i] >= 'A' && s[i] <= 'Z') {
            size_t j = i + 1;
            while (j < s.size() && py_space(s[j])) j++;
            size_t n = number_at(s, j, true);
            if (n) {
                double v;
                py_float(s.substr(j, n), v);
                out[s[i]] = v;
                i = j + n;
                continue;
            }
        }
        i++;
    }
    return out;
}

std::vector<std::string> load(const std::string& path) {
    std::ifstream f(path, std::ios::binary);
    if (!f) throw std::runtime_error("cannot open " + path);
    std::stringstream ss;
    ss << f.rdbuf();
    std::string text = ss.str();
    std::vector<std::string> lines;
    size_t start = 0, i = 0;
    while (i < text.size()) {
        char c = text[i];
        size_t sep = 0;
        if (c == '\r')
            sep = (i + 1 < text.size() && text[i + 1] == '\n') ? 2 : 1;
        else if (c == '\n' || c == '\x0b' || c == '\x0c' || c == '\x1c' || c == '\x1d' || c == '\x1e')
            sep = 1;
        else if ((unsigned char)c == 0xC2 && i + 1 < text.size() && (unsigned char)text[i + 1] == 0x85)
            sep = 2;     // U+0085
        else if ((unsigned char)c == 0xE2 && i + 2 < text.size() && (unsigned char)text[i + 1] == 0x80 &&
                 ((unsigned char)text[i + 2] == 0xA8 || (unsigned char)text[i + 2] == 0xA9))
            sep = 3;     // U+2028, U+2029
        if (sep) {
            lines.emplace_back(text, start, i - start);
            i += sep;
            start = i;
        } else {
            i++;
        }
    }
    if (start < text.size()) lines.emplace_back(text, start, text.size() - start);
    return lines;
}

void read(const std::vector<std::string>& lines, const std::function<void(size_t, const Event&)>& emit) {
    double x = 0, y = 0, z = 0, e = 0, f = 3000.0;
    bool abs_xyz = true, abs_e = false;
    for (size_t idx = 0; idx < lines.size(); idx++) {
        std::string_view line = py_strip(lines[idx]);
        if (line.empty()) continue;
        if (line[0] == ';') {
            comment(line, idx, emit);
            continue;
        }
        std::string_view code = py_strip(line.substr(0, line.find(';')));
        if (code.empty()) continue;
        size_t w_end = 0;
        while (w_end < code.size() && !py_space(code[w_end])) w_end++;
        std::string word = py_upper(code.substr(0, w_end));
        if (word == "G0" || word == "G1" || word == "G2" || word == "G3") {
            auto v = words(code.substr(word.size()));
            auto has = [&](char k) { return v.count(k) > 0; };
            if (has('F')) f = v['F'];
            double nx = has('X') ? (abs_xyz ? v['X'] : x + v['X']) : x;
            double ny = has('Y') ? (abs_xyz ? v['Y'] : y + v['Y']) : y;
            double nz = has('Z') ? (abs_xyz ? v['Z'] : z + v['Z']) : z;
            double de = 0.0;
            if (has('E')) {
                // Klipper and Marlin both make E relative under G91, whatever M82/M83 say
                bool e_abs = abs_e && abs_xyz;
                de = e_abs ? v['E'] - e : v['E'];
                e = e_abs ? v['E'] : e + v['E'];
            }
            if ((word == "G2" || word == "G3") && (has('I') || has('J') || has('R'))) {
                double ci = 0, cj = 0;
                bool ok;
                if (has('R') && !(has('I') || has('J'))) {
                    ok = centre_from_r(x, y, nx, ny, v['R'], word == "G2", ci, cj);
                } else {
                    ci = has('I') ? v['I'] : 0.0;
                    cj = has('J') ? v['J'] : 0.0;
                    ok = true;
                }
                std::vector<std::pair<double, double>> pts =
                    ok ? arc_points(x, y, nx, ny, ci, cj, word == "G2") : std::vector<std::pair<double, double>>{{nx, ny}};
                std::vector<double> lens;
                double px = x, py = y;
                for (auto& [qx, qy] : pts) {
                    lens.push_back(py_hypot({qx - px, qy - py}));
                    px = qx;
                    py = qy;
                }
                double total = 0;
                for (double l : lens) total += l;
                if (total == 0.0) total = 1.0;
                px = x;
                py = y;
                double pz = z;
                for (size_t k = 0; k < pts.size(); k++) {
                    auto [qx, qy] = pts[k];
                    double qz = z + (nz - z) * double(k + 1) / double(pts.size());
                    Event ev;
                    ev.kind = Kind::move;
                    ev.move = {px, py, pz, qx, qy, qz, de * lens[k] / total, f, word};
                    emit(idx, ev);
                    px = qx;
                    py = qy;
                    pz = qz;
                }
            } else {
                Event ev;
                ev.kind = Kind::move;
                ev.move = {x, y, z, nx, ny, nz, de, f, word};
                emit(idx, ev);
            }
            x = nx;
            y = ny;
            z = nz;
        } else if (word == "G90") {
            abs_xyz = true;
        } else if (word == "G91") {
            abs_xyz = false;
        } else if (word == "M82") {
            abs_e = true;
        } else if (word == "M83") {
            abs_e = false;
        } else if (word == "G92") {
            auto v = words(code.substr(3));
            if (v.empty()) e = 0.0;
            if (v.count('E')) e = v['E'];
            if (v.count('X')) x = v['X'];
            if (v.count('Y')) y = v['Y'];
            if (v.count('Z')) z = v['Z'];
        } else if (word == "SET_VELOCITY_LIMIT") {
            double a;
            if (search_value(code, "ACCEL=", a)) {
                Event ev;
                ev.kind = Kind::accel;
                ev.value = a;
                emit(idx, ev);
            }
            if (search_value(code, "SQUARE_CORNER_VELOCITY=", a)) {
                Event ev;
                ev.kind = Kind::scv;
                ev.value = a;
                emit(idx, ev);
            }
            Event ev;
            ev.kind = Kind::cmd;
            ev.text = std::string(code);
            emit(idx, ev);
        } else if (word == "M204") {
            auto v = words(code.substr(4));
            if (v.count('S') || v.count('P')) {
                Event ev;
                ev.kind = Kind::accel;
                ev.value = v.count('S') ? v['S'] : v['P'];
                emit(idx, ev);
            }
            Event ev;
            ev.kind = Kind::cmd;
            ev.text = std::string(code);
            emit(idx, ev);
        } else {
            // ^T(\d+)\b on the line as written (a lower-case t is not a tool change)
            size_t k = 1;
            while (k < code.size() && digit(code[k])) k++;
            bool boundary = k == code.size() || !(std::isalnum((unsigned char)code[k]) || code[k] == '_' ||
                                                  (unsigned char)code[k] >= 0x80);
            Event ev;
            if (code[0] == 'T' && k > 1 && boundary) {
                ev.kind = Kind::tool;
                ev.tool = std::stoi(std::string(code.substr(1, k - 1)));
                ev.text = std::string(code);
            } else {
                ev.kind = Kind::cmd;
                ev.text = std::string(code);
            }
            emit(idx, ev);
        }
    }
}

}  // namespace cb
