"""Dump the Python reader's events, floats as their 64-bit patterns (the format of cpp/tools/dump_events.cpp).
python tools/dump_events.py in.gcode out.txt"""
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from color_batching.reader import load, read  # noqa: E402


def bits(v):
    return struct.pack(">d", float(v)).hex()


if __name__ == "__main__":
    with open(sys.argv[2], "w", encoding="utf-8", newline="\n") as out:
        for idx, kind, v in read(load(sys.argv[1])):
            if kind == "move":
                out.write(f"{idx} move {' '.join(bits(getattr(v, k)) for k in ('x0', 'y0', 'z0', 'x1', 'y1', 'z1', 'de', 'f'))}"
                          f" {v.word} {bits(v.xy)}\n")
            elif kind == "tool":
                out.write(f"{idx} tool {v[0]} {v[1]}\n")
            elif kind == "layer":
                out.write(f"{idx} layer\n")
            elif kind in ("accel", "scv", "z", "height", "width"):
                out.write(f"{idx} {kind} {bits(v)}\n")
            else:
                out.write(f"{idx} {kind} {v}\n")
