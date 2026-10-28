# Color Batching status (repo shawn-makes-stuff/Color-Batching; Python package still colorstack/)

## Results (contact 0.2; cube/Stitch cone 55) — all verified: 0 collisions, 0 mm outer-surface colour change
| file | swaps | time (slicer-anchored) |
|---|---|---|
| Anycubic cube (work/cube_34m_print_1h2m_flush.gcode, lift-and-wait on) | 57 -> 35 | 1h35m56s -> 1h11m10s |
| cube_sphere (with the since-removed non-planar tops, max_angle 22; before the under rule) | 91 -> 47 | 2h23m16s -> 1h32m44s |
| samples/cube1h43 (Orca, default contact) | 77 -> 47 | 1h43m19s -> 1h12m41s |
| samples/stitch | 318 -> 229 | 7h20m49s -> 5h49m10s |
| samples/stitch, stack.staggered=true (off by default since 3bac17d) | 318 -> 196 | 7h20m49s -> 5h14m24s |
| samples/stitch, staggered + local_lift + tower_between (before the under rule) | 318 -> 148 | 7h20m49s -> 4h23m12s |

Staggered gives nothing on the 2-colour files (the band plan is kept). Stitch processing: 81 s before staggered,
129 s with it (KD trees cached by path set: 95% of builds were repeats; per-object pass skipped when no tool spans
two objects; staggered skipped when no layer has 3+ colours; outputs byte-identical). App progress: GET /api/progress,
bar + time left on Optimize; stages weighted by PLAN_WEIGHT / WRITE_WEIGHT (pipeline.py) and layers by colours²
(time left within ~10 s over the last minute on Stitch, optimistic in the first half). Not exact, so not done:
reusing candidates across runs (0 identical repeats measured) and trying the last good colour order first. verify on Stitch: 0 nozzle/travel collisions for all variants, but `late-support` 2 (bands) / 3
(staggered): a line straddling the seam of two colours' walls (0.21 mm from each) is printed before one of its two
supports. plan_band's resting check is strict `< width/2`, so a line exactly on the seam rests on neither and is not
handed over. Not fixed, on purpose: each spot is one 0.2 mm sample of a line resting half on each colour (a ~45°
overhang that long), and handing such visible seam lines to the later colour would move surface colour or add swaps.

2026-10-16 options: stack.staggered (off by default; on gives the Stitch rows above), cooling.min_layer_time (0 = file's slow_down_layer_time per filament,
where slow_down_for_layer_cooling is on), cooling.tower_between (off; cube -31 s), travel.local_lift (since 2026-10-16 always on, setting removed; cube
-41 s; lifts by the full cone reach at the tallest printed point, global top past clear_height). The pipeline now
picks between plans by total time (motion + swaps x per-swap cost), not motion alone. With staggered off and new
options off, all four regression outputs are byte-identical to before.

First real print (Anycubic cube, Kobra 3 + ACE, 2026-10-16): 31 swaps as planned, no issues; took 1h29m vs the
1h02m estimate. The ACE swap takes ~2 min, the slicer counts 66 s. Decision: keep the file-based estimate, no
per-printer compensation.

## Never print under another colour (2026-10-28, branch color-batching, Python and C++ byte-identical)
Strict verify check `overhead` (no contact allowance): no line may go down under earlier plastic overlapping it by
more than Orca's own neighbour overlap (h * (1 - pi/4), Flow.cpp) + grid step. Before: cube 245 spots, Stitch 4099.
plan_band hands a piece of earlier-colour line to the later colour T where it overlaps T's line below by more than
that + UNDER_TOL 0.02 (any height difference):
- lower layers in the band: `Samples.gaps`, exact segment-to-segment over the whole piece each sample stands for
  (between split()'s cuts), so nothing between samples or at cut points is missed (tools/test_gaps.py);
- scarf seams: a loop's end passes over its own ramp start; if the ramp piece is handed over, the end over it goes too
  (`_over_ramp`); a ramp handed to T on a layer also takes other paths' lines overlapping it (Orca's infill/wall
  overlap), the layer planned again until stable.
