# ColorStack status

## Results (contact 0.2; cube/Stitch cone 55) — all verified: 0 collisions, 0 mm outer-surface colour change
| file | swaps | time (slicer-anchored) |
|---|---|---|
| Anycubic cube (work/cube_34m_print_1h2m_flush.gcode, lift-and-wait on) | 57 -> 31 | 1h35m56s -> 1h06m34s |
| cube_sphere (non-planar tops, max_angle 22) | 91 -> 47 | 2h23m16s -> 1h32m44s |
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
where slow_down_for_layer_cooling is on), cooling.tower_between (off; cube -31 s), travel.local_lift (off; cube
-41 s; lifts by the full cone reach at the tallest printed point, global top past clear_height). The pipeline now
picks between plans by total time (motion + swaps x per-swap cost), not motion alone. With staggered off and new
options off, all four regression outputs are byte-identical to before.

First real print (Anycubic cube, Kobra 3 + ACE, 2026-10-16): 31 swaps as planned, no issues; took 1h29m vs the
1h02m estimate. The ACE swap takes ~2 min, the slicer counts 66 s. Decision: keep the file-based estimate, no
per-printer compensation.

## Open
- Slicer post-processing (in-place script + popout window) stashed 2026-10-16 in stash/slicer-postprocess/
  (snapshots + NOTES.md): not shipped until it behaves consistently. The web app is the product for now.
- Nozzle not measured (placeholders: tip 1.0 mm, cone 45, clear height 2.0).
- Swap blocks written as moves (Bambu AMS, MMU) not replayed: needs sample files.
- Pinned 2026-10-16: wall edges overhanging a later colour. Stitch: 2835 mm of wall edge hangs over a colour not yet
  printed, ~98% by <= 0.17 mm (40% of the bead), ~5 mm beyond half the bead (worst 0.38 mm). The resting check only
  tests the line centre (supported by any earlier plastic); a fix would probe across the bead width like
  interface_mask. Cost unmeasured.
- Ideas: tower growing with swaps rather than layers; smooth (non-planar) colour boundaries.
- Rejected with data: purge-volume colour order (+2 swaps on Stitch), wider lines, adaptive layer heights.
  2026-10-16: exact DP over band boundaries = greedy (cube 41 = 41); purge into the incoming colour's infill
  absorbs only 6% of flush; DP colour order at the same swap count saves 4% flush on Stitch, 0 on cube.
- Measured levers (2026-10-16, contact 0.2): Stitch swaps by band colour count: 2 colours 73 swaps / 161 layers,
  3 colours 60 / 53, 4 colours 48 / 27. Z moves 4m03 -> 12m19 on Stitch (every travel under a taller stack lifts
  to the global top). Cooling slow-down adds ~6.5 min on Stitch, ~2 min on cube.

## Testing
- Regression: run the four files above before and after a change and diff the outputs; `colorstack.verify` on
  each. Headless Edge screenshots need their own `--user-data-dir` (run via PowerShell `Start-Process`).
- 2026-10-16 cleanup fixed two old bugs found by the regression: G4 dwells were never counted in the estimate,
  and Klipper `SET_VELOCITY_LIMIT ACCEL=` was never read (accel not restated per feature in Klipper files).
