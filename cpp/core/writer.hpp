// Write a band plan back out as G-code (port of colorstack/writer.py).
#pragma once

#include <map>
#include <optional>
#include <set>
#include <string>
#include <tuple>
#include <vector>

#include "geometry.hpp"
#include "model.hpp"

namespace cb {

struct Unit {                     // what prints together: a tool, per object when per-object planning
    int tool = 0;
    std::optional<std::string> obj;
    bool operator==(const Unit& o) const { return tool == o.tool && obj == o.obj; }
    bool operator<(const Unit& o) const { return std::tie(tool, obj) < std::tie(o.tool, o.obj); }
};

struct Band {
    std::vector<int> layers;                                    // program layer indices
    std::vector<Unit> order;
    std::vector<std::vector<std::pair<Unit, PathP>>> work;      // per band layer: (owner, path)
    double ceded_mm = 0, visible_ceded_mm = 0;
    std::vector<std::vector<Unit>> origin;                      // per layer, per piece: the owner it started with
    std::map<Unit, std::pair<double, double>> ceded_by;         // first owner -> (ceded mm, visible ceded mm)
    std::vector<int> tools() const;
};

struct WriterOptions {
    double swap_lift = 1.0;
    bool stacked_fan = true, lift_wait = false;
    double min_layer_time = 0.0;
    bool tower_between = false, respect_min_layer_time = true;
};

std::string fmt(double v);        // f"{v:.3f}" without trailing zeros (and point)

class Writer {
public:
    Writer(const Program& program, const Nozzle& nozzle, const WriterOptions& opts);
    std::vector<std::string> write(const std::vector<Band>& bands, const std::vector<std::string>& summary_lines);

    // what the run did, for the summary
    std::optional<std::vector<double>> min_layer_time;
    std::string min_layer_source;
    int slowed = 0, short_ = 0, fanned = 0, waited = 0;

private:
    const Program& p;
    Nozzle nozzle;
    WriterOptions o;
    Heights heights;
    // retraction (the printer's, then the loaded filament's overrides)
    double retract, retract_f, deretract_f, z_hop, travel_f, min_travel, restart_extra, wipe_dist, before_wipe;
    bool firmware, wipe;
    struct MachineTravel { double retract, z_hop, retract_f, deretract_f, restart_extra, min_travel, wipe_dist, before_wipe; bool wipe; };
    MachineTravel machine{};
    std::vector<double> area, flow_ratio, max_vol;
    std::optional<std::vector<std::vector<double>>> flush;
    double flush_mult;
    std::optional<double> nozzle_t;
    std::vector<int> left;
    int settled = 0;
    std::map<int, std::vector<std::string>> waiting;
    std::optional<std::vector<double>> min_speed, fan_min, fan_max, fan_layer_time;
    int no_fan_layers;
    std::vector<double> layer_time;
    double wipe_f;
    bool role_wipe;
    std::vector<std::pair<int, std::vector<std::string>>> tc_end, tc_start;
    std::vector<std::string> out;
    std::optional<std::pair<double, double>> pos;
    std::optional<double> z, f, accel, scv, height;
    double top = 0.0;
    Vlim vlim;
    Fans fans;
    std::map<StateKey, std::string> state;
    std::optional<std::string> obj, type;
    std::optional<double> width;
    int tool;
    PathP last;
    const std::vector<std::pair<double, double>>* last_pts = nullptr;
    PathP last_pts_owner;
    std::optional<double> object_f;
    bool retracted = false;
    double retracted_by = 0.0;
    std::set<int> touched;

    void use_filament(std::optional<int> t);
    void emit(const std::string& line) { out.push_back(line); }
    double purge_length(int a, int b) const;
    void retract_move();
    void plain_retract();
    void deretract();
    void lift(double z);
    void move_to(double x, double y, double z);
    double clear_z(double x, double y, double z, std::optional<std::pair<double, double>> frm = std::nullopt,
                   std::optional<double> frm_z = std::nullopt) const;
    bool can_replay(const Path& p, const PathP& q) const;
    void set_accel(std::optional<double> a);
    void replay(const std::vector<LeadItem>& lead);
    void travel(double x, double y, double z, std::optional<std::pair<bool, bool>> orca, std::optional<double> high);
    void end_object();
    void set_temp(std::optional<double> t);
    void change_tool(int tool, std::optional<double> temp, const State& state);
    void set_state(const State& items);
    void layer_start(const Layer& layer);
    void outside(const std::vector<std::string>& lines);
    void settle();
    PathP own(const PathP& p, int tool, const Layer& layer);
    PathP refill(const PathP& p, int tool);
    void path(const PathP& p, const Layer& layer);
    std::pair<std::vector<PathP>, double> cool(const std::vector<PathP>& paths, int tool, int layer_index, double extra);
    std::vector<PathP> fan(const std::vector<PathP>& paths, int k, double layer_time, int layer_index);
    void wait(double seconds);
};

}  // namespace cb
