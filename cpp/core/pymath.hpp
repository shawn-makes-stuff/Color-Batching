// Python's own float helpers, ported so results match the reference implementation bit for bit.
// math.hypot / math.dist do not call the C library: CPython 3.11 computes them with vector_norm
// (Modules/mathmodule.c, v3.11.9), ported here as is.
#pragma once

#include <cmath>
#include <initializer_list>

namespace cb {

inline double vector_norm(const double* vec, int n, double max) {
    const double T27 = 134217729.0;     // ldexp(1.0, 27) + 1.0
    double x, scale, oldcsum, csum = 1.0, frac1 = 0.0, frac2 = 0.0, frac3 = 0.0;
    double t, hi, lo, h;
    int max_e;
    if (std::isinf(max)) return max;
    if (max == 0.0 || n <= 1) return max;
    std::frexp(max, &max_e);
    if (max_e >= -1023) {
        scale = std::ldexp(1.0, -max_e);
        for (int i = 0; i < n; i++) {
            x = vec[i];
            x *= scale;
            t = x * T27;
            hi = t - (t - x);
            lo = x - hi;
            x = hi * hi;
            oldcsum = csum;
            csum += x;
            frac1 += (oldcsum - csum) + x;
            x = 2.0 * hi * lo;
            oldcsum = csum;
            csum += x;
            frac2 += (oldcsum - csum) + x;
            frac3 += lo * lo;
        }
        h = std::sqrt(csum - 1.0 + (frac1 + frac2 + frac3));
        x = h;
        t = x * T27;
        hi = t - (t - x);
        lo = x - hi;
        x = -hi * hi;
        oldcsum = csum;
        csum += x;
        frac1 += (oldcsum - csum) + x;
        x = -2.0 * hi * lo;
        oldcsum = csum;
        csum += x;
        frac2 += (oldcsum - csum) + x;
        x = -lo * lo;
        oldcsum = csum;
        csum += x;
        frac3 += (oldcsum - csum) + x;
        x = csum - 1.0 + (frac1 + frac2 + frac3);
        return (h + x / (2.0 * h)) / scale;
    }
    // max_e < -1023: ldexp(1.0, -max_e) would overflow, divide by max instead
    for (int i = 0; i < n; i++) {
        x = vec[i];
        x /= max;
        x = x * x;
        oldcsum = csum;
        csum += x;
        frac1 += (oldcsum - csum) + x;
    }
    return max * std::sqrt(csum - 1.0 + frac1);
}

// math.hypot(*coordinates) (no NaN handling: G-code coordinates are finite)
inline double py_hypot(std::initializer_list<double> coords) {
    double vec[8];
    int n = 0;
    double max = 0.0;
    for (double c : coords) {
        double x = std::fabs(c);
        vec[n++] = x;
        if (x > max) max = x;
    }
    return vector_norm(vec, n, max);
}

// math.dist((ax, ay), (bx, by))
inline double py_dist(double ax, double ay, double bx, double by) {
    return py_hypot({ax - bx, ay - by});
}

}  // namespace cb
