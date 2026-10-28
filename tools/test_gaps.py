"""Samples.gaps against brute force: per sample, the least gap over its whole piece of the line (between split()'s
cuts) to the other lines, measured on a fine walk. python tools/test_gaps.py"""
import math
import random
import sys
from pathlib import Path as FsPath

import numpy as np

sys.path.insert(0, str(FsPath(__file__).resolve().parents[1]))
from colorstack.geometry import Samples  # noqa: E402
from colorstack.model import Path  # noqa: E402


def line(pts, w):
    return Path(tool=0, type="Inner wall", width=w, z=0.2, pts=pts, e=[1.0] * (len(pts) - 1),
                f=[1.0] * (len(pts) - 1), accel=None, fans={}, obj=None)


def to_seg(px, py, a, b):
    dx, dy = b[0] - a[0], b[1] - a[1]
    n2 = dx * dx + dy * dy
    t = 0.0 if n2 == 0 else max(0.0, min(1.0, ((px - a[0]) * dx + (py - a[1]) * dy) / n2))
    return math.hypot(px - a[0] - t * dx, py - a[1] - t * dy)


rng = random.Random(1)
for trial in range(200):
    walk = lambda n: [(rng.uniform(0, 3), rng.uniform(0, 3)) for _ in range(n)]    # noqa: E731
    p = line(walk(rng.randint(2, 5)), 0.45)
    others = [line(walk(rng.randint(2, 4)), rng.choice([0.42, 0.45, 0.5])) for _ in range(rng.randint(1, 3))]
    s = Samples(0.2)
    tree = s.tree(others)
    got = s.gaps(tree, p)
    pts, seg, ts = s.of(p)
    for k in range(len(pts)):
        # the piece sample k stands for, walked finely
        i = seg[k]
        a, b = np.array(p.pts[i]), np.array(p.pts[i + 1])
        t0 = 0.0 if k == 0 or seg[k - 1] != i else (ts[k - 1] + ts[k]) / 2
        t1 = 1.0 if k == len(pts) - 1 or seg[k + 1] != i else (ts[k] + ts[k + 1]) / 2
        want = min(to_seg(*(a + (b - a) * (t0 + (t1 - t0) * u / 400)), q.pts[m], q.pts[m + 1]) - (p.width + q.width) / 2
                   for u in range(401) for q in others for m in range(len(q.pts) - 1))
        if want > 0.1:      # only lines in reach are measured
            assert got[k] > 0.1, (trial, k, got[k], want)
            continue
        assert got[k] <= want + 1e-9, (trial, k, got[k], want)
        assert got[k] >= want - 0.01, (trial, k, got[k], want)      # the walk's own step
print("gaps ok")