Cost: cube 41 -> 47 swaps, Stitch 181 -> 229, small 167 -> 187. Remaining verify flags, measured exactly
(scratchpad exact2.py): <= 0.021 mm past Orca's overlap (wall beside wall, stitch / flush_into), scarf ramp pieces
meeting end to end at a colour cut (0.011 mm higher, nothing above), and one Orca-own spot (also in the original).
Also: sequential printing (print_sequence by object / complete_objects) refused (parse, tools/test_guards.py).
Every output carries the comment `; Processed by ColorStack (<repo url>)` (parse.PROCESSED); parse refuses a file
that has it (tools/test_guards.py).
Full audit 2026-10-28 (19 files, vegeta/same skipped): all checks clean except overhead flags on flush_into 1,
stitch 2, stitch_stg 3 (all <= 0.015 mm, wall beside wall) and scarf_grid 31 (as above).
Prime tower (2026-10-28): Orca sizes each wipe block by its pair's flush only with purge_in_prime_tower +
single_extruder_multi_material on a Type 2 tower (else every block is prime_volume and reusing blocks is safe; Type 1
= Bambu, flush via {flush_length_N}, swap blocks not handled yet). Then the bands share one running tower
(tower.RunTower): each swap takes the wipe lines in order, up the layers, until it has its own pair's flush, the tower
at most clear_height above the band's top; the footprint is never changed. Test file samples/compat/v/tower_purge
(make_variants): before 115 of 187 kept swaps under Orca's own purge level (0.93 of the flush), now 13 (banded) / 0
(staggered); outputs of other files byte-identical. Scratchpad checks: purge_cmp.py, tower_lead.py.
Open (noted, user 2026-10-28): those 13 swaps are at z 23-28 where the planner makes 3 swaps per 1-layer band and
Orca's tower holds ~2 per layer; on a tower-only printer (tool change without {purge}) they under-purge. Reported as
tower_purge_short (CLI line). Letting the tower run further ahead (10.6 mm, 0 collisions) needs real nozzle numbers.
Not tested: wipe_tower_no_sparse_layers (Orca would not slice the variant).
Exe icon: cpp/app/colorstack.ico (from viewer/icon.png) via cpp/app/icon.rc.
Defaults (2026-10-28, after 0.1.0 shipped contact 0.0 = almost no batching: cube 77->75, Stitch 318->317):
contact 0.2 (all measured results + the first real print) and staggered on (Stitch 229->196 swaps, -35 min, +5 s
processing; Vegeta 958->822, -2h27m, processing 50 s -> 440 s; staggered output not checked on Vegeta). Swept with
no gain: cone 30/55, tip 0.8/1.5, clear 3 (inert at contact 0.2; clear 1 costs 4 swaps on cube), min_piece 1/4,
resolution 0.05/0.2, max_band_layers 4. Cooling defaults kept (measured in 8fdabaf). Next lever: contact 0.4 (cube
47->34, Stitch 229->204) needs a print test.
Staggered planner can get stuck (scarf_grid layer 50: a colour run ahead printed a wall a later colour's line would go
under); the pipeline now drops a failed staggered plan and keeps the banded one. Rename 2026-10-28: user-visible
"Color Batching", output .color-batching.gcode, settings color-batching.toml (0.1.0's colorstack.toml still used
when it is the only one), marker "Processed by Color Batching (...)"; the 0.1.0 marker is still refused.

## Non-planar: removed (2026-10-16)
The user dropped all non-planar features (smooth tops, colour joins, undersides on support): no visible gain, out of
scope. Code, numbers and the research are parked in stash/nonplanar/ (NOTES.md). Do not re-add unless asked.

## Orca 2.5.0-alpha plugins (researched 2026-10-16 from source at tag v2.5.0-alpha, 3880f2c)
- Fit: SlicingPipeline capability at Step.psGCodePostProcess gets ctx.gcode_path (edit in place), ctx.host,
  ctx.output_name, ctx.config_value(); runs on the slicing worker thread, no orca.host.ui calls there (deadlock
  warning in SlicingPipelinePluginCapability.hpp). Template: sandboxes/orca_gcode_stamp_plugin_any.py. Deps via
  PEP 723 header, installed by bundled uv; bundled Python 3.10-3.13 (tomllib needs 3.11).
