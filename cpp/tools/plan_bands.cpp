// Plan a file with the C++ planner (default settings, plan + tower compensation, as tools/dump_bands.py), write the
// bands in dump_bands.py's format and the G-code for them: both must equal the Python ones.
// plan_bands in.gcode bands.txt out.gcode [per_object]
#include <chrono>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <string>

#include "../core/parse.hpp"
#include "../core/plan.hpp"
#include "../core/tower.hpp"
#include "../core/writer.hpp"

namespace {

std::string b(double v) {
    uint64_t u;
    std::memcpy(&u, &v, sizeof u);
    char buf[17];
    std::snprintf(buf, sizeof buf, "%016llx", (unsigned long long)u);
    return buf;
}
std::string b(const std::optional<double>& v) { return v ? b(*v) : "None"; }
std::string s(const std::optional<std::string>& v) { return v ? *v : "None"; }
std::string s(const std::optional<int>& v) { return v ? std::to_string(*v) : "None"; }

void write_path(std::string& w, const cb::Path& p) {
    w += "P\t" + s(p.tool) + "\t" + p.type + "\t" + b(p.width) + "\t" + b(p.z) + "\t" + b(p.accel) + "\t" + s(p.obj) + "\t" +
         std::to_string(p.seq) + "\t" + std::to_string(int(p.retract_before)) + "\t" + std::to_string(int(p.hop_before)) +
         "\t" + std::to_string(int(p.head)) + "\t" + std::to_string(int(p.tail)) + "\t" + s(p.tower) + "\t" +
         std::to_string(p.block) + "\t" + b(p.scv) + "\t" + b(p.temp) + "\t" + b(p.height) + "\n";
    w += "pts";
    for (auto [x, y] : p.pts) w += "\t" + b(x) + "," + b(y);
    w += "\ne";
    for (double v : p.e) w += "\t" + b(v);
    w += "\nf";
    for (double v : p.f) w += "\t" + b(v);
    w += "\nzs\t";
    if (p.zs.empty()) w += "None";
    for (size_t k = 0; k < p.zs.size(); k++) w += (k ? "\t" : "") + b(p.zs[k]);
    w += "\n";
    for (auto& [k, v] : p.fans) w += "fan\t" + k + "\t" + std::to_string(v) + "\n";
    for (auto& [k, v] : p.vlim) w += "vlim\t" + k + "\t" + v + "\n";
    for (auto& [key, line] : p.state) {
        std::string names;
        for (size_t k = 0; k < key.second.size(); k++) names += (k ? "," : "") + key.second[k];
        w += "st\t" + key.first + "\t" + names + "\t" + line + "\n";
    }
    for (auto& it : p.lead) {
        if (it.kind == 'm') w += "lm\t" + b(it.x) + "\t" + b(it.y) + "\t" + b(it.z) + "\t" + b(it.de) + "\t" + b(it.f) + "\n";
        else if (it.kind == 'a') w += "la\t" + b(it.a) + "\n";
        else w += "ll\t" + it.text + "\n";
    }
    w += "end\n";
}

void save(const char* path, const std::string& text) {
    FILE* f = std::fopen(path, "wb");
    std::fwrite(text.data(), 1, text.size(), f);
    std::fclose(f);
}

}  // namespace

int main(int argc, char** argv) {
    if (argc < 4) {
        std::fprintf(stderr, "plan_bands in.gcode bands.txt out.gcode [per_object]\n");
        return 2;
    }
    cb::Program prog = cb::parse(argv[1]);
    cb::Nozzle nozzle{1.0 / 2, 45.0, 2.0, 0.2};     // DEFAULTS with contact 0.2, as tools/dump_bands.py
    cb::Options opts;
    opts.per_object = argc > 4 && std::string(argv[4]) == "per_object";
    opts.free_support = prog.free_support;
    opts.materials = cb::materials(prog);
    auto t0 = std::chrono::steady_clock::now();
    auto bands = cb::plan(prog, nozzle, opts);
    cb::compensate(prog, bands);
    std::fprintf(stderr, "planned %zu bands in %.2f s\n", bands.size(),
                 std::chrono::duration<double>(std::chrono::steady_clock::now() - t0).count());
    std::string w = "bands\t" + std::to_string(bands.size()) + "\n";
    for (auto& band : bands) {
        w += "band";
        for (int li : band.layers) w += "\t" + std::to_string(prog.layers[li].index);
        w += "\norder";
        for (auto& u : band.order) w += "\t" + std::to_string(u.tool) + "\t" + s(u.obj);
        w += "\n";
        for (size_t j = 0; j < band.work.size(); j++) {
            w += "layer\t" + std::to_string(j) + "\t" + std::to_string(band.work[j].size()) + "\n";
            for (auto& [o, p] : band.work[j]) {
                w += "W\t" + std::to_string(o.tool) + "\t" + s(o.obj) + "\n";
                write_path(w, *p);
            }
        }
    }
    save(argv[2], w);
    cb::WriterOptions wo;      // DEFAULTS, as tools/dump_bands.py
    wo.swap_lift = 1.0;
    wo.stacked_fan = true;
    wo.lift_wait = false;
    wo.min_layer_time = 0.0;
    wo.tower_between = true;
    wo.respect_min_layer_time = true;
    cb::Writer writer(prog, nozzle, wo);
    std::string g;
    for (auto& ln : writer.write(bands, {"COLORSTACK writer comparison"})) g += ln + "\n";
    save(argv[3], g);
    return 0;
}
