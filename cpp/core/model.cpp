#include "model.hpp"

#include <cstdlib>

#include "pymath.hpp"
#include "reader.hpp"

namespace cb {

const std::set<std::string> VISIBLE_TYPES = {"Outer wall", "Overhang wall", "Top surface", "Bottom surface", "Ironing"};
const std::set<std::string> NEUTRAL_TYPES = {"Prime tower"};
const std::set<std::string> BARRIERS = {"M600", "M601", "M0", "M1", "M25", "M125", "PAUSE"};

void set_fan(Fans& fans, const std::string& key, int value) {
    for (auto& kv : fans)
        if (kv.first == key) {
            kv.second = value;
            return;
        }
    fans.emplace_back(key, value);
}

double Path::length() const {
    double total = 0;
    for (size_t k = 0; k < e.size(); k++) total += py_dist(pts[k].first, pts[k].second, pts[k + 1].first, pts[k + 1].second);
    return total;
}

bool Layer::barrier() const {
    for (const auto& c : cmds) {
        std::string_view s = py_strip(c);
        size_t n = 0;
        while (n < s.size() && !py_space(s[n])) n++;
        if (BARRIERS.count(py_upper(s.substr(0, n)))) return true;
    }
    return false;
}

std::optional<double> Program::temp_at(int tool, int index) const {
    auto it = temps.find(tool);
    if (it == temps.end() || it->second.empty()) return std::nullopt;
    return it->second[index];
}

const State* Program::state_at(int tool, int index, const std::string& type) const {
    // the rows for (tool, type), else every row of the tool in the table's order; the nearest layer, the first on a tie
    const std::vector<std::pair<int, State>>* exact = nullptr;
    for (const auto& kv : states)
        if (kv.first.first == tool && kv.first.second == type) exact = &kv.second;
    const State* best = nullptr;
    int best_d = 0;
    auto consider = [&](const std::vector<std::pair<int, State>>& rows) {
        for (const auto& r : rows) {
            int d = std::abs(r.first - index);
            if (!best || d < best_d) {
                best = &r.second;
                best_d = d;
            }
        }
    };
    if (exact && !exact->empty()) {
        consider(*exact);
    } else {
        for (const auto& kv : states)
            if (kv.first.first == tool) consider(kv.second);
    }
    return best;
}

std::optional<std::vector<double>> Program::setting_list(const std::string& key) const {
    auto it = settings.find(key);
    if (it == settings.end()) return std::nullopt;
    std::vector<double> out;
    std::string_view s = it->second;
    size_t start = 0;
    while (true) {
        size_t comma = s.find(',', start);
        double v;
        if (!py_float(s.substr(start, comma == std::string_view::npos ? std::string_view::npos : comma - start), v))
            return std::nullopt;
        out.push_back(v);
        if (comma == std::string_view::npos) break;
        start = comma + 1;
    }
    return out;
}

double first_setting(const std::map<std::string, std::string>& settings, const std::string& key, double fallback) {
    auto it = settings.find(key);
    if (it == settings.end()) return fallback;
    double v;
    std::string_view s = it->second;
    return py_float(s.substr(0, s.find(',')), v) ? v : fallback;
}

}  // namespace cb
