"""Nozzle model and path geometry: sampling, splitting, nearest-plastic queries and height maps.

Nozzle: a flat tip of tip_radius under a cone of cone_angle from vertical; material dz above the tip must be
tip_radius + dz*tan(cone_angle) away.
"""
import math
from dataclasses import dataclass, replace

import numpy as np
from scipy.spatial import cKDTree


@dataclass
class Nozzle:
    tip_radius: float
    cone_angle: float      # degrees from vertical
    clear_height: float
    contact: float = 0.0   # mm a neighbour may stand above the tip under the nozzle face

    def reach(self, dz):
        return self.tip_radius + dz * math.tan(math.radians(self.cone_angle))

    def over(self, dz):
        return dz - self.contact


class Grid:
    def __init__(self, program, res):
        xs, ys = [], []
        for layer in program.layers:
            for p in layer.paths:
                a = np.asarray(p.pts)
                xs += [a[:, 0].min(), a[:, 0].max()]
                ys += [a[:, 1].min(), a[:, 1].max()]
        m = 3.0
        self.x0, self.y0 = min(xs) - m, min(ys) - m
        self.res = res
        self.shape = (int((max(ys) + m - self.y0) / res) + 1, int((max(xs) + m - self.x0) / res) + 1)

    def cells(self, pts):
        a = np.asarray(pts)
        c = np.rint((a[:, 0] - self.x0) / self.res).astype(int)
        r = np.rint((a[:, 1] - self.y0) / self.res).astype(int)
        return r, c


def densify(p, step):
    """Samples along a path: (points Nx2, segment index, t within segment). n samples per segment at
    t = (k + 0.5) / n, all segments at once (the same arithmetic as one segment at a time)."""
    pts = p.pts
    counts = np.array([max(1, int(math.hypot(b[0] - a[0], b[1] - a[1]) / step)) for a, b in zip(pts, pts[1:])])
    seg = np.repeat(np.arange(len(counts)), counts)
    starts = np.cumsum(counts) - counts
    n = counts[seg]
    t = (np.arange(len(seg)) - starts[seg] + 0.5) / n
    a = np.asarray(pts, dtype=float)
    p0, d = a[:-1][seg], (a[1:] - a[:-1])[seg]
    return np.column_stack([p0[:, 0] + d[:, 0] * t, p0[:, 1] + d[:, 1] * t]), seg, t


def split(p, seg, ts, mask):
    """Split path p where the per-sample mask changes. Returns [(flag, Path)]."""
    out = []
    cur_flag, cur_pts, cur_e, cur_f = None, None, None, None

    def flush():
        if cur_pts is not None and len(cur_pts) > 1:
            out.append((cur_flag, replace(p, pts=cur_pts, e=cur_e, f=cur_f, head=p.head and not out,
                                          tail=False)))

    for i in range(len(p.e)):
        sel = seg == i
        flags, tt = mask[sel], ts[sel]
        # cut midway between samples whose flag differs
        cuts = [0.0]
        cf = [flags[0]]
        for k in range(1, len(flags)):
            if flags[k] != flags[k - 1]:
                cuts.append((tt[k] + tt[k - 1]) / 2)
                cf.append(flags[k])
        cuts.append(1.0)
        (x0, y0), (x1, y1) = p.pts[i], p.pts[i + 1]
        for k in range(len(cf)):
            ta, tb = cuts[k], cuts[k + 1]
            a = (x0 + (x1 - x0) * ta, y0 + (y1 - y0) * ta)
            b = (x0 + (x1 - x0) * tb, y0 + (y1 - y0) * tb)
            if cf[k] != cur_flag:
                flush()
                cur_flag, cur_pts, cur_e, cur_f = cf[k], [a], [], []
            cur_pts.append(b)
            cur_e.append(p.e[i] * (tb - ta))
            cur_f.append(p.f[i])
    flush()
    if out:
        out[-1] = (out[-1][0], replace(out[-1][1], tail=p.tail))
    return out


