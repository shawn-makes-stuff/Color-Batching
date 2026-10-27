// Port of colorstack/writer.py, method for method.
#include "writer.hpp"

#include <algorithm>
#include <charconv>
#include <cmath>
#include <set>

#include "pymath.hpp"
#include "reader.hpp"

namespace cb {

namespace {

const double PI = 3.141592653589793;
const std::set<std::string> PA_WORDS = {"SET_PRESSURE_ADVANCE", "M900", "M572"};
const std::set<std::string> SETTLED_WORDS = {"M140", "M190", "M141", "M191", "M600", "M601", "M0", "M1", "M25", "M125", "PAUSE"};

std::string fixed(double v, int digits) {
    char buf[512];
    auto r = std::to_chars(buf, buf + sizeof buf, v, std::chars_format::fixed, digits);
    return std::string(buf, r.ptr);
}

std::string first_word(std::string_view s) {
    s = py_strip(s);
    size_t n = 0;
    while (n < s.size() && !py_space(s[n])) n++;
    return py_upper(s.substr(0, n));
}

std::string replace_all(std::string s, const std::string& from, const std::string& to) {
    size_t i = 0;
    while ((i = s.find(from, i)) != std::string::npos) {
        s.replace(i, from.size(), to);
        i += to.size();
    }
    return s;
}

// Python dict equality: same keys and values, any order
bool same_fans(const Fans& a, const Fans& b) {
    if (a.size() != b.size()) return false;
    for (auto& [k, v] : a) {
        auto it = std::find_if(b.begin(), b.end(), [&](auto& kv) { return kv.first == k; });
        if (it == b.end() || it->second != v) return false;
    }
    return true;
}

int fan_get(const Fans& f, const std::string& key, int fallback) {
    for (auto& [k, v] : f)
        if (k == key) return v;
    return fallback;
}

double print_time(const std::vector<PathP>& paths) {
    double t = 0;
    for (auto& p : paths)
        for (size_t i = 0; i < p->e.size(); i++)
            if (p->f[i] > 0) t += py_dist(p->pts[i].first, p->pts[i].second, p->pts[i + 1].first, p->pts[i + 1].second) / (p->f[i] / 60);
    return t;
}

double print_time(const std::vector<std::vector<PathP>*>& groups) {
    double t = 0;
    for (auto* g : groups) t += print_time(*g);
    return t;
}

std::optional<double> travel_accel(const Path& p) {
    std::optional<double> a;
    for (auto& it : p.lead) {
        if (it.kind == 'a')
            a = it.a;
        else if (it.kind == 'm' && it.de == 0)
            return a;
    }
    return std::nullopt;
}

// the slicer's swap block for tool; a tool the original never swapped takes the first one stored (also when its own
// is empty: Python's `or`)
const std::vector<std::string>& block_for(const std::vector<std::pair<int, std::vector<std::string>>>& blocks, int tool) {
    static const std::vector<std::string> none;
    for (auto& kv : blocks)
        if (kv.first == tool && !kv.second.empty()) return kv.second;
    return blocks.empty() ? none : blocks.front().second;
}

std::optional<std::vector<double>> list_or(const Program& p, const std::string& a, const std::string& b) {
    auto v = p.setting_list(a);
    if (v && !v->empty()) return v;
    v = p.setting_list(b);
    if (v && !v->empty()) return v;
    return std::nullopt;
}

std::vector<double> list_default(const Program& p, const std::string& key, std::vector<double> fallback) {
    auto v = p.setting_list(key);
    return v ? *v : fallback;
}

}  // namespace

std::string fmt(double v) {
    std::string s = fixed(v, 3);
    while (!s.empty() && s.back() == '0') s.pop_back();
    if (!s.empty() && s.back() == '.') s.pop_back();
    return s;
}

std::vector<int> Band::tools() const {
    std::vector<int> seq;
    for (auto& u : order)
        if (seq.empty() || seq.back() != u.tool) seq.push_back(u.tool);
    return seq;
}

Writer::Writer(const Program& program, const Nozzle& nz, const WriterOptions& opts)
    : p(program), nozzle(nz), o(opts), heights(program), tool(program.first_tool) {
    const Travel& t = p.travel;
    retract = t.retract;
    retract_f = t.retract_f;
    deretract_f = t.deretract_f;
    z_hop = t.z_hop;
    travel_f = t.travel_f;
    min_travel = t.min_travel;
    firmware = t.firmware;
    restart_extra = t.restart_extra;
    std::vector<double> diam = list_default(p, "filament_diameter", {1.75});
    for (double d : diam) area.push_back(PI * d * d / 4);
    flow_ratio = list_default(p, "filament_flow_ratio", {1.0});
    max_vol = list_default(p, "filament_max_volumetric_speed", {});
    size_t n = diam.size();
    auto flat = p.setting_list("flush_volumes_matrix");
    if (flat && !flat->empty() && flat->size() == n * n) {
        flush.emplace();
        for (size_t i = 0; i < n; i++) flush->emplace_back(flat->begin() + i * n, flat->begin() + (i + 1) * n);
    }
    flush_mult = list_default(p, "flush_multiplier", {1.0})[0];
    nozzle_t = p.first_temp;
    // cooling (Orca/Bambu or PrusaSlicer names)
    min_layer_time = list_or(p, "slow_down_layer_time", "slowdown_below_layer_time");
    auto on = list_or(p, "slow_down_for_layer_cooling", "cooling");
    if (min_layer_time && on) {
        for (size_t k = 0; k < min_layer_time->size(); k++)
            if ((*on)[std::min(k, on->size() - 1)] == 0.0) (*min_layer_time)[k] = 0.0;
    }
    min_layer_source = min_layer_time ? "file" : "none";
    if (o.min_layer_time > 0) {
        min_layer_time = std::vector<double>(n, o.min_layer_time);
        min_layer_source = "override";
    }
    min_speed = list_or(p, "slow_down_min_speed", "min_print_speed");
    fan_min = p.setting_list("fan_min_speed");
    fan_max = p.setting_list("fan_max_speed");
    fan_layer_time = p.setting_list("fan_cooling_layer_time");
    no_fan_layers = int(list_default(p, "close_fan_the_first_x_layers", {0})[0]);
    // each layer's print time in the original (same measure as cool's)
    for (auto& L : p.layers) layer_time.push_back(print_time(L.paths));
    // wipe while retracting, as the slicer does
    wipe = list_default(p, "wipe", {0})[0] != 0.0;
    wipe_dist = list_default(p, "wipe_distance", {0.0})[0];
    auto setting_str = [&](const std::string& key, const std::string& fallback) {
        auto it = p.settings.find(key);
        return it == p.settings.end() ? fallback : it->second;
    };
    {
        std::string ws = setting_str("wipe_speed", "100%");
        std::string_view w = py_strip(std::string_view(ws).substr(0, ws.find(',')));
        double v;
        if (!w.empty() && w.back() == '%') {
            std::string_view num = w;
            while (!num.empty() && num.back() == '%') num.remove_suffix(1);
            wipe_f = py_float(num, v) ? travel_f * v / 100 : travel_f;
        } else {
            wipe_f = py_float(w, v) ? v * 60 : travel_f;
        }
    }
    {
        std::string rb = setting_str("retract_before_wipe", "0%");
        std::string_view r = py_strip(std::string_view(rb).substr(0, rb.find(',')));
        while (!r.empty() && r.back() == '%') r.remove_suffix(1);
        double v;
        before_wipe = py_float(r, v) ? v / 100 : 0.0;
    }
    role_wipe = py_strip(setting_str("role_based_wipe_speed", "0")) == "1";
    machine = {retract, z_hop, retract_f, deretract_f, restart_extra, min_travel, wipe_dist, before_wipe, wipe};
    use_filament(p.first_tool);
    // the slicer's swap blocks per tool, minus the part fan (fans follow each path)
    auto own_blocks = [](const std::vector<std::pair<int, std::vector<std::string>>>& blocks) {
        std::vector<std::pair<int, std::vector<std::string>>> outb;
        for (auto& [t, lines] : blocks) {
            std::vector<std::string> kept;
            for (auto& ln : lines)
                if (!(ln.rfind("M106", 0) == 0 && ln.find(" P") == std::string::npos)) kept.push_back(ln);
            outb.emplace_back(t, kept);
        }
        return outb;
    };
    tc_end = own_blocks(p.toolchange_end);
    tc_start = own_blocks(p.toolchange_start);
}

void Writer::use_filament(std::optional<int> t) {
    // Orca's per-filament overrides of the printer's retraction ("nil" = the printer's value), in Python's order
    struct Key { const char* name; char attr; };
    static const Key keys[] = {{"filament_retraction_length", 'r'}, {"filament_z_hop", 'z'},
                               {"filament_retraction_speed", 's'}, {"filament_deretraction_speed", 'd'},
                               {"filament_retract_restart_extra", 'x'}, {"filament_retraction_minimum_travel", 'm'},
                               {"filament_wipe", 'w'}, {"filament_wipe_distance", 'D'}, {"filament_retract_before_wipe", 'b'}};
    for (auto& k : keys) {
        double value = 0;
        bool bvalue = false;
        switch (k.attr) {
            case 'r': value = machine.retract; break;
            case 'z': value = machine.z_hop; break;
            case 's': value = machine.retract_f; break;
            case 'd': value = machine.deretract_f; break;
            case 'x': value = machine.restart_extra; break;
            case 'm': value = machine.min_travel; break;
            case 'w': bvalue = machine.wipe; break;
            case 'D': value = machine.wipe_dist; break;
            case 'b': value = machine.before_wipe; break;
        }
        auto it = p.settings.find(k.name);
        std::string all = it == p.settings.end() ? "" : it->second;
        std::vector<std::string_view> values;
        std::string_view sv = all;
        size_t start = 0;
        while (true) {
            size_t c = sv.find(',', start);
            values.push_back(sv.substr(start, c == std::string_view::npos ? std::string_view::npos : c - start));
            if (c == std::string_view::npos) break;
            start = c + 1;
        }
        std::string_view raw = t ? py_strip(values[std::min<size_t>(size_t(*t), values.size() - 1)]) : std::string_view();
        if (!raw.empty() && raw != "nil") {
            std::string_view num = raw;
            while (!num.empty() && num.back() == '%') num.remove_suffix(1);
            double v;
            if (py_float(num, v)) {
                if (k.attr == 'w')
                    bvalue = v != 0.0;
                else if (k.attr == 'b')
                    value = v / 100;
                else if (k.attr == 's' || k.attr == 'd')
                    value = v * 60;
                else
                    value = v;
                if (k.attr == 'd' && v == 0) value = retract_f;     // Orca: 0 = the retraction speed
            }
        }
        switch (k.attr) {
            case 'r': retract = value; break;
            case 'z': z_hop = value; break;
            case 's': retract_f = value; break;
            case 'd': deretract_f = value; break;
            case 'x': restart_extra = value; break;
            case 'm': min_travel = value; break;
            case 'w': wipe = bvalue; break;
            case 'D': wipe_dist = value; break;
            case 'b': before_wipe = value; break;
        }
    }
}

double Writer::purge_length(int a, int b) const {
    if (!flush || std::max(a, b) >= int(flush->size())) return 0.0;
    return (*flush)[a][b] * flush_mult / area[std::min<size_t>(size_t(b), area.size() - 1)];
}

void Writer::retract_move() {
    if (retracted) return;
    retracted = true;
    retracted_by = retract;     // primed back by the same amount, also after a swap (as Orca does)
    const auto* pts = last_pts;
    if (!(wipe && wipe_dist > 0 && pts && pts->size() > 1 && pos &&
          py_dist(pos->first, pos->second, pts->back().first, pts->back().second) < 1e-6) ||
        firmware) {
        plain_retract();
        return;
    }
    std::vector<std::pair<std::pair<double, double>, double>> route;
    double left_ = wipe_dist;
    for (size_t i = pts->size() - 1; i >= 1; i--) {
        auto a = (*pts)[i], b = (*pts)[i - 1];
        double d = py_dist(a.first, a.second, b.first, b.second);
        if (d <= 0) continue;
        if (d >= left_) {
            double t = left_ / d;
            route.push_back({{a.first + (b.first - a.first) * t, a.second + (b.second - a.second) * t}, left_});
            break;
        }
        route.push_back({b, d});
        left_ -= d;
    }
    double total = 0;
    for (auto& r : route) total += r.second;
    double wf = (role_wipe && object_f && *object_f != 0.0) ? *object_f : wipe_f;
    wf = std::max(wf, 600.0);      // Orca: >= 10 mm/s
    double before = retract * before_wipe;
    double during = std::min(retract - before, retract_f * total / wf);
    before = retract - during;
    if (before > 1e-6) emit("G1 E-" + fixed(before, 5) + " F" + fmt(retract_f));
    if (during <= 1e-6) return;
    emit(";WIPE_START");
    for (auto& [xy, d] : route) emit("G1 X" + fmt(xy.first) + " Y" + fmt(xy.second) + " E-" + fixed(during * d / total, 5) + " F" + fmt(wf));
    emit(";WIPE_END");
    pos = route.back().first;
    f = wf;
    last_pts = nullptr;
}

void Writer::plain_retract() { emit(firmware ? "G10" : "G1 E-" + fmt(retract) + " F" + fmt(retract_f)); }

void Writer::deretract() {
    if (!retracted) return;
    if (firmware) {
        emit("G11");
    } else {
        emit("G1 E" + fmt(retracted_by + restart_extra) + " F" + fmt(deretract_f));
        f = deretract_f;
    }
    retracted = false;
}

void Writer::lift(double nz) {
    if (!z || std::fabs(nz - *z) > 1e-6) {
        emit("G1 Z" + fmt(nz) + " F" + fmt(travel_f));
        z = nz;
        f = travel_f;
    }
}

void Writer::move_to(double x, double y, double nz) {
    emit("G1 X" + fmt(x) + " Y" + fmt(y) + " F" + fmt(travel_f));
    f = travel_f;
    lift(nz);
    pos = std::make_pair(x, y);
    deretract();
}

double Writer::clear_z(double x, double y, double nz, std::optional<std::pair<double, double>> frm,
                       std::optional<double> frm_z) const {
    if (!frm) frm = pos;
    if (!frm_z) frm_z = z;
    double base = frm_z ? std::min(nz, *frm_z) : nz;
    if (!frm || top <= base + 1e-6 || top - base > nozzle.clear_height) return top;
    return heights.max_near(frm->first, frm->second, x, y, nozzle.reach(top - base));
}

bool Writer::can_replay(const Path& pp, const PathP& q) const {
    if (!(!pp.lead.empty() && q && q->tail && pp.head && pp.seq == q->seq + 1 && pp.tool == q->tool &&
          (!pp.tool || *pp.tool == tool) && !retracted && pos && z &&
          py_dist(pos->first, pos->second, q->pts.back().first, q->pts.back().second) < 1e-6 &&
          std::fabs(*z - q->z1()) < 1e-6))
        return false;
    auto cpos = *pos;
    double cz = *z;
    for (auto& it : pp.lead) {
        if (it.kind != 'm') continue;
        if (py_dist(cpos.first, cpos.second, it.x, it.y) > 1e-9 &&
            clear_z(it.x, it.y, it.z, cpos, cz) > std::min(cz, it.z) + 1e-6)
            return false;
        cpos = {it.x, it.y};
        cz = it.z;
    }
    return py_dist(cpos.first, cpos.second, pp.pts.front().first, pp.pts.front().second) < 1e-6 && std::fabs(cz - pp.z0()) < 1e-6;
}

void Writer::set_accel(std::optional<double> a) {
    if (a && (!accel || *a != *accel)) {
        emit(replace_all(p.accel_format, "{a}", fmt(*a)));
        accel = a;
    }
}

void Writer::replay(const std::vector<LeadItem>& lead) {
    double balance = 0.0;
    for (auto& it : lead) {
        if (it.kind == 'a') {
            set_accel(it.a);
            continue;
        }
        if (it.kind == 'l') {
            emit(it.text);
            if (it.text == "G10")
                balance = -1.0;
            else if (it.text == "G11")
                balance = 0.0;
            continue;
        }
        std::string words;
        if (py_dist(pos->first, pos->second, it.x, it.y) > 1e-9) words += " X" + fmt(it.x) + " Y" + fmt(it.y);
        if (std::fabs(it.z - *z) > 1e-9) words += " Z" + fmt(it.z);
        if (it.de != 0) {
            words += " E" + fixed(it.de, 5);
            balance += it.de;
        }
        if (!f || it.f != *f) words += " F" + fmt(it.f);
        if (!words.empty()) emit("G1" + words);
        pos = std::make_pair(it.x, it.y);
        z = it.z;
        f = it.f;
    }
    // the slicer rounds each E to 5 decimals: a retract undone leaves a few 1e-5 mm, a real one tenths
    retracted = balance < -retract / 2;
    retracted_by = -balance;
    last_pts = nullptr;
}

void Writer::travel(double x, double y, double nz, std::optional<std::pair<bool, bool>> orca, std::optional<double> high_) {
    double high = high_ ? *high_ : top;
    if (orca && pos && z) {
        auto [ret, hop] = *orca;
        if (ret) retract_move();
        if (hop || std::fabs(nz - *z) > 1e-6) lift(std::max(nz, *z) + (hop ? z_hop : 0.0));
        move_to(x, y, nz);
        return;
    }
    if (pos && z && std::fabs(nz - *z) < 1e-6) {
        double d = py_dist(pos->first, pos->second, x, y);
        if (d < 1e-4) return;
        if (d <= min_travel && high <= nz + 1e-6) {
            move_to(x, y, nz);
            return;
        }
    }
    retract_move();
    double zz = (z && *z != 0.0) ? *z : 0.0;
    lift(std::max({high, nz, zz}) + z_hop);
    move_to(x, y, nz);
}

void Writer::end_object() {
    if (obj) {
        emit("EXCLUDE_OBJECT_END NAME=" + *obj);
        obj.reset();
    }
}

void Writer::set_temp(std::optional<double> t) {
    if (t && (!nozzle_t || *t != *nozzle_t)) {
        outside({"M104 S" + fmt(*t)});
        nozzle_t = t;
    }
}

void Writer::change_tool(int t, std::optional<double> temp, const State& st) {
    if (t == tool) return;
    end_object();
    retract_move();
    // the printer's swap routine travels to the purge tray from here: start above everything printed
    double zz = (z && *z != 0.0) ? *z : 0.0;
    lift(std::max(top, zz) + std::max(z_hop, o.swap_lift));
    emit("; COLORSTACK TOOLCHANGE T" + std::to_string(tool) + " -> T" + std::to_string(t));
    set_temp(temp);
    for (auto& ln : block_for(tc_end, tool)) out.push_back(ln);
    emit(replace_all(replace_all(p.tool_format, "{n}", std::to_string(t)), "{purge}", fixed(purge_length(tool, t), 3)));
    for (auto& ln : block_for(tc_start, t)) out.push_back(ln);
    tool = t;
    use_filament(t);
    last.reset();
    // the swap moves the head and may touch fans and machine state; the filament stays retracted until the next
    // travel primes it, as the slicer unretracts after T
    pos.reset();
    z.reset();
    fans.clear();
    state.clear();
    State pa;
    for (auto& kv : st)
        if (PA_WORDS.count(kv.first.first)) pa.push_back(kv);
    set_state(pa);
}

void Writer::set_state(const State& items) {
    for (auto& [k, ln] : items) {
        auto it = state.find(k);
        if (it == state.end() || it->second != ln) {
            emit(ln);
            state[k] = ln;
        }
    }
}

void Writer::layer_start(const Layer& layer) {
    if (touched.count(layer.index)) return;
    touched.insert(layer.index);
    std::vector<std::string> now, later;
    for (auto& c : layer.cmds) (SETTLED_WORDS.count(first_word(c)) ? later : now).push_back(c);
    outside(now);
    if (!later.empty()) waiting[layer.index] = later;
    settle();
    if (p.stats_info)
        outside({"SET_PRINT_STATS_INFO TOTAL_LAYER=" + std::to_string(p.layers.size()) +
                 " CURRENT_LAYER=" + std::to_string(layer.index + 1)});
}

void Writer::outside(const std::vector<std::string>& lines) {
    if (lines.empty()) return;
    end_object();
    for (auto& ln : lines) out.push_back(ln);
}

void Writer::settle() {
    while (settled < int(left.size()) && left[settled] == 0) settled++;
    for (auto it = waiting.begin(); it != waiting.end() && it->first <= settled;) {
        auto lines = it->second;
        it = waiting.erase(it);
        outside(lines);
    }
}

PathP Writer::own(const PathP& pp, int t, const Layer& layer) {
    if (!pp->tool) {
        State keep;
        for (auto& kv : pp->state)
            if (!PA_WORDS.count(kv.first.first)) keep.push_back(kv);
        if (keep == pp->state) return pp;
        auto np = std::make_shared<Path>(*pp);
        np->state = keep;
        return np;
    }
    if (*pp->tool == t) return pp;
    PathP q = refill(pp, t);
    auto fit = p.fans.find(std::make_tuple(t, layer.index, q->type));
    const State* st = p.state_at(t, layer.index, q->type);
    bool new_fans = fit != p.fans.end() && !same_fans(fit->second, q->fans);
    bool new_state = st && *st != q->state;
    if (!new_fans && !new_state) return q;
    auto np = std::make_shared<Path>(*q);
    if (new_fans) np->fans = fit->second;
    if (new_state) np->state = *st;
    return np;
}

PathP Writer::refill(const PathP& pp, int t) {
    auto at = [](const std::vector<double>& v, int i) { return v[std::min<size_t>(size_t(i), v.size() - 1)]; };
    double scale = (at(flow_ratio, t) / at(flow_ratio, *pp->tool)) * (at(area, *pp->tool) / at(area, t));
    std::vector<double> e, fv = pp->f;
    for (double v : pp->e) e.push_back(v * scale);
    if (!max_vol.empty()) {
        double cap = at(max_vol, t) * 60 / at(area, t);       // mm of filament per minute
        for (size_t k = 0; k < e.size(); k++) {
            double length = py_dist(pp->pts[k].first, pp->pts[k].second, pp->pts[k + 1].first, pp->pts[k + 1].second);
            if (e[k] > 0 && length > 0 && fv[k] * e[k] / length > cap) fv[k] = cap * length / e[k];
        }
    }
    if (e == pp->e && fv == pp->f) return pp;
    auto np = std::make_shared<Path>(*pp);
    np->e = e;
    np->f = fv;
    return np;
}

void Writer::path(const PathP& pp, const Layer& layer) {
    const Path& P = *pp;
    // the loaded filament's temperature for this layer; tower lines at whatever the loaded filament is at
    if (P.tool) set_temp(p.temp_at(tool, layer.index));
    if (P.obj != obj) {
        end_object();
        if (P.obj) emit("EXCLUDE_OBJECT_START NAME=" + *P.obj);
        obj = P.obj;
    }
    auto [x0, y0] = P.pts.front();
    PathP q = last;
    double z0 = P.z0();         // a scarf seam ramp starts below the layer
    double high = clear_z(x0, y0, z0);
    if (can_replay(P, q)) {
        replay(P.lead);
    } else if (q && high <= std::max(P.z, q->z) + 1e-6) {
        // nothing taller on the way: travel as the slicer did into this path (its retract and hop)
        bool moved = !(q->tail && P.head && P.seq == q->seq + 1 && P.tool == q->tool);
        bool far = pos && py_dist(pos->first, pos->second, x0, y0) > min_travel;
        bool in_tower = P.tower && q->tower;
        set_accel(travel_accel(P));
        travel(x0, y0, z0, std::make_pair(P.retract_before || (moved && far), P.hop_before && !(moved && in_tower)), high);
    } else {
        set_accel(travel_accel(P));
        travel(x0, y0, z0, std::nullopt, high);
    }
    if (P.accel && (!accel || *P.accel != *accel)) {
        emit(replace_all(p.accel_format, "{a}", fmt(*P.accel)));
        accel = P.accel;
    }
    if (P.scv && (!scv || *P.scv != *scv)) {
        emit("SET_VELOCITY_LIMIT SQUARE_CORNER_VELOCITY=" + fmt(*P.scv));
        scv = P.scv;
    }
    if (!P.vlim.empty() && P.vlim != vlim) {
        std::string line = "SET_VELOCITY_LIMIT";
        for (auto& [k, v] : P.vlim) line += " " + k + "=" + v;
        emit(line);
        vlim = P.vlim;
    }
    set_state(P.state);
    for (auto& [k, v] : P.fans) {
        auto it = std::find_if(fans.begin(), fans.end(), [&](auto& kv) { return kv.first == k; });
        if (it == fans.end() || it->second != v) {
            emit("M106 " + (k.empty() ? std::string() : k + " ") + "S" + std::to_string(v));
            set_fan(fans, k, v);
        }
    }
    if (!type || P.type != *type) {
        emit(";TYPE:" + P.type);
        type = P.type;
    }
    if (P.width != width) {
        emit(";WIDTH:" + fmt(*P.width));
        width = P.width;
    }
    double h = (P.height && *P.height != 0.0) ? *P.height : layer.height;
    if (!height || h != *height) {
        emit(";HEIGHT:" + fmt(h));
        height = h;
    }
    for (size_t i = 0; i < P.e.size(); i++) {
        auto [x, y] = P.pts[i + 1];
        double e = P.e[i], fv = P.f[i];
        std::string line = "G1 X" + fmt(x) + " Y" + fmt(y);
        if (!P.zs.empty() && std::fabs(P.zs[i + 1] - *z) > 1e-9) {     // scarf seam ramp
            line += " Z" + fmt(P.zs[i + 1]);
            z = P.zs[i + 1];
        }
        if (e > 0) line += " E" + fixed(e, 5);
        if (!f || fv != *f) line += " F" + fmt(fv);
        emit(line);
        f = fv;
    }
    pos = P.pts.back();
    last_pts = &pp->pts;
    last_pts_owner = pp;
    if (!P.tower) object_f = P.f.back();
    top = std::max(top, P.z);
    heights.add(P);
    last = pp;
    if (--left[layer.index] == 0) settle();
}

std::pair<std::vector<PathP>, double> Writer::cool(const std::vector<PathP>& paths, int t, int layer_index, double extra) {
    if (!min_layer_time || min_layer_time->empty() || paths.empty()) return {paths, 0.0};
    size_t k = (t < int(min_layer_time->size())) ? size_t(t) : 0;
    double need = (*min_layer_time)[k];
    double floor = (min_speed && !min_speed->empty() ? (*min_speed)[std::min(k, min_speed->size() - 1)] : 0.0) * 60;
    double tt = print_time(paths);
    if (need <= 0 || tt <= 0) return {paths, 0.0};
    if (tt + extra >= layer_time[layer_index] * 0.999) return {paths, 0.0};   // as long as the original layer
    need = std::min(need, layer_time[layer_index]);     // never more cooling time than the original gave
    if (tt + extra >= need || !o.respect_min_layer_time) return {fan(paths, int(k), tt + extra, layer_index), 0.0};
    // slow every extrusion proportionally, not below the minimum speed; outer walls keep their sliced speed
    std::vector<PathP> walls;
    for (auto& pp : paths)
        if (pp->type == "Outer wall") walls.push_back(pp);
    double fixed_t = print_time(walls);
    double target = need - extra - fixed_t;
    std::vector<PathP> outp;
    if (target > tt - fixed_t && tt - fixed_t > 0) {
        double factor = (tt - fixed_t) / target;
        for (auto& pp : paths) {
            if (pp->type == "Outer wall") {
                outp.push_back(pp);
                continue;
            }
            auto np = std::make_shared<Path>(*pp);
            for (double& fv : np->f) fv = std::max(fv * factor, std::min(fv, floor));
            outp.push_back(np);
        }
    } else {
        outp = paths;
    }
    double t2 = print_time(outp) + extra;
    slowed++;
    if (t2 < need - 0.05) short_++;
    return {fan(outp, int(k), t2, layer_index), std::max(0.0, need - t2)};
}

std::vector<PathP> Writer::fan(const std::vector<PathP>& paths, int k, double lt, int layer_index) {
    auto ok = [](const std::optional<std::vector<double>>& v) { return v && !v->empty(); };
    if (!(o.stacked_fan && ok(fan_max) && ok(fan_min) && ok(fan_layer_time))) return paths;
    if (layer_index < no_fan_layers) return paths;
    auto pick = [&](const std::vector<double>& v) { return v[std::min<size_t>(size_t(k), v.size() - 1)]; };
    double lo = pick(*fan_min), hi = pick(*fan_max);
    double slow = pick(*min_layer_time), full = pick(*fan_layer_time);
    double pct;
    if (lt < slow) {
        pct = hi;
    } else if (lt < full && full > slow) {
        double tt = (lt - slow) / (full - slow);
        pct = std::floor(tt * lo + (1 - tt) * hi + 0.5);
    } else {
        return paths;
    }
    int s = int(std::nearbyint(pct * 255 / 100));       // Python round(): half to even
    std::vector<PathP> outp;
    for (auto& pp : paths) {
        if (fan_get(pp->fans, "", 0) < s) {
            auto np = std::make_shared<Path>(*pp);
            set_fan(np->fans, "", s);
            fanned++;
            outp.push_back(np);
        } else {
            outp.push_back(pp);
        }
    }
    return outp;
}

void Writer::wait(double seconds) {
    retract_move();
    double zz = (z && *z != 0.0) ? *z : 0.0;
    lift(std::max(top, zz) + std::max(z_hop, o.swap_lift));
    emit("G4 P" + std::to_string((long long)std::nearbyint(seconds * 1000)) + " ; ColorStack: rest of the minimum layer time");
    waited++;
}

std::vector<std::string> Writer::write(const std::vector<Band>& bands, const std::vector<std::string>& summary_lines) {
    for (auto& ln : p.preamble) out.push_back(ln);
    for (auto& s : summary_lines) out.push_back("; " + s);
    if (p.abs_e) emit("M83 ; ColorStack writes relative extrusion");
    left.assign(p.layers.size(), 0);
    for (auto& band : bands)
        for (size_t j = 0; j < band.layers.size(); j++) left[band.layers[j]] += int(band.work[j].size());
    for (auto& band : bands) {
        for (auto& unit : band.order) {
            size_t nl = band.layers.size();
            std::vector<std::vector<PathP>> items(nl), parts(nl), first(nl), between(nl);
            for (size_t j = 0; j < nl; j++) {
                const Layer& L = p.layers[band.layers[j]];
                for (auto& [owner, pp] : band.work[j])
                    if (owner == unit) items[j].push_back(own(pp, unit.tool, L));
                for (auto& pp : items[j])
                    if (!pp->tower) parts[j].push_back(pp);
            }
            std::optional<int> first_layer;
            PathP first_path;
            for (size_t j = 0; j < nl && !first_layer; j++)
                if (!parts[j].empty()) {
                    first_layer = p.layers[band.layers[j]].index;
                    first_path = parts[j].front();
                }
            static const State empty;
            change_tool(unit.tool, first_layer ? p.temp_at(unit.tool, *first_layer) : std::nullopt,
                        first_path ? first_path->state : empty);
            int t = unit.tool;
            // tower right after the swap; with tower_between only the wipe, walls and grid between layers
            auto late = [&](const PathP& pp) { return o.tower_between && pp->tower && (*pp->tower == "wall" || *pp->tower == "grid"); };
            for (size_t j = 0; j < nl; j++)
                for (auto& pp : items[j]) {
                    if (pp->tower && !late(pp)) first[j].push_back(pp);
                    if (late(pp)) between[j].push_back(pp);
                }
            for (size_t j = 0; j < nl; j++) {
                const Layer& L = p.layers[band.layers[j]];
                if (!first[j].empty()) layer_start(L);
                for (auto& pp : first[j]) path(pp, L);
            }
            for (size_t j = 0; j < nl; j++) {
                const Layer& L = p.layers[band.layers[j]];
                std::vector<PathP> mine = parts[j];
                const std::vector<PathP>& rest = between[j];
                double shortfall = 0.0;
                if (!mine.empty() || !rest.empty()) layer_start(L);
                if (!mine.empty() && j + 1 < nl && !parts[j + 1].empty()) {
                    auto r = cool(mine, t, L.index, print_time(rest));
                    mine = r.first;
                    shortfall = r.second;
                }
                for (auto& pp : mine) path(pp, L);
                for (auto& pp : rest) path(pp, L);
                if (shortfall > 0.05 && o.lift_wait) wait(shortfall);
            }
        }
        for (int li : band.layers) layer_start(p.layers[li]);    // a layer with no extrusion still gets its commands
    }
    end_object();
    while (!waiting.empty()) {      // left over only if a layer never settled (nothing below it printed)
        auto lines = waiting.begin()->second;
        waiting.erase(waiting.begin());
        outside(lines);
    }
    if (!retracted) plain_retract();
    lift(top + z_hop);
    if (p.abs_e) {
        emit("G92 E0");
        emit("M82 ; back to the file's absolute extrusion for its end G-code");
    }
    for (auto& ln : p.postamble) out.push_back(ln);
    return out;
}

}  // namespace cb
