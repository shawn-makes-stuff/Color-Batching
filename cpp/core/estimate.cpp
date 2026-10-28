// Port of colorstack/estimate.py.
#include "estimate.hpp"

#include <algorithm>
#include <cmath>

#include "model.hpp"
#include "reader.hpp"

namespace cb {

namespace {

struct M {
    double d, vmax, a;
    bool has_u;
    double ux, uy, uz;
    size_t line;
};

// time of moves; per-move (line, s) into steps
double flush_moves(const std::vector<M>& moves, double scv, Steps* steps) {
    size_t n = moves.size();
    if (!n) return 0.0;
    std::vector<double> jv2(n + 1, 0.0);       // max speed^2 at the junction before move k
    for (size_t k = 1; k < n; k++) {
        const M& m0 = moves[k - 1];
        const M& m1 = moves[k];
        if (!m0.has_u || !m1.has_u) continue;
        double cos_t = -(m0.ux * m1.ux + m0.uy * m1.uy + m0.uz * m1.uz);
        double lim2 = std::min(m0.vmax * m0.vmax, m1.vmax * m1.vmax);
        if (cos_t < -0.999999) {               // straight on
            jv2[k] = lim2;
            continue;
        }
        double sin_d2 = std::sqrt(std::max(0.0, 0.5 * (1.0 - cos_t)));
        if (sin_d2 > 0.999999) continue;       // full reversal
        double a = std::min(m0.a, m1.a);
        double jd = scv * scv * (std::sqrt(2.0) - 1) / a;
        jv2[k] = std::min(jd * a * sin_d2 / (1.0 - sin_d2), lim2);
    }
    std::vector<double> end2(n, 0.0);          // backward pass: brake in time for the next junction
    double nxt = 0.0;
    for (size_t kk = n; kk-- > 0;) {
        const M& m = moves[kk];
        end2[kk] = std::min(jv2[kk + 1], nxt);
        nxt = std::min({jv2[kk], end2[kk] + 2 * m.a * m.d, m.vmax * m.vmax});
    }
    double t = 0.0, cur = 0.0;
    for (size_t k = 0; k < n; k++) {
        const M& m = moves[k];
        double d = m.d, v = m.vmax, a = m.a;
        double s2 = k ? std::min({cur, jv2[k], v * v}) : 0.0;
        double e2 = std::min({end2[k], s2 + 2 * a * d, v * v});
        double vc2 = std::max({std::min(v * v, a * d + (s2 + e2) / 2), s2, e2});
        double vs = std::sqrt(s2), vc = std::sqrt(vc2), ve = std::sqrt(e2);
        double dc = std::max(0.0, d - (vc2 - s2) / (2 * a) - (vc2 - e2) / (2 * a));
        double tk = (vc - vs) / a + (vc - ve) / a + (vc > 0 ? dc / vc : 0.0);
        t += tk;
        if (steps) steps->emplace_back(m.line, tk);
        cur = e2;
    }
    return t;
}

bool digit(char c) { return c >= '0' && c <= '9'; }

}  // namespace

Limits machine_limits(const std::map<std::string, std::string>& s, const PrinterFallback& fb) {
    auto first = [&](const char* k, double d) { return first_setting(s, k, d); };
    return {std::min(first("machine_max_speed_x", fb.max_velocity), first("machine_max_speed_y", fb.max_velocity)),
            first("machine_max_acceleration_extruding", fb.max_accel),
            first("machine_max_jerk_x", fb.square_corner_velocity),
            first("machine_max_speed_z", fb.max_z_velocity),
            first("machine_max_acceleration_z", fb.max_z_accel),
            first("machine_unload_filament_time", 0.0) + first("machine_load_filament_time", 0.0)};
}

Estimate estimate(const std::vector<std::string>& lines, const Limits& lim, Steps* steps) {
    Estimate r;
    double accel = lim.a, scv = lim.scv;
    std::vector<M> moves;
    std::optional<int> tool;
    read(lines, [&](size_t i, const Event& ev) {
        if (ev.kind == Kind::move) {
            const Move& v = ev.move;
            r.extruded[tool] += v.de;
            double dx = v.x1 - v.x0, dy = v.y1 - v.y0, dz = v.z1 - v.z0;
            double d = std::sqrt(dx * dx + dy * dy + dz * dz);
            if (d > 1e-9) {
                double vmax = std::min(v.f / 60, lim.v), a = accel;
                if (std::fabs(dz) > 1e-9) {
                    double ratio = d / std::fabs(dz);
                    vmax = std::min(vmax, lim.zv * ratio);
                    a = std::min(a, lim.za * ratio);
                }
                moves.push_back({d, vmax, a, true, dx / d, dy / d, dz / d, i});
            } else if (v.de != 0) {
                moves.push_back({std::fabs(v.de), v.f / 60, 5000.0, false, 0, 0, 0, i});   // retract / deretract
            }
            return;
        }
        if (ev.kind == Kind::accel) {
            accel = std::min(ev.value, lim.a);
            return;
        }
        std::string w;
        if (ev.kind == Kind::cmd) {
            std::string_view s = py_strip(ev.text);
            size_t n = 0;
            while (n < s.size() && !py_space(s[n])) n++;
            w = py_upper(s.substr(0, n));
        }
        if (ev.kind != Kind::scv && ev.kind != Kind::tool && w != "G4" && w != "M400" && w != "PRINT_END") return;
        r.motion_s += flush_moves(moves, scv, steps);
        moves.clear();
        if (ev.kind == Kind::scv) {
            scv = ev.value;
        } else if (ev.kind == Kind::tool) {
            if (tool && ev.tool != *tool) {
                r.swaps++;
                r.swaps_into[ev.tool]++;
                if (steps) steps->emplace_back(i, std::nullopt);
            }
            tool = ev.tool;
        } else if (w == "G4") {
            // re.search(r"([PS])(\d*\.?\d+)", code[2:])
            std::string_view s = std::string_view(ev.text).substr(2);
            for (size_t k = 0; k < s.size(); k++) {
                if (s[k] != 'P' && s[k] != 'S') continue;
                size_t j = k + 1, d0 = j;
                while (j < s.size() && digit(s[j])) j++;
                size_t len = 0;
                if (j + 1 < s.size() && s[j] == '.' && digit(s[j + 1])) {
                    j++;
                    while (j < s.size() && digit(s[j])) j++;
                    len = j - d0;
                } else if (j > d0) {
                    len = j - d0;
                }
                if (!len) continue;
                double val;
                py_float(s.substr(d0, len), val);
                double sec = val / (s[k] == 'P' ? 1000 : 1);
                r.dwell_s += sec;
                if (steps) steps->emplace_back(i, sec);
                break;
            }
        }
    });
    r.motion_s += flush_moves(moves, scv, steps);
    return r;
}

double parse_duration(const std::string& text) {
    // sum of int(v) * unit for re.findall(r"(\d+)\s*([dhms])", text)
    long long total = 0;
    for (size_t i = 0; i < text.size();) {
        if (digit(text[i])) {
            size_t j = i;
            while (j < text.size() && digit(text[j])) j++;
            size_t k = j;
            while (k < text.size() && py_space(text[k])) k++;
            if (k < text.size() && (text[k] == 'd' || text[k] == 'h' || text[k] == 'm' || text[k] == 's')) {
                long long v = std::stoll(text.substr(i, j - i));
                total += v * (text[k] == 'd' ? 86400 : text[k] == 'h' ? 3600 : text[k] == 'm' ? 60 : 1);
                i = k + 1;
                continue;
            }
        }
        i++;
    }
    return double(total);
}

std::string fmt_duration(double seconds) {
    long long s = (long long)std::nearbyint(seconds);      // Python round(): half to even
    long long h = s / 3600, r = s % 3600, m = r / 60, sec = r % 60;
    return (h ? std::to_string(h) + "h " : std::string()) + std::to_string(m) + "m " + std::to_string(sec) + "s";
}

std::optional<double> file_estimate(const std::vector<std::string>& lines) {
    for (const auto& raw : lines) {
        std::string_view s = py_strip(raw);
        // ^;\s*estimated printing time(?: \(normal mode\))?\s*=\s*(.+)$
        if (!s.empty() && s[0] == ';') {
            size_t j = 1;
            while (j < s.size() && py_space(s[j])) j++;
            const std::string_view key = "estimated printing time";
            if (s.substr(j, key.size()) == key) {
                size_t k = j + key.size();
                const std::string_view normal = " (normal mode)";
                size_t k2 = k;
                if (s.substr(k, normal.size()) == normal) k2 = k + normal.size();
                for (size_t start : {k2, k}) {      // with the optional group, then without (backtracking)
                    size_t q = start;
                    while (q < s.size() && py_space(s[q])) q++;
                    if (q < s.size() && s[q] == '=') {
                        q++;
                        while (q < s.size() && py_space(s[q])) q++;
                        if (q < s.size()) {
                            double d = parse_duration(std::string(s.substr(q)));
                            if (d != 0.0) return d;
                            return std::nullopt;
                        }
                    }
                    if (start == k) break;
                }
            }
        }
        if (s.substr(0, 6) == ";TIME:" && s.size() > 6 && digit(s[6])) {
            size_t j = 6;
            while (j < s.size() && digit(s[j])) j++;
            double v;
            py_float(s.substr(6, j - 6), v);
            return v;
        }
    }
    return std::nullopt;
}

std::pair<double, std::string> swap_each(const Estimate& o, std::optional<double> slicer, const Limits& lim) {
    if (slicer) return {o.swaps ? std::max(0.0, *slicer - o.motion_s - o.dwell_s) / o.swaps : 0.0, "slicer estimate"};
    return {lim.swap_s, "ColorStack model"};
}

void anchor(Estimate& o, Estimate& p, std::optional<double> slicer, const Limits& lim) {
    auto [each, source] = swap_each(o, slicer, lim);
    for (Estimate* r : {&o, &p}) {
        r->swap_s = r->swaps * each;
        r->total_s = r->motion_s + r->dwell_s + r->swap_s;
        r->source = source;
        r->swap_each_s = each;
    }
    if (slicer) {
        o.total_s = *slicer;
        p.total_s = *slicer + (p.motion_s - o.motion_s) + (p.dwell_s - o.dwell_s) - (o.swaps - p.swaps) * each;
    }
}

}  // namespace cb
