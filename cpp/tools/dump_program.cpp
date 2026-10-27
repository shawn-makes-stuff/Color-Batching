// Dump a parsed program in the format of tools/dump_program.py (floats as 64-bit patterns).
#include <algorithm>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <map>
#include <string>

#include "../core/parse.hpp"

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

std::string fans(const cb::Fans& f) {
    std::string out;
    for (size_t k = 0; k < f.size(); k++) out += (k ? "," : "") + f[k].first + "=" + std::to_string(f[k].second);
    return out;
}

std::string state(const cb::State& st) {
    std::string out;
    for (size_t k = 0; k < st.size(); k++) {
        const auto& [key, line] = st[k];
        std::string names;
        for (size_t n = 0; n < key.second.size(); n++) names += (n ? "." : "") + key.second[n];
        out += (k ? ";" : "") + key.first + "(" + names + ")=" + line;
    }
    return out;
}

std::string lead(const std::vector<cb::LeadItem>& items) {
    std::string out;
    for (size_t k = 0; k < items.size(); k++) {
        const auto& it = items[k];
        out += k ? " | " : "";
        if (it.kind == 'm')
            out += "m " + b(it.x) + " " + b(it.y) + " " + b(it.z) + " " + b(it.de) + " " + b(it.f);
        else if (it.kind == 'a')
            out += "a " + b(it.a);
        else
            out += "l " + it.text;
    }
    return out;
}

}  // namespace

int main(int argc, char** argv) {
    if (argc != 3) {
        std::fprintf(stderr, "dump_program in.gcode out.txt\n");
        return 2;
    }
    cb::Program prog;
    try {
        prog = cb::parse(argv[1]);
    } catch (const std::exception& e) {
        std::fprintf(stderr, "%s\n", e.what());
        return 1;
    }
    FILE* out = std::fopen(argv[2], "wb");
    auto w = [&](const std::string& t) { std::fwrite(t.data(), 1, t.size(), out); };
    for (auto& [k, v] : prog.settings) w("setting " + k + "=" + v + "\n");
    for (auto [name, lines] : {std::make_pair("header", &prog.header), std::make_pair("preamble", &prog.preamble),
                               std::make_pair("postamble", &prog.postamble)}) {
        w(std::string(name) + " " + std::to_string(lines->size()) + "\n");
        for (auto& ln : *lines) w("| " + ln + "\n");
    }
    w("first_tool " + std::to_string(prog.first_tool) + "\ntool_format " + prog.tool_format + "\nabs_e " +
      std::to_string(int(prog.abs_e)) + "\nstats_info " + std::to_string(int(prog.stats_info)) + "\naccel_format " +
      prog.accel_format + "\nfirst_temp " + b(prog.first_temp) + "\n");
    const auto& t = prog.travel;
    w("travel deretract_f " + b(t.deretract_f) + "\ntravel firmware " + std::to_string(int(t.firmware)) +
      "\ntravel min_travel " + b(t.min_travel) + "\ntravel restart_extra " + b(t.restart_extra) + "\ntravel retract " +
      b(t.retract) + "\ntravel retract_f " + b(t.retract_f) + "\ntravel travel_f " + b(t.travel_f) +
      "\ntravel z_hop " + b(t.z_hop) + "\n");
    std::vector<std::pair<std::string, std::string>> fs;
    for (auto& [obj, typ] : prog.free_support) fs.emplace_back(s(obj), typ);
    std::sort(fs.begin(), fs.end());
    for (auto& [obj, typ] : fs) w("free_support " + obj + " " + typ + "\n");
    for (auto [name, blocks] : {std::make_pair("toolchange_end", &prog.toolchange_end),
                                std::make_pair("toolchange_start", &prog.toolchange_start)})
        for (auto& [tool, lines] : std::map<int, std::vector<std::string>>(blocks->begin(), blocks->end())) {
            w(std::string(name) + " " + std::to_string(tool) + " " + std::to_string(lines.size()) + "\n");
            for (auto& ln : lines) w("| " + ln + "\n");
        }
    for (auto& [tool, col] : prog.temps) {
        std::string row = "temps " + std::to_string(tool) + " ";
        for (size_t k = 0; k < col.size(); k++) row += (k ? " " : "") + b(col[k]);
        w(row + "\n");
    }
    for (auto& [key, f] : prog.fans)
        w("fans " + std::to_string(std::get<0>(key)) + " " + std::to_string(std::get<1>(key)) + " " + std::get<2>(key) +
          ": " + fans(f) + "\n");
    for (size_t n = 0; n < prog.states.size(); n++) {
        const auto& [key, rows] = prog.states[n];
        std::string row = "states " + std::to_string(n) + " " + std::to_string(key.first) + " " + key.second + ": ";
        for (size_t r = 0; r < rows.size(); r++) row += (r ? " # " : "") + std::to_string(rows[r].first) + " " + state(rows[r].second);
        w(row + "\n");
    }
    for (auto& L : prog.layers) {
        w("layer " + std::to_string(L.index) + " " + b(L.z) + " " + b(L.height) + " paths " +
          std::to_string(L.paths.size()) + " cmds " + std::to_string(L.cmds.size()) + "\n");
        for (auto& c : L.cmds) w("| " + c + "\n");
        for (auto& pp : L.paths) {
            const cb::Path& p = *pp;
            w("path " + s(p.tool) + " " + p.type + " w=" + b(p.width) + " z=" + b(p.z) + " accel=" + b(p.accel) +
              " obj=" + s(p.obj) + " seq=" + std::to_string(p.seq) + " rb=" + std::to_string(int(p.retract_before)) +
              " hb=" + std::to_string(int(p.hop_before)) + " head=" + std::to_string(int(p.head)) + " tail=" +
              std::to_string(int(p.tail)) + " tower=" + s(p.tower) + " block=" + std::to_string(p.block) + " scv=" +
              b(p.scv) + " temp=" + b(p.temp) + " height=" + b(p.height) + "\n");
            std::string vl;
            for (size_t k = 0; k < p.vlim.size(); k++) vl += (k ? "," : "") + p.vlim[k].first + "=" + p.vlim[k].second;
            w("  fans " + fans(p.fans) + "\n  vlim " + vl + "\n  state " + state(p.state) + "\n");
            std::string pts, e, f, zs;
            for (size_t k = 0; k < p.pts.size(); k++) pts += (k ? " " : "") + b(p.pts[k].first) + "," + b(p.pts[k].second);
            for (size_t k = 0; k < p.e.size(); k++) e += (k ? " " : "") + b(p.e[k]);
            for (size_t k = 0; k < p.f.size(); k++) f += (k ? " " : "") + b(p.f[k]);
            for (size_t k = 0; k < p.zs.size(); k++) zs += (k ? " " : "") + b(p.zs[k]);
            w("  pts " + pts + "\n  e " + e + "\n  f " + f + "\n  zs " + (p.zs.empty() ? "None" : zs) + "\n  lead " +
              lead(p.lead) + "\n");
        }
    }
    std::fclose(out);
    return 0;
}
