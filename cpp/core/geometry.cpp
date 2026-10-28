#include "geometry.hpp"

#include <algorithm>
#include <cmath>
#include <cstdint>
#include <cstring>

#include "pymath.hpp"

namespace cb {

namespace {
const double PI = 3.141592653589793;
const double DEG_TO_RAD = PI / 180.0;    // CPython math.radians: x * degToRad
}  // namespace

double Nozzle::reach(double dz) const { return tip_radius + dz * std::tan(cone_angle * DEG_TO_RAD); }

Samples1 densify(const Path& p, double step) {
    Samples1 s;
    for (size_t i = 0; i + 1 < p.pts.size(); i++) {
        auto [ax, ay] = p.pts[i];
        auto [bx, by] = p.pts[i + 1];
        long long n = std::max<long long>(1, (long long)(py_hypot({bx - ax, by - ay}) / step));
        double dx = bx - ax, dy = by - ay;
        for (long long k = 0; k < n; k++) {
            double t = (double(k) + 0.5) / double(n);
            s.x.push_back(ax + dx * t);
            s.y.push_back(ay + dy * t);
            s.seg.push_back(int(i));
            s.t.push_back(t);
        }
    }
    return s;
}

KDTree::KDTree(std::vector<std::pair<double, double>> pts) : pts_(std::move(pts)) {
    if (!pts_.empty()) build(0, int(pts_.size()));
}

int KDTree::build(int begin, int end) {
    Node nd{{INFINITY, INFINITY}, {-INFINITY, -INFINITY}, begin, end};
    for (int k = begin; k < end; k++) {
        nd.lo[0] = std::min(nd.lo[0], pts_[k].first);
        nd.hi[0] = std::max(nd.hi[0], pts_[k].first);
        nd.lo[1] = std::min(nd.lo[1], pts_[k].second);
        nd.hi[1] = std::max(nd.hi[1], pts_[k].second);
    }
    int at = int(nodes_.size());
    nodes_.push_back(nd);
    if (end - begin <= 16) return at;
    bool by_x = nd.hi[0] - nd.lo[0] >= nd.hi[1] - nd.lo[1];
    int mid = (begin + end) / 2;
    std::nth_element(pts_.begin() + begin, pts_.begin() + mid, pts_.begin() + end,
                     [by_x](const auto& a, const auto& b) { return by_x ? a.first < b.first : a.second < b.second; });
    int l = build(begin, mid);
    int r = build(mid, end);
    nodes_[at].left = l;
    nodes_[at].right = r;
    return at;
}

namespace {
// squared distance to a box: never more than to any point in it (rounding is monotonic), so pruning on it never
// skips the nearest point
double box2(const double lo[2], const double hi[2], double qx, double qy) {
    double dx = qx < lo[0] ? lo[0] - qx : qx > hi[0] ? qx - hi[0] : 0.0;
    double dy = qy < lo[1] ? lo[1] - qy : qy > hi[1] ? qy - hi[1] : 0.0;
    return dx * dx + dy * dy;
}
}  // namespace

void KDTree::search(int n, double qx, double qy, double& best) const {
    const Node& nd = nodes_[n];
    if (nd.left < 0) {
        for (int k = nd.begin; k < nd.end; k++) {
            double dx = qx - pts_[k].first, dy = qy - pts_[k].second;
            double d = dx * dx + dy * dy;      // scipy's sqeuclidean for 2 dimensions
            if (d < best) best = d;
        }
        return;
    }
    const Node& a = nodes_[nd.left];
    const Node& b = nodes_[nd.right];
    double da = box2(a.lo, a.hi, qx, qy), db = box2(b.lo, b.hi, qx, qy);
    if (da <= db) {
        if (da < best) search(nd.left, qx, qy, best);
        if (db < best) search(nd.right, qx, qy, best);
    } else {
        if (db < best) search(nd.right, qx, qy, best);
        if (da < best) search(nd.left, qx, qy, best);
    }
}

double KDTree::nearest(double qx, double qy, double bound) const {
    double b2 = bound * bound, best = b2;
    if (!nodes_.empty() && box2(nodes_[0].lo, nodes_[0].hi, qx, qy) < best) search(0, qx, qy, best);
    return best < b2 ? std::sqrt(best) : INFINITY;
}

std::vector<double> nearest(const KDTree* tree, const Samples1& s, double bound) {
    std::vector<double> out(s.x.size(), INFINITY);
    if (tree)
        for (size_t k = 0; k < out.size(); k++) out[k] = tree->nearest(s.x[k], s.y[k], bound);
    return out;
}

std::vector<std::pair<bool, PathP>> split(const Path& p, const Samples1& s, const std::vector<char>& mask) {
    std::vector<std::pair<bool, PathP>> out;
    std::optional<bool> cur_flag;
    std::vector<std::pair<double, double>> cur_pts;
    std::vector<double> cur_e, cur_f, cur_z;
    auto flush = [&]() {
        if (!cur_flag || cur_pts.size() <= 1) return;
        auto q = std::make_shared<Path>(p);
        q->pts = cur_pts;
        q->e = cur_e;
        q->f = cur_f;
        q->head = p.head && out.empty();
        q->tail = false;
        if (!p.zs.empty()) {
            auto [lo, hi] = std::minmax_element(cur_z.begin(), cur_z.end());
            if (*hi - *lo > 1e-6) {
                q->zs = cur_z;
                q->z = *hi;
            } else {
                q->zs.clear();
                q->z = cur_z[0];
            }
        }
        out.emplace_back(*cur_flag, q);
    };
    size_t k0 = 0;
    for (size_t i = 0; i < p.e.size(); i++) {
        size_t k1 = k0;
        while (k1 < s.seg.size() && s.seg[k1] == int(i)) k1++;
        // cut midway between samples whose flag differs
        std::vector<double> cuts{0.0};
        std::vector<bool> cf{mask[k0] != 0};
        for (size_t k = k0 + 1; k < k1; k++)
            if (mask[k] != mask[k - 1]) {
                cuts.push_back((s.t[k] + s.t[k - 1]) / 2);
                cf.push_back(mask[k] != 0);
            }
        cuts.push_back(1.0);
        auto [x0, y0] = p.pts[i];
        auto [x1, y1] = p.pts[i + 1];
        double z0 = 0, z1 = 0;
        if (!p.zs.empty()) z0 = p.zs[i], z1 = p.zs[i + 1];
        for (size_t k = 0; k < cf.size(); k++) {
            double ta = cuts[k], tb = cuts[k + 1];
            std::pair<double, double> a{x0 + (x1 - x0) * ta, y0 + (y1 - y0) * ta};
            std::pair<double, double> b{x0 + (x1 - x0) * tb, y0 + (y1 - y0) * tb};
            if (!cur_flag || cf[k] != *cur_flag) {
                flush();
                cur_flag = cf[k];
                cur_pts = {a};
                cur_e.clear();
                cur_f.clear();
                cur_z.clear();
                if (!p.zs.empty()) cur_z.push_back(z0 + (z1 - z0) * ta);
            }
            cur_pts.push_back(b);
            if (!p.zs.empty()) cur_z.push_back(z0 + (z1 - z0) * tb);
            cur_e.push_back(p.e[i] * (tb - ta));
            cur_f.push_back(p.f[i]);
        }
        k0 = k1;
    }
    flush();
    if (!out.empty()) {
        auto q = std::make_shared<Path>(*out.back().second);
        q->tail = p.tail;
        out.back().second = q;
    }
    return out;
}

std::vector<char> absorb_short_runs(const std::vector<char>& mask, double min_samples) {
    std::vector<char> out = mask;
    size_t n = mask.size(), i = 0;
    while (i < n) {
        if (out[i]) {
            i++;
            continue;
        }
        size_t j = i;
        while (j < n && !mask[j]) j++;
        if (double(j - i) < min_samples) std::fill(out.begin() + i, out.begin() + j, char(1));
        i = j;
    }
    return out;
}

std::vector<char> interface_mask(const Path& p, const Samples1& s, const KDTree* layer_tree) {
    size_t n = s.x.size();
    std::vector<char> out(n, 0);
    if (!layer_tree || !p.visible() || (p.type != "Outer wall" && p.type != "Overhang wall")) return out;
    double w = *p.width, near = 0.45 * w;
    std::vector<char> sides[2];
    for (int side = 0; side < 2; side++) {
        double sgn = side == 0 ? 1.0 : -1.0;
        sides[side].assign(n, 0);
        for (size_t k = 0; k < n; k++) {
            auto [ax, ay] = p.pts[size_t(s.seg[k])];
            auto [bx, by] = p.pts[size_t(s.seg[k]) + 1];
            double dx = bx - ax, dy = by - ay;
            double len = std::hypot(dx, dy);      // numpy hypot = the C library's
            if (len == 0) len = 1;
            double nx = -dy / len, ny = dx / len;
            for (double off : {0.75, 1.25, 1.75, 2.25}) {      // line widths out, own line excluded
                double qx = s.x[k] + sgn * nx * off * w, qy = s.y[k] + sgn * ny * off * w;
                if (layer_tree->nearest(qx, qy, w) < near) {
                    sides[side][k] = 1;
                    break;
                }
            }
        }
    }
    for (size_t k = 0; k < n; k++) out[k] = sides[0][k] && sides[1][k];
    return out;
}

namespace {
size_t mix(size_t h, size_t v) { return h ^ (v + 0x9e3779b97f4a7c15ull + (h << 6) + (h >> 2)); }

std::vector<const Path*> key_of(const std::vector<PathP>& paths) {
    std::vector<const Path*> k;
    k.reserve(paths.size());
    for (const auto& p : paths) k.push_back(p.get());
    return k;
}
}  // namespace

size_t Samples::KeyHash::operator()(const std::vector<const Path*>& k) const {
    size_t h = k.size();
    for (const Path* p : k) h = mix(h, std::hash<const Path*>()(p));
    return h;
}

size_t Samples::AnswerHash::operator()(const AnswerKey& k) const {
    uint64_t b;
    std::memcpy(&b, &k.bound, sizeof b);
    return mix(mix(std::hash<const void*>()(k.tree), std::hash<const Path*>()(k.path)), std::hash<uint64_t>()(b));
}

const Samples1& Samples::of(const PathP& p) {
    auto it = cache_.find(p.get());
    if (it == cache_.end()) it = cache_.emplace(p.get(), std::make_pair(p, densify(*p, step))).first;
    return it->second.second;
}

const KDTree* Samples::tree(const std::vector<PathP>& paths) {
    auto key = key_of(paths);
    auto it = trees_.find(key);
    if (it == trees_.end()) {
        Kept<KDTree> kept{paths, nullptr};
        if (!paths.empty()) {
            std::vector<std::pair<double, double>> pts;
            for (const auto& p : paths) {
                const Samples1& s = of(p);
                for (size_t k = 0; k < s.x.size(); k++) pts.emplace_back(s.x[k], s.y[k]);
            }
            kept.value = std::make_unique<KDTree>(std::move(pts));
        }
        it = trees_.emplace(std::move(key), std::move(kept)).first;
    }
    return it->second.value.get();
}

const Samples::Ramp* Samples::ramp(const std::vector<PathP>& paths) {
    auto key = key_of(paths);
    auto it = ramps_.find(key);
    if (it == ramps_.end()) {
        std::vector<std::pair<double, double>> pts;
        double drop = 0.0;
        for (const auto& p : paths) {
            if (p->zs.empty()) continue;
            const Samples1& s = of(p);
            double low_min = INFINITY;
            for (size_t k = 0; k < s.x.size(); k++) {
                size_t g = size_t(s.seg[k]);
                double z = p->zs[g] + (p->zs[g + 1] - p->zs[g]) * s.t[k];
                if (z < p->z - 1e-6) {
                    pts.emplace_back(s.x[k], s.y[k]);
                    low_min = std::min(low_min, z);
                }
            }
            if (low_min != INFINITY) drop = std::max(drop, p->z - low_min);
        }
        Kept<Ramp> kept{paths, nullptr};
        if (!pts.empty()) kept.value = std::make_unique<Ramp>(Ramp{KDTree(std::move(pts)), drop});
        it = ramps_.emplace(std::move(key), std::move(kept)).first;
    }
    return it->second.value.get();
}

const std::vector<double>& Samples::nearest(const KDTree* tree, const PathP& p, double bound) {
    AnswerKey key{tree, p.get(), bound};
    auto it = answers_.find(key);
    if (it == answers_.end()) it = answers_.emplace(key, cb::nearest(tree, of(p), bound)).first;
    return it->second;
}

Grid::Grid(const Program& program, double res_) : res(res_) {
    double xmin = INFINITY, xmax = -INFINITY, ymin = INFINITY, ymax = -INFINITY;
    for (const auto& L : program.layers)
        for (const auto& p : L.paths)
            for (auto [x, y] : p->pts) {
                xmin = std::min(xmin, x);
                xmax = std::max(xmax, x);
                ymin = std::min(ymin, y);
                ymax = std::max(ymax, y);
            }
    const double m = 3.0;
    x0 = xmin - m;
    y0 = ymin - m;
    rows = int((ymax + m - y0) / res) + 1;
    cols = int((xmax + m - x0) / res) + 1;
}

long long Grid::row(double y) const { return (long long)std::nearbyint((y - y0) / res); }
long long Grid::col(double x) const { return (long long)std::nearbyint((x - x0) / res); }

Heights::Heights(const Program& program, double res) : grid(program, res) {
    h.assign(size_t(grid.rows) * size_t(grid.cols), 0.0);
    double w = -INFINITY;
    for (const auto& L : program.layers)
        for (const auto& p : L.paths) w = std::max(w, *p->width);
    pad = w / 2 + res;
}

void Heights::add(const Path& p) {
    Samples1 s = densify(p, grid.res / 2);
    for (size_t k = 0; k < s.x.size(); k++) {
        long long r = grid.row(s.y[k]), c = grid.col(s.x[k]);
        double& cell = h[size_t(r) * size_t(grid.cols) + size_t(c)];
        cell = std::max(cell, p.z);
    }
}

double Heights::max_near(double ax, double ay, double bx, double by, double radius) const {
    const Grid& g = grid;
    radius = radius + pad;
    // int() truncates toward zero, as Python's does
    int c0 = std::max(0, int(std::trunc((std::min(ax, bx) - radius - g.x0) / g.res)));
    int c1 = std::min(g.cols, int(std::trunc((std::max(ax, bx) + radius - g.x0) / g.res)) + 2);
    int r0 = std::max(0, int(std::trunc((std::min(ay, by) - radius - g.y0) / g.res)));
    int r1 = std::min(g.rows, int(std::trunc((std::max(ay, by) + radius - g.y0) / g.res)) + 2);
    if (c0 >= c1 || r0 >= r1) return 0.0;
    double dx = bx - ax, dy = by - ay;
    double n2 = dx * dx + dy * dy;
    bool found = false;
    double best = 0.0;
    for (int r = r0; r < r1; r++) {
        double py = g.y0 + double(r) * g.res;
        for (int c = c0; c < c1; c++) {
            double px = g.x0 + double(c) * g.res;
            double t = 0.0;
            if (n2 > 0) t = std::min(1.0, std::max(0.0, ((px - ax) * dx + (py - ay) * dy) / n2));
            if (std::hypot(px - ax - t * dx, py - ay - t * dy) <= radius) {      // numpy hypot = the C library's
                double v = h[size_t(r) * size_t(g.cols) + size_t(c)];
                if (!found || v > best) best = v;
                found = true;
            }
        }
    }
    return found ? best : 0.0;
}

}  // namespace cb
