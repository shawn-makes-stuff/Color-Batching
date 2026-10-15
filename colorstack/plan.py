"""Band planning: each tool prints several layers in a row without the nozzle hitting earlier material.

Nozzle: a flat tip of tip_radius under a cone of cone_angle from vertical; material dz above the tip must be
tip_radius + dz*tan(cone_angle) away. Within a band the units print one after another, each bottom-up; material
of an earlier unit that a later nozzle would hit is ceded to the later unit. Ceding a visible surface moves a
colour edge: allowed up to surface_tolerance, else the band is shortened.
"""
import math
from dataclasses import dataclass, replace
from itertools import permutations

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


@dataclass
class Options:
    max_band_layers: int
    surface_tolerance: float
    resolution: float = 0.1
    min_piece: float = 2.0
    per_object: bool = True


@dataclass
class Band:
    layers: list
    order: list             # units (tool, object), each prints all its band layers before the next
    work: list              # per layer: [(owner unit, Path)]
    ceded_mm: float
    visible_ceded_mm: float

    @property
    def tools(self):
        seq = []
        for tool, _ in self.order:
            if not seq or seq[-1] != tool:
                seq.append(tool)
        return seq


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


def sample_z(p, seg, ts):
    if p.zs is None:
        return np.full(len(seg), p.z)
    z = np.asarray(p.zs)
    return z[seg] + (z[seg + 1] - z[seg]) * ts


def densify(p, step):
    """Samples along a path: (points Nx2, segment index, t within segment)."""
    pts, seg, ts = [], [], []
    for i in range(len(p.e)):
        (x0, y0), (x1, y1) = p.pts[i], p.pts[i + 1]
        n = max(1, int(math.hypot(x1 - x0, y1 - y0) / step))
        t = (np.arange(n) + 0.5) / n
        pts.append(np.column_stack([x0 + (x1 - x0) * t, y0 + (y1 - y0) * t]))
        seg.append(np.full(n, i))
        ts.append(t)
    return np.vstack(pts), np.concatenate(seg), np.concatenate(ts)


def split(p, seg, ts, mask):
    """Split path p where the per-sample mask changes. Returns [(flag, Path)]."""
    out = []
    cur_flag, cur_pts, cur_e, cur_f, cur_z = None, None, None, None, None

    def flush():
        if cur_pts is not None and len(cur_pts) > 1:
            out.append((cur_flag, replace(p, pts=cur_pts, e=cur_e, f=cur_f, head=p.head and not out,
                                          tail=False, zs=cur_z if p.zs is not None else None)))

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
        z0, z1 = p.z_at(i), p.z_at(i + 1)
        for k in range(len(cf)):
            ta, tb = cuts[k], cuts[k + 1]
            a = (x0 + (x1 - x0) * ta, y0 + (y1 - y0) * ta)
            b = (x0 + (x1 - x0) * tb, y0 + (y1 - y0) * tb)
            if cf[k] != cur_flag:
                flush()
                cur_flag, cur_pts, cur_e, cur_f, cur_z = cf[k], [a], [], [], [z0 + (z1 - z0) * ta]
            cur_pts.append(b)
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

    def of(self, p):
        hit = self.cache.get(id(p))
        if hit is None or hit[0] is not p:
            hit = (p, densify(p, self.step))
            self.cache[id(p)] = hit
        return hit[1]

    def tree(self, paths):
        pts = [self.of(p)[0] for p in paths]
        return cKDTree(np.vstack(pts)) if pts else None


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


def unit_of(p, per_object):
    return (p.tool, p.obj if per_object else None)


