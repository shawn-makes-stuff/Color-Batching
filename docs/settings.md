# ColorStack settings

Set them in the app's side panel (Save defaults writes `colorstack.toml`), by editing `colorstack.toml`, or per
run with `python -m colorstack in.gcode --set section.key=value`.

| Setting | Default | Meaning |
|---|---|---|
| `nozzle.tip_diameter` | 1.0 mm | Flat face at the nozzle tip. |
| `nozzle.cone_angle` | 45° | Cone half-angle from vertical. |
| `nozzle.clear_height` | 2.0 mm | How far the tip sticks out below the heater block / sock / duct. |
| `nozzle.contact` | 0.0 mm | How much taller a neighbouring edge may be while the nozzle face slides past. 0.2 = two layers per colour, 0.4 = three. |
| `stack.max_band_layers` | 10 | Most layers one colour prints before the next swap. |
| `stack.surface_tolerance` | 0.0 mm | How far a colour edge on a visible surface may move. 0 = never. |
| `stack.min_piece` | 2.0 mm | Shorter leftovers between handed-over pieces are handed over too. |
| `stack.per_object` | on | Also plan each object on its own; the faster plan is kept. |
| `stack.staggered` | off | Also plan each colour on its own schedule (fewer swaps where 3+ colours share layers); the faster plan is kept. Slower processing. |
| `stack.resolution` | 0.1 mm | Sampling step for the collision planning. |
| `nonplanar.smooth_tops` | off | Replace stair-stepped shallow tops with one continuous skin that follows the surface. |
| `nonplanar.max_angle` | 15° | Steepest surface printed non-planar (also capped by the nozzle). |
| `nonplanar.min_area` | 20 mm² | Smallest region worth a non-planar skin. |
| `nonplanar.max_tread` | 6.0 mm | A flat wider than this is a real flat, not a stair step. |
| `nonplanar.max_segment` | 0.4 mm | Longest straight piece of a skin line. |
| `cooling.stacked_fan` | on | Raise the part fan on regions printed straight on top of themselves, by the slicer's own fan rule. |
| `cooling.lift_wait` | off | If such a region is still under the minimum layer time at minimum speed, lift clear and wait. |
| `cooling.min_layer_time` | 0 s | Minimum layer time for stacked regions; 0 = the file's own per filament. |
| `cooling.tower_between` | off | Print the tower's walls and grid between a colour's stacked layers, as cooling time. |
| `swap.lift` | 1.0 mm | Lift above the tallest printed point before each colour change (at least the slicer's Z hop). |
| `travel.local_lift` | off | Travels lift only above plastic the nozzle could reach on the way. Faster; trusts the G-code's heights. |
| `export.printer_host` | (empty) | Moonraker address for Send to printer, e.g. `192.168.1.20` or `192.168.1.20:7125`. |
| `printer.*` | Klipper defaults | Motion limits for the time estimate, only for files that do not carry the slicer's machine limits. |

## How it works
- **Nozzle rule.** The nozzle is a flat tip under a cone. Material standing dz above the tip must be at least
  `tip_radius + dz * tan(cone_angle)` away, less `contact`. Layers per colour come out at about
  `1 + contact / layer height`.
- **Bands.** Layers are grouped greedily; inside a band each colour prints its layers bottom-up. Material a later
  nozzle would hit, or that would rest on a later colour's layer, is handed to the later colour. Only internal
  colour interfaces are handed over: the outside keeps its colours exactly.
- **Staggered colours.** Each colour runs on its own schedule; colours that do not touch stand ahead together
  while the colour between them catches up in one long run.
- **Writing.** Travels, retracts, wipes and hops follow the file's own settings. Stacked regions keep the file's
  minimum layer time and fan rule. Each remaining swap keeps its prime tower wipe; unused blocks become grid.
- **Time and filament.** Klipper-style motion with the file's machine limits; the per-swap cost is what the
  slicer's own total leaves after motion. The file's summary (times in every mode, colour changes, filament used)
  and its progress lines are rewritten; the flush per filament is the slicer's, scaled to the swaps left.
