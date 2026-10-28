// The parsed print (port of color_batching/model.py): layers of extrusion paths, with the slicer's settings and the state
// each path printed under. Paths are shared pointers: the planner tells paths apart by identity, as Python does by
// object. Tables whose iteration order matters in Python keep insertion order here.
#pragma once

#include <map>
#include <memory>
#include <optional>
#include <set>
#include <string>
#include <tuple>
#include <utility>
#include <vector>

namespace cb {

extern const std::set<std::string> VISIBLE_TYPES;
extern const std::set<std::string> NEUTRAL_TYPES;   // printed by whichever tool is loaded
extern const std::set<std::string> BARRIERS;        // pause / filament change: no band crosses the layer

// {"": S, "P2": S, ...} in the order the file first set them (the writer writes M106 lines in that order)
using Fans = std::vector<std::pair<std::string, int>>;
void set_fan(Fans& fans, const std::string& key, int value);

// what a state command sets (its word and parameter names) -> the line, sorted by key
using StateKey = std::pair<std::string, std::vector<std::string>>;
using State = std::vector<std::pair<StateKey, std::string>>;
using Vlim = std::vector<std::pair<std::string, std::string>>;   // other SET_VELOCITY_LIMIT parameters, sorted

struct LeadItem {          // one step of the slicer's way into a path
    char kind;             // 'm' move, 'l' line (G10 / G11, wipe markers), 'a' acceleration
    double x = 0, y = 0, z = 0, de = 0, f = 0;
    std::string text;
    double a = 0;
};

struct Path {
    std::optional<int> tool;        // none = prime tower
    std::string type;
    std::optional<double> width;
    double z = 0;
    std::vector<std::pair<double, double>> pts;   // one more than segments
    std::vector<double> e, f;                     // filament mm, mm/min per segment
    std::optional<double> accel;
    Fans fans;
    std::optional<std::string> obj;               // EXCLUDE_OBJECT name
    int seq = 0;
    bool retract_before = true, hop_before = true, head = true, tail = true;
    std::optional<std::string> tower;             // "wipe", "grid", "wall"
    int block = -1;
    std::optional<double> scv, temp;
    Vlim vlim;
    std::optional<double> height;
    State state;
    std::vector<double> zs;                       // Z per point on a scarf seam ramp, empty otherwise
    std::vector<LeadItem> lead;

    bool visible() const { return VISIBLE_TYPES.count(type) > 0; }
    double length() const;
    double z0() const { return zs.empty() ? z : zs.front(); }
    double z1() const { return zs.empty() ? z : zs.back(); }
};
using PathP = std::shared_ptr<Path>;

struct Layer {
    int index = 0;
    double z = 0, height = 0;
    std::vector<PathP> paths;
    std::vector<std::string> cmds;   // written at the layer's start
    bool barrier() const;
};

struct Travel {
    double retract, retract_f, deretract_f, z_hop, travel_f, min_travel, restart_extra;
    bool firmware;
};

struct Program {
    std::map<std::string, std::string> settings;
    std::vector<std::string> preamble, postamble, header;
    std::vector<Layer> layers;
    int first_tool = 0;
    std::string tool_format = "T{n}";
    bool abs_e = false, stats_info = false;
    std::string accel_format = "SET_VELOCITY_LIMIT ACCEL={a}";
    std::optional<double> first_temp;
    std::map<int, std::vector<std::optional<double>>> temps;        // tool -> per layer index
    std::map<std::tuple<int, int, std::string>, Fans> fans;          // (tool, layer, type) -> fans set for it
    // (tool, type) -> [(layer index, state)], in insertion order (state_at falls back to iterating it)
    std::vector<std::pair<std::pair<int, std::string>, std::vector<std::pair<int, State>>>> states;
    Travel travel{};
    std::set<std::pair<std::optional<std::string>, std::string>> free_support;
    // tool -> swap block lines, in insertion order (the writer falls back to the first one stored)
    std::vector<std::pair<int, std::vector<std::string>>> toolchange_end, toolchange_start;

    std::optional<double> temp_at(int tool, int index) const;
    const State* state_at(int tool, int index, const std::string& type) const;
    // the setting's comma-separated values as floats, none when missing or not numbers
    std::optional<std::vector<double>> setting_list(const std::string& key) const;
    // filament mm Orca flushes changing from tool a to b ({flush_length}): flush_volumes_matrix x flush_multiplier
    // over the new filament's area; 0 without a matrix
    double flush_length(int a, int b) const;
};

// first value of a per-filament / per-mode setting
double first_setting(const std::map<std::string, std::string>& settings, const std::string& key, double fallback);

}  // namespace cb
