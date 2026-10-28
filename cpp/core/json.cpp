#include "json.hpp"

#include <cctype>
#include <charconv>
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <stdexcept>

namespace cb {

std::string py_repr(double v) {
    if (std::isnan(v)) return "nan";
    if (std::isinf(v)) return v > 0 ? "inf" : "-inf";
    char buf[64];
    auto r = std::to_chars(buf, buf + sizeof buf, v, std::chars_format::scientific);     // shortest: d.ddde+XX
    std::string t(buf, r.ptr);
    std::string sign;
    if (t[0] == '-') {
        sign = "-";
        t.erase(0, 1);
    }
    size_t e = t.find('e');
    int exp = std::atoi(t.c_str() + e + 1);
    std::string digits = t.substr(0, e);
    if (digits.size() > 1) digits.erase(1, 1);      // drop the point
    if (-4 <= exp && exp < 16) {
        if (exp >= 0) {
            if (int(digits.size()) <= exp + 1) return sign + digits + std::string(size_t(exp + 1) - digits.size(), '0') + ".0";
            return sign + digits.substr(0, size_t(exp + 1)) + "." + digits.substr(size_t(exp + 1));
        }
        return sign + "0." + std::string(size_t(-exp - 1), '0') + digits;
    }
    char ex[8];
    std::snprintf(ex, sizeof ex, "e%c%02d", exp < 0 ? '-' : '+', std::abs(exp));
    return sign + digits.substr(0, 1) + (digits.size() > 1 ? "." + digits.substr(1) : "") + ex;
}

const Json* Json::get(std::string_view key) const {
    if (type != Obj) return nullptr;
    for (const auto& [k, v] : o)
        if (k == key) return &v;
    return nullptr;
}

Json& Json::operator[](const std::string& key) {
    if (type == Null) type = Obj;
    for (auto& [k, v] : o)
        if (k == key) return v;
    o.emplace_back(key, Json());
    return o.back().second;
}

namespace {

void quote(std::string& out, const std::string& s) {
    out += '"';
    for (unsigned char c : s) {
        switch (c) {
            case '"': out += "\\\""; break;
            case '\\': out += "\\\\"; break;
            case '\n': out += "\\n"; break;
            case '\r': out += "\\r"; break;
            case '\t': out += "\\t"; break;
            default:
                if (c < 0x20) {
                    char u[8];
                    std::snprintf(u, sizeof u, "\\u%04x", c);
                    out += u;
                } else {
                    out += char(c);
                }
        }
    }
    out += '"';
}

void dump_to(std::string& out, const Json& j) {
    switch (j.type) {
        case Json::Null: out += "null"; break;
        case Json::Bool: out += j.b ? "true" : "false"; break;
        case Json::Int: out += std::to_string(j.i); break;
        case Json::Num:      // as json.dumps: repr, Infinity / NaN
            out += std::isnan(j.d) ? "NaN" : std::isinf(j.d) ? (j.d > 0 ? "Infinity" : "-Infinity") : py_repr(j.d);
            break;
        case Json::Str: quote(out, j.s); break;
        case Json::Arr:
            out += '[';
            for (size_t k = 0; k < j.a.size(); k++) {
                if (k) out += ", ";
                dump_to(out, j.a[k]);
            }
            out += ']';
            break;
        case Json::Obj:
            out += '{';
            for (size_t k = 0; k < j.o.size(); k++) {
                if (k) out += ", ";
                quote(out, j.o[k].first);
                out += ": ";
                dump_to(out, j.o[k].second);
            }
            out += '}';
            break;
    }
}

struct Parser {
    std::string_view t;
    size_t p = 0;
    [[noreturn]] void fail() { throw std::runtime_error("bad JSON at " + std::to_string(p)); }
    void ws() {
        while (p < t.size() && (t[p] == ' ' || t[p] == '\t' || t[p] == '\n' || t[p] == '\r')) p++;
    }
    bool word(std::string_view w) {
        if (t.substr(p, w.size()) != w) return false;
        p += w.size();
        return true;
    }
    void utf8(std::string& out, unsigned cp) {
        if (cp < 0x80) {
            out += char(cp);
        } else if (cp < 0x800) {
            out += char(0xC0 | (cp >> 6));
            out += char(0x80 | (cp & 0x3F));
        } else if (cp < 0x10000) {
            out += char(0xE0 | (cp >> 12));
            out += char(0x80 | ((cp >> 6) & 0x3F));
            out += char(0x80 | (cp & 0x3F));
        } else {
            out += char(0xF0 | (cp >> 18));
            out += char(0x80 | ((cp >> 12) & 0x3F));
            out += char(0x80 | ((cp >> 6) & 0x3F));
            out += char(0x80 | (cp & 0x3F));
        }
    }
    unsigned hex4() {
        if (p + 4 > t.size()) fail();
        unsigned v = 0;
        for (int k = 0; k < 4; k++) {
            char c = t[p++];
            v = v * 16 + unsigned(c >= '0' && c <= '9' ? c - '0' : c >= 'a' && c <= 'f' ? c - 'a' + 10
                                  : c >= 'A' && c <= 'F' ? c - 'A' + 10 : (fail(), 0));
        }
        return v;
    }
    std::string str() {
        if (t[p] != '"') fail();
        p++;
        std::string out;
        while (true) {
            if (p >= t.size()) fail();
            char c = t[p++];
            if (c == '"') return out;
            if (c != '\\') {
                out += c;
                continue;
            }
            if (p >= t.size()) fail();
            char e = t[p++];
            switch (e) {
                case '"': out += '"'; break;
                case '\\': out += '\\'; break;
                case '/': out += '/'; break;
                case 'b': out += '\b'; break;
                case 'f': out += '\f'; break;
                case 'n': out += '\n'; break;
                case 'r': out += '\r'; break;
                case 't': out += '\t'; break;
                case 'u': {
                    unsigned cp = hex4();
                    if (cp >= 0xD800 && cp < 0xDC00 && word("\\u")) cp = 0x10000 + ((cp - 0xD800) << 10) + (hex4() - 0xDC00);
                    utf8(out, cp);
                    break;
                }
                default: fail();
            }
        }
    }
    Json value() {
        ws();
        if (p >= t.size()) fail();
        char c = t[p];
        if (c == '{') {
            p++;
            Json j = Json::object();
            ws();
            if (p < t.size() && t[p] == '}') {
                p++;
                return j;
            }
            while (true) {
                ws();
                std::string k = str();
                ws();
                if (p >= t.size() || t[p++] != ':') fail();
                j[k] = value();
                ws();
                if (p >= t.size()) fail();
                if (t[p] == ',') {
                    p++;
                    continue;
                }
                if (t[p++] == '}') return j;
                fail();
            }
        }
        if (c == '[') {
            p++;
            Json j = Json::array();
            ws();
            if (p < t.size() && t[p] == ']') {
                p++;
                return j;
            }
            while (true) {
                j.a.push_back(value());
                ws();
                if (p >= t.size()) fail();
                if (t[p] == ',') {
                    p++;
                    continue;
                }
                if (t[p++] == ']') return j;
                fail();
            }
        }
        if (c == '"') return Json(str());
        if (word("true")) return Json(true);
        if (word("false")) return Json(false);
        if (word("null")) return Json();
        size_t q = p;
        bool real = false;
        while (q < t.size() && (std::isdigit((unsigned char)t[q]) || t[q] == '-' || t[q] == '+' || t[q] == '.' || t[q] == 'e' || t[q] == 'E')) {
            real = real || t[q] == '.' || t[q] == 'e' || t[q] == 'E';
            q++;
        }
        if (q == p) fail();
        std::string num(t.substr(p, q - p));
        p = q;
        if (real) {
            double v;
            auto r = std::from_chars(num.data(), num.data() + num.size(), v);
            if (r.ec != std::errc() || r.ptr != num.data() + num.size()) fail();
            return Json(v);
        }
        long long v;
        auto r = std::from_chars(num.data(), num.data() + num.size(), v);
        if (r.ec != std::errc() || r.ptr != num.data() + num.size()) fail();
        return Json(v);
    }
};

}  // namespace

std::string Json::dump() const {
    std::string out;
    dump_to(out, *this);
    return out;
}

Json Json::parse(std::string_view text) {
    Parser ps{text};
    Json j = ps.value();
    ps.ws();
    if (ps.p != text.size()) ps.fail();
    return j;
}

}  // namespace cb
