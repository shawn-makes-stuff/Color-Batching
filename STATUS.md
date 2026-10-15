# ColorStack status

## Results (contact 0.2; cube/Stitch cone 55) — all verified: 0 collisions, 0 mm outer-surface colour change
| file | swaps | time (slicer-anchored) |
|---|---|---|
| Anycubic cube (work/cube_34m_print_1h2m_flush.gcode, lift-and-wait on) | 57 -> 31 | 1h35m56s -> 1h06m34s |
| cube_sphere (non-planar tops, max_angle 22) | 91 -> 47 | 2h23m16s -> 1h32m44s |
| samples/cube1h43 (Orca, default contact) | 77 -> 41 | 1h43m19s -> 1h06m56s |
| samples/stitch | 318 -> 181 | 7h20m49s -> 5h02m50s |

First real print (Anycubic cube, Kobra 3 + ACE, 2026-10-16): 31 swaps as planned, no issues; took 1h29m vs the
1h02m estimate. The ACE swap takes ~2 min, the slicer counts 66 s. Decision: keep the file-based estimate, no
per-printer compensation.

## Open
- Nozzle not measured (placeholders: tip 1.0 mm, cone 45, clear height 2.0).
- Swap blocks written as moves (Bambu AMS, MMU) not replayed: needs sample files.
- Ideas: tower growing with swaps rather than layers; smooth (non-planar) colour boundaries.
- Rejected with data: purge-volume colour order (+2 swaps on Stitch), wider lines, adaptive layer heights.

## Testing
- Regression: run the four files above before and after a change and diff the outputs; `colorstack.verify` on
  each. Headless Edge screenshots need their own `--user-data-dir` (run via PowerShell `Start-Process`).
- 2026-10-16 cleanup fixed two old bugs found by the regression: G4 dwells were never counted in the estimate,
  and Klipper `SET_VELOCITY_LIMIT ACCEL=` was never read (accel not restated per feature in Klipper files).