def absorb_short_runs(mask, min_samples):
    """Also hand over unflagged runs shorter than min_samples (no slivers needing their own travel). Handing
    over more is always collision-safe: the later tool prints it at the same layer."""
    out = mask.copy()
    n = len(mask)
    i = 0
    while i < n:
        if out[i]:
            i += 1
            continue
        j = i
        while j < n and not mask[j]:
            j += 1
        if j - i < min_samples:
            out[i:j] = True
        i = j
    return out


class Samples:
    """Centreline samples per path, cached (paths are never mutated, only replaced)."""

    def __init__(self, step):
        self.step = step
        self.cache = {}
        self.trees = {}

    def of(self, p):
        hit = self.cache.get(id(p))
        if hit is None or hit[0] is not p:
            hit = (p, densify(p, self.step))
            self.cache[id(p)] = hit
        return hit[1]

    def tree(self, paths):
        """KD tree of the paths' samples, cached by the set of paths (the same sets come back many times; the
        entry keeps the paths alive so their ids stay theirs)."""
        key = tuple(map(id, paths))
        hit = self.trees.get(key)
        if hit is None:
            pts = [self.of(p)[0] for p in paths]
            hit = (tuple(paths), cKDTree(np.vstack(pts)) if pts else None)
            self.trees[key] = hit
        return hit[1]


def _nearest(tree, pts, bound):
    if tree is None:
        return np.full(len(pts), np.inf)
    return tree.query(pts, distance_upper_bound=bound)[0]


def interface_mask(p, pts, seg, layer_tree):
    """Samples of an outer wall that lie inside the part (where two colours meet) rather than on its surface:
    plastic within ~1 mm on both sides of the line."""
    if layer_tree is None or not p.visible or p.type not in ("Outer wall", "Overhang wall"):
        return np.zeros(len(pts), bool)
    a = np.asarray(p.pts)
    d = a[seg + 1] - a[seg]
    n = np.hypot(d[:, 0], d[:, 1])
    n[n == 0] = 1
    nx, ny = -d[:, 1] / n, d[:, 0] / n
    sides = []
    for sgn in (1, -1):
        found = np.zeros(len(pts), bool)
        for off in (0.75, 1.25, 1.75, 2.25):          # line widths out, own line excluded
            probe = np.column_stack([pts[:, 0] + sgn * nx * off * p.width, pts[:, 1] + sgn * ny * off * p.width])
            dist, _ = layer_tree.query(probe, distance_upper_bound=p.width)
            found |= dist < 0.45 * p.width
        sides.append(found)
    return sides[0] & sides[1]


class Heights:
    """Tallest plastic printed so far, on a grid, for travels that lift only as high as they must."""

    def __init__(self, program, res=0.5):
        self.grid = Grid(program, res)
        self.h = np.zeros(self.grid.shape)
        self.pad = max(p.width for L in program.layers for p in L.paths) / 2 + res

    def add(self, p):
        r, c = self.grid.cells(densify(p, self.grid.res / 2)[0])
        np.maximum.at(self.h, (r, c), p.z)

    def max_near(self, a, b, radius):
        """Tallest plastic within radius of the segment a-b (line width and grid step added)."""
        g, radius = self.grid, radius + self.pad
        c0 = max(0, int((min(a[0], b[0]) - radius - g.x0) / g.res))
        c1 = min(g.shape[1], int((max(a[0], b[0]) + radius - g.x0) / g.res) + 2)
        r0 = max(0, int((min(a[1], b[1]) - radius - g.y0) / g.res))
        r1 = min(g.shape[0], int((max(a[1], b[1]) + radius - g.y0) / g.res) + 2)
        if c0 >= c1 or r0 >= r1:
            return 0.0
        px, py = np.meshgrid(g.x0 + np.arange(c0, c1) * g.res, g.y0 + np.arange(r0, r1) * g.res)
        dx, dy = b[0] - a[0], b[1] - a[1]
        n2 = dx * dx + dy * dy
        t = np.clip(((px - a[0]) * dx + (py - a[1]) * dy) / n2, 0, 1) if n2 > 0 else 0.0
        near = np.hypot(px - a[0] - t * dx, py - a[1] - t * dy) <= radius
        sub = self.h[r0:r1, c0:c1][near]
        return float(sub.max()) if sub.size else 0.0
