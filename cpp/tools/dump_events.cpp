// Dump the reader's events, floats as their 64-bit patterns, in the format of tools/dump_events.py.
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <string>

#include "../core/reader.hpp"

static std::string bits(double v) {
    uint64_t u;
    std::memcpy(&u, &v, sizeof u);
    char buf[17];
    std::snprintf(buf, sizeof buf, "%016llx", (unsigned long long)u);
    return buf;
}

int main(int argc, char** argv) {
    if (argc != 3) {
        std::fprintf(stderr, "dump_events in.gcode out.txt\n");
        return 2;
    }
    auto lines = cb::load(argv[1]);
    FILE* out = std::fopen(argv[2], "wb");
    static const char* names[] = {"move", "tool", "accel", "scv", "layer", "z", "height", "type", "width", "cmd", "comment"};
    cb::read(lines, [&](size_t idx, const cb::Event& e) {
        const char* k = names[int(e.kind)];
        switch (e.kind) {
            case cb::Kind::move: {
                const auto& m = e.move;
                std::fprintf(out, "%zu move %s %s %s %s %s %s %s %s %s %s\n", idx, bits(m.x0).c_str(), bits(m.y0).c_str(),
                             bits(m.z0).c_str(), bits(m.x1).c_str(), bits(m.y1).c_str(), bits(m.z1).c_str(),
                             bits(m.de).c_str(), bits(m.f).c_str(), m.word.c_str(), bits(m.xy()).c_str());
                break;
            }
            case cb::Kind::tool:
                std::fprintf(out, "%zu tool %d %s\n", idx, e.tool, e.text.c_str());
                break;
            case cb::Kind::layer:
                std::fprintf(out, "%zu layer\n", idx);
                break;
            case cb::Kind::accel: case cb::Kind::scv: case cb::Kind::z: case cb::Kind::height: case cb::Kind::width:
                std::fprintf(out, "%zu %s %s\n", idx, k, bits(e.value).c_str());
                break;
            default:
                std::fprintf(out, "%zu %s %s\n", idx, k, e.text.c_str());
        }
    });
    std::fclose(out);
    return 0;
}
