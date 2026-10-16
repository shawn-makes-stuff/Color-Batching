# ColorStack

Post-processor for multicolour G-code. A normal multicolour print swaps filament every layer; ColorStack lets
each colour print several layers in a row before swapping, wherever the nozzle can do that without hitting what
is already printed. Fewer swaps, less purge, shorter prints. Works on Orca / Bambu / Prusa / Anycubic / Cura
output, Klipper or Marlin.

## Use
```
pip install numpy scipy
python -m colorstack.app [file.gcode]            # local app on http://127.0.0.1:8765
python -m colorstack in.gcode [-o out.gcode] [--set section.key=value]
python -m colorstack.verify in.gcode out.gcode   # independent collision / surface check
```
The app has the settings, the before / after times, a side-by-side viewer (Orca's segment shader) and export
(download, Export as with a save dialog, or send to a Moonraker printer). In the viewer the view cube (lower left)
or keys 0-6 switch views as in Orca: 0 iso, 1 top, 2 bottom, 3 front, 4 rear, 5 left, 6 right.

Settings are saved in `colorstack.toml` (the app's Save defaults); the CLI overrides them with `--set`.

## Settings
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
| `stack.staggered` | on | Also plan each colour on its own schedule (fewer swaps where 3+ colours share layers); the faster plan is kept. Slower processing. |
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
- **Bands** (`plan.py`). Layers are grouped greedily; inside a band each colour prints its layers bottom-up.
  Material a later nozzle would hit, or that would rest on a later colour's layer, is handed to the later colour.
  Only internal colour interfaces are handed over: the outside keeps its colours exactly.
- **Staggered colours** (`plan_staggered`). Each colour runs on its own schedule; colours that do not touch stand
  ahead together while the colour between them catches up in one long run. Same collision and surface rules.
- **Writing** (`emit.py`). Travels, retracts, wipes and hops follow the file's own settings; the slicer's travel
  is kept wherever the order is unchanged. Stacked regions keep the file's minimum layer time and fan rule.
- **Prime tower** (`tower.py`). Each remaining swap keeps its wipe block; unused blocks become sparse grid.
- **Time** (`estimate.py`). Klipper-style motion with the file's machine limits; the per-swap cost is what the
  slicer's own total leaves after motion. Progress (M73) and the estimate line are rewritten.
- **Non-planar tops** (`nonplanar.py`). After Ahlers' non-planar Slic3r.

Prior art: Petsiuk et al., "Interlayer Tool Clustering", Rapid Prototyping Journal 30(8), 2024.

## Limits
- The nozzle defaults are placeholders: measure tip diameter, cone angle and clear height.
- Swap routines written as G-code moves (Bambu AMS M620/M621, MMU without a tower) are not replayed; only the
  tool line and the slicer's non-move tool change commands are.