def plan_band(layers, order, samples, nozzle, opts):
    """Print `layers` as one band with the units in `order`. None if a visible edge would move too far."""
    first = order[0]
    work = [[(first if p.tool is None else unit_of(p, opts.per_object), p) for p in L.paths] for L in layers]
    ceded = vis_ceded = 0.0
    margin_mm = samples.step          # samples lie up to step/2 off the true line
    max_need = nozzle.reach(sum(L.height for L in layers) + 1.0) + margin_mm + 1.0
    layer_trees = [samples.tree([q for q in L.paths if q.tool is not None]) for L in layers]
    for k in range(1, len(order)):
        T = order[k]
        earlier = set(order[:k])
        trees_T = []        # per band layer, after ceding
        trees_E = []
        for j, L in enumerate(layers):
            own_tree = samples.tree([p for o, p in work[j] if o == T])
            new_work = []
            for owner, p in work[j]:
                if owner not in earlier:
                    new_work.append((owner, p))
                    continue
                pts, seg, ts = samples.of(p)
                zs = sample_z(p, seg, ts)
                margin = np.full(len(pts), np.inf)
                # raised (non-planar) material can stand above T's nozzle on the same layer too
                for i in range(j + 1):
                    tree = trees_T[i] if i < j else own_tree
                    if tree is None:
                        continue
                    # path Z carries Orca's Z offset, layer Z does not
                    dz = nozzle.over((zs - p.z) + (L.z - layers[i].z))
                    if (dz <= 1e-6).all():
                        continue
                    d = _nearest(tree, pts, max_need + p.width)
                    need = np.where(dz > 1e-6, nozzle.reach(dz) + margin_mm, -np.inf)
                    margin = np.minimum(margin, d - need)
                mask = margin < p.width / 2
                if j > 0:
                    # resting only on T's previous layer (printed later): hand it to T, or it prints in mid-air
                    on_T = _nearest(trees_T[j - 1], pts, p.width) < p.width / 2
                    on_E = _nearest(trees_E[j - 1], pts, p.width) < p.width / 2
                    mask |= on_T & ~on_E
                if mask.any() and opts.min_piece > 0:
                    mask = absorb_short_runs(mask, opts.min_piece / samples.step)
                if not mask.any():
                    new_work.append((owner, p))
                    continue
                vis = np.full(len(pts), p.visible)
                if p.visible:
                    vis &= ~interface_mask(p, pts, seg, layer_trees[j])
                if (vis & mask).any():
                    d_own = _nearest(own_tree, pts[vis & mask], opts.surface_tolerance + p.width + 1.0)
                    if ((d_own - p.width / 2) > opts.surface_tolerance).any():
                        return None
                vis_ceded += float((vis & mask).sum()) * samples.step
                for flag, piece in split(p, seg, ts, mask):
                    if flag:
                        ceded += piece.length()
                    new_work.append((T if flag else owner, piece))
            work[j] = new_work
            trees_T.append(samples.tree([p for o, p in work[j] if o == T]))
            trees_E.append(samples.tree([p for o, p in work[j] if o in earlier]))
    return Band(layers, list(order), work, ceded, vis_ceded)


def _units_by_tool(group, per_object):
    """Each tool's units, objects in the order the slicer first prints them."""
    first_seen = {}
    for L in group:
        for p in L.paths:
            if p.tool is not None:
                first_seen.setdefault(unit_of(p, per_object), p.seq)
    out = {}
    for u in sorted(first_seen, key=first_seen.get):
        out.setdefault(u[0], []).append(u)
    return out


def _best_band(group, loaded, samples, nozzle, opts):
    units = _units_by_tool(group, opts.per_object)
    tools = set(units)
    if not tools:
        return plan_band(group, [(loaded, None)], samples, nozzle, opts)
    starts = [loaded] if loaded in tools else sorted(tools)
    best, best_key = None, None
    for s in starts:
        rest = [t for t in sorted(tools) if t != s]
        for perm in permutations(rest):
            order = [u for t in (s, *perm) for u in units[t]]
            b = plan_band(group, order, samples, nozzle, opts)
            if b is None:
                continue
            # purge-volume order was tried here: no gain on 2 colours, +2 swaps on 4-colour Stitch
            key = (b.visible_ceded_mm, b.ceded_mm)
            if best is None or key < best_key:
                best, best_key = b, key
    return best


def plan(program, nozzle, opts):
    """Greedy: grow each band one layer at a time while a valid plan exists."""
    samples = Samples(opts.resolution * 2)
    layers = program.layers
    bands, loaded, i = [], program.first_tool, 0
    max_k = max(1, min(opts.max_band_layers, int(nozzle.clear_height / max(L.height for L in layers) + 1e-9)))
    while i < len(layers):
        best = _best_band(layers[i:i + 1], loaded, samples, nozzle, opts)
        if best is None:
            raise RuntimeError(f"no valid plan for layer {layers[i].index}")
        k = 1
        while k < max_k and i + k < len(layers):
            b = _best_band(layers[i:i + k + 1], loaded, samples, nozzle, opts)
            if b is None:
                break
            best, k = b, k + 1
        bands.append(best)
        loaded = best.order[-1][0]
        i += len(best.layers)
    return bands
