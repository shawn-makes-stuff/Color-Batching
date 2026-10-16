"""Shrink Orca's prime tower to the swaps that are left.

A tower layer is walls, one wipe block per tool change, and a sparse grid over the rest. Each remaining swap
keeps a wipe: the n-th tool swapped in during a band wipes on the n-th block of the band's first layer. Unused
blocks become grid lines (the grid's own spacing, width and flow). The tool that finishes the band prints the
rest of the band's tower.
"""
import statistics
from dataclasses import replace


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

    def __init__(self, program):
        self.tpl = _grid_template(program)
        self.layers = [[p for p in L.paths if p.tool is None] for L in program.layers]
        self.next = 0              # tower layers below are printed
        self.used = set()          # (layer, block) wiped on
        self.kept = self.replaced = 0

    def wipe(self):
        """[(layer, paths)]: one free wipe block on the lowest unprinted tower layer, if it has one."""
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

    def complete(self, upto):
        """[(layer, paths)]: what is left of the tower layers below upto."""
        out = []
        for j in range(self.next, min(upto, len(self.layers))):
            paths = []
            for p in self.layers[j]:
                if p.tower == "wipe" and (j, p.block) in self.used:
                    continue
                if p.tower == "wipe" and self.tpl is not None:
                    paths += _grid_over(p, self.tpl)
                    self.replaced += 1
                else:
                    paths.append(p)
            out.append((j, paths))
        self.next = max(self.next, upto)
        return out


def compensate(program, bands):
    """Rewrite each band's tower paths. Returns (wipe blocks kept, wipe blocks turned into grid)."""
    tpl = _grid_template(program)
    kept = replaced = 0
    for band in bands:
        first_of = {}
        for u in band.order:
            first_of.setdefault(u[0], u)
        tool_seq = band.tools
        last = first_of[tool_seq[-1]]
        blocks = sorted({p.block for _, p in band.work[0] if p.tower == "wipe"})
        wiper = dict(zip(blocks, [first_of[t] for t in tool_seq[1:]]))
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
    return kept, replaced
