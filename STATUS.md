# ColorStack status

## Results (contact 0.2; cube/Stitch cone 55) — all verified: 0 collisions, 0 mm outer-surface colour change
| file | swaps | time (slicer-anchored) |
|---|---|---|
| Anycubic cube (work/cube_34m_print_1h2m_flush.gcode, lift-and-wait on) | 57 -> 31 | 1h35m56s -> 1h06m34s |
| cube_sphere (with the since-removed non-planar tops, max_angle 22) | 91 -> 47 | 2h23m16s -> 1h32m44s |
| samples/cube1h43 (Orca, default contact) | 77 -> 41 | 1h43m19s -> 1h06m56s |
| samples/stitch | 318 -> 181 | 7h20m49s -> 5h02m50s |
| samples/stitch, stack.staggered=true (off by default since 3bac17d) | 318 -> 148 | 7h20m49s -> 4h31m32s |
| samples/stitch, staggered + local_lift + tower_between | 318 -> 148 | 7h20m49s -> 4h23m12s |

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
   shipped as stack.free_support, OFF by default, until found (scarf_grid has Default support too: 0 late-support).
14. Scarf seams (user's GUI export samples/compat/scarf_grid.gcode: seam_slope_type all, conditional off, grid
   support, 13053 Z-changing extrusion segments): Path.zs (Z per point, z = top, z0 / z1); spiral_mode refused
   explicitly; planner: per layer a KD tree of the later colour's ramp samples, checked with the ramp's deepest drop
   (on the ramp's own layer too); split() cuts zs; writer writes Z on ramp segments, travels to the ramp start.
   Result: 226 -> 187 swaps, 297 -> 256 min, 0 collisions, ramps kept (13068 segments). Planned as if ramps were
   flat: 117 swaps but 4248 collisions (the rule is needed). Ramp samples grouped by depth: no fewer swaps and 1
   collision, reverted. Reference files byte-identical.
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
