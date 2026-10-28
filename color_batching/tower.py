"""Shrink Orca's prime tower to the swaps that are left.

A tower layer is walls, one wipe block per tool change, and a sparse grid over the rest. Each remaining swap
keeps a wipe: the n-th tool swapped in during a band (counting a swap at its start) wipes on the n-th block of the
band's first layer. Unused
blocks become grid lines (the grid's own spacing, width and flow). The tool that finishes the band prints the
rest of the band's tower.

When Orca sized each block by its colour pair's flush (tower_flush), the bands share one running tower instead
(RunTower): a swap takes the wipe lines from the lowest unprinted tower layer on, in the slicer's order and up the
layers, until they hold its own pair's flush (the next swap goes on from the line after, inside the same block if it
must), finishing each layer it uses up, so it may wipe on tower layers above its band, though never more than the
nozzle's clear height above the band's top (as high as any stack may stand); the band's last tool prints the tower up
to the band's top. The tower stays the size the slicer made it: the swaps left need less than all of it. A swap that
cannot get its whole flush that way is counted (short).
"""
import math
import statistics
from dataclasses import replace


def tower_flush(program):
    """Whether Orca sized each wipe block by its colour pair's flush rather than prime_volume for every pair:
    purge_in_prime_tower with single_extruder_multi_material on a Type 2 tower (Orca 2.3 ToolOrdering.cpp,
    Print.cpp: volume_to_wipe = flush_volumes_matrix x flush_multiplier)."""
    s = program.settings
    return (s.get("purge_in_prime_tower") == "1" and s.get("single_extruder_multi_material") == "1"
            and s.get("wipe_tower_type", "type2") != "type1")


def _grid_template(program):
    xs, epm, widths, feeds = set(), [], [], []
    for L in program.layers:
        for p in L.paths:
            if p.tower != "grid":
                continue
            for i in range(len(p.e)):
                (x0, y0), (x1, y1) = p.pts[i], p.pts[i + 1]
                if abs(x1 - x0) < 1e-3 and abs(y1 - y0) > 1.0:      # the grid's long Y lines
                    xs.add(round(x0, 2))
                    epm.append(p.e[i] / abs(y1 - y0))
                    feeds.append(p.f[i])
            widths.append(p.width)
    if not xs:
        return None
    return sorted(xs), statistics.median(epm), statistics.median(widths), statistics.median(feeds)


def _grid_over(p, tpl):
    """Grid lines over the area of wipe block p."""
    xs, epm, width, feed = tpl
    ax = [q[0] for q in p.pts]
    ay = [q[1] for q in p.pts]
    x0, x1, y0, y1 = min(ax), max(ax), min(ay), max(ay)
    cols = [x for x in xs if x0 - 1e-6 <= x <= x1 + 1e-6]
    if not cols or y1 - y0 < 1e-3:
        return []
    pts, es, fs = [], [], []
    up = True
    for x in cols:
        a, b = (y0, y1) if up else (y1, y0)
        if pts:                     # unextruded step along the block's edge
            es.append(0.0)
            fs.append(feed)
        pts += [(x, a), (x, b)]
        es.append(epm * abs(b - a))
        fs.append(feed)
        up = not up
    return [replace(p, pts=pts, e=es, f=fs, width=width, tower="grid", block=-1)]


