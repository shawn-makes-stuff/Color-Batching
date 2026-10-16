# /// script
# requires-python = ">=3.12"
# dependencies = ["numpy", "scipy"]
#
# [tool.orcaslicer.plugin]
# name = "ColorStack Probe"
# description = "Checks what a ColorStack tab inside OrcaSlicer can do. Logs only; changes nothing."
# author = "ColorStack"
# version = "0.0.1"
# ///
"""ColorStack probe for OrcaSlicer 2.5 plugins.

Answers, in probe.jsonl next to this file:
  - Python, numpy, scipy inside Orca; Orca's process id (the slice-time G-code is named after it)
  - every lifecycle event, and whether GCodeExportFinished carries the sliced G-code path and the file is readable
  - which pipeline steps fire and what psGCodePostProcess gets (only if enabled under Process > Others)
  - preset access: printer presets, physical printers, whether print_host / an API key are readable (never logged)
  - the tab: browser, WebGL2, CDN reach, save dialog / download, message speed for a whole G-code, threads
Nothing is written anywhere but probe.jsonl; G-code files are only read.
"""
import json
import os
import sys
import threading
import time

import orca

HERE = os.path.dirname(os.path.abspath(__file__))
LOG = os.path.join(HERE, "probe.jsonl")
CHUNK = 1_000_000           # characters per message in the transfer test
state = {"slice": None}     # path of the last sliced G-code, from GCodeExportFinished


def log(kind, **data):
    rec = {"t": round(time.time(), 3), "thread": threading.current_thread().name, "kind": kind, **data}
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, default=str) + "\n")


def guarded(kind, fn):
    """Run one check; log its result or the error, never raise into Orca."""
    try:
        log(kind, ok=True, result=fn())
    except BaseException as e:      # PermissionError from the audit hook included
        log(kind, ok=False, error=f"{type(e).__name__}: {e}")


def env():
    out = {"python": sys.version, "executable": sys.executable, "pid": os.getpid(), "here": HERE}
    for name in ("numpy", "scipy"):
        try:
            out[name] = __import__(name).__version__
        except Exception as e:
            out[name] = f"missing: {type(e).__name__}: {e}"
    return out


def host_info():
    out = {"app_info": orca.host.app_info()}
    for name in ("selected_printer",):
        try:
            out[name] = str(getattr(orca.host, name)())
        except Exception as e:
            out[name] = f"{type(e).__name__}: {e}"
    try:
        out["plate"] = orca.host.plater().current_plate_index()
    except Exception as e:
        out["plate"] = f"{type(e).__name__}: {e}"
    return out


def secret_shape(v):
    """Whether a setting is readable and how long it is, never its value."""
    return None if v is None else {"readable": True, "length": len(str(v)), "empty": not str(v).strip()}


def presets():
    bundle = orca.host.preset_bundle()
    out = {"collections": [a for a in dir(bundle) if not a.startswith("_")]}
    printer = bundle.printers.selected_preset()
    out["printer"] = printer.name
    keys = set(printer.config_keys())
    out["printer_host_keys"] = sorted(k for k in keys if "host" in k or "apikey" in k or "print_host" in k)
    for k in ("print_host", "printhost_apikey", "print_host_webui", "host_type"):
        if k in keys:
            v = printer.config_value(k)
            out[k] = v if k in ("host_type",) else secret_shape(v)
    try:
        out["full_print_host"] = secret_shape(bundle.full_config_value("print_host"))
    except Exception as e:
        out["full_print_host"] = f"{type(e).__name__}: {e}"
    for attr in ("physical_printers", "physical"):
        coll = getattr(bundle, attr, None)
        if coll is None:
            out[attr] = "not exposed"
            continue
        try:
            names = coll.preset_names()
            out[attr] = {"names": names}
            if names:
                p = coll.find_preset(names[0])
                pk = set(p.config_keys())
                out[attr]["first"] = {k: (p.config_value(k) if k == "host_type" else secret_shape(p.config_value(k)))
                                      for k in ("print_host", "printhost_apikey", "host_type") if k in pk}
        except Exception as e:
            out[attr] = f"{type(e).__name__}: {e}"
    return out


