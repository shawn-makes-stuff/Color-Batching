"""Dump a parsed program (colorstack.parse) as text, floats as their 64-bit patterns, in the format of
cpp/tools/dump_program.cpp.  python tools/dump_program.py in.gcode out.txt"""
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from colorstack.parse import parse  # noqa: E402


def b(v):
    return "None" if v is None else struct.pack(">d", float(v)).hex()


def s(v):
    return "None" if v is None else str(v)


def fans(f):
    return ",".join(f"{k}={v}" for k, v in f.items())


def state(st):
    return ";".join(f"{w}({'.'.join(names)})={line}" for (w, names), line in st)


def lead(items):
    out = []
    for it in items:
        if it[0] == "move":
            out.append("m " + " ".join(b(v) for v in it[1:]))
        elif it[0] == "accel":
            out.append("a " + b(it[1]))
        else:
            out.append("l " + it[1])
    return " | ".join(out)


def dump(prog, out):
    w = out.write
    for k in sorted(prog.settings):
        w(f"setting {k}={prog.settings[k]}\n")
    for name in ("header", "preamble", "postamble"):
        lines = getattr(prog, name)
        w(f"{name} {len(lines)}\n")
        for ln in lines:
            w(f"| {ln}\n")
    w(f"first_tool {prog.first_tool}\ntool_format {prog.tool_format}\nabs_e {int(prog.abs_e)}\n"
      f"stats_info {int(prog.stats_info)}\naccel_format {prog.accel_format}\nfirst_temp {b(prog.first_temp)}\n")
    for k in sorted(prog.travel):
        v = prog.travel[k]
        w(f"travel {k} {int(v) if isinstance(v, bool) else b(v)}\n")
    for obj, typ in sorted(prog.free_support, key=lambda k: (s(k[0]), k[1])):
        w(f"free_support {s(obj)} {typ}\n")
    for name in ("toolchange_end", "toolchange_start"):
        blocks = getattr(prog, name)
        for t in sorted(blocks):
            w(f"{name} {t} {len(blocks[t])}\n")
            for ln in blocks[t]:
                w(f"| {ln}\n")
    for t in sorted(prog.temps):
        w(f"temps {t} {' '.join(b(v) for v in prog.temps[t])}\n")
    for (t, idx, typ) in sorted(prog.fans):
        w(f"fans {t} {idx} {typ}: {fans(prog.fans[(t, idx, typ)])}\n")
    for n, ((t, typ), rows) in enumerate(prog.states.items()):      # insertion order: state_at depends on it
        w(f"states {n} {t} {typ}: {' # '.join(f'{idx} {state(st)}' for idx, st in rows)}\n")
    for L in prog.layers:
        w(f"layer {L.index} {b(L.z)} {b(L.height)} paths {len(L.paths)} cmds {len(L.cmds)}\n")
        for c in L.cmds:
            w(f"| {c}\n")
        for p in L.paths:
            w(f"path {s(p.tool)} {p.type} w={b(p.width)} z={b(p.z)} accel={b(p.accel)} obj={s(p.obj)} seq={p.seq} "
              f"rb={int(p.retract_before)} hb={int(p.hop_before)} head={int(p.head)} tail={int(p.tail)} "
              f"tower={s(p.tower)} block={p.block} scv={b(p.scv)} temp={b(p.temp)} height={b(p.height)}\n")
            w(f"  fans {fans(p.fans)}\n  vlim {','.join(f'{k}={v}' for k, v in p.vlim)}\n  state {state(p.state)}\n")
            w(f"  pts {' '.join(b(x) + ',' + b(y) for x, y in p.pts)}\n  e {' '.join(b(v) for v in p.e)}\n"
              f"  f {' '.join(b(v) for v in p.f)}\n  zs {' '.join(b(v) for v in p.zs) if p.zs else 'None'}\n"
              f"  lead {lead(p.lead)}\n")


if __name__ == "__main__":
    with open(sys.argv[2], "w", encoding="utf-8", newline="\n") as out:
        dump(parse(sys.argv[1]), out)
