"""Band planning: each tool prints several layers in a row without the nozzle hitting earlier material.

Within a band the units print one after another, each bottom-up; material of an earlier unit that a later nozzle
would hit is ceded to the later unit. Ceding a visible surface moves a colour edge: allowed up to
surface_tolerance, else the band is shortened.
"""
import math
from dataclasses import dataclass
from itertools import permutations

import numpy as np

from .geometry import Samples, _nearest, absorb_short_runs, interface_mask, seg_dist, split


@dataclass
class Options:
    max_band_layers: int
    surface_tolerance: float
    resolution: float = 0.1
    min_piece: float = 2.0
    per_object: bool = True
    free_support: frozenset = frozenset()   # (object, type) of support the slicer gave no filament (Program)
    materials: tuple = ()      # per tool (base material, soluble, support material): nothing is handed between two


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


def unit_of(p, per_object):
    return (p.tool, p.obj if per_object else None)

DONE = ("done", None)     # owner of material already printed
UNDER_TOL = 0.02          # mm past Orca's line overlap before a line counts as under another (verify.check: 0.05)
UNDER_SQUISH = 1 - 0.25 * math.pi    # Orca's neighbouring lines overlap by height * this (Flow.cpp spacing)


def _material(opts, tool):
    return opts.materials[min(tool, len(opts.materials) - 1)] if opts.materials else None


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
        ramps_T = []        # per band layer, T's scarf seam ramps (Samples.ramp)
        for j, L in enumerate(layers):
            if j < start:   # T printed this layer already
                trees_T.append(None)
                trees_E.append(None)
                ramps_T.append(None)
                continue
            own = [p for o, p in work[j] if o == T]
            own_tree = samples.tree(own)
            own_ramp = samples.ramp(own)
            # T's lines on this layer that run below its top (scarf ramps), by the path they came from: T's own, then
            # the ones handed to it here too, so the layer is planned again until no more are
            ramps = [(s, q) for s, (o, q) in enumerate(work[j]) if o == T and q.zs]
            kept_totals = ceded, vis_ceded, {u: list(v) for u, v in ceded_by.items()}
            while True:
                ceded, vis_ceded = kept_totals[0], kept_totals[1]
                ceded_by = {u: list(v) for u, v in kept_totals[2].items()}
                new_work, new_origin, new_src = [], [], []
                for s, ((owner, p), first_owner) in enumerate(zip(work[j], origin[j])):
                    if owner not in earlier:
                        new_work.append((owner, p))
                        new_origin.append(first_owner)
                        new_src.append(s)
                        continue
                    pts, seg, ts = samples.of(p)
                    margin = np.full(len(pts), np.inf)
                    under = np.zeros(len(pts), bool)
                    for i in range(j + 1):
                        if i < j and trees_T[i] is not None:
                            # layer Z, not path Z: path Z carries Orca's Z offset
                            dz = nozzle.over(L.z - layers[i].z)
                            if dz > 1e-6:
                                d = samples.nearest(trees_T[i], p, max_need + p.width)
                                margin = np.minimum(margin, d - nozzle.reach(dz) - margin_mm)
                            if L.z - layers[i].z > 1e-6:
                                # over T's line on a lower layer by more than Orca's neighbouring lines overlap: T would
                                # print under it. Any height, no contact allowance.
                                under |= samples.gaps(trees_T[i], p) + layers[i].height * UNDER_SQUISH + UNDER_TOL < 0
                        # T's scarf seam ramps: its nozzle runs up to `drop` below that layer there (on this layer too)
                        ramp = ramps_T[i] if i < j else own_ramp
                        if ramp is not None:
                            dz = nozzle.over(L.z - layers[i].z + ramp[1])
                            if dz > 1e-6:
                                d = samples.nearest(ramp[0], p, max_need + ramp[1] + p.width)
                                margin = np.minimum(margin, d - nozzle.reach(dz) - margin_mm)
                    others = samples.tree([q for r, q in ramps if r != s])
                    if others is not None:
                        # T's ramp on this layer runs under the line (Orca's lines overlap walls, e.g. infill): same rule
                        under |= samples.gaps(others, p) + L.height * UNDER_SQUISH + UNDER_TOL < 0
                    mask = (margin < p.width / 2) | under
                    if owner == DONE:
                        if mask.any():
                            return None
                        new_work.append((owner, p))
                        new_origin.append(first_owner)
                        new_src.append(s)
                        continue
                    if j > 0:
                        # resting only on T's previous layer (printed later): hand it to T, or it prints in mid-air
                        on_T = samples.nearest(trees_T[j - 1], p, p.width) < p.width / 2
                        on_E = samples.nearest(trees_E[j - 1], p, p.width) < p.width / 2
                        mask |= on_T & ~on_E
                    if mask.any() and opts.min_piece > 0:
                        mask = absorb_short_runs(mask, opts.min_piece / samples.step)
                    if p.zs and mask.any():
                        mask = _over_ramp(p, mask, samples, L.height)
                    if not mask.any():
                        new_work.append((owner, p))
                        new_origin.append(first_owner)
                        new_src.append(s)
                        continue
                    if owner[0] != T[0] and _material(opts, owner[0]) != _material(opts, T[0]):
                        return None     # never hand plastic to a material it would not bond like (pipeline.materials)
                    if p.type.startswith("Support") and owner[0] != T[0] and (p.obj, p.type) not in opts.free_support:
                        return None     # support keeps the filament chosen for it (interface material is chosen on purpose)
                    vis =np.full(len(pts), p.visible)
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
                        new_src.append(s)
                got = [(s, q) for s, (o, q) in zip(new_src, new_work) if o == T and q.zs]
                if [(s, q.pts) for s, q in got] == [(s, q.pts) for s, q in ramps]:
                    break
                ramps = got
            work[j], origin[j] = new_work, new_origin
            trees_T.append(samples.tree([p for o, p in work[j] if o == T]))
            trees_E.append(samples.tree([p for o, p in work[j] if o in earlier]))
            ramps_T.append(samples.ramp([p for o, p in work[j] if o == T]))
    return Band(layers, list(order), work, ceded, vis_ceded, origin, ceded_by)


