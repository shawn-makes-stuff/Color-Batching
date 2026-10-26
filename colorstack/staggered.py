"""Staggered colours: each unit on its own schedule instead of shared bands."""
from itertools import permutations

from .geometry import Samples
from .plan import DONE, Band, _max_layers, _progress_at, plan_band, unit_of
from .tower import RunTower


def plan_staggered(program, nozzle, opts, progress=None):
    """Each unit on its own schedule. A run prints one unit's next layers as far as the units below could still
    finish the window after it as a band, and no further than the next barrier layer while any unit is below it.
    Next run: the most layers for no swap, else for one swap; ties go to a
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
    barriers = [L.index for L in layers if L.barrier]
    done = _progress_at(layers) if progress else None
    loaded, last, top = program.first_tool, None, 0     # top: layers below are printed by somebody
    bands = []
    while True:
        effs = {u: eff(u) for u in units}
        active = {u: e for u, e in effs.items() if e < n}
        if not active:
            break
        lo_all = min(active.values())
        if progress:
            progress(done[lo_all])
        cap = next((k for k in barriers if k > lo_all), n)     # nobody prints past a barrier until all reach it
        cands = []
        for u, e in active.items():
            best = None
            b = max(top, e + 1)
            while b <= cap:
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
            raise RuntimeError(f"no valid run at layer {layers[lo_all].index}")
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
