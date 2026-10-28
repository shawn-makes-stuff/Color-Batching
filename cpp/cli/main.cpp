// color-batching input.gcode [-o output.gcode] [-c config.toml] [--set section.key=value ...]
// (port of colorstack/__main__.py)
#include <chrono>
#include <cmath>
#include <cstdio>
#include <filesystem>
#include <string>

#include "../core/config.hpp"
#include "../core/pipeline.hpp"

namespace {

std::string fmt_time(double s) {
    long long t = (long long)std::nearbyint(s), h = t / 3600, m = t % 3600 / 60, sec = t % 60;
    char buf[64];
    if (h) std::snprintf(buf, sizeof buf, "%lldh%02lldm%02llds", h, m, sec);
    else std::snprintf(buf, sizeof buf, "%lldm%02llds", m, sec);
    return buf;
}

int usage() {
    std::fprintf(stderr, "usage: color-batching input [-o OUTPUT] [-c CONFIG] [--set SECTION.KEY=VALUE]\n");
    return 2;
}

}  // namespace

int main(int argc, char** argv) {
    namespace fs = std::filesystem;
    std::string input, output;
    fs::path root = cb::find_root(fs::absolute(fs::path(argv[0])).parent_path());
    fs::path config = cb::config_path(root);
    cb::Json values = cb::Json::object();
    for (int k = 1; k < argc; k++) {
        std::string a = argv[k];
        auto next = [&]() -> std::string {
            if (k + 1 >= argc) throw std::invalid_argument(a + " needs a value");
            return argv[++k];
        };
        try {
            if (a == "-o" || a == "--output") {
                output = next();
            } else if (a == "-c" || a == "--config") {
                config = next();
            } else if (a == "--set") {
                std::string item = next();
                size_t eq = item.find('='), dot = item.find('.');
                if (eq == std::string::npos || dot == std::string::npos || dot > eq) return usage();
                values[item.substr(0, dot)][item.substr(dot + 1, eq - dot - 1)] = item.substr(eq + 1);
            } else if (a == "-h" || a == "--help") {
                usage();
                return 0;
            } else if (input.empty() && (a.empty() || a[0] != '-')) {
                input = a;
            } else {
                return usage();
            }
        } catch (const std::exception&) {
            return usage();
        }
    }
    if (input.empty()) return usage();
    try {
        cb::Json cfg = cb::load_config(config);
        cb::apply(cfg, values);
        if (output.empty()) output = (fs::path(input).parent_path() / fs::path(input).stem()).string() + ".color-batching.gcode";
        auto t0 = std::chrono::steady_clock::now();
        cb::Json r = cb::run(input, output, cfg);
        auto i = [&](const char* k) { return r.get(k)->i; };
        auto d = [&](const char* k) { return r.get(k)->d; };
        auto b = [&](const char* k) { return r.get(k)->b; };
        std::printf("tower wipes kept %lld, turned into grid %lld", i("tower_wipes_kept"), i("tower_wipes_to_grid"));
        if (i("tower_purge_short")) std::printf(", %lld swaps short of their flush on the tower", i("tower_purge_short"));
        std::printf("\n");
        std::string cooling = "cooling: " + std::to_string(i("cooled_groups")) + " stacked regions slowed to the minimum layer time";
        if (i("cooled_short")) cooling += ", " + std::to_string(i("cooled_short")) + " still short at minimum speed";
        cooling += "; fan raised on " + std::to_string(i("fanned_paths")) + " lines";
        if (i("waits")) cooling += "; " + std::to_string(i("waits")) + " lift-and-waits";
        cooling += std::string("; per-object batching ") + (b("per_object") ? "used" : "not used");
        cooling += std::string("; staggered colours ") + (b("staggered") ? "used" : "not used");
        std::printf("%s\n", cooling.c_str());
        std::string mlt;
        for (const auto& t : r.get("min_layer_time")->a) {
            char buf[32];
            std::snprintf(buf, sizeof buf, "%g", t.d);
            mlt += (mlt.empty() ? "" : "/") + std::string(buf);
        }
        std::printf("minimum layer time %s s (%s)\n", mlt.empty() ? "none" : mlt.c_str(),
                    r.get("min_layer_source")->s == "override" ? "override" : "from the file");
        std::printf("bands %lld, ceded %.0f mm (%.0f mm visible)\n", i("bands"), d("ceded_mm"), d("visible_ceded_mm"));
        std::printf("%-10s%12s%12s\n", "", "original", "batched");
        const cb::Json &o = *r.get("original"), &p = *r.get("processed");
        std::printf("%-10s%12lld%12lld\n", "swaps", o.get("swaps")->i, p.get("swaps")->i);
        for (auto [k, label] : {std::pair{"motion_s", "motion"}, std::pair{"swap_s", "swapping"}, std::pair{"total_s", "total"}})
            std::printf("%-10s%12s%12s\n", label, fmt_time(o.get(k)->d).c_str(), fmt_time(p.get(k)->d).c_str());
        std::printf("times anchored to the %s (%s per colour change)\n", o.get("source")->s.c_str(),
                    fmt_time(o.get("swap_each_s")->d).c_str());
        std::printf("wrote %s in %.1fs\n", output.c_str(),
                    std::chrono::duration<double>(std::chrono::steady_clock::now() - t0).count());
    } catch (const std::exception& e) {
        std::fprintf(stderr, "color-batching: %s\n", e.what());
        return 1;
    }
    return 0;
}