def _over_ramp(p, mask, samples, height):
    """A scarf loop's end passes over its start, which runs up from below the layer. Where a handed-over piece of that
    ramp lies under a kept piece of the same path by more than Orca's neighbouring lines overlap, the kept piece goes
    too, or it would print first with the ramp under it. Neighbours along the path (a width and a sample either way)
    are beside it, not over it."""
    pts, seg, ts = samples.of(p)
    xy, zs = np.asarray(p.pts, dtype=float), np.asarray(p.zs)
    z = zs[seg] + (zs[seg + 1] - zs[seg]) * ts
    lens = np.hypot(xy[1:, 0] - xy[:-1, 0], xy[1:, 1] - xy[:-1, 1])
    arc = (np.cumsum(lens) - lens)[seg] + lens[seg] * ts
    ramp = np.flatnonzero(mask & (z < p.z - 1e-6))
    kept = np.flatnonzero(~mask)
    if not len(ramp) or not len(kept):
        return mask
    L, R = samples.pieces(p)
    over = ((seg_dist(L[kept], R[kept], L[ramp], R[ramp]) - p.width + height * UNDER_SQUISH + UNDER_TOL < 0)
            & (z[kept][:, None] > z[ramp] + 1e-6)
            & (np.abs(arc[kept][:, None] - arc[ramp]) > p.width + 2 * samples.step))
    out = mask.copy()
    out[kept[over.any(axis=1)]] = True
    return out


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
    """Greedy: grow each band one layer at a time while a valid plan exists, never into a barrier layer (pause or
    filament change: a band starts there). progress(fraction) as it goes."""
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
        while k < max_k and i + k < len(layers) and not layers[i + k].barrier:
            b = _best_band(layers[i:i + k + 1], loaded, samples, nozzle, opts)
            if b is None:
                break
            best, k = b, k + 1
        bands.append(best)
        loaded = best.order[-1][0]
        i += len(best.layers)
        samples.clear_answers()
    return bands


def _progress_at(layers):
    """Fraction of the planning done once layers[:i] are planned, for i = 0..n: layers weighted by colours
    squared (a layer with one colour plans many times faster than one with four)."""
    w = np.cumsum([0] + [max(1, len({p.tool for p in L.paths if p.tool is not None})) ** 2 for L in layers])
    return w / w[-1]


def _max_layers(program, nozzle, opts):
    return max(1, min(opts.max_band_layers, int(nozzle.clear_height / max(L.height for L in program.layers) + 1e-9)))
