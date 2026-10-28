// Finish a writer output as the pipeline does, in the format of tools/dump_finish.py.
//   finish original.gcode written.gcode final.gcode figures.txt
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <string>

#include "../core/estimate.hpp"
#include "../core/parse.hpp"
#include "../core/reader.hpp"
#include "../core/summary.hpp"

namespace {
std::string b(double v) {
    uint64_t u;
    std::memcpy(&u, &v, sizeof u);
    char buf[17];
    std::snprintf(buf, sizeof buf, "%016llx", (unsigned long long)u);
    return buf;
}
std::string figures(const cb::Estimate& r) {
    std::string ext, into;
    for (auto& [t, v] : r.extruded) ext += (ext.empty() ? "" : " ") + (t ? std::to_string(*t) : "None") + "=" + b(v);
    for (auto& [t, n] : r.swaps_into) into += (into.empty() ? "" : " ") + std::to_string(t) + "=" + std::to_string(n);
    return "motion " + b(r.motion_s) + " dwell " + b(r.dwell_s) + " swaps " + std::to_string(r.swaps) + " swap " +
           b(r.swap_s) + " total " + b(r.total_s) + " each " + b(r.swap_each_s) + " source " + r.source +
           "\n  extruded " + ext + "\n  into " + into + "\n";
}
}  // namespace

int main(int argc, char** argv) {
    if (argc != 5) return 2;
    auto original = cb::load(argv[1]);
    auto written = cb::load(argv[2]);
    cb::Limits lim = cb::machine_limits(cb::parse_settings(original), cb::PrinterFallback{});
    cb::Estimate o = cb::estimate(original, lim);
    auto slicer = cb::file_estimate(original);
    cb::Steps steps;
    cb::Estimate p = cb::estimate(written, lim, &steps);
    cb::anchor(o, p, slicer, lim);
    auto lines = cb::stamp_progress(written, steps, p.swap_each_s, p.total_s);
    cb::stamp_header(lines, o, p);
    FILE* out = std::fopen(argv[3], "wb");
    for (auto& ln : lines) {
        std::fwrite(ln.data(), 1, ln.size(), out);
        std::fputc('\n', out);
    }
    std::fclose(out);
    FILE* fig = std::fopen(argv[4], "wb");
    std::string s = "slicer " + (slicer ? b(*slicer) : std::string("None")) + "\nsteps " + std::to_string(steps.size()) +
                    "\noriginal " + figures(o) + "processed " + figures(p);
    std::fwrite(s.data(), 1, s.size(), fig);
    std::fclose(fig);
    return 0;
}
