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
The app has the settings, the before/after time table, a side-by-side viewer (Orca's segment shader) and
export (download, save to folder, send to a Moonraker printer). Settings live in `colorstack.toml`; defaults in
`colorstack/pipeline.py`.

## How it works
- **Nozzle rule.** The nozzle is a flat tip under a cone. Material standing dz above the tip must be at least
  `tip_radius + dz * tan(cone_angle)` away; `contact` lets a neighbouring edge stand that much above the tip
  while the flat face slides past. Layers per colour come out at about `1 + contact / layer height`.
- **Bands** (`plan.py`). Layers are grouped greedily; inside a band each colour prints its layers bottom-up, one
  colour after another. Material a later nozzle would hit, or that would rest on a later colour's layer, is
  handed to the later colour. Only internal colour interfaces are handed over: the outside surface keeps its
  colours exactly (`surface_tolerance` allows a shift if wanted).
- **Writing** (`emit.py`). Travels, retracts, wipes and hops follow the file's own settings; the slicer's own
  travel is kept wherever the order is unchanged. Before a swap the nozzle lifts above the tallest printed point.
  Regions printed straight on top of themselves get the file's minimum layer time and fan rule.
- **Prime tower** (`tower.py`). Each remaining swap keeps its wipe block; unused blocks become sparse grid.
- **Time** (`estimate.py`). Klipper-style motion with the file's machine limits; the per-swap cost is whatever
  the slicer's own total leaves after motion. Progress (M73) and the estimate line are rewritten.
- **Non-planar tops** (`nonplanar.py`, off by default). Shallow curved tops get one continuous skin that follows
  the surface instead of stair steps (after Ahlers' non-planar Slic3r).

## Limits
- Nozzle geometry defaults are placeholders: measure tip diameter, cone angle and clear height.
- Swap routines written as G-code moves (Bambu AMS M620/M621, MMU without a tower) are not replayed; only the
  tool line and the slicer's non-move tool change commands are.
