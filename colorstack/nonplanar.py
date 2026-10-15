"""Non-planar top skins (after Ahlers' non-planar Slic3r, Hamburg 2018).

The stair-stepped top of a curved or sloped surface is rebuilt as a smooth surface S: on each tread it rises
from the tread's outer edge (z) to the next layer's edge (z + h). Treads only count when they really form a
staircase, and wider than max_tread they are real flats. Where S is shallow enough for the nozzle (max_angle and
the nozzle's own collision angle), spans at most clear_height and covers min_area, the stepped top lines are
replaced by one skin of zigzag lines following S, printed after the region's highest layer.
"""
import math
from dataclasses import dataclass, replace

import numpy as np
from scipy.ndimage import binary_fill_holes, distance_transform_edt, grey_dilation, label

from .plan import Grid, densify, split

RAMP_TYPES = {"Top surface", "Ironing"}


@dataclass
class SmoothOptions:
    max_tread: float = 6.0
    max_segment: float = 0.4
    resolution: float = 0.1
    max_angle: float = 15.0
    min_area: float = 20.0
    skin_angle: float = 45.0    # direction of the skin lines


def _mask(grid, paths, fill=True):
    """Cells covered by the paths' lines; fill also closes infill gaps."""
    m = np.zeros(grid.shape, bool)
    by_w = {}
    for p in paths:
        by_w.setdefault(round(p.width, 2), []).append(densify(p, grid.res / 2)[0])
    for w, groups in by_w.items():
        c = np.zeros(grid.shape, bool)
        for s in groups:
            r, cc = grid.cells(s)
            c[r, cc] = True
        m |= distance_transform_edt(~c, sampling=grid.res) <= w / 2
    return binary_fill_holes(m) if fill else m


def collision_angle(nozzle):
    """Steepest slope the nozzle can follow: the flat tip may stand at most `contact` into the slope over
    its radius, and the cone flank must stay above it."""
    cone = 90.0 - nozzle.cone_angle
    if nozzle.tip_radius <= 0:
        return cone
    return min(cone, math.degrees(math.atan2(nozzle.contact, nozzle.tip_radius)))


def smooth_tops(program, opts, nozzle):
    """Adds the skins to the program. Returns the number of skin paths."""
    grid = Grid(program, opts.resolution)
    layers = program.layers
    shape = grid.shape
    S = np.full(shape, np.nan)           # smooth surface height (nozzle Z)
    base = np.full(shape, np.nan)        # top of the layer under the tread
    owner = np.full(shape, -1, int)      # layer index of the tread
    masks = {}

    def mask(i):
        if i not in masks:
            masks[i] = _mask(grid, layers[i].paths) if 0 <= i < len(layers) else np.zeros(shape, bool)
        return masks[i]

    slack = 3 * grid.res
    pad = int(math.ceil(opts.max_tread / grid.res)) + 4
    # every layer's exposed top is part of the surface (on a dome mostly walls: that is where the steps are)
    for i in range(len(layers)):
        L = layers[i]
        if not L.paths:
            continue
        here, up = mask(i), mask(i + 1)
        tread = here & ~up
        if not tread.any():
            continue
        rr, cc = np.nonzero(here)
        r0, r1 = max(rr.min() - pad, 0), min(rr.max() + pad + 1, shape[0])
        c0, c1 = max(cc.min() - pad, 0), min(cc.max() + pad + 1, shape[1])
        win = (slice(r0, r1), slice(c0, c1))
        tops = [p for p in L.paths if p.type in RAMP_TYPES]
        top_lines = _mask(grid, tops, fill=False)[win] if tops else np.zeros((r1 - r0, c1 - c0), bool)
        h = (layers[i + 1].z - L.z) if i + 1 < len(layers) else L.height
        zpath = next((p.z for p in L.paths if p.zs is None), L.paths[0].z)
        t = tread[win]
        lift = np.zeros(t.shape)
        if up.any() and h > 0:
            d_out = distance_transform_edt(here[win], sampling=grid.res)
            d_in = distance_transform_edt(~up[win], sampling=grid.res)
            below = (mask(i - 1) & ~here)[win] if i > 0 else np.zeros(t.shape, bool)
            d_low = distance_transform_edt(~below, sampling=grid.res) if below.any() else np.full(t.shape, np.inf)
            two = mask(i + 2)[win]
            d_in2 = distance_transform_edt(~two, sampling=grid.res) if two.any() else np.full(t.shape, np.inf)
            span = d_out + d_in
            stair = ((span <= opts.max_tread) & np.isfinite(d_in) & (d_low - d_out <= slack)
                     & (d_in2 - d_in >= slack))
            lift = np.where(stair, np.clip(h * d_out / np.maximum(span, 1e-9), 0, h), 0.0)
        sub_S, sub_base, sub_owner = S[win], base[win], owner[win]
        sub_S[t] = zpath + lift[t]
        # what the skin rests on: under a removed top line the layer below, under a wall the wall itself
        sub_base[t] = np.where(top_lines[t], zpath - L.height, zpath)
        sub_owner[t] = i
        masks.pop(i - 1, None)          # keep memory bounded

    defined = ~np.isnan(S)
    if not defined.any():
        return 0
    # slope, from neighbouring defined cells only
    filled = np.where(defined, S, 0.0)
    gy, gx = np.gradient(filled, grid.res)
    ok_y = defined & np.roll(defined, 1, 0) & np.roll(defined, -1, 0)
    ok_x = defined & np.roll(defined, 1, 1) & np.roll(defined, -1, 1)
    slope = np.hypot(np.where(ok_x, gx, 0.0), np.where(ok_y, gy, 0.0))
    limit = math.tan(math.radians(min(opts.max_angle, collision_angle(nozzle))))
    region_cells = defined & (slope <= limit)
    lab, n = label(region_cells)
    regions = []
    for k in range(1, n + 1):
        sel = lab == k
        area = sel.sum() * grid.res ** 2
        zs = S[sel]
        span = zs.max() - zs.min()
        levels = len(np.unique(owner[sel]))
        if area >= opts.min_area and levels >= 2 and span <= nozzle.clear_height:
            regions.append(sel)
    if not regions:
        return 0

    stair = np.where(owner >= 0, np.array([L.paths[0].z if L.paths else 0.0 for L in layers] + [0.0])[owner], np.nan)
    # a skin must clear what exists when it prints: layers up to its own and skins that finish no higher
    tops = [int(owner[sel].max()) for sel in regions]
    done = np.zeros(shape, bool)
    half_w = float(np.median([p.width for L in layers for p in L.paths])) / 2
    for t in sorted(set(tops)):
        group = np.logical_or.reduce([sel for sel, tt in zip(regions, tops) if tt == t])
        exists = np.where(done | group, S, np.where((owner >= 0) & (owner <= t), stair, np.nan))
        _lift_clear(S, exists, group, grid, nozzle, half_w)
        done |= group
    made = 0
    for sel in regions:
        made += _skin(program, grid, sel, S, base, owner, opts)
    return made


