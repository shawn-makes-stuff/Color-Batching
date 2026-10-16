# ColorStack

A multicolour print normally swaps filament on every layer that has more than one colour. <br>ColorStack rewrites the
sliced G-code so each colour prints several layers per colour in one go, wherever the nozzle can do so
without colliding. <br>Fewer swaps = less purge waste, shorter prints. 

![Original and ColorStack playing side by side](docs/cube-example.gif)

*Example test cube: <br> Unprocessed file - 49 colour changes, 1h10m estimated print time <br> ColorStack - 19 colour changes, 40m estimated print time*

## Setup
Python 3.11 or newer, then:
```
pip install numpy scipy
python -m colorstack.app
```
The app opens in your browser at http://127.0.0.1:8765.

## Quick start
1. **Measure your nozzle.** These three numbers decide how many layers can stack safely; the other defaults are
   fine to start with.
   - **Tip diameter**: the flat face at the very tip (calipers, or a close-up photo next to a ruler). Usually
     about 1 mm.
   - **Cone angle**: the angle of the nozzle's taper, measured from vertical (half the full cone angle). From a
     side photo; most nozzles are 40-60°.
   - **Clear height**: how far the tip sticks out below the lowest part of the heater block, silicone sock or fan
     duct. <br>In real world usage, the full clear height will almost never be utilized.
2. **Enter them** in the app's Nozzle card and press Save defaults.
3. **Slice** your multicolour model as usual and export the G-code.
4. **Import** it in the app (top bar), press **Optimize file**, and check the two prints side by side.
5. **Export** the optimized file, or send it straight to a Moonraker printer.

Prefer the command line? `python -m colorstack in.gcode -o out.gcode` uses the same saved settings.

All settings are described in [docs/settings.md](docs/settings.md).

## Credit
Builds on the idea of interlayer tool clustering from A. Petsiuk, B. Bloch, D. Vogt, M. Debora and J. M. Pearce,
"Tool change reduction for multicolor fused filament fabrication through interlayer tool clustering implemented
in PrusaSlicer", Rapid Prototyping Journal 30(8), 1592-1609, 2024 (doi:10.1108/RPJ-01-2024-0050).
