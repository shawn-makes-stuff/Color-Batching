"""Entry point for the CLI and the app: settings in, processed file + summary out."""
import copy
import re
import tomllib
from pathlib import Path

from .emit import Writer
from .estimate import anchor, estimate, file_estimate, machine_limits, stamp_progress, swap_each
from .gcode import parse
from .nonplanar import SmoothOptions, smooth_tops
from .plan import Nozzle, Options, plan, plan_staggered
from .reader import load
from .tower import compensate

CONFIG_PATH = Path(__file__).resolve().parent.parent / "colorstack.toml"

DEFAULTS = {
    "nozzle": {
        "tip_diameter": 1.0,
        "cone_angle": 45.0,
        "clear_height": 2.0,
        "contact": 0.0,
    },
    "stack": {
        "max_band_layers": 10,
        "surface_tolerance": 0.0,
        "min_piece": 2.0,
        "per_object": True,        # plan with and without, keep the faster
        "staggered": False,        # also plan each colour on its own schedule, keep the faster
        "resolution": 0.1,
    },
    "nonplanar": {
        "smooth_tops": False,
        "max_angle": 15.0,
        "min_area": 20.0,
        "max_tread": 6.0,
        "max_segment": 0.4,
    },
    "cooling": {
        "stacked_fan": True,
        "lift_wait": False,
        "min_layer_time": 0.0,     # seconds; 0 = the file's own per filament
        "tower_between": False,
    },
    "swap": {
        "lift": 1.0,
    },
    "travel": {
        "local_lift": False,
    },
    "export": {
        "printer_host": "",
    },
    "printer": {                  # only for files without the slicer's machine limits
        "max_velocity": 500.0,
        "max_accel": 20000.0,
        "square_corner_velocity": 9.0,
        "max_z_velocity": 20.0,
        "max_z_accel": 500.0,
    },
}


def load_config(path=CONFIG_PATH):
    cfg = copy.deepcopy(DEFAULTS)
    if Path(path).exists():
        with open(path, "rb") as f:
            for section, values in tomllib.load(f).items():
                cfg.setdefault(section, {}).update(values)
    return cfg


def apply(cfg, values):
    """Merge {section: {key: value}} over cfg, every value converted to its default's type."""
    for section, defaults in DEFAULTS.items():
        for key, default in defaults.items():
            v = values.get(section, {}).get(key, cfg[section].get(key, default))
            cfg[section][key] = (v is True or str(v).lower() == "true") if isinstance(default, bool) else type(default)(v)
    return cfg


def save_config(cfg, path=CONFIG_PATH):
    lines = ["# ColorStack settings (written by the app; safe to edit by hand)"]
    for section, values in cfg.items():
        lines.append(f"\n[{section}]")
        for k, v in values.items():
            if isinstance(v, bool):
                v = str(v).lower()
            elif isinstance(v, str):
                v = '"' + v.replace("\\", "\\\\").replace('"', '\\"') + '"'
            lines.append(f"{k} = {v}")
    Path(path).write_text("\n".join(lines) + "\n", encoding="utf-8")


def _stamp_estimate(lines, seconds):
    """The new total in the file's estimate line, for the printer's UI."""
    h, r = divmod(int(round(seconds)), 3600)
    m, sec = divmod(r, 60)
    value = (f"{h}h " if h else "") + f"{m}m {sec}s"
    for pattern, new in ((r"^(; estimated printing time \(normal mode\) = ).*$", lambda mm: mm.group(1) + value),
                         (r"^;TIME:\d+", f";TIME:{int(round(seconds))}")):
        for k, ln in enumerate(lines):
            if re.match(pattern, ln):
                lines[k] = re.sub(pattern, new, ln)
                break


PLAN_WEIGHT = {False: 22.0, True: 41.0}   # relative cost of a banded / staggered plan, measured on a 4-colour file
WRITE_WEIGHT = 3.0                         # write + time one plan


