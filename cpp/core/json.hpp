// A small JSON value for the app's API and the settings (objects keep insertion order, as Python dicts do).
#pragma once

#include <string>
#include <string_view>
#include <utility>
#include <vector>

namespace cb {

struct Json {
    enum Type { Null, Bool, Int, Num, Str, Arr, Obj } type = Null;
    bool b = false;
    long long i = 0;
    double d = 0;
    std::string s;
    std::vector<Json> a;
    std::vector<std::pair<std::string, Json>> o;

    Json() = default;
    Json(bool v) : type(Bool), b(v) {}
    Json(int v) : type(Int), i(v) {}
    Json(long long v) : type(Int), i(v) {}
    Json(double v) : type(Num), d(v) {}
    Json(const char* v) : type(Str), s(v) {}
    Json(std::string v) : type(Str), s(std::move(v)) {}
    static Json array() { Json j; j.type = Arr; return j; }
    static Json object() { Json j; j.type = Obj; return j; }

    const Json* get(std::string_view key) const;       // none when missing or not an object
    Json& operator[](const std::string& key);           // inserts (as null) when missing
    std::string dump() const;
    static Json parse(std::string_view text);           // throws std::runtime_error
};

std::string py_repr(double v);     // Python repr(float): shortest round trip, '1.0', '1e-05', '1e+16'

}  // namespace cb
