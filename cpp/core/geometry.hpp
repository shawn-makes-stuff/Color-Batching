// Nozzle model and path geometry (port of colorstack/geometry.py; the planner's parts follow in step 5).
#pragma once

#include <vector>

#include "model.hpp"

namespace cb {

struct Nozzle {
    double tip_radius, cone_angle, clear_height, contact = 0.0;
    // material dz above the tip must be this far away
    double reach(double dz) const;
    double over(double dz) const { return dz - contact; }
};

struct Samples1 {                 // densify's result
    std::vector<double> x, y;     // sample points
    std::vector<int> seg;         // segment index of each
    std::vector<double> t;        // position within the segment
};
// samples along a path: n per segment at t = (k + 0.5) / n, n = max(1, int(length / step))
Samples1 densify(const Path& p, double step);

struct Grid {
    double x0, y0, res;
    int rows, cols;
    Grid(const Program& program, double res);
    long long row(double y) const;    // np.rint((y - y0) / res)
    long long col(double x) const;
};

// tallest plastic printed so far, on a grid, for travels that lift only as high as they must
struct Heights {
    Grid grid;
    std::vector<double> h;
    double pad;
    explicit Heights(const Program& program, double res = 0.5);
    void add(const Path& p);
    // tallest plastic within radius of the segment a-b (line width and grid step added)
    double max_near(double ax, double ay, double bx, double by, double radius) const;
};

}  // namespace cb
