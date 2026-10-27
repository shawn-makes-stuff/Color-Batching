"""Band planning: each tool prints several layers in a row without the nozzle hitting earlier material.

Within a band the units print one after another, each bottom-up; material of an earlier unit that a later nozzle
would hit is ceded to the later unit. Ceding a visible surface moves a colour edge: allowed up to
surface_tolerance, else the band is shortened.
"""
from dataclasses import dataclass
from itertools import permutations

import numpy as np

from .geometry import Samples, _nearest, absorb_short_runs, interface_mask, split


@dataclass
class Options:
    max_band_layers: int
    surface_tolerance: float
    resolution: float = 0.1
    min_piece: float = 2.0
    per_object: bool = True
    free_support: frozenset = frozenset()   # (object, type) of support the slicer gave no filament (Program)


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
            new_work, new_origin = [], []
            for (owner, p), first_owner in zip(work[j], origin[j]):
                if owner not in earlier:
                    new_work.append((owner, p))
                    new_origin.append(first_owner)
                    continue
                pts, seg, ts = samples.of(p)
                margin = np.full(len(pts), np.inf)
                for i in range(j + 1):
                    if i < j and trees_T[i] is not None:
                        # layer Z, not path Z: path Z carries Orca's Z offset
                        dz = nozzle.over(L.z - layers[i].z)
                        if dz > 1e-6:
                            d = _nearest(trees_T[i], pts, max_need + p.width)
                            margin = np.minimum(margin, d - nozzle.reach(dz) - margin_mm)
                    # T's scarf seam ramps: its nozzle runs up to `drop` below that layer there (on this layer too)
                    ramp = ramps_T[i] if i < j else own_ramp
                    if ramp is not None:
                        dz = nozzle.over(L.z - layers[i].z + ramp[1])
                        if dz > 1e-6:
                            d = _nearest(ramp[0], pts, max_need + ramp[1] + p.width)
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
            work[j], origin[j] = new_work, new_origin
            trees_T.append(samples.tree([p for o, p in work[j] if o == T]))
            trees_E.append(samples.tree([p for o, p in work[j] if o in earlier]))
            ramps_T.append(samples.ramp([p for o, p in work[j] if o == T]))
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
    return bands


def _progress_at(layers):
    """Fraction of the planning done once layers[:i] are planned, for i = 0..n: layers weighted by colours
    squared (a layer with one colour plans many times faster than one with four)."""
    w = np.cumsum([0] + [max(1, len({p.tool for p in L.paths if p.tool is not None})) ** 2 for L in layers])
    return w / w[-1]


def _max_layers(program, nozzle, opts):
    return max(1, min(opts.max_band_layers, int(nozzle.clear_height / max(L.height for L in program.layers) + 1e-9)))
