// Port of colorstack/parse.py, step for step. Regular expressions are reproduced by hand with Python's matching rules.
#include "parse.hpp"

#include <algorithm>
#include <charconv>
#include <cmath>
#include <set>
#include <stdexcept>

#include "pymath.hpp"
#include "reader.hpp"

namespace cb {

namespace {

const std::set<std::string> DROP = {"G92", "M73", "SET_PRINT_STATS_INFO", "G17", "M117", "SET_VELOCITY_LIMIT",
                                    "M204", "M82", "M83", "G90", "G91"};
const std::set<std::string> TEMPS = {"M104", "M109", "M140", "M190", "M141", "M191"};
const std::set<std::string> STATE_WORDS = {"SET_PRESSURE_ADVANCE", "M900", "M572", "M220", "M221", "M205", "M566"};
const double PI = 3.141592653589793;

bool digit(char c) { return c >= '0' && c <= '9'; }
bool letter(char c) { return (c >= 'A' && c <= 'Z') || (c >= 'a' && c <= 'z'); }
bool word_char(char c) { return letter(c) || digit(c) || c == '_' || (unsigned char)c >= 0x80; }
bool starts(std::string_view s, std::string_view p) { return s.substr(0, p.size()) == p; }

// first whitespace-separated word, upper-cased (Python: s.split()[0].upper())
std::string first_word(std::string_view s) {
    s = py_strip(s);
    size_t n = 0;
    while (n < s.size() && !py_space(s[n])) n++;
    return py_upper(s.substr(0, n));
}

// length of \d*\.?\d+ at s[i] (Python's backtracking), 0 if none
size_t number_at(std::string_view s, size_t i) {
    size_t j = i;
    while (j < s.size() && digit(s[j])) j++;
    if (j + 1 < s.size() && s[j] == '.' && digit(s[j + 1])) {
        j++;
        while (j < s.size() && digit(s[j])) j++;
        return j - i;
    }
    return j > i ? j - i : 0;
}

bool at_boundary(std::string_view s, size_t i) { return i == 0 || !word_char(s[i - 1]); }

// re.search(r"\b" + letter + r"(\d*\.?\d+)", s)
bool search_number(std::string_view s, char key, double& out) {
    for (size_t i = 0; i < s.size(); i++) {
        if (s[i] != key || !at_boundary(s, i)) continue;
        size_t n = number_at(s, i + 1);
        if (n && py_float(s.substr(i + 1, n), out)) return true;
    }
    return false;
}

// re.search(r"\b" + letter + r"(\d+)", s): the digits
bool search_digits(std::string_view s, char key, std::string& out) {
    for (size_t i = 0; i < s.size(); i++) {
        if (s[i] != key || !at_boundary(s, i)) continue;
        size_t j = i + 1;
        while (j < s.size() && digit(s[j])) j++;
        if (j > i + 1) {
            out = std::string(s.substr(i + 1, j - i - 1));
            return true;
        }
    }
    return false;
}

std::optional<double> temp_after(std::string_view cmd, int tool, std::optional<double> current) {
    double s;
    std::string t;
    bool has_s = search_number(cmd, 'S', s);
    bool has_t = search_digits(cmd, 'T', t);
    if (has_s && (!has_t || std::stoi(t) == tool)) return s;
    return current;
}

void fan_after(Fans& fans, const std::string& word, std::string_view s) {
    std::string p;
    bool has_p = search_digits(s, 'P', p);
    std::string key = (!has_p || p == "0") ? "" : "P" + p;
    double sv;
    int value = word == "M107" ? 0 : search_number(s, 'S', sv) ? int(std::trunc(sv)) : 255;
    set_fan(fans, key, value);
}

StateKey state_key(const std::string& word, std::string_view line) {
    std::string_view code = line.substr(0, line.find(';'));
    std::set<std::string> names;
    // \b([A-Za-z_]+)\s*=
    for (size_t i = 0; i < code.size();) {
        if ((letter(code[i]) || code[i] == '_') && at_boundary(code, i)) {
            size_t j = i;
            while (j < code.size() && (letter(code[j]) || code[j] == '_')) j++;
            size_t k = j;
            while (k < code.size() && py_space(code[k])) k++;
            if (k < code.size() && code[k] == '=') {
                names.insert(py_upper(code.substr(i, j - i)));
                i = k + 1;
                continue;
            }
        }
        i++;
    }
    // \s([A-Za-z])-?\.?\d
    for (size_t i = 0; i + 1 < code.size();) {
        if (py_space(code[i]) && letter(code[i + 1])) {
            size_t k = i + 2;
            if (k < code.size() && code[k] == '-') k++;
            if (k < code.size() && code[k] == '.') k++;
            if (k < code.size() && digit(code[k])) {
                names.insert(py_upper(code.substr(i + 1, 1)));
                i = k + 1;
                continue;
            }
        }
        i++;
    }
    return {word, std::vector<std::string>(names.begin(), names.end())};
}

// re.findall(r"(\w+)=(\S+)", s)
std::vector<std::pair<std::string, std::string>> key_values(std::string_view s) {
    std::vector<std::pair<std::string, std::string>> out;
    for (size_t i = 0; i < s.size();) {
        if (word_char(s[i])) {
            size_t j = i;
            while (j < s.size() && word_char(s[j])) j++;
            if (j + 1 < s.size() && s[j] == '=' && !py_space(s[j + 1])) {
                size_t k = j + 1;
                while (k < s.size() && !py_space(s[k])) k++;
                out.emplace_back(std::string(s.substr(i, j - i)), std::string(s.substr(j + 1, k - j - 1)));
                i = k;
                continue;
            }
        }
        i++;
    }
    return out;
}

// re.sub(r"PURGE_LENGTH=\S+", "PURGE_LENGTH={purge}", s)
std::string purge_placeholder(std::string s) {
    const std::string key = "PURGE_LENGTH=";
    std::string out;
    size_t i = 0;
    while (i < s.size()) {
        if (s.compare(i, key.size(), key) == 0 && i + key.size() < s.size() && !py_space(s[i + key.size()])) {
            size_t k = i + key.size();
            while (k < s.size() && !py_space(s[k])) k++;
            out += "PURGE_LENGTH={purge}";
            i = k;
        } else {
            out += s[i++];
        }
    }
    return out;
}

// the most common value, the first seen on a tie (Counter.most_common(1)), else fallback
template <typename T>
T most_common(const std::vector<T>& vals, T fallback) {
    if (vals.empty()) return fallback;
    std::vector<std::pair<T, int>> counts;     // first-seen order
    for (const auto& v : vals) {
        auto it = std::find_if(counts.begin(), counts.end(), [&](const auto& c) { return c.first == v; });
        if (it == counts.end())
            counts.emplace_back(v, 1);
        else
            it->second++;
    }
    auto best = counts.begin();
    for (auto it = counts.begin(); it != counts.end(); ++it)
        if (it->second > best->second) best = it;
    return best->first;
}

}  // namespace

double py_round(double x, int n) {
    char buf[512];
    auto r = std::to_chars(buf, buf + sizeof buf, x, std::chars_format::fixed, n);
    double out;
    std::from_chars(buf, r.ptr, out);
    return out;
}

std::map<std::string, std::string> parse_settings(const std::vector<std::string>& lines) {
    // ^;\s*(\w+)\s*=\s*(.*)$, the value stripped, the first of each key
    std::map<std::string, std::string> out;
    for (const auto& ln : lines) {
        if (ln.empty() || ln[0] != ';' || ln.find('=') == std::string::npos) continue;
        std::string_view s = ln;
        size_t i = 1;
        while (i < s.size() && py_space(s[i])) i++;
        size_t k0 = i;
        while (i < s.size() && word_char(s[i])) i++;
        if (i == k0) continue;
        size_t k1 = i;
        while (i < s.size() && py_space(s[i])) i++;
        if (i >= s.size() || s[i] != '=') continue;
        out.emplace(std::string(s.substr(k0, k1 - k0)), std::string(py_strip(s.substr(i + 1))));
    }
    return out;
}

namespace {

void check_printer(const Program& prog) {
    auto nozzles = prog.setting_list("nozzle_diameter");
    size_t n = nozzles ? nozzles->size() : 0;
    if (n > 1 && first_setting(prog.settings, "single_extruder_multi_material", 0) != 1)
        throw std::runtime_error("the printer has " + std::to_string(n) +
                                 " nozzles; Color Batching supports one nozzle with a filament changer only");
    if (first_setting(prog.settings, "belt_printer", 0) == 1)
        throw std::runtime_error("belt printers are not supported (tilted layers)");
    if (first_setting(prog.settings, "spiral_mode", 0) == 1)
        throw std::runtime_error("spiral vase mode is not supported");
    // Orca print_sequence / PrusaSlicer complete_objects: each object finished before the next; layers of different
    // objects at one Z would be merged and the gantry would meet the finished ones
    auto seq = prog.settings.find("print_sequence");
    if ((seq != prog.settings.end() && py_strip(seq->second) == "by object") ||
        first_setting(prog.settings, "complete_objects", 0) == 1)
        throw std::runtime_error("sequential printing (one object after another) is not supported");
}

}  // namespace

Program parse(const std::string& path_in) {
    std::vector<std::string> lines = load(path_in);
    for (const char* m : {PROCESSED, PROCESSED_BEFORE})
        if (std::find(lines.begin(), lines.end(), "; " + std::string(m)) != lines.end())
            throw std::runtime_error("this file was already processed by Color Batching; open the original");
    Program prog;
    prog.settings = parse_settings(lines);
    if (!lines.empty() && starts(lines[0], "; HEADER_BLOCK_START")) {
        for (const auto& ln : lines) {
            prog.header.push_back(ln);
            if (starts(ln, "; HEADER_BLOCK_END")) break;
        }
    }

    // first pass: where the layers start and end, the tool before them
    bool any_marker = false;
    size_t first_marker = 0, first_extrusion = 0, last_extrusion = 0;
    bool any_extrusion = false;
    std::vector<std::pair<size_t, int>> tools_seen;
    read(lines, [&](size_t i, const Event& ev) {
        if (ev.kind == Kind::layer && !any_marker) {
            any_marker = true;
            first_marker = i;
        } else if (ev.kind == Kind::move && ev.move.de > 0 && ev.move.xy() > 1e-9) {
            if (!any_extrusion) first_extrusion = i;
            any_extrusion = true;
            last_extrusion = i;
        } else if (ev.kind == Kind::tool) {
            tools_seen.emplace_back(i, ev.tool);
        }
    });
    if (!any_extrusion) throw std::runtime_error("no extrusion moves found");
    size_t first_layer = any_marker ? first_marker : first_extrusion;
    // end G-code: Orca/Prusa mark it ;TYPE:Custom after the layers; otherwise it starts after the last extrusion
    size_t last_custom = 0;
    bool any_custom = false;
    for (size_t i = first_layer + 1; i < lines.size(); i++)
        if (starts(lines[i], ";TYPE:Custom")) {
            any_custom = true;
            last_custom = i;
        }
    size_t end = (any_custom && last_custom > last_extrusion) ? last_custom : last_extrusion + 1;
    prog.preamble.assign(lines.begin(), lines.begin() + first_layer);
    prog.postamble.assign(lines.begin() + std::min(end, lines.size()), lines.end());
    check_printer(prog);

    for (const auto& ln : lines)
        if (starts(ln, "SET_PRINT_STATS_INFO")) {
            prog.stats_info = true;
            break;
        }
    std::string last_mode;
    for (const auto& ln : prog.preamble) {
        std::string w = py_upper(py_strip(std::string_view(ln).substr(0, ln.find(';'))));
        if (w == "M82" || w == "M83") last_mode = w;
    }
    prog.abs_e = last_mode == "M82";

    // nozzle temperature: the first layer's from the settings (start G-code may set it by macro), else the start
    // G-code's own M104/M109
    int pre_tool = 0;
    for (auto& [i, t] : tools_seen)
        if (i < first_layer) pre_tool = t;
    auto initial = prog.setting_list("nozzle_temperature_initial_layer");
    if (!initial || initial->empty()) initial = prog.setting_list("first_layer_temperature");
    bool has_initial = initial && !initial->empty();
    std::optional<double> temp;
    if (has_initial) temp = (*initial)[std::min<size_t>(pre_tool, initial->size() - 1)];

    std::vector<Layer> layers;
    Layer* cur = nullptr;
    std::optional<double> accel, scv, width, height;
    std::string typ;
    std::optional<std::string> obj;
    Vlim vlim;
    Fans fans;
    std::map<StateKey, std::string> state;
    std::vector<LeadItem> lead;
    std::vector<double> zs;
    PathP path;
    int seq = 0, tool = 0;
    bool retracted = false, hopped = false, in_toolchange = false, fmt_set = false, accel_seen = false;
    bool firmware = false;
    std::vector<std::string> tc_lines;
    std::optional<size_t> tc_split;
    int tc_from = 0;
    std::string tower_sec = "wall";
    int block = -1;
    std::vector<std::pair<double, double>> retracts;
    std::vector<double> deretracts, hops, travels;

    auto new_layer = [&]() {
        layers.emplace_back();
        layers.back().index = int(layers.size()) - 1;
        cur = &layers.back();
    };
    auto close = [&]() {
        if (path && !path->e.empty()) {
            auto [lo, hi] = std::minmax_element(zs.begin(), zs.end());
            if (*hi - *lo > 1e-6) {     // a scarf seam ramp: Z per point, z the top
                path->zs = zs;
                path->z = *hi;
            }
            cur->paths.push_back(path);
            seq++;
            retracted = hopped = false;
        }
        path.reset();
    };

    bool stop = false;
    read(lines, [&](size_t i, const Event& ev) {
        if (stop || i >= end) {
            stop = true;
            return;
        }
        bool live = i >= first_layer;
        if (ev.kind == Kind::move) {
            const Move& m = ev.move;
            if (!live) return;
            double mxy = m.xy();
            if (!any_marker && m.de > 0 && mxy > 1e-9 && (cur == nullptr || m.z1 > cur->z + 1e-4)) {
                close();
                new_layer();
                cur->z = m.z1;
            }
            if (cur == nullptr) new_layer();
            if (mxy < 1e-9 && m.de == 0 && std::fabs(m.z1 - m.z0) < 1e-9) return;    // feedrate-only line
            if (m.de > 0 && mxy > 1e-9) {
                if (!path) {
                    path = std::make_shared<Path>();
                    bool neutral = NEUTRAL_TYPES.count(typ) > 0;
                    if (!neutral) path->tool = tool;
                    path->type = typ;
                    path->width = width;
                    path->z = m.z1;
                    path->pts.emplace_back(m.x0, m.y0);
                    path->accel = accel;
                    path->fans = fans;
                    path->obj = obj;
                    path->seq = seq;
                    path->retract_before = retracted;
                    path->hop_before = hopped;
                    path->scv = scv;
                    path->temp = temp;
                    path->vlim = vlim;
                    path->height = height;
                    path->state.assign(state.begin(), state.end());
                    path->lead = std::move(lead);
                    lead.clear();
                    zs.assign(1, m.z0);
                    if (neutral) {
                        path->tower = tower_sec;
                        path->block = tower_sec == "wipe" ? block : -1;
                    }
                }
                path->pts.emplace_back(m.x1, m.y1);
                path->e.push_back(m.de);
                path->f.push_back(m.f);
                zs.push_back(m.z1);
            } else {
                close();        // travel, Z move, retract or deretract
                LeadItem it{'m', m.x1, m.y1, m.z1, m.de, m.f};
                lead.push_back(it);
                if (m.de < 0) {
                    retracted = true;
                    if (mxy < 1e-9) retracts.emplace_back(py_round(-m.de, 3), m.f);
                } else if (m.de > 0 && mxy < 1e-9) {
                    deretracts.push_back(m.f);
                }
                if (m.z1 > m.z0 + 1e-9) {
                    hopped = true;
                    if (mxy < 1e-9) hops.push_back(py_round(m.z1 - m.z0, 3));
                }
                if (mxy > 1e-9 && m.de == 0) travels.push_back(m.f);
            }
            return;
        }
        if (ev.kind == Kind::tool) {
            if (live) {
                close();
                if (!fmt_set) {
                    std::string fmt = ev.text;
                    size_t k = 1;
                    while (k < fmt.size() && digit(fmt[k])) k++;
                    if (fmt[0] == 'T' && k > 1) fmt = "T{n}" + fmt.substr(k);
                    prog.tool_format = purge_placeholder(fmt);
                    fmt_set = true;
                }
            } else {
                prog.first_tool = ev.tool;
            }
            if (in_toolchange) {
                tc_split = tc_lines.size();
                tc_from = tool;
            }
            tool = ev.tool;
            return;
        }
        if (ev.kind == Kind::accel) {
            if (live) {
                close();
                if (!accel_seen) {
                    prog.accel_format = first_word(lines[i]) == "M204" ? "M204 S{a}" : "SET_VELOCITY_LIMIT ACCEL={a}";
                    accel_seen = true;
                }
                LeadItem it{'a'};
                it.a = ev.value;
                lead.push_back(it);     // travel / wipe acceleration between paths (PrusaSlicer M204 S...)
            }
            accel = ev.value;
            return;
        }
        if (ev.kind == Kind::scv) {
            if (live) close();
            scv = ev.value;
            return;
        }
        if (!live) {
            if (ev.kind == Kind::cmd) {
                std::string w = first_word(ev.text);
                if (w == "M106" || w == "M107")
                    fan_after(fans, w, ev.text);
                else if ((w == "M104" || w == "M109") && !has_initial)
                    temp = temp_after(ev.text, tool, temp);
            }
            return;
        }
        switch (ev.kind) {
            case Kind::layer:
                close();
                new_layer();
                break;
            case Kind::z:
                if (cur) cur->z = ev.value;
                break;
            case Kind::height:      // Orca: after ;Z: the gap to the layer below, after ;TYPE: the line height
                close();
                height = ev.value;
                if (cur && cur->height == 0.0) cur->height = ev.value;
                break;
            case Kind::type:
                close();
                typ = ev.text;
                break;
            case Kind::width:
                close();
                width = ev.value;
                break;
            case Kind::comment: {
                const std::string& v = ev.text;
                if (starts(v, ";WIPE_START") || starts(v, ";WIPE_END")) {
                    LeadItem it{'l'};
                    it.text = v;
                    lead.push_back(it);
                } else if (starts(v, "; CP TOOLCHANGE UNLOAD")) {
                    in_toolchange = true;
                    tc_lines.clear();
                    tc_split.reset();
                } else if (starts(v, "; CP TOOLCHANGE WIPE")) {
                    in_toolchange = false;
                    if (tc_split) {     // filament end G-code before T, the next filament's start G-code after
                        auto setdefault = [](auto& blocks, int key, std::vector<std::string> lines) {
                            for (auto& kv : blocks)
                                if (kv.first == key) return;
                            blocks.emplace_back(key, std::move(lines));
                        };
                        setdefault(prog.toolchange_end, tc_from,
                                   std::vector<std::string>(tc_lines.begin(), tc_lines.begin() + *tc_split));
                        setdefault(prog.toolchange_start, tool,
                                   std::vector<std::string>(tc_lines.begin() + *tc_split, tc_lines.end()));
                    }
                    close();
                    block++;
                    tower_sec = "wipe";
                } else if (starts(v, "; WIPE_TOWER_END") || starts(v, "; CP EMPTY GRID END")) {
                    close();
                    tower_sec = "wall";
                } else if (starts(v, "; CP EMPTY GRID START")) {
                    close();
                    tower_sec = "grid";
                }
                break;
            }
            case Kind::cmd: {
                const std::string& v = ev.text;
                std::string w = first_word(v);
                std::vector<std::pair<std::string, std::string>> extra;
                if (w == "SET_VELOCITY_LIMIT")
                    for (auto& [k, val] : key_values(v)) {
                        std::string ku = py_upper(k);
                        if (ku == "ACCEL" || ku == "SQUARE_CORNER_VELOCITY") continue;
                        auto it = std::find_if(extra.begin(), extra.end(), [&](auto& kv) { return kv.first == ku; });
                        if (it == extra.end())
                            extra.emplace_back(ku, val);
                        else
                            it->second = val;
                    }
                if (w == "M106" || w == "M107") {
                    close();
                    fan_after(fans, w, v);
                    if (in_toolchange) tc_lines.push_back(v);
                } else if (w == "EXCLUDE_OBJECT_START") {
                    close();
                    size_t n = v.find("NAME=");
                    if (n != std::string::npos)
                        obj = v.substr(n + 5);
                    else
                        obj.reset();
                } else if (w == "EXCLUDE_OBJECT_END") {
                    close();
                    obj.reset();
                } else if (w == "M104" || w == "M109") {     // nozzle: kept per path, the writer sets it before each
                    close();
                    temp = temp_after(v, tool, temp);
                } else if (TEMPS.count(w)) {
                    close();
                    cur->cmds.push_back(v);
                } else if (STATE_WORDS.count(w)) {
                    close();
                    state[state_key(w, v)] = v;
                } else if (w == "SET_VELOCITY_LIMIT" && !extra.empty()) {
                    close();
                    std::map<std::string, std::string> merged(vlim.begin(), vlim.end());
                    for (auto& [k, val] : extra) merged[k] = val;
                    vlim.assign(merged.begin(), merged.end());
                } else if (DROP.count(w)) {
                } else if (w == "G10" || w == "G11") {      // firmware retraction: a retract / unretract like any other
                    close();
                    retracted = w == "G10";
                    firmware = true;
                    LeadItem it{'l'};
                    it.text = w;
                    lead.push_back(it);
                } else if (in_toolchange) {
                    tc_lines.push_back(v);
                } else if (w != "G4" && w != "M400") {
                    close();
                    cur->cmds.push_back(v);
                }
                break;
            }
            default:
                break;
        }
    });
    close();
    for (const auto& L : layers)
        if (!L.paths.empty()) {
            prog.first_temp = L.paths.front()->temp;
            break;
        }

    // layer Z / height when the slicer did not say
    double prev = 0.0;
    for (auto& L : layers) {
        if (L.z == 0.0 && !L.paths.empty()) {
            double lo = L.paths.front()->z;
            for (auto& p : L.paths) lo = std::min(lo, p->z);
            L.z = lo;
        }
        if (L.height == 0.0)
            L.height = L.z > prev ? py_round(L.z - prev, 4) : first_setting(prog.settings, "layer_height", 0.2);
        prev = L.z;
    }
    for (auto& L : layers)
        if (!L.paths.empty() || !L.cmds.empty()) prog.layers.push_back(std::move(L));
    // one physical layer per Z: a slicer that batches colours itself (PrusaSlicer 3.0) starts a layer marker per tool
    // pass and goes back down. Markers at the same Z become one layer (paths and commands in file order), layers in Z
    // order, a height that is not positive taken from the Z gap.
    bool revisits = false;
    for (size_t k = 1; k < prog.layers.size(); k++) revisits = revisits || prog.layers[k].z <= prog.layers[k - 1].z + 1e-4;
    if (revisits) {
        std::vector<Layer> merged;
        std::map<double, size_t> at;    // round(z, 4) -> position in merged
        for (auto& L : prog.layers) {
            double key = py_round(L.z, 4);
            auto it = at.find(key);
            if (it != at.end()) {
                Layer& m = merged[it->second];
                m.paths.insert(m.paths.end(), L.paths.begin(), L.paths.end());
                m.cmds.insert(m.cmds.end(), L.cmds.begin(), L.cmds.end());
                m.height = std::max(m.height, L.height);
            } else {
                at[key] = merged.size();
                merged.push_back(std::move(L));
            }
        }
        std::stable_sort(merged.begin(), merged.end(), [](const Layer& a, const Layer& b) { return a.z < b.z; });
        double prev_z = 0.0;
        for (auto& L : merged) {
            if (L.height <= 0)
                L.height = L.z > prev_z ? py_round(L.z - prev_z, 4) : first_setting(prog.settings, "layer_height", 0.2);
            prev_z = L.z;
        }
        prog.layers = std::move(merged);
    }
    for (size_t k = 0; k < prog.layers.size(); k++) prog.layers[k].index = int(k);

    std::map<std::pair<std::optional<std::string>, std::string>, std::set<int>> support_tools;
    for (auto& L : prog.layers)
        for (auto& p : L.paths)
            if (p->tool && starts(p->type, "Support")) support_tools[{p->obj, p->type}].insert(*p->tool);
    for (auto& [k, tools] : support_tools)
        if (tools.size() > 1) prog.free_support.insert(k);
    for (auto& L : prog.layers)
        for (auto& p : L.paths) {
            if (!p->tool) continue;
            prog.fans.emplace(std::make_tuple(*p->tool, L.index, p->type), p->fans);
            auto key = std::make_pair(*p->tool, p->type);
            auto it = std::find_if(prog.states.begin(), prog.states.end(), [&](auto& kv) { return kv.first == key; });
            if (it == prog.states.end()) {
                prog.states.emplace_back(key, std::vector<std::pair<int, State>>{});
                it = prog.states.end() - 1;
            }
            if (it->second.empty() || it->second.back().first != L.index) it->second.emplace_back(L.index, p->state);
        }
    std::set<int> used;
    for (auto& L : prog.layers)
        for (auto& p : L.paths)
            if (p->tool) used.insert(*p->tool);
    for (int t : used) {
        std::vector<std::optional<double>> col;
        for (auto& L : prog.layers) {
            std::optional<double> v;
            for (auto& p : L.paths)
                if (p->tool == t && p->temp) {
                    v = p->temp;
                    break;
                }
            col.push_back(v);
        }
        std::optional<double> last;     // fill from below, then from above
        for (auto& c : col) c = last = c ? c : last;
        last.reset();
        for (auto it = col.rbegin(); it != col.rend(); ++it) *it = last = *it ? *it : last;
        prog.temps[t] = col;
    }

    // line width when the slicer did not write it: filament volume / (length * layer height)
    auto diam = prog.setting_list("filament_diameter");
    if (!diam) diam = std::vector<double>{1.75};
    for (auto& L : prog.layers)
        for (auto& p : L.paths) {
            if (p->width) continue;
            double length = p->length();
            double d = (p->tool && size_t(*p->tool) < diam->size()) ? (*diam)[*p->tool] : (*diam)[0];
            double area = PI * d * d / 4;
            double e = 0;
            for (double v : p->e) e += v;
            double h = (p->height && *p->height != 0.0) ? *p->height : L.height;
            p->width = length > 0 ? py_round(e * area / std::max(length * h, 1e-9), 3) : 0.4;
        }

    // travel style: the slicer's settings, else what the file itself does
    auto [r_len, r_f] = most_common(retracts, std::make_pair(0.8, 1800.0));
    auto& t = prog.travel;
    const auto& st = prog.settings;
    t.retract = first_setting(st, "retraction_length", r_len);
    t.retract_f = first_setting(st, "retraction_speed", r_f / 60) * 60;
    double dr = first_setting(st, "deretraction_speed", 0) * 60;
    t.deretract_f = dr != 0.0 ? dr : most_common(deretracts, r_f);
    t.z_hop = first_setting(st, "z_hop", most_common(hops, 0.4));
    t.travel_f = first_setting(st, "travel_speed", most_common(travels, 9000.0) / 60) * 60;
    t.min_travel = first_setting(st, "retraction_minimum_travel", 1.0);
    t.firmware = firmware || first_setting(st, "use_firmware_retraction", 0) == 1;
    t.restart_extra = first_setting(st, "retract_restart_extra", 0.0);
    return prog;
}

}  // namespace cb
