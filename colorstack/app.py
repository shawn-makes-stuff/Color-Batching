"""Local app: settings, processing and preview.  python -m colorstack.app [file.gcode] [--port 8765]
Serves viewer/index.html and keeps uploads and results under work/."""
import argparse
import json
import re
import shutil
import threading
import time
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from .pipeline import DEFAULTS, apply, load_config, run, save_config

ROOT = Path(__file__).resolve().parent.parent
VIEWER = ROOT / "viewer" / "index.html"
WORK = ROOT / "work"

# key -> (label, unit, help); the README's settings table says the same
HELP = {
    "nozzle.tip_diameter": ("Tip diameter", "mm", "Flat face at the nozzle tip."),
    "nozzle.cone_angle": ("Cone angle", "°", "Cone half-angle from vertical."),
    "nozzle.clear_height": ("Clear height", "mm", "How far the tip sticks out below the heater block / sock / duct."),
    "nozzle.contact": ("Edge contact", "mm", "How much taller a neighbouring colour's edge may be while the nozzle "
                       "face slides past it. 0.2 = two layers per colour, 0.4 = three."),
    "stack.max_band_layers": ("Max layers per colour", "", "Most layers one colour prints before the next swap."),
    "stack.surface_tolerance": ("Edge shift", "mm", "How far a colour edge on a visible surface may move. 0 = never."),
    "stack.min_piece": ("Min piece", "mm", "Shorter leftovers between handed-over pieces are handed over too."),
    "stack.per_object": ("Per-object batching", "", "Finish one object's block before moving to the next, where "
                         "that is faster (both ways are planned and the faster kept)."),
    "stack.staggered": ("Staggered colours", "", "Each colour on its own schedule: fewer swaps where 3+ colours "
                        "share layers. Both ways are planned, the faster kept. Slower processing on those files."),
    "stack.resolution": ("Resolution", "mm", "Sampling step for the collision planning."),
    "nonplanar.smooth_tops": ("Non-planar top skin", "", "Replace the stair-stepped top lines of shallow curved / "
                              "sloped tops with one continuous skin that follows the surface."),
    "nonplanar.max_angle": ("Max angle", "°", "Steepest surface printed non-planar. Also capped by the nozzle: the "
                            "flat tip may touch the slope by at most Edge contact."),
    "nonplanar.min_area": ("Min area", "mm²", "Smallest region worth a non-planar skin."),
    "nonplanar.max_tread": ("Max tread", "mm", "A flat wider than this is a real flat, not a stair step."),
    "nonplanar.max_segment": ("Max segment", "mm", "Longest straight piece of a ramped line."),
    "cooling.respect_min_layer_time": ("Respect minimum layer time", "", "Slow regions printed again straight on top "
                                       "of themselves down to the minimum layer time, as the slicer does. Off: keep "
                                       "the sliced speeds (the fan rule still applies)."),
    "cooling.stacked_fan": ("Fan for stacked regions", "", "Raise the part fan on regions printed again straight on "
                            "top of themselves, by the slicer's own fan rule."),
    "cooling.lift_wait": ("Lift and wait", "", "If a stacked region is still under the minimum layer time at "
                          "minimum speed, lift clear and wait out the rest. Can leave ooze strings."),
    "cooling.min_layer_time": ("Min layer time", "s", "Minimum layer time for stacked regions. 0 = the file's own "
                               "(per filament, where its slow-down is on)."),
    "cooling.tower_between": ("Tower between layers", "", "Print the prime tower's walls and grid between a colour's "
                              "stacked layers, as cooling time. The wipe still comes right after the swap."),
    "swap.lift": ("Swap lift", "mm", "Before each colour change, lift this far above the tallest point printed so "
                  "far (at least the slicer's Z hop), so the trip to the purge tray clears every stack."),
    "travel.local_lift": ("Local travel lift", "", "Travels lift only where they would pass over plastic standing "
                          "higher than the layer being printed (another colour's stack); elsewhere they travel as "
                          "the slicer did. Off: every travel lifts above the tallest point printed anywhere."),
    "export.printer_host": ("Printer", "", "Moonraker address for Send to printer, e.g. 192.168.1.20 or "
                            "192.168.1.20:7125."),
    "printer.max_velocity": ("Max velocity", "mm/s", "Fallback for the time estimate, for files without the "
                             "slicer's machine limits."),
    "printer.max_accel": ("Max accel", "mm/s²", "Fallback for the time estimate."),
    "printer.square_corner_velocity": ("Square corner velocity", "mm/s", "Fallback for the time estimate."),
    "printer.max_z_velocity": ("Max Z velocity", "mm/s", "Fallback for the time estimate."),
    "printer.max_z_accel": ("Max Z accel", "mm/s²", "Fallback for the time estimate."),
}

state = {"input": None}
busy = threading.Lock()
progress = {"fraction": 0.0, "stage": "", "started": 0.0, "running": False}


def safe_name(name):
    name = re.sub(r"[^\w.\- ]", "_", Path(unquote(name)).name).strip() or "upload.gcode"
    return name if name.lower().endswith(".gcode") else name + ".gcode"


def config_payload():
    cfg = load_config()
    sections = []
    for section, defaults in DEFAULTS.items():
        fields = []
        for key, default in defaults.items():
            label, unit, text = HELP.get(f"{section}.{key}", (key, "", ""))
            kind = ("bool" if isinstance(default, bool) else "int" if isinstance(default, int)
                    else "str" if isinstance(default, str) else "float")
            fields.append({"key": key, "label": label, "unit": unit, "help": text, "type": kind,
                           "value": cfg[section].get(key, default), "default": default})
        sections.append({"name": section, "fields": fields})
    return {"sections": sections}


