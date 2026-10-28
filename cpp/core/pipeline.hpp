// Entry point for the CLI and the app: settings in, processed file + summary out (port of colorstack/pipeline.py).
#pragma once

#include <functional>
#include <stdexcept>
#include <string>

#include "json.hpp"

namespace cb {

struct Cancelled : std::runtime_error {      // run() was asked to stop (the app's page was refreshed or closed)
    Cancelled() : std::runtime_error("cancelled") {}
};

using Progress = std::function<void(double fraction, const std::string& stage)>;

// process input_path into output_path; returns the summary. progress(fraction, stage) as it goes, the fraction
// weighted by what each stage costs on a 4-colour file. cancelled(): true to stop; checked at every progress step and
// every 0.3 s while the variants plan; throws Cancelled.
Json run(const std::string& input_path, const std::string& output_path, const Json& cfg,
         const Progress& progress = nullptr, const std::function<bool()>& cancelled = nullptr);

}  // namespace cb
