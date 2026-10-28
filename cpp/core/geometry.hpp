// Nozzle model and path geometry: sampling, splitting, nearest-plastic queries and height maps (port of
// colorstack/geometry.py).
#pragma once

#include <unordered_map>
#include <utility>
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

// 2-D nearest-neighbour tree. Distances come out as scipy's cKDTree gives them: sqrt(dx*dx + dy*dy) of the nearest
// point closer than the bound (strictly, compared squared), inf when there is none.
class KDTree {
public:
    KDTree(std::vector<std::pair<double, double>> pts);
    double nearest(double qx, double qy, double bound) const;
    size_t size() const { return pts_.size(); }

private:
    struct Node {
        double lo[2], hi[2];
        int begin, end, left = -1, right = -1;
    };
    std::vector<std::pair<double, double>> pts_;
    std::vector<Node> nodes_;
    int build(int begin, int end);
    void search(int n, double qx, double qy, double& best) const;
};

// _nearest: per sample, the distance to tree's nearest point (inf beyond bound or without a tree)
std::vector<double> nearest(const KDTree* tree, const Samples1& s, double bound);

// split path p where the per-sample mask changes: [(flag, piece)]; a scarf ramp's Z per point is cut with it (a
// piece that ends up flat has none)
std::vector<std::pair<bool, PathP>> split(const Path& p, const Samples1& s, const std::vector<char>& mask);

// also hand over unflagged runs shorter than min_samples (no slivers needing their own travel)
std::vector<char> absorb_short_runs(const std::vector<char>& mask, double min_samples);

// samples of an outer wall that lie inside the part (where two colours meet) rather than on its surface: plastic
// within ~1 mm on both sides of the line
std::vector<char> interface_mask(const Path& p, const Samples1& s, const KDTree* layer_tree);

// centreline samples per path, cached; KD trees cached by the set of paths; nearest answers kept for one band
class Samples {
public:
    struct Ramp {        // where the paths' nozzle runs below their top (scarf seam ramps)
        KDTree tree;
        double drop;     // the deepest drop below the path's top
    };
    explicit Samples(double step) : step(step) {}
    const double step;
    const Samples1& of(const PathP& p);
    const KDTree* tree(const std::vector<PathP>& paths);    // none for no paths
    const Ramp* ramp(const std::vector<PathP>& paths);      // none where no path dips
    const std::vector<double>& nearest(const KDTree* tree, const PathP& p, double bound);
    void clear_answers() { answers_.clear(); }

private:
    struct KeyHash {
        size_t operator()(const std::vector<const Path*>& k) const;
    };
    struct AnswerKey {
        const void* tree;
        const Path* path;
        double bound;
        bool operator==(const AnswerKey& o) const { return tree == o.tree && path == o.path && bound == o.bound; }
    };
    struct AnswerHash {
        size_t operator()(const AnswerKey& k) const;
    };
    template <typename T>
    struct Kept {        // the entry keeps its paths alive so their addresses stay theirs
        std::vector<PathP> paths;
        std::unique_ptr<T> value;
    };
    std::unordered_map<const Path*, std::pair<PathP, Samples1>> cache_;
    std::unordered_map<std::vector<const Path*>, Kept<KDTree>, KeyHash> trees_;
    std::unordered_map<std::vector<const Path*>, Kept<Ramp>, KeyHash> ramps_;
    std::unordered_map<AnswerKey, std::vector<double>, AnswerHash> answers_;
};

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
