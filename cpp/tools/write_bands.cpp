// Read a band plan written by tools/dump_bands.py, write it with the C++ writer: the output must equal the Python
// writer's for the same plan.  write_bands in.gcode bands.txt out.gcode
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <fstream>
#include <sstream>
#include <stdexcept>
#include <string>
#include <vector>

#include "../core/parse.hpp"
#include "../core/writer.hpp"

namespace {

std::vector<std::string> split_tabs(const std::string& line) {
    std::vector<std::string> out;
    size_t start = 0;
    while (true) {
        size_t t = line.find('\t', start);
        out.push_back(line.substr(start, t == std::string::npos ? std::string::npos : t - start));
        if (t == std::string::npos) break;
        start = t + 1;
    }
    return out;
}

double bits(const std::string& h) {
    uint64_t u = std::stoull(h, nullptr, 16);
    double v;
    std::memcpy(&v, &u, sizeof v);
    return v;
}
std::optional<double> obits(const std::string& h) { return h == "None" ? std::nullopt : std::optional<double>(bits(h)); }
std::optional<std::string> ostr(const std::string& s) { return s == "None" ? std::nullopt : std::optional<std::string>(s); }

cb::PathP read_path(std::istream& in, const std::string& header) {
    auto f = split_tabs(header);
    auto p = std::make_shared<cb::Path>();
    if (f[1] != "None") p->tool = std::stoi(f[1]);
    p->type = f[2];
    p->width = obits(f[3]);
    p->z = bits(f[4]);
    p->accel = obits(f[5]);
    p->obj = ostr(f[6]);
    p->seq = std::stoi(f[7]);
    p->retract_before = f[8] == "1";
    p->hop_before = f[9] == "1";
    p->head = f[10] == "1";
    p->tail = f[11] == "1";
    p->tower = ostr(f[12]);
    p->block = std::stoi(f[13]);
    p->scv = obits(f[14]);
    p->temp = obits(f[15]);
    p->height = obits(f[16]);
    std::string line;
    while (std::getline(in, line)) {
        if (line == "end") break;
        auto g = split_tabs(line);
        const std::string& k = g[0];
        if (k == "pts") {
            for (size_t i = 1; i < g.size(); i++) {
                size_t c = g[i].find(',');
                p->pts.emplace_back(bits(g[i].substr(0, c)), bits(g[i].substr(c + 1)));
            }
        } else if (k == "e") {
            for (size_t i = 1; i < g.size(); i++) p->e.push_back(bits(g[i]));
        } else if (k == "f") {
            for (size_t i = 1; i < g.size(); i++) p->f.push_back(bits(g[i]));
        } else if (k == "zs") {
            if (g[1] != "None")
                for (size_t i = 1; i < g.size(); i++) p->zs.push_back(bits(g[i]));
        } else if (k == "fan") {
            p->fans.emplace_back(g[1], std::stoi(g[2]));
        } else if (k == "vlim") {
            p->vlim.emplace_back(g[1], g[2]);
        } else if (k == "st") {
            std::vector<std::string> names;
            if (!g[2].empty()) {
                size_t start = 0;
                while (true) {
                    size_t c = g[2].find(',', start);
                    names.push_back(g[2].substr(start, c == std::string::npos ? std::string::npos : c - start));
                    if (c == std::string::npos) break;
                    start = c + 1;
                }
            }
            p->state.push_back({{g[1], names}, g[3]});
        } else if (k == "lm") {
            cb::LeadItem it{'m', bits(g[1]), bits(g[2]), bits(g[3]), bits(g[4]), bits(g[5])};
            p->lead.push_back(it);
        } else if (k == "la") {
            cb::LeadItem it{'a'};
            it.a = bits(g[1]);
            p->lead.push_back(it);
        } else if (k == "ll") {
            cb::LeadItem it{'l'};
            it.text = g[1];
            p->lead.push_back(it);
        }
    }
    return p;
}

}  // namespace

int main(int argc, char** argv) {
    if (argc != 4) {
        std::fprintf(stderr, "write_bands in.gcode bands.txt out.gcode\n");
        return 2;
    }
    cb::Program prog = cb::parse(argv[1]);
    std::ifstream in(argv[2], std::ios::binary);
    std::string line;
    std::vector<cb::Band> bands;
    size_t j = 0;       // band layer the next work items belong to
    while (std::getline(in, line)) {
        auto g = split_tabs(line);
        if (g[0] == "band") {
            bands.emplace_back();
            for (size_t i = 1; i < g.size(); i++) bands.back().layers.push_back(std::stoi(g[i]));
            bands.back().work.resize(bands.back().layers.size());
        } else if (g[0] == "order") {
            for (size_t i = 1; i + 1 < g.size(); i += 2) bands.back().order.push_back({std::stoi(g[i]), ostr(g[i + 1])});
        } else if (g[0] == "layer") {
            j = std::stoul(g[1]);
        } else if (g[0] == "W") {
            cb::Unit owner{std::stoi(g[1]), ostr(g[2])};
            std::string header;
            std::getline(in, header);
            bands.back().work[j].emplace_back(owner, read_path(in, header));
        }
    }
    cb::Nozzle nozzle{1.0 / 2, 45.0, 2.0, 0.2};     // DEFAULTS with contact 0.2, as tools/dump_bands.py
    cb::WriterOptions opts;
    opts.swap_lift = 1.0;
    opts.stacked_fan = true;
    opts.lift_wait = false;
    opts.min_layer_time = 0.0;
    opts.tower_between = true;
    opts.respect_min_layer_time = true;
    cb::Writer writer(prog, nozzle, opts);
    auto lines = writer.write(bands, {"COLORSTACK writer comparison"});
    FILE* out = std::fopen(argv[3], "wb");
    for (auto& ln : lines) {
        std::fwrite(ln.data(), 1, ln.size(), out);
        std::fputc('\n', out);
    }
    std::fclose(out);
    return 0;
}