def read_slice(path):
    with open(path, encoding="utf-8", errors="replace") as f:
        head = [next(f, "") for _ in range(3)]
    return {"path": path, "bytes": os.path.getsize(path), "head": [h.rstrip() for h in head]}


PAGE = r"""
<style>
  body { margin: 0; padding: 16px; font: 13px system-ui, sans-serif; }
  button { margin: 0 6px 6px 0; }
  pre { white-space: pre-wrap; font-size: 12px; }
  canvas { width: 160px; height: 90px; border: 1px solid var(--orca-border, #888); }
</style>
<h3>ColorStack probe</h3>
<p>Results go to probe.jsonl. Buttons that need a click (save dialog, download) are below.</p>
<button id="all">Run automatic checks</button>
<button id="transfer">Transfer the last slice</button>
<button id="save">Save dialog</button>
<button id="dl">Download link</button>
<button id="thread">Background thread</button>
<input id="host" value="192.168.1.148" size="16"> <button id="net">Printer host GET</button>
<canvas id="gl"></canvas>
<pre id="out"></pre>
<script>
(function () {
  const out = document.getElementById("out");
  const show = (s) => { out.textContent = s + "\n" + out.textContent; };
  const report = (check, result) => { orca.postMessage({ cmd: "report", check, result }); show(check + ": " + JSON.stringify(result)); };
  let chunks = [], t0 = 0;
  orca.onMessage((m) => {
    if (!m) return;
    if (m.cmd === "chunk") {
      if (m.i === 0) { chunks = []; t0 = performance.now(); }
      chunks.push(m.data);
      if (m.i === m.n - 1) {
        const text = chunks.join("");
        report("transfer", { ms: Math.round(performance.now() - t0), chars: text.length, chunks: m.n,
                             lines: text.split("\n").length, first: text.slice(0, 60) });
      }
    } else if (m.cmd === "info") show("plugin: " + JSON.stringify(m.data));
  });
  async function auto() {
    const r = { ua: navigator.userAgent, dpr: devicePixelRatio, size: [innerWidth, innerHeight],
                saveFilePicker: typeof window.showSaveFilePicker, openFilePicker: typeof window.showOpenFilePicker,
                location: location.href, theme: getComputedStyle(document.documentElement).getPropertyValue("--orca-fg") };
    try { localStorage.setItem("cs-probe", "1"); r.localStorage = localStorage.getItem("cs-probe") === "1"; } catch (e) { r.localStorage = String(e); }
    const gl = document.getElementById("gl").getContext("webgl2");
    r.webgl2 = !!gl;
    if (gl) {
      const ext = gl.getExtension("WEBGL_debug_renderer_info");
      r.renderer = ext ? gl.getParameter(ext.UNMASKED_RENDERER_WEBGL) : gl.getParameter(gl.RENDERER);
      gl.clearColor(0.06, 0.62, 0.41, 1); gl.clear(gl.COLOR_BUFFER_BIT);
    }
    try {
      const t = performance.now();
      const res = await fetch("https://cdn.jsdelivr.net/npm/three@0.160.0/build/three.module.js");
      r.cdn = { status: res.status, bytes: (await res.text()).length, ms: Math.round(performance.now() - t) };
    } catch (e) { r.cdn = String(e); }
    try { await import("https://cdn.jsdelivr.net/npm/three@0.160.0/build/three.module.js"); r.esm_import = true; } catch (e) { r.esm_import = String(e); }
    report("page", r);
    orca.postMessage({ cmd: "presets" });
  }
  document.getElementById("all").onclick = auto;
  document.getElementById("transfer").onclick = () => orca.postMessage({ cmd: "transfer" });
  document.getElementById("thread").onclick = () => orca.postMessage({ cmd: "thread" });
  document.getElementById("net").onclick = () => orca.postMessage({ cmd: "net", host: document.getElementById("host").value });
  document.getElementById("save").onclick = async () => {
    try {
      const h = await showSaveFilePicker({ suggestedName: "colorstack_probe_save.txt" });
      const w = await h.createWritable(); await w.write("ColorStack probe save test\n"); await w.close();
      report("save_dialog", { ok: true, name: h.name });
    } catch (e) { report("save_dialog", { ok: false, error: String(e) }); }
  };
  document.getElementById("dl").onclick = () => {
    const a = Object.assign(document.createElement("a"), {
      href: URL.createObjectURL(new Blob(["ColorStack probe download test\n"], { type: "text/plain" })),
      download: "colorstack_probe_download.txt" });
    document.body.append(a); a.click(); a.remove();
    report("download", { clicked: true, note: "check the Downloads folder" });
  };
  auto();
})();
</script>
"""