def send_to_printer(path, host, start):
    """Moonraker upload (POST /server/files/upload, root gcodes); starts the print only when asked."""
    host = host.strip().rstrip("/")
    url = (host if host.startswith("http") else "http://" + host) + "/server/files/upload"
    boundary = "----colorstack" + str(abs(hash(path.name)))
    crlf = "\r\n"
    parts = [("root", "gcodes")] + ([("print", "true")] if start else [])
    body = b"".join(f'--{boundary}{crlf}Content-Disposition: form-data; name="{k}"{crlf}{crlf}{v}{crlf}'.encode()
                    for k, v in parts)
    body += (f'--{boundary}{crlf}Content-Disposition: form-data; name="file"; filename="{path.name}"{crlf}'
             f"Content-Type: application/octet-stream{crlf}{crlf}").encode()
    body += path.read_bytes() + f"{crlf}--{boundary}--{crlf}".encode()
    req = urllib.request.Request(url, data=body, method="POST",
                                 headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
    with urllib.request.urlopen(req, timeout=120) as r:
        reply = json.loads(r.read() or b"{}")
    return {"ok": True, "url": url, "printing": start, "moonraker": reply}


def time_left(fraction, elapsed):
    return elapsed * (1 - fraction) / fraction if fraction > 0.12 else None


class Handler(BaseHTTPRequestHandler):
    def reply(self, code, body, ctype="application/json", extra=None):
        if not isinstance(body, bytes):
            body = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def body(self):
        return self.rfile.read(int(self.headers.get("Content-Length") or 0))

    def do_GET(self):
        u = urlparse(self.path)
        if u.path == "/":
            self.reply(200, VIEWER.read_bytes(), "text/html; charset=utf-8")
        elif u.path in ("/icon.svg", "/icon.png"):
            self.reply(200, (VIEWER.parent / u.path[1:]).read_bytes(),
                       "image/svg+xml" if u.path.endswith("svg") else "image/png")
        elif u.path == "/api/config":
            self.reply(200, config_payload())
        elif u.path == "/api/state":
            self.reply(200, state)
        elif u.path == "/api/progress":
            f, elapsed = progress["fraction"], time.time() - progress["started"] if progress["running"] else 0.0
            left = time_left(f, elapsed) if progress["running"] else None
            self.reply(200, {"running": progress["running"], "fraction": f, "stage": progress["stage"],
                             "elapsed": elapsed, "left": left})
        elif u.path.startswith("/work/"):
            f = WORK / safe_name(u.path[6:])
            if not f.is_file():
                return self.reply(404, {"error": "not found"})
            extra = {"Content-Disposition": f'attachment; filename="{f.name}"'} \
                if "download" in parse_qs(u.query) else None
            self.reply(200, f.read_bytes(), "text/plain; charset=utf-8", extra)
        else:
            self.reply(404, {"error": "not found"})

    def do_POST(self):
        u = urlparse(self.path)
        try:
            if u.path == "/api/config":
                save_config(apply(load_config(), json.loads(self.body())))
                self.reply(200, {"ok": True})
            elif u.path == "/api/upload":
                name = safe_name(parse_qs(u.query).get("name", ["upload.gcode"])[0])
                WORK.mkdir(exist_ok=True)
                (WORK / name).write_bytes(self.body())
                state["input"] = name
                self.reply(200, {"name": name, "url": f"/work/{name}"})
            elif u.path == "/api/send":
                req = json.loads(self.body())
                src = WORK / safe_name(req["output"])
                if not src.is_file():
                    return self.reply(404, {"error": "process a file first"})
                self.reply(200, send_to_printer(src, req["host"], bool(req.get("start"))))
            elif u.path == "/api/process":
                req = json.loads(self.body())
                src = WORK / safe_name(req["input"])
                if not src.is_file():
                    return self.reply(404, {"error": f"{src.name} is not uploaded"})
                if not busy.acquire(blocking=False):
                    return self.reply(409, {"error": "already processing"})
                progress.update(fraction=0.0, stage="", started=time.time(), running=True)
                try:
                    out = WORK / (src.name[:-6] + ".colorstack.gcode")
                    result = run(str(src), str(out), apply(load_config(), req.get("config", {})),
                                 lambda f, stage: progress.update(fraction=f, stage=stage))
                finally:
                    progress["running"] = False
                    busy.release()
                result["output"] = out.name
                result["input"] = src.name
                self.reply(200, result)
            else:
                self.reply(404, {"error": "not found"})
        except Exception as e:
            self.reply(500, {"error": f"{type(e).__name__}: {e}"})

    def log_message(self, *args):
        pass


def main():
    ap = argparse.ArgumentParser(prog="colorstack.app")
    ap.add_argument("file", nargs="?", help="Orca .gcode to preload")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--no-browser", action="store_true")
    a = ap.parse_args()
    WORK.mkdir(exist_ok=True)
    if a.file:
        name = safe_name(a.file)
        if Path(a.file).resolve() != (WORK / name).resolve():
            shutil.copyfile(a.file, WORK / name)
        state["input"] = name
    srv = ThreadingHTTPServer(("127.0.0.1", a.port), Handler)
    url = f"http://127.0.0.1:{a.port}/"
    if not a.no_browser:
        threading.Timer(0.5, webbrowser.open, [url]).start()
    print(f"ColorStack app at {url}  (Ctrl+C to stop)")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
