// Port of colorstack/pipeline.py. The variants run in threads (Python: processes).
#include "pipeline.hpp"

#include <atomic>
#include <chrono>
#include <cstdio>
#include <future>
#include <set>

#include "config.hpp"
#include "estimate.hpp"
#include "parse.hpp"
#include "plan.hpp"
#include "reader.hpp"
#include "staggered.hpp"
#include "summary.hpp"
#include "tower.hpp"
#include "writer.hpp"

namespace cb {

namespace {

const double PLAN_WEIGHT[2] = {22.0, 41.0};     // relative cost of a banded / staggered plan, on a 4-colour file
const double WRITE_WEIGHT = 3.0;                 // write + time one plan

struct VariantResult {
    Estimate est;
    Steps steps;
    std::vector<std::string> lines;
    Json summary;
};

Json estimate_json(const Estimate& e) {
    Json j = Json::object();
    j["motion_s"] = e.motion_s;
    j["dwell_s"] = e.dwell_s;
    j["swaps"] = e.swaps;
    Json& ext = j["extruded"] = Json::object();
    for (const auto& [t, mm] : e.extruded) ext[t ? std::to_string(*t) : "null"] = mm;
    Json& into = j["swaps_into"] = Json::object();
    for (const auto& [t, k] : e.swaps_into) into[std::to_string(t)] = k;
    j["swap_s"] = e.swap_s;
    j["total_s"] = e.total_s;
    j["source"] = e.source;
    j["swap_each_s"] = e.swap_each_s;
    return j;
}

// plan, write and time one way of printing prog
VariantResult variant(const Program& prog, const Nozzle& nozzle, const Json& cfg, const Limits& lim, bool staggered,
                      bool per_object, const std::function<void(double, const std::string&)>& report) {
    Options opts;
    opts.max_band_layers = int(num(cfg, "stack", "max_band_layers"));
    opts.surface_tolerance = num(cfg, "stack", "surface_tolerance");
    opts.resolution = num(cfg, "stack", "resolution");
    opts.min_piece = num(cfg, "stack", "min_piece");
    opts.per_object = per_object;
    if (flag(cfg, "stack", "free_support")) opts.free_support = prog.free_support;
    opts.materials = materials(prog);
    const double split = PLAN_WEIGHT[staggered] / (PLAN_WEIGHT[staggered] + WRITE_WEIGHT);
    const std::string stage = staggered ? "Planning staggered colours" : "Planning colour bands";
    auto planned = [&](double f) { report(split * f, stage); };
    std::vector<Band> bands;
    std::pair<int, int> wipes;
    if (staggered) {
        std::tie(bands, wipes) = plan_staggered(prog, nozzle, opts, planned);
    } else {
        bands = plan(prog, nozzle, opts, planned);
        wipes = compensate(prog, bands);
    }
    report(split, "Writing and timing");
    double ceded = 0, visible = 0;
    int multi = 0;
    for (const auto& b : bands) {
        ceded += b.ceded_mm;
        visible += b.visible_ceded_mm;
        multi += b.tools().size() > 1;
    }
    char buf[256];
    std::vector<std::string> notes{PROCESSED};
    notes.push_back("COLORSTACK bands=" + std::to_string(bands.size()) + " multi_tool_bands=" + std::to_string(multi) +
                    " staggered=" + (staggered ? "True" : "False"));
    std::snprintf(buf, sizeof buf, "COLORSTACK ceded_mm=%.1f visible_ceded_mm=%.1f", ceded, visible);
    notes.push_back(buf);
    auto val = [&](const char* key) { return py_repr(num(cfg, "nozzle", key)); };     // f"{float}" is repr
    notes.push_back("COLORSTACK nozzle tip_diameter=" + val("tip_diameter") + " cone_angle=" + val("cone_angle") +
                    " clear_height=" + val("clear_height") + " contact=" + val("contact"));
    WriterOptions wo;
    wo.swap_lift = num(cfg, "swap", "lift");
    wo.stacked_fan = flag(cfg, "cooling", "stacked_fan");
    wo.lift_wait = flag(cfg, "cooling", "lift_wait");
    wo.min_layer_time = num(cfg, "cooling", "min_layer_time");
    wo.tower_between = flag(cfg, "cooling", "tower_between");
    wo.respect_min_layer_time = flag(cfg, "cooling", "respect_min_layer_time");
    Writer writer(prog, nozzle, wo);
    VariantResult r;
    r.lines = writer.write(bands, notes);
    r.est = estimate(r.lines, lim, &r.steps);
    report(1.0, "Writing and timing");
    Json& s = r.summary = Json::object();
    s["bands"] = (long long)bands.size();
    s["ceded_mm"] = ceded;
    s["visible_ceded_mm"] = visible;
    s["per_object"] = per_object;
    s["staggered"] = staggered;
    Json& mlt = s["min_layer_time"] = Json::array();
    if (writer.min_layer_time)
        for (double t : *writer.min_layer_time) mlt.a.push_back(t);
    s["min_layer_source"] = writer.min_layer_source;
    s["cooled_groups"] = writer.slowed;
    s["cooled_short"] = writer.short_;
    s["fanned_paths"] = writer.fanned;
    s["waits"] = writer.waited;
    s["tower_wipes_kept"] = wipes.first;
    s["tower_wipes_to_grid"] = wipes.second;
    return r;
}

}  // namespace

Json run(const std::string& input_path, const std::string& output_path, const Json& cfg, const Progress& progress,
         const std::function<bool()>& cancelled) {
    auto report = [&](double fraction, const std::string& stage) {
        if (cancelled && cancelled()) throw Cancelled();
        if (progress) progress(fraction, stage);
    };
    Nozzle nozzle{num(cfg, "nozzle", "tip_diameter") / 2, num(cfg, "nozzle", "cone_angle"),
                  num(cfg, "nozzle", "clear_height"), num(cfg, "nozzle", "contact")};
    report(0.0, "Reading the file");
    Program prog = parse(input_path);
    PrinterFallback fb{num(cfg, "printer", "max_velocity"), num(cfg, "printer", "max_accel"),
                       num(cfg, "printer", "square_corner_velocity"), num(cfg, "printer", "max_z_velocity"),
                       num(cfg, "printer", "max_z_accel")};
    Limits lim = machine_limits(prog.settings, fb);
    auto original = load(input_path);
    // plan every way asked for that can differ, keep the fastest (swaps included). Per-object units are the tools
    // themselves when no tool spans two objects; staggering gains nothing with at most two colours a layer.
    std::set<std::pair<int, std::optional<std::string>>> tool_objs;
    std::set<int> tools;
    bool many_colours = false;
    for (const auto& L : prog.layers) {
        std::set<int> here;
        for (const auto& p : L.paths)
            if (p->tool) {
                tool_objs.insert({*p->tool, p->obj});
                tools.insert(*p->tool);
                here.insert(*p->tool);
            }
        many_colours = many_colours || here.size() > 2;
    }
    bool split_objects = tool_objs.size() > tools.size();
    std::vector<std::pair<bool, bool>> variants;
    for (bool s : flag(cfg, "stack", "staggered") && many_colours ? std::vector<bool>{false, true} : std::vector<bool>{false})
        for (bool po : flag(cfg, "stack", "per_object") && split_objects ? std::vector<bool>{false, true} : std::vector<bool>{false})
            variants.emplace_back(s, po);
    Estimate o;
    std::vector<VariantResult> results;
    if (variants.size() == 1) {
        report(0.05, "Timing the original");
        o = estimate(original, lim);
        results.push_back(variant(prog, nozzle, cfg, lim, variants[0].first, variants[0].second,
                                  [&](double f, const std::string& stage) { report(0.1 + (0.97 - 0.1) * f, stage); }));
    } else {
        // the variants do not depend on each other: one thread each, the original timed meanwhile
        report(0.05, "Planning colour bands");
        std::atomic<bool> stop{false};
        auto quiet = [&stop](double, const std::string&) {
            if (stop) throw Cancelled();
        };
        std::vector<std::future<VariantResult>> jobs;
        for (auto [s, po] : variants)
            jobs.push_back(std::async(std::launch::async, [&, s = s, po = po] {
                return variant(prog, nozzle, cfg, lim, s, po, quiet);
            }));
        try {
            o = estimate(original, lim);
            size_t finished = 0;
            while (finished < jobs.size()) {
                for (auto& j : jobs)      // wait up to 0.3 s on the first one still running
                    if (j.wait_for(std::chrono::seconds(0)) != std::future_status::ready) {
                        j.wait_for(std::chrono::milliseconds(300));
                        break;
                    }
                finished = 0;
                for (auto& j : jobs) finished += j.wait_for(std::chrono::seconds(0)) == std::future_status::ready;
                if (cancelled && cancelled()) throw Cancelled();
                report(0.1 + 0.87 * double(finished) / double(jobs.size()), "Planning colour bands");
            }
            for (auto& j : jobs) results.push_back(j.get());
        } catch (...) {
            stop = true;     // the threads end at their next progress step; their futures wait for that
            throw;
        }
    }
    auto slicer = file_estimate(original);
    double each = swap_each(o, slicer, lim).first;
    size_t best = 0;
    double best_total = 0;
    for (size_t k = 0; k < results.size(); k++) {     // in variant order: the first of equally fast plans wins
        const Estimate& e = results[k].est;
        double total = e.motion_s + e.dwell_s + e.swaps * each;
        if (k == 0 || total < best_total) {
            best = k;
            best_total = total;
        }
    }
    report(0.97, "Saving");
    VariantResult& r = results[best];
    Estimate& p = r.est;
    anchor(o, p, slicer, lim);
    auto lines = stamp_progress(r.lines, r.steps, p.swap_each_s, p.total_s);
    stamp_header(lines, o, p);
    // LF, as the slicers write G-code
    FILE* f = std::fopen(output_path.c_str(), "wb");
    if (!f) throw std::runtime_error("cannot write " + output_path);
    for (const auto& ln : lines) {
        std::fwrite(ln.data(), 1, ln.size(), f);
        std::fputc('\n', f);
    }
    std::fclose(f);
    Json out = Json::object();
    out["original"] = estimate_json(o);
    out["processed"] = estimate_json(p);
    for (auto& kv : r.summary.o) out[kv.first] = kv.second;
    out["output"] = output_path;
    return out;
}

}  // namespace cb
