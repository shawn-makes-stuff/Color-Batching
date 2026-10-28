// Settings: defaults, colorstack.toml, and the label / unit / help the app shows for each (port of
// colorstack/config.py). A config is a JSON object of sections, each an object of settings, in file order.
#pragma once

#include <filesystem>
#include <string>
#include <vector>

#include "json.hpp"

namespace cb {

const Json& defaults();

struct Help {
    const char *key, *label, *unit, *text;
};
const std::vector<Help>& help();       // key "section.name"; the README's settings table says the same

// the folder holding viewer/ and colorstack.toml: the first parent of `start` that has viewer/index.html, else start
std::filesystem::path find_root(const std::filesystem::path& start);

Json load_config(const std::filesystem::path& path);
// merge {section: {key: value}} over cfg, every value converted to its default's type (throws on a bad value)
Json& apply(Json& cfg, const Json& values);
void save_config(const Json& cfg, const std::filesystem::path& path);

// typed reads of an applied config
double num(const Json& cfg, const char* section, const char* key);
bool flag(const Json& cfg, const char* section, const char* key);

}  // namespace cb