def _lift_clear(S, exists, sel, grid, nozzle, half_w):
    """Raise skin points the nozzle could not reach: no point may lie lower than any existing point y by more
    than contact + max(0, distance - tip_radius) / tan(cone). Max-plus dilation to a fixed point."""
    tan = math.tan(math.radians(nozzle.cone_angle)) or 1e-6
    rows, cols = np.nonzero(sel)
    probe = int(math.ceil(nozzle.clear_height * tan / grid.res)) + 1
    pr0, pr1 = max(rows.min() - probe, 0), min(rows.max() + probe + 1, S.shape[0])
    pc0, pc1 = max(cols.min() - probe, 0), min(cols.max() + probe + 1, S.shape[1])
    near = exists[pr0:pr1, pc0:pc1]
    rise = max(0.0, min(float(np.nanmax(near)) - float(np.nanmin(S[sel])), nozzle.clear_height))
    k = int(math.ceil((nozzle.tip_radius + half_w + grid.res + rise * tan) / grid.res)) + 1
    r0, r1 = max(rows.min() - k, 0), min(rows.max() + k + 1, S.shape[0])
    c0, c1 = max(cols.min() - k, 0), min(cols.max() + k + 1, S.shape[1])
    win = (slice(r0, r1), slice(c0, c1))
    region = sel[win]
    ref = np.where(np.isnan(exists[win]), -1e3, exists[win])
    oy, ox = np.mgrid[-k:k + 1, -k:k + 1]
    rho = np.hypot(oy, ox) * grid.res
    # 0.03 mm: straight skin segments cut slightly below the curve between their points
    cost = max(nozzle.contact - 0.03, 0.0) + np.maximum(0.0, rho - half_w - nozzle.tip_radius - grid.res) / tan
    for _ in range(50):
        need = grey_dilation(ref, structure=-cost, mode="constant", cval=-1e3)
        new = np.where(region, np.maximum(ref, need), ref)   # only skin cells move
        if np.allclose(new, ref):
            break
        ref = new
    sub = S[win]
    sub[region] = ref[region]


def _layer_of(layers, p):
    return min(layers, key=lambda L: abs(L.z - p.z) if L.paths else 1e9)


