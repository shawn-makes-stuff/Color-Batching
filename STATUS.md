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
