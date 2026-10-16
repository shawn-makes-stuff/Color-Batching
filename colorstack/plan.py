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

from .tower import RunTower


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
    origin: list | None = None      # per layer, per piece: the owner it started with
    ceded_by: dict | None = None    # first owner -> [ceded mm, visible ceded mm]

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


def unit_of(p, per_object):
    return (p.tool, p.obj if per_object else None)


DONE = ("done", None)     # owner of material already printed


def plan_band(layers, order, samples, nozzle, opts, work=None, starts=None):
    """Print `layers` as one band with the units in `order`. None if a visible edge would move too far.
    work: per layer [(owner, path)] when some of it is printed already (owner DONE: never handed over, every
    later nozzle must clear it); starts: per unit, the band layer it starts at (the ones below are printed).
    The band's `origin` gives, per piece, the owner it started with."""
    first = order[0]
    if work is None:
        work = [[(first if p.tool is None else unit_of(p, opts.per_object), p) for p in L.paths] for L in layers]
    work = list(work)
    origin = [[o for o, _ in w] for w in work]
    starts = starts or {}
    ceded = vis_ceded = 0.0
    ceded_by = {}
    margin_mm = samples.step          # samples lie up to step/2 off the true line
    max_need = nozzle.reach(sum(L.height for L in layers) + 1.0) + margin_mm + 1.0
    layer_trees = [samples.tree([q for q in L.paths if q.tool is not None]) for L in layers]
    has_done = any(o == DONE for w in work for o, _ in w)
    for k in range(0 if has_done else 1, len(order)):
        T = order[k]
        earlier = set(order[:k]) | {DONE}
        start = starts.get(T, 0)
        trees_T = []        # per band layer, after ceding
        trees_E = []
        for j, L in enumerate(layers):
            if j < start:   # T printed this layer already
                trees_T.append(None)
                trees_E.append(None)
                continue
            own_tree = samples.tree([p for o, p in work[j] if o == T])
            new_work, new_origin = [], []
            for (owner, p), first_owner in zip(work[j], origin[j]):
                if owner not in earlier:
                    new_work.append((owner, p))
                    new_origin.append(first_owner)
                    continue
                pts, seg, ts = samples.of(p)
                margin = np.full(len(pts), np.inf)
                for i in range(j):
                    if trees_T[i] is None:
                        continue
                    # layer Z, not path Z: path Z carries Orca's Z offset
                    dz = nozzle.over(L.z - layers[i].z)
                    if dz <= 1e-6:
                        continue
                    d = _nearest(trees_T[i], pts, max_need + p.width)
                    margin = np.minimum(margin, d - nozzle.reach(dz) - margin_mm)
                mask = margin < p.width / 2
                if owner == DONE:
                    if mask.any():
                        return None
                    new_work.append((owner, p))
                    new_origin.append(first_owner)
                    continue
                if j > 0:
                    # resting only on T's previous layer (printed later): hand it to T, or it prints in mid-air
                    on_T = _nearest(trees_T[j - 1], pts, p.width) < p.width / 2
                    on_E = _nearest(trees_E[j - 1], pts, p.width) < p.width / 2
                    mask |= on_T & ~on_E
                if mask.any() and opts.min_piece > 0:
                    mask = absorb_short_runs(mask, opts.min_piece / samples.step)
                if not mask.any():
                    new_work.append((owner, p))
                    new_origin.append(first_owner)
                    continue
                vis = np.full(len(pts), p.visible)
                if p.visible:
                    vis &= ~interface_mask(p, pts, seg, layer_trees[j])
                if (vis & mask).any():
                    d_own = _nearest(own_tree, pts[vis & mask], opts.surface_tolerance + p.width + 1.0)
                    if ((d_own - p.width / 2) > opts.surface_tolerance).any():
                        return None
                v = float((vis & mask).sum()) * samples.step
                vis_ceded += v
                by = ceded_by.setdefault(first_owner, [0.0, 0.0])
                by[1] += v
                for flag, piece in split(p, seg, ts, mask):
                    if flag:
                        ceded += piece.length()
                        by[0] += piece.length()
                    new_work.append((T if flag else owner, piece))
                    new_origin.append(first_owner)
            work[j], origin[j] = new_work, new_origin
            trees_T.append(samples.tree([p for o, p in work[j] if o == T]))
            trees_E.append(samples.tree([p for o, p in work[j] if o in earlier]))
    return Band(layers, list(order), work, ceded, vis_ceded, origin, ceded_by)


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
            # ordering by purge volume was tried: no gain with 2 colours, more swaps with 4
            key = (b.visible_ceded_mm, b.ceded_mm)
            if best is None or key < best_key:
                best, best_key = b, key
    return best


def plan(program, nozzle, opts, progress=None):
    """Greedy: grow each band one layer at a time while a valid plan exists. progress(fraction) as it goes."""
    samples = Samples(opts.resolution * 2)
    layers = program.layers
    bands, loaded, i = [], program.first_tool, 0
    max_k = _max_layers(program, nozzle, opts)
    done = _progress_at(layers) if progress else None
    while i < len(layers):
        if progress:
            progress(done[i])
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