def _skin(program, grid, sel, S, base, owner, opts):
    layers = program.layers
    involved = sorted(set(np.unique(owner[sel]).tolist()))
    removed = []
    for i in involved:
        L = layers[i]
        keep = []
        for p in L.paths:
            if p.type not in RAMP_TYPES:
                keep.append(p)
                continue
            pts, seg, ts = densify(p, grid.res / 2)
            r, c = grid.cells(pts)
            inside = sel[r, c]
            if not inside.any():
                keep.append(p)
                continue
            removed.append(p)
            if not inside.all():
                keep.extend(q for flag, q in split(p, seg, ts, inside) if not flag)
        L.paths = keep
    # width, flow, speed and colour from the removed top lines, or on a dome the walls inside the region
    ref = removed
    if not ref:
        for i in involved:
            for p in layers[i].paths:
                if p.type in ("Outer wall", "Inner wall"):
                    r, c = grid.cells(densify(p, grid.res / 2)[0])
                    if sel[r, c].any():
                        ref.append(p)
    if not ref:
        return 0
    width = float(np.median([p.width for p in ref]))
    # filament per mm^3 of line, to scale to the skin's width and gap
    e_norm = float(np.median([sum(p.e) / max(p.length(), 1e-9) / (p.width * _layer_of(layers, p).height)
                              for p in ref]))
    feed = float(np.median([f for p in ref for f in p.f]))
    tmpl = ref[0]
    # colour per cell: the nearest reference line
    tool_cells = np.full(S.shape, -1, int)
    hit = np.zeros(S.shape, bool)
    for p in ref:
        r, c = grid.cells(densify(p, grid.res / 2)[0])
        tool_cells[r, c] = -1 if p.tool is None else p.tool
        hit[r, c] = True
    _, (ir, ic) = distance_transform_edt(~hit, return_indices=True)
    tool_cells = tool_cells[ir, ic]

    a = math.radians(opts.skin_angle)
    ux, uy = math.cos(a), math.sin(a)          # along a line
    vx, vy = -uy, ux                            # across the lines
    rows, cols = np.nonzero(sel)
    xs = grid.x0 + cols * grid.res
    ys = grid.y0 + rows * grid.res
    across = xs * vx + ys * vy
    along = xs * ux + ys * uy
    t0, t1 = along.min() - 1, along.max() + 1
    n = max(2, int((t1 - t0) / (grid.res / 2)))
    t = np.linspace(t0, t1, n)
    step = max(1, int(round(opts.max_segment / ((t1 - t0) / (n - 1)))))
    top = layers[max(involved)]
    zplane = next((p.z for p in top.paths if p.zs is None), tmpl.z)
    paths = []
    forward = True
    for v in np.arange(across.min() + width / 2, across.max(), width):
        px, py = v * vx + t * ux, v * vy + t * uy
        c = np.rint((px - grid.x0) / grid.res).astype(int)
        r = np.rint((py - grid.y0) / grid.res).astype(int)
        valid = (r >= 0) & (r < S.shape[0]) & (c >= 0) & (c < S.shape[1])
        inside = np.zeros(n, bool)
        inside[valid] = sel[r[valid], c[valid]]
        runs, k = [], 0
        while k < n:                     # runs of inside samples, split where the colour changes
            if not inside[k]:
                k += 1
                continue
            m, tool = k, tool_cells[r[k], c[k]]
            while m < n and inside[m] and tool_cells[r[m], c[m]] == tool:
                m += 1
            if m - k >= 3:
                runs.append((k, m - 1, tool))
            k = m
        if not forward:
            runs = [(e, s, tl) for s, e, tl in reversed(runs)]
        forward = not forward
        for s_idx, e_idx, tool in runs:
            sgn = 1 if e_idx >= s_idx else -1
            idx = list(range(s_idx, e_idx, sgn * step)) + [e_idx]
            pts = [(float(px[q]), float(py[q])) for q in idx]
            zs = [float(S[r[q], c[q]]) for q in idx]
            es, fs = [], []
            for q in range(len(idx) - 1):
                ln = math.dist(pts[q], pts[q + 1])
                c0, c1 = (r[idx[q]], c[idx[q]]), (r[idx[q + 1]], c[idx[q + 1]])
                gap = ((S[c0] - base[c0]) + (S[c1] - base[c1])) / 2
                es.append(e_norm * width * max(gap, 0.0) * ln)
                fs.append(feed)
            if len(pts) < 2:
                continue
            paths.append(replace(tmpl, tool=None if tool < 0 else int(tool), type="Top surface", width=width,
                                 z=zplane, pts=pts, e=es, f=fs, zs=zs, seq=-1, head=True, tail=True,
                                 retract_before=True, hop_before=True, tower=None, block=-1))
    top.paths.extend(paths)
    return len(paths)
