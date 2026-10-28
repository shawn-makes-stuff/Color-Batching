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
    """Split path p where the per-sample mask changes. Returns [(flag, Path)]; a scarf ramp's Z per point is cut
    with it (a piece that ends up flat has none)."""
    out = []
    cur_flag, cur_pts, cur_e, cur_f, cur_z = None, None, None, None, None

    def flush():
        if cur_pts is not None and len(cur_pts) > 1:
            z = {}
            if cur_z is not None:
                z = ({"zs": tuple(cur_z), "z": max(cur_z)} if max(cur_z) - min(cur_z) > 1e-6
                     else {"zs": None, "z": cur_z[0]})
            out.append((cur_flag, replace(p, pts=cur_pts, e=cur_e, f=cur_f, head=p.head and not out,
                                          tail=False, **z)))

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
        z0, z1 = (p.zs[i], p.zs[i + 1]) if p.zs else (None, None)
        for k in range(len(cf)):
            ta, tb = cuts[k], cuts[k + 1]
            a = (x0 + (x1 - x0) * ta, y0 + (y1 - y0) * ta)
            b = (x0 + (x1 - x0) * tb, y0 + (y1 - y0) * tb)
            if cf[k] != cur_flag:
                flush()
                cur_flag, cur_pts, cur_e, cur_f = cf[k], [a], [], []
                cur_z = [z0 + (z1 - z0) * ta] if p.zs else None
            cur_pts.append(b)
            if p.zs:
                cur_z.append(z0 + (z1 - z0) * tb)
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
        self.ramps = {}
        self.answers = {}      # (tree, samples, bound) -> distances, for one band (clear_answers)
        self.segs = {}         # id(tree) -> per sample: its segment's start, end (N x 2 each), width, segment id

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
            if pts:
                a, b, w, g = [], [], [], []
                for k, p in enumerate(paths):
                    _, seg, _ = self.of(p)
                    xy = np.asarray(p.pts, dtype=float)
                    a.append(xy[seg])
                    b.append(xy[seg + 1])
                    w.append(np.full(len(seg), p.width))
                    g.append(seg + sum(len(q.pts) for q in paths[:k]))      # unique per (path, segment)
                self.segs[id(hit[1])] = (np.vstack(a), np.vstack(b), np.concatenate(w), np.concatenate(g))
        return hit[1]


    def ramp(self, paths):
        """Where the paths' nozzle runs below their top (scarf seam ramps): (KD tree of those samples, the deepest
        drop below the path's top), or None. Cached like tree. (Grouping the samples by depth gave no fewer swaps.)"""
        key = tuple(map(id, paths))
        hit = self.ramps.get(key)
        if hit is None:
            pts, drop = [], 0.0
            for p in paths:
                if not p.zs:
                    continue
                xy, seg, t = self.of(p)
                zs = np.asarray(p.zs)
                z = zs[seg] + (zs[seg + 1] - zs[seg]) * t
                low = z < p.z - 1e-6
                if low.any():
                    pts.append(xy[low])
                    drop = max(drop, float(p.z - z[low].min()))
            hit = (tuple(paths), (cKDTree(np.vstack(pts)), drop) if pts else None)
            self.ramps[key] = hit
        return hit[1]


    def nearest(self, tree, p, bound):
        """_nearest for path p's samples, the answer kept until clear_answers: a band is planned again for every
        colour order and every layer it grows by, asking the same questions (61% of the work on Stitch)."""
        key = (id(tree), id(p), bound)
        hit = self.answers.get(key)
        if hit is None:
            hit = _nearest(tree, self.of(p)[0], bound)
            self.answers[key] = hit
        return hit

    def pieces(self, p):
        """Per sample of path p, the ends of the piece of p it stands for: between the cuts split() makes (halfway to
        the next sample on its segment, else the segment's end) and the path's ends. (L, R), N x 2 each."""
        pts, seg, ts = self.of(p)
        xy = np.asarray(p.pts, dtype=float)
        a, b = xy[seg[:-1]], xy[seg[:-1] + 1]
        t = ((ts[:-1] + ts[1:]) / 2)[:, None]
        cut = np.where((seg[:-1] == seg[1:])[:, None], a + (b - a) * t, xy[seg[1:]])
        return np.vstack([xy[:1], cut]), np.vstack([cut, xy[-1:]])

    def gaps(self, tree, p):
        """Per sample of path p: the least distance from the piece of p it stands for (between the cuts split() makes,
        halfway to the next sample on its segment or at the segment's end, and the path's ends) to a line of the
        tree's paths, less both half widths; exact on both lines' segments, inf with none near. Kept until
        clear_answers. A point of a piece is under one step from its sample, and a point on a tree segment under one
        step from that segment's nearest sample, so every segment within reach has a sample in the query radius."""
        key = (id(tree), id(p), "gaps")
        hit = self.answers.get(key)
        if hit is None:
            pts = self.of(p)[0]
            L, R = self.pieces(p)
            A, B, W, G = self.segs[id(tree)]
            near = tree.query_ball_point(pts, (p.width + W.max()) / 2 + 2 * self.step)
            idx = np.fromiter((i for c in near for i in c), int)
            if not len(idx):
                hit = np.full(len(pts), np.inf)
            else:
                _, first = np.unique(G[idx], return_index=True)      # one sample per segment
                c = idx[first]
                hit = (seg_dist(L, R, A[c], B[c]) - (p.width + W[c]) / 2).min(axis=1)
            self.answers[key] = hit
        return hit

    def clear_answers(self):
        self.answers.clear()


def _to_seg(P, a, b):
    """Distance from each point P (N x 2) to each segment a-b (M x 2 each): N x M."""
    d = b - a
    n2 = d[:, 0] * d[:, 0] + d[:, 1] * d[:, 1]
    px, py = P[:, 0:1], P[:, 1:2]
    t = ((px - a[:, 0]) * d[:, 0] + (py - a[:, 1]) * d[:, 1]) / np.where(n2 > 0, n2, 1.0)
    t = np.where(n2 > 0, np.clip(t, 0.0, 1.0), 0.0)
    return np.hypot(px - (a[:, 0] + t * d[:, 0]), py - (a[:, 1] + t * d[:, 1]))


def seg_dist(L, R, a, b):
    """Least distance between each segment L-R (N x 2 each) and each segment a-b (M x 2 each): N x M."""
    dist = np.minimum(np.minimum(_to_seg(L, a, b), _to_seg(R, a, b)),
                      np.minimum(_to_seg(a, L, R).T, _to_seg(b, L, R).T))
    return np.where(_crosses(L, R, a, b), 0.0, dist)


def _crosses(L, R, a, b):
    """Whether each segment L-R (N x 2 each) properly crosses each segment a-b (M x 2 each): N x M."""
    ex, ey = (R - L)[:, 0:1], (R - L)[:, 1:2]
    dx, dy = b[:, 0] - a[:, 0], b[:, 1] - a[:, 1]
    lx, ly, rx, ry = L[:, 0:1], L[:, 1:2], R[:, 0:1], R[:, 1:2]
    o1 = ex * (a[:, 1] - ly) - ey * (a[:, 0] - lx)
    o2 = ex * (b[:, 1] - ly) - ey * (b[:, 0] - lx)
    o3 = dx * (ly - a[:, 1]) - dy * (lx - a[:, 0])
    o4 = dx * (ry - a[:, 1]) - dy * (rx - a[:, 0])
    return (o1 * o2 < 0) & (o3 * o4 < 0)


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