class ProbeTab(orca.pages.PagesPluginCapabilityBase):
    def get_name(self):
        return "ColorStack Probe"

    def get_ui(self):
        guarded("host_info", host_info)
        return PAGE

    def on_message(self, message):
        cmd = (message or {}).get("cmd")
        if cmd == "report":
            log("page_" + str(message.get("check")), result=message.get("result"))
        elif cmd == "presets":
            guarded("presets", presets)
            if state["slice"]:
                guarded("slice_file", lambda: read_slice(state["slice"]))
        elif cmd == "transfer":
            self.transfer()
        elif cmd == "thread":
            def work():
                t = time.time()
                sum(i * i for i in range(2_000_000))
                log("thread_done", seconds=round(time.time() - t, 3))
                self.post_message({"cmd": "info", "data": "thread finished, posted from a worker"})
            try:
                threading.Thread(target=work, name="colorstack-probe-worker").start()
                log("thread_start", ok=True)
            except BaseException as e:
                log("thread_start", ok=False, error=f"{type(e).__name__}: {e}")
        elif cmd == "net":
            def get():
                import urllib.request
                url = "http://" + message.get("host", "").strip() + "/server/info"
                with urllib.request.urlopen(url, timeout=5) as r:
                    return {"url": url, "status": r.status, "bytes": len(r.read())}
            guarded("net", get)

    def transfer(self):
        path = state["slice"]
        if not path:
            log("transfer", ok=False, error="no slice seen yet: slice a plate first")
            self.post_message({"cmd": "info", "data": "slice a plate first"})
            return
        try:
            t = time.time()
            with open(path, encoding="utf-8", errors="replace") as f:
                text = f.read()
            n = max(1, -(-len(text) // CHUNK))
            for i in range(n):
                self.post_message({"cmd": "chunk", "i": i, "n": n, "data": text[i * CHUNK:(i + 1) * CHUNK]})
            log("transfer", ok=True, path=path, chars=len(text), chunks=n, post_seconds=round(time.time() - t, 3))
        except BaseException as e:
            log("transfer", ok=False, error=f"{type(e).__name__}: {e}")

    def on_lifecycle_event(self, event, ctx):
        rec = {"event": str(event), "name": ctx.name, "code": str(ctx.code), "msg": ctx.msg, "id": ctx.id,
               "source": ctx.source, "index": ctx.index, "device_id": ctx.device_id, "job_id": ctx.job_id}
        log("event", **rec)
        # only remember the path: events may come on the slicing thread, where a permission prompt for
        # reading outside the data folder could deadlock; the tab reads it on the UI thread
        if str(event).endswith("GCodeExportFinished") and ctx.msg:
            path = ctx.msg.split("\n")[0].strip()
            if path.lower().endswith(".gcode"):
                state["slice"] = path


class ProbeStep(orca.slicing.SlicingPipelineCapabilityBase):
    def get_name(self):
        return "ColorStack Probe Step"

    def execute(self, ctx):
        rec = {"step": str(ctx.step), "has_print": ctx.print is not None, "has_object": ctx.object is not None}
        if ctx.step == orca.slicing.Step.psGCodePostProcess:
            rec.update(gcode_path=ctx.gcode_path, host=ctx.host, output_name=ctx.output_name)
            try:
                rec["printer_model"] = ctx.config_value("printer_model")
                rec["single_extruder_multi_material"] = ctx.config_value("single_extruder_multi_material")
            except Exception as e:
                rec["config_error"] = f"{type(e).__name__}: {e}"
            try:
                rec["bytes"] = os.path.getsize(ctx.gcode_path)
            except Exception as e:
                rec["size_error"] = f"{type(e).__name__}: {e}"
        log("step", **rec)
        return orca.ExecutionResult.success()


@orca.plugin
class ColorStackProbe(orca.base):
    def register_capabilities(self):
        guarded("env", env)
        orca.register_capability(ProbeTab)
        orca.register_capability(ProbeStep)
