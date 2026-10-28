// Port of color_batching/summary.py.
#include "summary.hpp"

#include <charconv>
#include <cmath>
#include <map>

#include "reader.hpp"

namespace cb {

namespace {

bool starts(std::string_view s, std::string_view p) { return s.substr(0, p.size()) == p; }
bool digit(char c) { return c >= '0' && c <= '9'; }
bool letter(char c) { return (c >= 'A' && c <= 'Z') || (c >= 'a' && c <= 'z'); }

std::string fixed(double v, int digits) {
    char buf[512];
    auto r = std::to_chars(buf, buf + sizeof buf, v, std::chars_format::fixed, digits);
    return std::string(buf, r.ptr);
}

std::vector<double> numbers(std::string_view text) {      // [float(x) for x in text.split(",")]
    std::vector<double> out;
    size_t start = 0;
    while (true) {
        size_t c = text.find(',', start);
        double v = 0;
        py_float(text.substr(start, c == std::string_view::npos ? std::string_view::npos : c - start), v);
        out.push_back(v);
        if (c == std::string_view::npos) break;
        start = c + 1;
    }
    return out;
}

std::string floats(const std::vector<double>& values) {
    std::string out;
    for (size_t k = 0; k < values.size(); k++) out += (k ? ", " : "") + fixed(values[k], 2);
    return out;
}

double get(const std::map<std::optional<int>, double>& m, int t) {
    auto it = m.find(t);
    return it == m.end() ? 0.0 : it->second;
}
int get(const std::map<int, int>& m, int t) {
    auto it = m.find(t);
    return it == m.end() ? 0 : it->second;
}

// re.sub(r"([A-Za-z]+)(\d+(?:\.\d+)?)", f, s)
template <typename F>
std::string sub_numbers(std::string_view s, F&& f) {
    std::string out;
    size_t i = 0;
    while (i < s.size()) {
        if (letter(s[i])) {
            size_t j = i;
            while (j < s.size() && letter(s[j])) j++;
            if (j < s.size() && digit(s[j])) {
                size_t k = j;
                while (k < s.size() && digit(s[k])) k++;
                if (k + 1 < s.size() && s[k] == '.' && digit(s[k + 1])) {
                    k++;
                    while (k < s.size() && digit(s[k])) k++;
                }
                out += f(std::string(s.substr(i, j - i)), std::string(s.substr(j, k - j)), std::string(s.substr(i, k - i)));
                i = k;
                continue;
            }
        }
        out += s[i++];
    }
    return out;
}

}  // namespace

void stamp_header(std::vector<std::string>& lines, const Estimate& o, const Estimate& p) {
    double ratio = o.total_s != 0.0 ? p.total_s / o.total_s : 1.0;
    std::map<int, std::pair<double, double>> lengths;     // tool -> (original mm, new mm)
    auto used_mm = [&](const std::vector<double>& values) {
        std::vector<double> out;
        for (size_t t = 0; t < values.size(); t++) {
            double mm = values[t];
            double flush = std::max(0.0, mm - get(o.extruded, int(t)));
            int into_o = get(o.swaps_into, int(t)), into_p = get(p.swaps_into, int(t));
            double share = into_o ? (1 - double(into_p) / double(into_o)) : 0.0;
            double nw = mm + get(p.extruded, int(t)) - get(o.extruded, int(t)) - flush * share;
            out.push_back(mm != 0.0 ? std::max(0.0, nw) : 0.0);
            lengths[int(t)] = {mm, out.back()};
        }
        return out;
    };
    auto per_tool = [&](const std::vector<double>& values) {      // cm3, g, cost: in proportion to the length
        std::vector<double> out;
        for (size_t t = 0; t < values.size(); t++) {
            auto it = lengths.find(int(t));
            out.push_back(it != lengths.end() && it->second.first != 0.0 ? values[t] * it->second.second / it->second.first : values[t]);
        }
        return out;
    };
    for (auto& ln : lines) {
        if (!starts(ln, ";")) continue;        // every rule matches a comment line
        const std::string normal = "; estimated printing time (normal mode) = ";
        const std::string silent = "; estimated printing time (silent mode) = ";
        const std::string sport = "; estimated printing time (sport mode) = ";
        if (starts(ln, normal)) {
            ln = normal + fmt_duration(p.total_s);
        } else if (starts(ln, silent) || starts(ln, sport)) {
            size_t n = starts(ln, silent) ? silent.size() : sport.size();
            ln = ln.substr(0, n) + fmt_duration(parse_duration(ln.substr(n)) * ratio);
        } else if (starts(ln, ";TIME:") && ln.size() > 6 && digit(ln[6])) {
            ln = ";TIME:" + std::to_string((long long)std::nearbyint(p.total_s));
        } else if (starts(ln, "; total filament change = ") && ln.size() > 26 && digit(ln[26])) {
            ln = "; total filament change = " + std::to_string(p.swaps);
        } else if (starts(ln, "; filament used [mm] = ")) {
            const size_t n = 23;
            ln = ln.substr(0, n) + floats(used_mm(numbers(std::string_view(ln).substr(n))));
        } else {
            for (const char* prefix : {"; filament used [cm3] = ", "; filament used [g] = ", "; filament cost = "}) {
                std::string pre = prefix;
                if (starts(ln, pre)) {
                    ln = pre + floats(per_tool(numbers(std::string_view(ln).substr(pre.size()))));
                    break;
                }
            }
        }
    }
    // totals from the per-tool lines just written
    std::map<std::string, double> sums;
    for (auto& ln : lines)
        for (const char* key : {"filament used [g]", "filament cost"}) {
            std::string pre = std::string("; ") + key + " = ";
            if (starts(ln, pre)) {
                double s = 0;
                for (double v : numbers(std::string_view(ln).substr(pre.size()))) s += v;
                sums[key] = s;
            }
        }
    for (auto& ln : lines)
        for (const char* key : {"filament used [g]", "filament cost"}) {
            std::string pre = std::string("; total ") + key + " = ";
            if (starts(ln, pre) && sums.count(key)) ln = pre + fixed(sums[key], 2);
        }
}

std::vector<std::string> stamp_progress(const std::vector<std::string>& lines, const Steps& steps, double each, double total) {
    std::vector<size_t> at;
    for (size_t k = 0; k < lines.size(); k++)
        if (starts(lines[k], "M73 ")) at.push_back(k);
    if (at.empty() || total <= 0) return lines;
    size_t first_move = steps.empty() ? lines.size() : steps.front().first;
    std::vector<std::string> templates;
    for (size_t k : at)
        if (k < first_move) templates.push_back(lines[k]);
    if (templates.empty()) templates.push_back(lines[at[0]]);
    std::optional<double> ref;
    for (auto& t : templates) {
        if (ref) break;
        sub_numbers(t, [&](const std::string& name, const std::string& val, const std::string& whole) {
            if (!ref && name == "R") {
                double v;
                py_float(val, v);
                ref = v;
            }
            return whole;
        });
    }
    if (!ref || *ref == 0.0) return lines;

    auto render = [&](long long pct, double rem_s, bool start) {
        double scale = rem_s / 60 / *ref;
        std::vector<std::string> outv;
        for (auto& t : templates) {
            size_t c = t.find(';');
            std::string code = t.substr(0, c), rest = c == std::string::npos ? "" : t.substr(c + 1);
            auto num = [&](bool in_code) {
                return [&, in_code](const std::string& name, const std::string& val, const std::string& whole) -> std::string {
                    if (name == "M") return whole;
                    if (name == "P" || name == "Q") return name + std::to_string(pct);
                    double v;
                    py_float(val, v);
                    double x = v * scale;
                    size_t dot = val.find('.');
                    int dec = dot == std::string::npos ? 0 : int(val.size() - dot - 1);
                    if (dec && (start || !in_code)) return name + fixed(x, dec);    // decimals only on the first line
                    return name + std::to_string((long long)std::trunc(x));
                };
            };
            outv.push_back(sub_numbers(code, num(true)) + (c == std::string::npos ? "" : ";") + sub_numbers(rest, num(false)));
        }
        return outv;
    };
    std::map<size_t, std::vector<std::string>> after;
    double elapsed = 0.0;
    std::pair<long long, long long> last = {0, (long long)std::floor(total / 60)};
    for (auto& [k, sec] : steps) {
        elapsed += sec ? *sec : each;
        double rem = std::max(0.0, total - elapsed);
        std::pair<long long, long long> key = {std::min<long long>(99, (long long)std::trunc(elapsed / total * 100)),
                                               (long long)std::floor(rem / 60)};
        if (key != last) {
            after[k] = render(key.first, rem, false);
            last = key;
        }
    }
    size_t end = steps.empty() ? lines.size() : steps.back().first;
    std::vector<std::string> outl;
    for (size_t k = 0; k < lines.size(); k++) {
        if (k == at[0]) {
            auto r = render(0, total, true);
            outl.insert(outl.end(), r.begin(), r.end());
        } else if (!starts(lines[k], "M73 ") || k > end) {
            outl.push_back(lines[k]);
        }
        auto it = after.find(k);
        if (it != after.end()) outl.insert(outl.end(), it->second.begin(), it->second.end());
    }
    return outl;
}

}  // namespace cb
