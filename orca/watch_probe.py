"""Print new probe.jsonl records and plugin-related lines from Orca's newest log, as they appear.
python orca/watch_probe.py   (one line per event, for a monitor)"""
import glob
import json
import os
import re
import sys
import time

DATA = os.path.join(os.environ["APPDATA"], "OrcaSlicer")
PROBE = os.path.join(DATA, "orca_plugins", "colorstack_probe", "probe.jsonl")
LOGS = os.path.join(DATA, "log", "debug_*.log*")
KEEP = re.compile(r"plugin|python|ColorStack|uv |audit|permission|Traceback|Error|error", re.I)


def follow():
    pos = {}
    started = time.time()
    while True:
        files = [PROBE] + sorted((f for f in glob.glob(LOGS) if os.path.getmtime(f) >= started - 5),
                                 key=os.path.getmtime)[-1:]
        for f in files:
            if not os.path.exists(f):
                continue
            size = os.path.getsize(f)
            if f not in pos:
                pos[f] = 0 if f == PROBE else size   # whole probe log, only new Orca log lines
            if size < pos[f]:
                pos[f] = 0
            if size == pos[f]:
                continue
            with open(f, encoding="utf-8", errors="replace") as fh:
                fh.seek(pos[f])
                chunk = fh.read()
                pos[f] = fh.tell()
            for line in chunk.splitlines():
                if f == PROBE:
                    try:
                        r = json.loads(line)
                        print(f"PROBE {r.pop('kind')}: {json.dumps(r)[:600]}", flush=True)
                    except ValueError:
                        print("PROBE (bad line) " + line[:300], flush=True)
                elif KEEP.search(line):
                    print("ORCA " + line.strip()[:400], flush=True)
        time.sleep(1)


if __name__ == "__main__":
    try:
        follow()
    except KeyboardInterrupt:
        sys.exit(0)
