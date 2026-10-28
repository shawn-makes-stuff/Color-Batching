"""Entry point for the CLI and the app: settings in, processed file + summary out."""
from concurrent.futures import FIRST_COMPLETED, ProcessPoolExecutor, wait
from concurrent.futures.process import BrokenProcessPool
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


def materials(prog):
    """Per tool: (base material, soluble, support material). Free support moves only between filaments where all three
    match, so nothing goes where it would not bond like the original: the base material is Orca's filament_type
    without its suffix (PLA, PLA-CF and PLA-AERO are PLA; PETG and PCTG differ), and soluble (PVA, BVOH) or support
    filaments never mix with model filament (filament_soluble, filament_is_support)."""
    types = [t.strip().upper().split("-")[0] for t in prog.settings.get("filament_type", "").split(";")]
    soluble = prog.setting_list("filament_soluble", int, [0])
    support = prog.setting_list("filament_is_support", int, [0])

    def at(values, k):
        return values[min(k, len(values) - 1)]
    return tuple((t, at(soluble, k), at(support, k)) for k, t in enumerate(types))


class Cancelled(Exception):
    """run() was asked to stop (the app's page was refreshed or closed mid-run)."""


def _stop(pool):
    """End a pool's workers now: shutdown() alone lets running variants finish."""
    # ProcessPoolExecutor has no public kill; _processes holds the worker processes (CPython 3.8-3.13)
    for proc in list(getattr(pool, "_processes", {}).values()):
        proc.terminate()
    pool.shutdown(wait=False, cancel_futures=True)


def _progress_span(report, a, b):
    """A progress callback for one variant, mapped onto [a, b] of the whole run."""
    def planned(f, stage):
        report(a + (b - a) * f, stage)
    return planned


def _variant(prog, nozzle, cfg, lim, staggered, per_object, progress=None):
    """Plan, write and time one way of printing prog (runs in a worker process when there are several)."""
    report = progress or (lambda fraction, stage: None)
    st, co, nz = cfg["stack"], cfg["cooling"], cfg["nozzle"]
    opts = Options(int(st["max_band_layers"]), st["surface_tolerance"], st["resolution"], st["min_piece"],
                   per_object, prog.free_support if st["free_support"] else frozenset(),
                   materials(prog))
    split = PLAN_WEIGHT[staggered] / (PLAN_WEIGHT[staggered] + WRITE_WEIGHT)
    stage = "Planning staggered colours" if staggered else "Planning colour bands"
    if staggered:
        bands, wipes = plan_staggered(prog, nozzle, opts, lambda f: report(split * f, stage))
    else:
        bands = plan(prog, nozzle, opts, lambda f: report(split * f, stage))
        wipes = compensate(prog, bands)
    report(split, "Writing and timing")
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
    report(1.0, "Writing and timing")
    return {"est": est, "steps": steps, "lines": lines, "summary": {
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
        "tower_wipes_kept": wipes[0],
        "tower_wipes_to_grid": wipes[1],
    }}


def run(input_path, output_path, cfg, progress=None, cancelled=None):
    """Process input_path into output_path. Returns a JSON-able summary. progress(fraction, stage) as it goes,
    the fraction weighted by what each stage costs on a 4-colour file. cancelled(): true to stop; checked at every
    progress step, and every 0.3 s while worker processes plan (they are ended); raises Cancelled."""
    def report(fraction, stage):
        if cancelled and cancelled():
            raise Cancelled()
        if progress:
            progress(fraction, stage)
    nz, st = cfg["nozzle"], cfg["stack"]
    nozzle = Nozzle(nz["tip_diameter"] / 2, nz["cone_angle"], nz["clear_height"], nz["contact"])
    report(0.0, "Reading the file")
    prog = parse(input_path)
    lim = machine_limits(prog.settings, cfg["printer"])
    original = load(input_path)
    # plan every way asked for that can differ, keep the fastest (swaps included). Per-object units are the
    # tools themselves when no tool spans two objects; staggering gains nothing with at most two colours a layer.
    tooled = [p for L in prog.layers for p in L.paths if p.tool is not None]
    split_objects = len({(p.tool, p.obj) for p in tooled}) > len({p.tool for p in tooled})
    many_colours = any(len({p.tool for p in L.paths if p.tool is not None}) > 2 for L in prog.layers)
    variants = [(s, po) for s in ([False, True] if st["staggered"] and many_colours else [False])
                for po in ([False, True] if st["per_object"] and split_objects else [False])]
    if len(variants) == 1:
        report(0.05, "Timing the original")
        o = estimate(original, lim)
        results = [_variant(prog, nozzle, cfg, lim, *variants[0], progress=_progress_span(report, 0.1, 0.97))]
    else:
        # the variants do not depend on each other: one process each, the original timed meanwhile
        report(0.05, "Planning colour bands")
        try:
            with ProcessPoolExecutor(max_workers=len(variants)) as pool:
                jobs = [pool.submit(_variant, prog, nozzle, cfg, lim, *v) for v in variants]
                o = estimate(original, lim)
                pending = set(jobs)
                while pending:
                    _, pending = wait(pending, timeout=0.3, return_when=FIRST_COMPLETED)
                    if cancelled and cancelled():
                        _stop(pool)
                        raise Cancelled()
                    report(0.1 + 0.87 * (len(jobs) - len(pending)) / len(jobs), "Planning colour bands")
                results = [j.result() for j in jobs]
        except BrokenProcessPool:       # a host that cannot start worker processes: one after another
            o = estimate(original, lim)
            results = [_variant(prog, nozzle, cfg, lim, *v, progress=_progress_span(
                report, 0.1 + 0.87 * k / len(variants), 0.1 + 0.87 * (k + 1) / len(variants)))
                for k, v in enumerate(variants)]
    slicer = file_estimate(original)
    each, _ = swap_each(o, slicer, lim)
    best = None
    for r in results:          # in variant order: the first of equally fast plans wins, as before
        total = r["est"]["motion_s"] + r["est"]["dwell_s"] + r["est"]["swaps"] * each
        if best is None or total < best[0]:
            best = (total, r)
    report(0.97, "Saving")
    r = best[1]
    p, steps, lines = r["est"], r["steps"], r["lines"]
    anchor(o, p, slicer, lim)
    lines = stamp_progress(lines, steps, p["swap_each_s"], p["total_s"])
    stamp_header(lines, o, p)
    # LF, as the slicers write G-code (text mode would turn it into CR LF on Windows)
    Path(output_path).write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    return {
        "original": o,
        "processed": p,
        **r["summary"],
        "output": str(output_path),
    }
