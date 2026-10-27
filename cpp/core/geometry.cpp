#include "geometry.hpp"

#include <algorithm>
#include <cmath>

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
