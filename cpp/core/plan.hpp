// Band planning: each tool prints several layers in a row without the nozzle hitting earlier material (port of
// colorstack/plan.py).
//
// Within a band the units print one after another, each bottom-up; material of an earlier unit that a later nozzle
// would hit is ceded to the later unit. Ceding a visible surface moves a colour edge: allowed up to
// surface_tolerance, else the band is shortened.
#pragma once

#include <functional>
#include <map>
#include <optional>
#include <set>
#include <string>
#include <tuple>
#include <vector>

#include "geometry.hpp"
#include "model.hpp"
#include "writer.hpp"

namespace cb {

using Material = std::tuple<std::string, int, int>;     // base material, soluble, support material

struct Options {
    int max_band_layers = 10;
    double surface_tolerance = 0.0, resolution = 0.1, min_piece = 2.0;
    bool per_object = true;
    // (object, type) of support the slicer gave no filament (Program)
    std::set<std::pair<std::optional<std::string>, std::string>> free_support;
    std::vector<Material> materials;     // per tool: nothing is handed between two that differ
};

// per tool: (base material, soluble, support material) from the file's filament settings (pipeline.materials)
std::vector<Material> materials(const Program& prog);

extern const Unit DONE;      // owner of material already printed

Unit unit_of(const Path& p, bool per_object);

using Work = std::vector<std::vector<std::pair<Unit, PathP>>>;

// print `layers` as one band with the units in `order`; none if a visible edge would move too far. work: per layer
// (owner, path) when some of it is printed already (owner DONE); starts: per unit, the band layer it starts at.
std::optional<Band> plan_band(const std::vector<const Layer*>& layers, const std::vector<Unit>& order, Samples& samples,
                              const Nozzle& nozzle, const Options& opts, const Work* work = nullptr,
                              const std::map<Unit, int>* starts = nullptr);

// greedy: grow each band one layer at a time while a valid plan exists, never into a barrier layer
std::vector<Band> plan(const Program& program, const Nozzle& nozzle, const Options& opts,
                       const std::function<void(double)>& progress = nullptr);

// fraction of the planning done once layers[:i] are planned: layers weighted by colours squared
std::vector<double> progress_at(const std::vector<Layer>& layers);
int max_layers(const Program& program, const Nozzle& nozzle, const Options& opts);

}  // namespace cb