- In-slicer steps (posSlice..psSkirtBrim) only edit geometry within a layer (print_z read-only, no tool order):
  ColorStack cannot run before G-code generation.
- Preview: BackgroundSlicingProcess.cpp ~l.286 post-processes the slice-time file and calls reload_gcode_moves ONLY
  for is_BBL_printer(); other printers post-process a copy at export, preview stays unprocessed.
  Print::reload_gcode_moves (Print.cpp ~l.5579) re-reads only moves + line ends, not print_statistics (times stay
  pre-processing). Orca opens a .gcode passed on its command line (or to the running instance) in its preview
  (GUI_App load_gcode). Lifecycle events include GCodeExportFinished / SlicingJobComplete (LifecycleEvents.hpp).
- Probe on the real 2.5.0-alpha (2026-10-16, orca/colorstack_probe.py, installed in %APPDATA%\OrcaSlicer\
  orca_plugins\colorstack_probe, results in probe.jsonl; orca/watch_probe.py streams them): Python 3.12.13 inside
  orca-slicer.exe, numpy 2.5.4 + scipy 1.18.1 installed by uv from the PEP 723 header. Plugin tab = WebView2
  Chromium 107, about:blank: WebGL2 yes (RTX 4070), CDN fetch + ES module import yes, localStorage no,
  showSaveFilePicker no, <a download> lands in Downloads. Every slice (non-BBL Cosmos printer too) fires
  GCodeExportStarted/Finished on the slicing thread with ctx.msg = Temp/orcaslicer_model/.../Metadata/.<pid>.<n>.gcode,
  then SlicingJobComplete on the main thread. Reading that file: one permission prompt, then allowed (a "[AUDIT]
  block" log line still appears each time). 2.3 MB to the page in 3 x 1 MB post_message chunks: 15 ms. threading
  works; urllib GET to Moonraker works after a prompt. Selected printer preset exposes print_host / host_type /
  printhost_apikey (physical-printer list not exposed); Plater has no current_plate_index. Plugin tabs are always
  appended after the built-in tabs.
- Must-guard features: IDEX/IQEX parallel + multi-nozzle (tool changes are nozzles, not swaps), belt_printer,
  colour mixing with enable_mixed_color_sublayer (sub-layers at different Z inside one layer; whole-layer mixing is
  plain multicolour). Prime tower changed in 2.5: re-check the CP TOOLCHANGE / tower markers on a 2.5 file.

## Improvement sheet (2026-10-26, branch refine-compat; Rusti3D method: baseline, hypothesis, measure)
C++ PORT (branch color-batching, plan in docs/CPP_PLAN.md; build: cmake -S cpp -B cpp/build -G "Visual Studio 17 2022"
-A x64 && cmake --build cpp/build --config Release; MSVC /fp:precise = CPython's C runtime on Windows):
- step 1 reader: identical events on 11 files (tools/dump_events.py vs cpp/build/Release/dump_events.exe).
- step 2 parser: identical program dumps on 13 files (tools/dump_program.py vs dump_program.exe).
- step 3 writer: identical G-code for the same Python plan on 14 files (tools/dump_bands.py writes the plan + the
  Python output, write_bands.exe writes the plan with the C++ writer).
- step 3 also identical on Vegeta (both variants, 3.0M lines; C++ parse + read plan + write 12 s).
- step 4 estimate + summary: tools/dump_finish.py vs finish.exe (estimate both files, anchor, M73 + header) -
  identical figures and finished files on 9 files. The Python pipeline now writes LF (was CR LF on Windows by text
  mode; outputs otherwise byte-identical).
- step 5 planner (KD tree matching scipy, Samples, split, interface mask, plan, tower.compensate): identical bands
  and G-code on 15 runs (v/*, cube, doorbell, scarf_grid, stitch, per_object cube + stitch, Vegeta) via
  tools/dump_bands.py vs plan_bands.exe. Planning alone: small 15.1 -> 1.6 s, same 66.4 -> 13.4 s (machine loaded).
- steps 6-8 written in one go, then one build (0 warnings) and one test pass (2026-10-28), all identical:
  - staggered + RunTower: bands and G-code identical on small, filaments, cube, stitch (dump_bands.py staggered).
  - CLI (cpp/build/Release/color-batching.exe, same options as python -m colorstack): output file AND printed
    summary identical on 17 runs: v/*, cube, doorbell, scarf_grid, stitch, stitch staggered, cube no per-object,
    --set overrides, Vegeta. Whole run: Vegeta 197 -> 36 s, stitch 26 -> 4 s, v/* ~50 -> ~5 s.
  - app (color-batching-app.exe, same HTTP API as colorstack/app.py; opens the default browser): GET pages /
    config / state / progress / icons / 404s equal to the Python app; POST config saves the same TOML as
    save_config (quirks included); upload + process + download = Python pipeline (summary + file); cancel ends
    both variant threads in 0.8 s (409); busy 409; Send to printer checked against a fake Moonraker (fields, file
    bytes, start on/off, errors); preload argument + URL quoting as Python. Tested in a scratch root on ports
    8791-8794, the real colorstack.toml / work/ untouched.
  - CI: .github/workflows/cpp.yml builds Windows / macOS / Linux, artifacts with viewer/. NOT run yet (needs a
    push). Risk: macOS libc++ may lack std::from_chars for double (used in reader/parse/json) - fix if CI fails.
- left: native webview window (app opens the browser for now); http only for Moonraker (no TLS); TOML reader
  handles the file's own subset; Python int() underscores not taken in filament_soluble lists.
- exactness notes: math.hypot/dist = CPython vector_norm (cpp/core/pymath.hpp); numpy hypot = the C library's;
  Python dict insertion order kept where it matters (fans, states, swap blocks); round() via to_chars/from_chars.

RESUME HERE (2026-10-27). Branch refine-compat (pushed 2026-10-26 up to fix 1). Done so far:
1. Modules reorganized (model/parse/geometry/plan/staggered/writer/config/summary); outputs byte-identical.
2. Tools: `tools/regress.py LABEL [files|--variants]` + `--diff A B`; `verify --fidelity` (state per mm + travels).
   Test files (gitignored): samples/compat/ (user's 3MF, vegeta.gcode export), variants by
   tools/make_variants.py (tools/variant.py edits the 3MF and slices with the Orca 2.5 CLI; keys must be
   listed in different_settings_to_system, objects dropped via printable=0). Results in work/regress/<label>/.
3. FIX 1 done: machine-state commands (PA, M220/M221, M205, M572, M566) stay with their paths; the incoming
   filament's PA right after each swap; handed-over pieces take the printing filament's state per feature; tower
   keeps the loaded PA. Measured (fidelity, PA on kept lines): filaments variant 255 m -> 0, adaptive_pa 310 m -> 0.
   Reference files: swaps/times unchanged; cube/support byte-identical, others differ only by M220 placement.
4. FIX 2 done: swap blocks per tool (filament end G-code before T for the tool left, start G-code after T for the
   tool entered; first swap of each in the original). swap_blocks variant (M118 markers): wrong blocks 167 -> 0.
5. FIX 3 done: firmware retraction (G10/G11 are retracts in the parser, the writer writes G10/G11 in such files,
   not G1 E): fw_retract variant G1 E retracts 564 -> 0, G10/G11 balanced. Found with it: after every swap the old
   writer never primed back (Orca: G1 E-.8 before T, G1 E.8 after); measured 0.8 mm x swaps left retracted (Stitch
   145.6 mm). Now the next travel unretracts: left retracted at the end = the original's 0.80.
6. FIX 4 done: a handed-over piece takes the printing filament's flow ratio and filament area for its E, and is slowed
   to that filament's max volumetric speed. filaments variant: handed lines at the wrong flow 166 mm -> 0, over the
   printing filament's volumetric limit 70.5 mm -> 0.
8. FIX 5 done: each path keeps the slicer's way into it (retract, wipe, lift, travel route, G10/G11); the writer replays
   it when the path follows the same line as in the slicer, with the same tool and state, and no taller plastic is
   within reach of the route; else it regenerates. Restart extra (retract_restart_extra) added to regenerated primes.
   Same-order starts arriving by another route: travel variant 3025 -> 242, Stitch 867 -> 150; the rest lift over
   taller stacked plastic. Cost: Anycubic cube 62.9 -> 64.1 min, Stitch 292.0 -> 293.1 (user: worth it).
   Full report: work/regress/f5.check (tools/check.py f5 --collisions / f5-v): 0 collisions (Stitch: the 2 known
   late-support spots), PA / swap blocks / E balance all as the original.
   Left: per-filament retraction on regenerated travels (filaments variant: T0 0.5 mm, writer uses 0.8); E written
   with 5 decimals gives ~1% flow error on tiny arc segments (scarf_arcs 948 mm, old) - write 6.
9. FIX 6 done: per-filament retraction overrides (Orca filament_extruder_override_keys, "nil" = printer's) for the
   writer's own travels; after a swap the filament is primed back by the amount retracted (Orca does the same).
   filaments variant: left retracted 0.50 -> 0.50 (= original; was 0.80). The ~1% flow error on arc chords is a
   checker artifact (3-decimal X/Y on 0.05 mm chords), not a print defect; keeping G2/G3 would remove it.
10. Printer guards: refuse more than one nozzle (nozzle_diameter count; covers multi-nozzle heads, IDEX/parallel,
   toolchangers: Orca does not tell them apart in the file, is_imex is kept out of the G-code) and belt_printer=1.
   tools/test_guards.py. Toolchangers can be allowed once a real toolchanger file is tested.
11. Cooling: verify.cooling_fidelity (per spot, time before the next layer vs the original, below the filament's
   minimum layer time = worse). tools/cooling_options.py compared the options; new defaults
   respect_min_layer_time on (outer walls keep their sliced speed: 0 mm changed) + tower_between on; lift_wait off
   (little gain, ooze). Cooled worse: cube1h43 2.8% -> 0.3%, Anycubic cube 9.8% -> 3.1%, variants 0.1-0.4%, Stitch
   1.7%. Cost: cubes ~+2.5 min, Stitch 293 -> 299 min. Rest: spot order within a layer (Orca guarantees layer time,
   not per-spot time). Fan speed-up timing: measured, no defect (no kept line gets a lower fan).
   Report: work/regress/f7.check.txt.
7. tools/check.py LABEL [--collisions]: one row per output (kept-line state diffs, wrong swap blocks, E balance,
   arrivals = how each extrusion start is reached vs the original, same-order starts only).
Baselines: base (5 reference files), base-v (variants), work/baseline/vegeta.* (1176 -> 1130 swaps, 325 s: low gain,
look at it with the island items below). Scarf variant: Orca CLI wrote no Z ramps even with seam_slope_type=all,
conditional=0 - get a GUI-exported scarf file from the user.
12. Vegeta's low gain (1176 -> 1130): 742 band rejections were support refused to another filament. Vegeta's support
   is Orca "Default" (support_filament 0 = "current filament is used", PrintConfig tooltip): printed by all 4
   filaments. Program.free_support = (object, type) of support the slicer printed with more than one filament; only
   that may be handed over (support always printed with one filament keeps it: Support test, TerrorCube).
   Vegeta 1176 -> 888 swaps (was 1130), 29h46 -> 24h40. Reference files byte-identical. Quality report: clean except
   24 late-support spots (work/regress/s1) - being compared with the same code without free support (s0).
13. Island order: tried, parked in stash/islands/ (NOTES.md, islands.patch): cube1h43 41 -> 51 swaps, 12x slower.
   What stops Vegeta's bands now: visible outer walls only (762 resting on the later colour, 246 nozzle hits);
   ceiling with every resting case solved 570 swaps, but at 2-layer bands a split colour costs as many swaps.
   s0 (same code, support kept): 1130 swaps, 4 late-support; s1 (free): 888, 24. So free support adds 20 spots:
   shipped as stack.free_support. The extra spots are not support: outer walls at a seam between two colours,
   resting half on each, printed before the lower colour (the known seam-straddle case, see Results); longer bands
   make it more frequent (4 -> 24 samples of 0.2 mm, ~5 mm in a 29 h print, for 242 fewer swaps). User
   2026-10-27: setting, default ON.
14. Scarf seams (user's GUI export samples/compat/scarf_grid.gcode: seam_slope_type all, conditional off, grid
   support, 13053 Z-changing extrusion segments): Path.zs (Z per point, z = top, z0 / z1); spiral_mode refused
   explicitly; planner: per layer a KD tree of the later colour's ramp samples, checked with the ramp's deepest drop
   (on the ramp's own layer too); split() cuts zs; writer writes Z on ramp segments, travels to the ramp start.
   Result (free support on): 226 -> 187 swaps, 297 -> 256 min, 0 collisions, ramps kept (13068 segments). With the
   default (free support off): 226 -> 217, 0 collisions - its Default grid support blocks the bands. Planned as if ramps were
   flat: 117 swaps but 4248 collisions (the rule is needed). Ramp samples grouped by depth: no fewer swaps and 1
   collision, reverted. Reference files byte-identical.
15. Materials (user, 2026-10-27: "things that wouldn't stick together or don't make sense should not be combined";
   support colour does not matter; interface keeps its filament only when one is chosen): nothing is handed between
   filaments unless Orca's filament_type base (suffix dropped: PLA-CF = PLA; PETG and PCTG differ), filament_soluble
   and filament_is_support all match (pipeline.materials). Free support covers base and interface printed with
   Default. tools/test_materials.py: Stitch with T1 as PETG, 0 segments moved between PETG and PLA (191 swaps vs 181).
   All-PLA files byte-identical.
16. 2026-10-27 fixes: (a) single-colour files were slowed again by the cooling rule (USB-C holder 32.0 -> 35.0
   min): a region taking as long as its original layer keeps the slicer's speeds and fans, and the target is never
   more than the original layer time -> 32.0 -> 32.0. (b) Travel / wipe accelerations between paths (PrusaSlicer
   M204 S15000, Orca SET_VELOCITY_LIMIT ACCEL) are part of the lead-in: replayed, and regenerated travels use the
   slicer's travel acceleration (USB motion 1913.5 -> 1870.1 s vs the original's 1870.0). (c) Nozzle guard:
   single_extruder_multi_material = 1 is one nozzle even when PrusaSlicer lists nozzle_diameter per filament.
   (d) Layer markers at the same Z are one physical layer, layers in Z order (files batched by the slicer itself,
   e.g. the user's PiggieSlicer / PrusaSlicer 3.0 clustering: ;Z goes back down, ;HEIGHT 0 / -0.16); that cube had
   103 late-support spots, now 0 collisions; Orca files byte-identical. (e) verify --fidelity also compares
   square corner velocity and jerk (no test file sets them yet: add a jerk variant).
   Known PrusaSlicer gap (Prusa support later): its retraction keys (retract_length, retract_speed, retract_lift,
   retract_before_travel, ...) are not read, so regenerated travels use the most common retract seen in the file.
   User: ignore the PiggieSlicer files (they carry the user's own clustering).
Priorities (user, 2026-10-27): Marlin low; Bambu AMS swaps later (large user base, must do), then Prusa MMU.
NEXT: Vegeta's 20 extra late-support with free support (find the spots: verify.check on work/regress/s1); speed
pass on large files (planning now the largest stage); island order needs a rethink (stash/islands).

Compatibility / never worse quality (found in the audit, Orca 2.3.2 source + the user's files):
- PA: Orca's per-filament SET_PRESSURE_ADVANCE after each swap (after `; CP TOOLCHANGE END`) is hoisted to the layer
  start; adaptive PA per feature is hoisted too. Tool change extras are the first swap's for every swap.
- Every mid-layer state command (M220/M221, M205 jerk, M572, G10/G11 firmware retraction) is hoisted to the layer start.
- Handed-over pieces keep the original filament's flow ratio / diameter E, speed (max volumetric speed of the new
  filament not checked), PA and retraction (temperature and fan already follow the printing tool).
- Travels are all regenerated: avoid-crossing-walls routes, slope/spiral lift, wipe on loops, restart extra lost.
  Retraction / hop from filament 0 only.
- Fan speed-up time (Orca FanMover): a reordered overhang gets its fan late.
- Cooling: stacked regions either uncooled (default) or outer walls slowed (speed-contrast lines);
  dont_slow_down_outer_wall ignored.
- Scarf seams (Z-changing extrusion) refused.
- Measuring tool: `colorstack.verify --fidelity` (per extruded mm: tool, type, speed, flow, PA, fans, temperature,
  accel, M220/M221 against the original; extrusion starts per feature).
Target (user, 2026-10-27): a native OrcaSlicer slicing mode (not a plugin), maybe an upstream PR; this Python
version is the reference implementation and test bench. Follow the slicer's settings even at a small time cost.
Printer types to check (user, 2026-10-27), each with an Orca CLI variant from a system printer profile:
- single nozzle + filament changer (AMS / ACE / MMU): what we test today;
- toolchangers (e.g. Prusa XL, Snapmaker U1): a tool change is seconds, no purge; parked tools are off the bed;
- IDEX (e.g. Sovol SV04, Snapmaker J1) and copy/mirror modes: second carriage parked or printing in parallel;
- multi-nozzle on one carriage (Bambu H2D, filament_map): REFUSE (user, 2026-10-27: too hard to account for the
  idle nozzle riding next to the active one).
- toolchangers / multiple toolheads: allow (little time saved, but no harm) once the file is handled correctly.
- belt printers (new in recent Orca): layers are tilted in machine coordinates, the vertical-cone nozzle model does
  not hold; refuse unless modelled. Detection keys to be read from Orca's source, not guessed.
For each: what a swap costs (the gain), whether the nozzle model holds, and what the file's tool change looks like.
Speed (user, 2026-10-27: must be quick on large many-colour models, without losing quality). Rule: every speed
change proves byte-identical output (tools/regress.py --diff) unless it is a measured, deliberate trade-off.
Profile, Stitch (153 s, machine busy): densify 50 s (per-segment numpy calls), estimate 37 s (reader regexes 22 s,
motion model 13 s), writer 41 s (height map 30 s = densify again), KD queries 22 s. Plan, in order:
1. DONE densify vectorised per path: bit-identical samples; Stitch 58.2 -> 43.4 s (same machine, old vs new).
2. DONE (small) reader / settings: cheap prefix tests before the layer and setting regexes (read 1.97 -> 1.67 s).
3. DONE plan variants (per-object on/off, staggered) in parallel processes; the original timed meanwhile; falls back
   to one after another if worker processes cannot start (embedded hosts). Outputs byte-identical (regress p1 vs p2,
   CLI, threaded app path, forced fallback). Stitch 43 -> 24 s, Vegeta 184 -> 114 s (325 s this morning).
5. 2026-10-27, measured and NOT kept (each identical output, no gain): skip KD queries by bounding box (trees span the
   whole layer, boxes always overlap: 143 -> 141 s), tighter query radius (138 s, noise), hand-written word parser +
   precomputed move length (reader 7.6 -> 9.1 s, slower: the C regex wins). Kept: per-band answer cache
   (b15461f). Vegeta per variant (machine loaded): plan ~50 s, write ~41 s, estimate ~30 s, send back 3 s; parse
   20 s. What is left is spread out; big further gains need native code (the Orca C++ integration).
4. Next hotspots (tools/stage_times.py, profile): planning (KD queries ~15 s profiled on Stitch), estimate of
   each output (reader + motion model), parse 16 s on Vegeta.
Planning (from the user, 2026-10-26):
- Island print order: colour 1 has islands with an overhang that colour 2 would have to print under (collision),
  same with colour 1 and 2 islands that both overhang. Optimise the order of these islands.
- Per-island printing: print one island's whole colour stack, then travel to the next island's stack (print the
  block before travelling), like per-object batching but per connected island.

## Open
- Diagonal cube print (2026-10-16): faint lines at every colour change = speed contrast. With respect_min_layer_time
  on, stacked layers' outer walls ran ~31 mm/s, the layers at each swap at the sliced 130 mm/s (seen in the viewer's
  Speed mode). Now off by default. Open fix: keep outer walls at speed (slow the rest), honour the file's
  dont_slow_down_outer_wall (currently ignored).
- Slicer post-processing (in-place script + popout window) stashed 2026-10-16 in stash/slicer-postprocess/
  (snapshots + NOTES.md): not shipped until it behaves consistently. The web app is the product for now.
- Nozzle not measured (placeholders: tip 1.0 mm, cone 45, clear height 2.0).
- Swap blocks written as moves (Bambu AMS, MMU) not replayed: needs sample files.
- Pinned 2026-10-16: wall edges overhanging a later colour. Stitch: 2835 mm of wall edge hangs over a colour not yet
  printed, ~98% by <= 0.17 mm (40% of the bead), ~5 mm beyond half the bead (worst 0.38 mm). The resting check only
  tests the line centre (supported by any earlier plastic); a fix would probe across the bead width like
  interface_mask. Cost unmeasured.
- Ideas: tower growing with swaps rather than layers.
- Rejected with data: purge-volume colour order (+2 swaps on Stitch), wider lines, adaptive layer heights.
  2026-10-16: exact DP over band boundaries = greedy (cube 41 = 41); purge into the incoming colour's infill
  absorbs only 6% of flush; DP colour order at the same swap count saves 4% flush on Stitch, 0 on cube.
- Measured levers (2026-10-16, contact 0.2): Stitch swaps by band colour count: 2 colours 73 swaps / 161 layers,
  3 colours 60 / 53, 4 colours 48 / 27. Z moves 4m03 -> 12m19 on Stitch (every travel under a taller stack lifts
  to the global top). Cooling slow-down adds ~6.5 min on Stitch, ~2 min on cube.

## Testing
- Issue #1 (2026-10-17, Orca normal/grid supports, independent support layer height): ;HEIGHT is now kept per path
  (Orca writes the Z gap after ;Z: and the line height after ;TYPE:; only the gap was kept, so 0.28 mm support
  layers showed as thin lines); support is never handed to another tool (band shortened instead). Support test:
  12 -> 6 swaps (4 before, with base support printed in the interface colour). Other regression files: only
  ;HEIGHT lines differ (Stitch bridges now 0.4 as sliced).
- Regression: run the four files above before and after a change and diff the outputs; `colorstack.verify` on
  each. Headless Edge screenshots need their own `--user-data-dir` (run via PowerShell `Start-Process`).
- Review 2026-10-16 fixed: nozzle temperature is now the loaded tool's temperature for the layer being printed
  (Program.temps from the original; first layer from nozzle_temperature_initial_layer, since start G-code may set it
  by macro) — before, a layer's M104 fired when the first colour reached it, so the Anycubic cube's first layer
  printed at 210 instead of 220; bed/chamber changes wait until all lower layers are printed; extra
  SET_VELOCITY_LIMIT parameters (ACCEL_TO_DECEL ...) kept; Z-changing extrusion (vase) refused instead of dropped;
  no double retract at the end. Second round: pause / filament change layers (gcode.BARRIERS) are barriers — no band
  or run crosses them and the command waits until all lower layers are printed (old code: a pause at cube layer 42/44
  left 24-32 mm of the layer below for after the pause; test: insert PAUSE after a ;Z: line, check material on the
  wrong side); a handed-over piece takes the printing tool's fan for that layer and type (Program.fans; Stitch's T1
  fans at 80%); compensate() wipes the first colour of a band that starts with a swap; layer commands and M104 are
  written outside EXCLUDE_OBJECT blocks. Regression: swaps and times unchanged, only object markers and ~24 Stitch fan
  lines differ. First round: only temperature lines differed from the earlier outputs (checked default and
  staggered + tower_between); temperature check script: every model move at the expected target.
- 2026-10-16 cleanup fixed two old bugs found by the regression: G4 dwells were never counted in the estimate,
  and Klipper `SET_VELOCITY_LIMIT ACCEL=` was never read (accel not restated per feature in Klipper files).
