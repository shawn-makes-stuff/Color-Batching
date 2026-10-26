"""Entry point for the CLI and the app: settings in, processed file + summary out."""
from pathlib import Path

from .estimate import anchor, estimate, file_estimate, machine_limits, swap_each
from .geometry import Nozzle
from .parse import parse
from .plan import Options, plan
from .reader import load
from .staggered import plan_staggered
from .summary import stamp_header, stamp_progress
from .tower import compensate
from .writer import Writer


PLAN_WEIGHT = {False: 22.0, True: 41.0}   # relative cost of a banded / staggered plan, measured on a 4-colour file
WRITE_WEIGHT = 3.0                         # write + time one plan


def run(input_path, output_path, cfg, progress=None):
    """Process input_path into output_path. Returns a JSON-able summary. progress(fraction, stage) as it goes,
    the fraction weighted by what each stage costs on a 4-colour file."""
    report = progress or (lambda fraction, stage: None)
    nz, st = cfg["nozzle"], cfg["stack"]
    nozzle = Nozzle(nz["tip_diameter"] / 2, nz["cone_angle"], nz["clear_height"], nz["contact"])
    report(0.0, "Reading the file")
    prog = parse(input_path)
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
        writer = Writer(prog, nozzle, cfg["swap"]["lift"], co["stacked_fan"], co["lift_wait"], co["min_layer_time"],
                        co["tower_between"], co["respect_min_layer_time"])
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
    stamp_header(lines, o, p)
    Path(output_path).write_text("\n".join(lines) + "\n", encoding="utf-8")
    return {
        "original": o,
        "processed": p,
        "bands": len(bands),
        "ceded_mm": sum(b.ceded_mm for b in bands),
        "visible_ceded_mm": sum(b.visible_ceded_mm for b in bands),
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