def _progress_at(layers):
    """Fraction of the planning done once layers[:i] are planned, for i = 0..n: layers weighted by colours
    squared (a layer with one colour plans many times faster than one with four)."""
    w = np.cumsum([0] + [max(1, len({p.tool for p in L.paths if p.tool is not None})) ** 2 for L in layers])
    return w / w[-1]


def _max_layers(program, nozzle, opts):
    return max(1, min(opts.max_band_layers, int(nozzle.clear_height / max(L.height for L in program.layers) + 1e-9)))


def plan_staggered(program, nozzle, opts, progress=None):
    """Each unit on its own schedule. A run prints one unit's next layers as far as the units below could still
    finish the window after it as a band. Next run: the most layers for no swap, else for one swap; ties go to a
    unit not touching the one just printed. Returns (one single-unit band per run, (wipes kept, wipes to grid))."""
    samples = Samples(opts.resolution * 2)
    layers = program.layers
    n = len(layers)
    max_k = _max_layers(program, nozzle, opts)
    own = [[(unit_of(p, opts.per_object), p) for p in L.paths if p.tool is not None] for L in layers]
    units = list(dict.fromkeys(o for w in own for o, _ in w))
    front = dict.fromkeys(units, 0)
    reach = nozzle.reach(nozzle.clear_height) + 1.0
    trees = {}

    def eff(u):
        """First layer with material u still has to print."""
        j = front[u]
        while j < n and all(o != u for o, _ in own[j]):
            j += 1
        front[u] = j
        return j

    def tree(j, u):
        if (j, u) not in trees:
            trees[(j, u)] = samples.tree([p for p in layers[j].paths if p.tool is not None
                                          and unit_of(p, opts.per_object) == u])
        return trees[(j, u)]

    def touching(u, v, lo):
        for j in range(lo, min(n, lo + max_k)):
            a, b = tree(j, u), tree(j, v)
            if a is not None and b is not None and (b.query(a.data, distance_upper_bound=reach)[0] < reach).any():
                return True
        return False

    def run(u, lo, b, play, effs):
        """u printing its layers up to b, the rest of `play` finishing the window after it in some order."""
        groups = {}
        for v in sorted((v for v in play if v != u), key=lambda v: (effs[v], v[0])):
            groups.setdefault(v[0], []).append(v)
        starts = {v: effs[v] - lo for v in play}
        for perm in permutations(groups):
            band = plan_band(layers[lo:b], [u] + [v for t in perm for v in groups[t]], samples, nozzle, opts,
                             own[lo:b], starts)
            if band is not None:
                return band
        return None

    tower = RunTower(program)
    done = _progress_at(layers) if progress else None
    loaded, last, top = program.first_tool, None, 0     # top: layers below are printed by somebody
    bands = []
    while True:
        effs = {u: eff(u) for u in units}
        active = {u: e for u, e in effs.items() if e < n}
        if not active:
            break
        if progress:
            progress(done[min(active.values())])
        cands = []
        for u, e in active.items():
            best = None
            b = max(top, e + 1)
            while b <= n:
                play = [v for v, ev in active.items() if ev < b]
                lo = min(active[v] for v in play)
                if b - lo > max_k:
                    break
                band = run(u, lo, b, play, effs)
                if band is None:
                    break
                best = (b, lo, band)
                b += 1
            if best is not None:
                cands.append((u, e) + best)
        if not cands:
            raise RuntimeError(f"no valid run at layer {layers[min(active.values())].index}")
        lo_all = min(active.values())
        u, e, b, lo, band = max(cands, key=lambda c: (c[0][0] == loaded, c[2] - c[1],
                                                      last is None or not touching(c[0], last, lo_all)))
        # commit u's pieces: printed now (DONE) or handed to the unit that prints them later
        mine = [[] for _ in range(b - lo)]
        for j in range(b - lo):
            keep = [(o, p) for o, p in own[lo + j] if o != u]
            for (o, p), first_owner in zip(band.work[j], band.origin[j]):
                if first_owner == u:
                    keep.append((DONE if o == u else o, p))
                    if o == u:
                        mine[j].append(p)
            own[lo + j] = keep
        top = max(top, b)
        tower_paths = (tower.wipe() if u[0] != loaded else []) + tower.complete(min(eff(v) for v in units))
        start = min([e] + [j for j, _ in tower_paths])     # u has nothing to print below e
        end = max([b] + [j + 1 for j, _ in tower_paths])    # above b: layers with nothing but tower
        work = [[] for _ in range(end - start)]
        for j, paths in tower_paths:
            work[j - start] += [(u, p) for p in paths]
        for j in range(e, b):
            work[j - start] += [(u, p) for p in mine[j - lo]]
        ceded, vis = band.ceded_by.get(u, (0.0, 0.0))
        bands.append(Band(layers[start:end], [u], work, ceded, vis))
        loaded, last = u[0], u
    return bands, (tower.kept, tower.replaced)
