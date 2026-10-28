// Port of color_batching/config.py.
#include "config.hpp"

#include <cmath>
#include <fstream>
#include <sstream>
#include <stdexcept>

#include "reader.hpp"

namespace cb {

namespace {

Json section(std::vector<std::pair<std::string, Json>> values) {
    Json j = Json::object();
    j.o = std::move(values);
    return j;
}

// str(v) as Python prints a setting value
std::string py_str(const Json& v) {
    switch (v.type) {
        case Json::Bool: return v.b ? "True" : "False";
        case Json::Int: return std::to_string(v.i);
        case Json::Num: return py_repr(v.d);
        case Json::Str: return v.s;
        case Json::Null: return "None";
        default: return v.dump();
    }
}

// Python int(s): optional sign, digits with single underscores between them, whitespace around
bool parse_int(std::string_view s, long long& out) {
    s = py_strip(s);
    std::string digits;
    size_t k = (!s.empty() && (s[0] == '+' || s[0] == '-')) ? 1 : 0;
    if (k == s.size()) return false;
    for (size_t j = k; j < s.size(); j++) {
        if (s[j] == '_' && j > k && j + 1 < s.size() && s[j - 1] != '_' && s[j + 1] != '_') continue;
        if (s[j] < '0' || s[j] > '9') return false;
        digits += s[j];
    }
    out = std::stoll(digits);
    if (s[0] == '-') out = -out;
    return true;
}

Json convert(const Json& v, const Json& def) {
    if (def.type == Json::Bool) {
        if (v.type == Json::Bool) return Json(v.b);
        std::string s = py_str(v);
        for (auto& c : s) c = char(std::tolower((unsigned char)c));
        return Json(s == "true");
    }
    if (def.type == Json::Int) {
        switch (v.type) {
            case Json::Bool: return Json((long long)v.b);
            case Json::Int: return v;
            case Json::Num:
                if (!std::isfinite(v.d)) throw std::invalid_argument("cannot convert float " + py_repr(v.d) + " to integer");
                return Json((long long)std::trunc(v.d));
            case Json::Str: {
                long long i;
                if (!parse_int(v.s, i)) throw std::invalid_argument("invalid literal for int() with base 10: '" + v.s + "'");
                return Json(i);
            }
            default: throw std::invalid_argument("int() argument must be a string or a number");
        }
    }
    if (def.type == Json::Num) {
        switch (v.type) {
            case Json::Bool: return Json(v.b ? 1.0 : 0.0);
            case Json::Int: return Json(double(v.i));
            case Json::Num: return v;
            case Json::Str: {
                double d;
                if (!py_float(v.s, d)) throw std::invalid_argument("could not convert string to float: '" + v.s + "'");
                return Json(d);
            }
            default: throw std::invalid_argument("float() argument must be a string or a real number");
        }
    }
    return Json(py_str(v));
}

// the TOML color-batching.toml uses: [section] tables of key = bool / number / string
// ponytail: no arrays, inline tables, dotted keys or multi-line strings; add them if the file ever needs them
Json parse_toml(const std::string& text) {
    Json out = Json::object();
    Json* cur = nullptr;
    std::istringstream in(text);
    std::string raw;
    int n = 0;
    auto fail = [&]() { throw std::runtime_error("color-batching.toml line " + std::to_string(n) + ": cannot read '" + raw + "'"); };
    while (std::getline(in, raw)) {
        n++;
        std::string_view line = py_strip(raw);
        if (line.empty() || line[0] == '#') continue;
        if (line[0] == '[') {
            size_t close = line.find(']');
            if (close == std::string_view::npos) fail();
            std::string name(py_strip(line.substr(1, close - 1)));
            cur = &out[name];
            if (cur->type == Json::Null) *cur = Json::object();
            continue;
        }
        size_t eq = line.find('=');
        if (eq == std::string_view::npos || !cur) fail();
        std::string key(py_strip(line.substr(0, eq)));
        std::string_view v = py_strip(line.substr(eq + 1));
        if (v.empty()) fail();
        Json value;
        if (v[0] == '"' || v[0] == '\'') {
            std::string s;
            size_t k = 1;
            bool closed = false;
            for (; k < v.size(); k++) {
                char c = v[k];
                if (c == v[0]) {
                    closed = true;
                    break;
                }
                if (c == '\\' && v[0] == '"' && k + 1 < v.size()) {
                    char e = v[++k];
                    s += e == 'n' ? '\n' : e == 't' ? '\t' : e == 'r' ? '\r' : e;
                } else {
                    s += c;
                }
            }
            if (!closed) fail();
            std::string_view rest = py_strip(v.substr(k + 1));
            if (!rest.empty() && rest[0] != '#') fail();
            value = Json(s);
        } else {
            v = py_strip(v.substr(0, v.find('#')));
            long long i;
            double d;
            if (v == "true" || v == "false") {
                value = Json(v == "true");
            } else if (parse_int(v, i)) {
                value = Json(i);
            } else {
                std::string num;
                for (char c : v)
                    if (c != '_') num += c;
                if (!py_float(num, d)) fail();
                value = Json(d);
            }
        }
        (*cur)[key] = value;
    }
    return out;
}

}  // namespace

const Json& defaults() {
    static const Json d = section({
        {"nozzle", section({{"tip_diameter", 1.0}, {"cone_angle", 45.0}, {"clear_height", 2.0}, {"contact", 0.2}})},
        {"stack", section({{"max_band_layers", 10},
                           {"surface_tolerance", 0.0},
                           {"min_piece", 2.0},
                           {"per_object", true},       // plan with and without, keep the faster
                           {"staggered", true},        // also plan each colour on its own schedule, keep the faster
                           {"free_support", true},     // support printed with any filament may change filament
                           {"resolution", 0.1}})},
        {"cooling", section({{"respect_min_layer_time", true},
                             {"stacked_fan", true},
                             {"lift_wait", false},
                             {"min_layer_time", 0.0},       // seconds; 0 = the file's own per filament
                             {"tower_between", true}})},
        {"swap", section({{"lift", 1.0}})},
        {"export", section({{"printer_host", ""}})},
        {"printer", section({{"max_velocity", 500.0},       // only for files without the slicer's machine limits
                             {"max_accel", 20000.0},
                             {"square_corner_velocity", 9.0},
                             {"max_z_velocity", 20.0},
                             {"max_z_accel", 500.0}})},
    });
    return d;
}

const std::vector<Help>& help() {
    static const std::vector<Help> h = {
        {"nozzle.tip_diameter", "Tip diameter", "mm", "Flat face at the nozzle tip."},
        {"nozzle.cone_angle", "Cone angle", "\xC2\xB0", "Cone half-angle from vertical."},
        {"nozzle.clear_height", "Clear height", "mm", "How far the tip sticks out below the heater block / sock / duct."},
        {"nozzle.contact", "Edge contact", "mm",
         "How much taller a neighbouring colour's edge may be while the nozzle face slides past it. 0.2 = two layers "
         "per colour, 0.4 = three."},
        {"stack.max_band_layers", "Max layers per colour", "", "Most layers one colour prints before the next swap."},
        {"stack.surface_tolerance", "Edge shift", "mm", "How far a colour edge on a visible surface may move. 0 = never."},
        {"stack.min_piece", "Min piece", "mm", "Shorter leftovers between handed-over pieces are handed over too."},
        {"stack.per_object", "Per-object batching", "",
         "Finish one object's block before moving to the next, where that is faster (both ways are planned and the "
         "faster kept)."},
        {"stack.staggered", "Staggered colours", "",
         "Each colour on its own schedule: fewer swaps where 3+ colours share layers. Both ways are planned, the "
         "faster kept. Slower processing on those files."},
        {"stack.free_support", "Free support filament", "",
         "Support the slicer printed with whatever filament was loaded (support filament 'Default') may be printed by "
         "another filament of the same material, so it stacks. Support (or interface) with a chosen filament keeps "
         "it."},
        {"stack.resolution", "Resolution", "mm", "Sampling step for the collision planning."},
        {"cooling.respect_min_layer_time", "Respect minimum layer time", "",
         "Slow regions printed again straight on top of themselves down to the minimum layer time, as the slicer "
         "does. Outer walls keep their sliced speed. Off: keep all sliced speeds (the fan rule still applies)."},
        {"cooling.stacked_fan", "Fan for stacked regions", "",
         "Raise the part fan on regions printed again straight on top of themselves, by the slicer's own fan rule."},
        {"cooling.lift_wait", "Lift and wait", "",
         "If a stacked region is still under the minimum layer time at minimum speed, lift clear and wait out the "
         "rest. Can leave ooze strings."},
        {"cooling.min_layer_time", "Min layer time", "s",
         "Minimum layer time for stacked regions. 0 = the file's own (per filament, where its slow-down is on)."},
        {"cooling.tower_between", "Tower between layers", "",
         "Print the prime tower's walls and grid between a colour's stacked layers, as cooling time. The wipe still "
         "comes right after the swap."},
        {"swap.lift", "Swap lift", "mm",
         "Before each colour change, lift this far above the tallest point printed so far (at least the slicer's Z "
         "hop), so the trip to the purge tray clears every stack."},
        {"export.printer_host", "Printer", "",
         "Moonraker address for Send to printer, e.g. 192.168.1.20 or 192.168.1.20:7125."},
        {"printer.max_velocity", "Max velocity", "mm/s",
         "Fallback for the time estimate, for files without the slicer's machine limits."},
        {"printer.max_accel", "Max accel", "mm/s\xC2\xB2", "Fallback for the time estimate."},
        {"printer.square_corner_velocity", "Square corner velocity", "mm/s", "Fallback for the time estimate."},
        {"printer.max_z_velocity", "Max Z velocity", "mm/s", "Fallback for the time estimate."},
        {"printer.max_z_accel", "Max Z accel", "mm/s\xC2\xB2", "Fallback for the time estimate."},
    };
    return h;
}

std::filesystem::path config_path(const std::filesystem::path& root) {
    auto p = root / "color-batching.toml", old = root / "colorstack.toml";
    return !std::filesystem::exists(p) && std::filesystem::exists(old) ? old : p;
}

std::filesystem::path find_root(const std::filesystem::path& start) {
    for (auto p = start; !p.empty(); p = p.parent_path()) {
        if (std::filesystem::exists(p / "viewer" / "index.html")) return p;
        if (p == p.parent_path()) break;
    }
    return start;
}

Json load_config(const std::filesystem::path& path) {
    Json cfg = defaults();
    if (std::filesystem::exists(path)) {
        std::ifstream f(path, std::ios::binary);
        std::stringstream ss;
        ss << f.rdbuf();
        for (const auto& [name, values] : parse_toml(ss.str()).o) {
            if (!cfg.get(name)) continue;       // sections of removed features are dropped
            for (const auto& [k, v] : values.o) cfg[name][k] = v;
        }
    }
    return cfg;
}

Json& apply(Json& cfg, const Json& values) {
    for (const auto& [name, defs] : defaults().o)
        for (const auto& [key, def] : defs.o) {
            const Json* sec = values.get(name);
            const Json* given = sec ? sec->get(key) : nullptr;
            const Json* have = cfg.get(name) ? cfg.get(name)->get(key) : nullptr;
            Json v = given ? *given : have ? *have : def;
            cfg[name][key] = convert(v, def);
        }
    return cfg;
}

void save_config(const Json& cfg, const std::filesystem::path& path) {
    std::string out = "# Color Batching settings (written by the app; safe to edit by hand)\n";
    for (const auto& [name, values] : cfg.o) {
        out += "\n[" + name + "]\n";
        for (const auto& [k, v] : values.o) {
            std::string text;
            if (v.type == Json::Bool) {
                text = v.b ? "true" : "false";
            } else if (v.type == Json::Str) {
                text = "\"";
                for (char c : v.s) text += c == '\\' ? "\\\\" : c == '"' ? "\\\"" : std::string(1, c);
                text += "\"";
            } else {
                text = py_str(v);
            }
            out += k + " = " + text + "\n";
        }
    }
    std::ofstream(path, std::ios::binary) << out;      // LF (Python's text mode writes CR LF on Windows)
}

double num(const Json& cfg, const char* sec, const char* key) {
    const Json* v = cfg.get(sec)->get(key);
    return v->type == Json::Int ? double(v->i) : v->d;
}

bool flag(const Json& cfg, const char* sec, const char* key) { return cfg.get(sec)->get(key)->b; }

}  // namespace cb