def run(input_path, output_path, cfg, progress=None):
    """Process input_path into output_path. Returns a JSON-able summary. progress(fraction, stage) as it goes,
    the fraction weighted by what each stage costs on a 4-colour file."""
    report = progress or (lambda fraction, stage: None)
    nz, st, npl = cfg["nozzle"], cfg["stack"], cfg["nonplanar"]
    nozzle = Nozzle(nz["tip_diameter"] / 2, nz["cone_angle"], nz["clear_height"], nz["contact"])
    report(0.0, "Reading the file")
    prog = parse(input_path)
    ramped = 0
    if npl["smooth_tops"]:
        ramped = smooth_tops(prog, SmoothOptions(max_tread=npl["max_tread"], max_segment=npl["max_segment"],
                                                 resolution=st["resolution"], max_angle=npl["max_angle"],
                                                 min_area=npl["min_area"]), nozzle)
    lim = machine_limits(prog.settings, cfg["printer"])
    original = load(input_path)
    report(0.05, "Timing the original")
    o = estimate(original, lim)
    slicer = file_estimate(original)
    each, _ = swap_each(o, slicer, lim)
    co = cfg["cooling"]
    # plan every way asked for that can differ, keep the fastest (swaps included). Per-object units are the
    # tools themselves when no tool spans two objects; staggering gains nothing with at most two colours a layer.
    tooled = [p for L in prog.layers for p in L.paths if p.tool is not None]
    split_objects = len({(p.tool, p.obj) for p in tooled}) > len({p.tool for p in tooled})
    many_colours = any(len({p.tool for p in L.paths if p.tool is not None}) > 2 for L in prog.layers)
    variants = [(s, po) for s in ([False, True] if st["staggered"] and many_colours else [False])
                for po in ([False, True] if st["per_object"] and split_objects else [False])]
    total_w = sum(PLAN_WEIGHT[s] + WRITE_WEIGHT for s, _ in variants)
    at = 0.1
    best = None
    for staggered, per_object in variants:
        opts = Options(int(st["max_band_layers"]), st["surface_tolerance"], st["resolution"], st["min_piece"],
                       per_object)
        span = 0.87 * PLAN_WEIGHT[staggered] / total_w
        stage = "Planning staggered colours" if staggered else "Planning colour bands"

        def planned(f, a=at, s=span, name=stage):
            report(a + s * f, name)
        if staggered:
            bands, wipes = plan_staggered(prog, nozzle, opts, planned)
        else:
            bands = plan(prog, nozzle, opts, planned)
            wipes = compensate(prog, bands)
        at += span
        report(at, "Writing and timing")
        notes = [f"COLORSTACK bands={len(bands)} multi_tool_bands={sum(len(b.tools) > 1 for b in bands)} "
                 f"staggered={staggered}",
                 f"COLORSTACK ceded_mm={sum(b.ceded_mm for b in bands):.1f} "
                 f"visible_ceded_mm={sum(b.visible_ceded_mm for b in bands):.1f}",
                 f"COLORSTACK nozzle tip_diameter={nz['tip_diameter']} cone_angle={nz['cone_angle']} "
                 f"clear_height={nz['clear_height']} contact={nz['contact']}"]
        writer = Writer(prog, cfg["swap"]["lift"], co["stacked_fan"], co["lift_wait"], co["min_layer_time"],
                        co["tower_between"], nozzle if cfg["travel"]["local_lift"] else None)
        lines = writer.write(bands, notes)
        steps = []
        est = estimate(lines, lim, steps)
        at += 0.87 * WRITE_WEIGHT / total_w
        total = est["motion_s"] + est["dwell_s"] + est["swaps"] * each
        if best is None or total < best[0]:
            best = (total, est, steps, lines, per_object, staggered, bands, wipes, writer)
    report(0.97, "Saving")
    _, p, steps, lines, per_object, staggered, bands, (wipes_kept, wipes_gridded), writer = best
    anchor(o, p, slicer, lim)
    lines = stamp_progress(lines, steps, p["swap_each_s"], p["total_s"])
    _stamp_estimate(lines, p["total_s"])
    Path(output_path).write_text("\n".join(lines) + "\n", encoding="utf-8")
    return {
        "original": o,
        "processed": p,
        "bands": len(bands),
        "ceded_mm": sum(b.ceded_mm for b in bands),
        "visible_ceded_mm": sum(b.visible_ceded_mm for b in bands),
        "ramped_paths": ramped,
        "per_object": per_object,
        "staggered": staggered,
        "min_layer_time": writer.min_layer_time,
        "min_layer_source": writer.min_layer_source,
        "cooled_groups": writer.slowed,
        "cooled_short": writer.short,
        "fanned_paths": writer.fanned,
        "waits": writer.waited,
        "tower_wipes_kept": wipes_kept,
        "tower_wipes_to_grid": wipes_gridded,
        "output": str(output_path),
    }