class RunTower:
    """The tower for staggered runs. A run that swaps tools in wipes on a free block of the lowest tower layer not
    printed yet; the run after which every unit is past some layers prints the rest of their tower, unused blocks
    as grid."""

    def __init__(self, program, clear=math.inf):
        self.program, self.flush, self.clear = program, tower_flush(program), clear
        self.tpl = _grid_template(program)
        self.layers = [[p for p in L.paths if p.tool is None] for L in program.layers]
        self.next = 0              # tower layers below are printed
        self.used = set()          # (layer, block) wiped on
        self.taken = {}            # tower_flush: layer -> wipe segments used, in the order of its wipe paths
        self.kept = self.replaced = self.short = 0

    def wipe(self, a, b, top):
        """[(layer, paths)] for a swap from tool a to b, the model printed below layer top: one free wipe block on
        the lowest unprinted tower layer, if it has one; with tower_flush, the pair's flush (see the module)."""
        if self.flush:
            layers = self.program.layers
            limit = layers[top - 1].z + self.clear + 1e-6
            below = next((j for j in range(top, len(layers)) if layers[j].z > limit), len(layers))
            return self._wipe_flush(self.program.flush_length(a, b), below)
        j = self.next
        if j >= len(self.layers):
            return []
        free = [p.block for p in self.layers[j] if p.tower == "wipe" and (j, p.block) not in self.used]
        if not free:
            return []
        self.used.add((j, free[0]))
        block = [p for p in self.layers[j] if p.tower == "wipe" and p.block == free[0]]
        self.kept += len(block)
        return [(j, block)]

    def _wipe_flush(self, mm, below):
        out, got = [], 0.0
        while self.next < below and got < mm:
            j = self.next
            k, cut, mine = 0, self.taken.get(j, 0), []
            for p in self.layers[j]:
                if p.tower != "wipe":
                    continue
                n = len(p.e)
                i = s = min(max(cut - k, 0), n)
                while s < n and got < mm:
                    got += p.e[s]
                    s += 1
                if s > i:
                    mine.append(p if (i, s) == (0, n) else replace(p, pts=p.pts[i:s + 1], e=p.e[i:s], f=p.f[i:s]))
                k += n
            self.taken[j] = cut + sum(len(q.e) for q in mine)
            self.kept += len(mine)
            if mine:
                out.append((j, mine))
            if got < mm:           # layer j's wipe lines used up: finish it, go on above
                out += self.complete(j + 1)
        self.short += got < mm
        return out

    def complete(self, upto):
        """[(layer, paths)]: what is left of the tower layers below upto."""
        out = []
        for j in range(self.next, min(upto, len(self.layers))):
            paths, k, cut = [], 0, self.taken.get(j, 0)
            for p in self.layers[j]:
                if p.tower == "wipe" and (j, p.block) in self.used:
                    continue
                if p.tower == "wipe":      # the part not wiped on (tower_flush)
                    i = min(max(cut - k, 0), len(p.e))
                    k += len(p.e)
                    if i == len(p.e):
                        continue
                    if i:
                        p = replace(p, pts=p.pts[i:], e=p.e[i:], f=p.f[i:])
                if p.tower == "wipe" and self.tpl is not None:
                    paths += _grid_over(p, self.tpl)
                    self.replaced += 1
                else:
                    paths.append(p)
            out.append((j, paths))
        self.next = max(self.next, upto)
        return out


def compensate(program, bands, nozzle):
    """Rewrite each band's tower paths. Returns (wipe blocks kept, wipe blocks turned into grid, swaps short of
    their flush)."""
    if tower_flush(program):
        return _run_bands(program, bands, nozzle)
    tpl = _grid_template(program)
    kept = replaced = 0
    loaded = program.first_tool
    for band in bands:
        first_of = {}
        for u in band.order:
            first_of.setdefault(u[0], u)
        tool_seq = band.tools
        last = first_of[tool_seq[-1]]
        blocks = sorted({p.block for _, p in band.work[0] if p.tower == "wipe"})
        swapped_in = tool_seq if tool_seq[0] != loaded else tool_seq[1:]     # a band may start with a swap
        wiper = dict(zip(blocks, [first_of[t] for t in swapped_in]))
        loaded = tool_seq[-1]
        for j, items in enumerate(band.work):
            new = []
            for owner, p in items:
                if p.tower is None:
                    new.append((owner, p))
                elif p.tower == "wipe" and p.block in wiper:
                    kept += 1
                    new.append((wiper[p.block], p))
                elif p.tower == "wipe" and tpl is not None:
                    new.extend((last, g) for g in _grid_over(p, tpl))
                    replaced += 1
                else:
                    new.append((last, p))
            band.work[j] = new
    return kept, replaced, 0


def _run_bands(program, bands, nozzle):
    """compensate with one running tower over the bands (tower_flush)."""
    tower = RunTower(program, nozzle.clear_height)
    loaded = program.first_tool
    for band in bands:
        first_of = {}
        for u in band.order:
            first_of.setdefault(u[0], u)
        tool_seq = band.tools
        chain = tool_seq if tool_seq[0] == loaded else [loaded] + tool_seq     # a band may start with a swap
        lo, top = band.layers[0].index, band.layers[-1].index + 1
        got = [(first_of[b], tower.wipe(a, b, top)) for a, b in zip(chain, chain[1:])]
        got.append((first_of[tool_seq[-1]], tower.complete(top)))
        loaded = tool_seq[-1]
        end = max([top] + [j + 1 for _, tp in got for j, _ in tp])     # above top: tower only
        work = [[(o, p) for o, p in items if p.tower is None] for items in band.work] + [[] for _ in range(top, end)]
        for u, tp in got:
            for j, paths in tp:            # j >= lo: the band before finished the tower up to its top
                work[j - lo] += [(u, p) for p in paths]
        band.layers, band.work = band.layers + program.layers[top:end], work
    return tower.kept, tower.replaced, tower.short
