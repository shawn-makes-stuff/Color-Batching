# Color Batching in C++

The Python package `color_batching/` is the reference. The C++ implementation replaces it as the standalone tool and later
becomes the native OrcaSlicer slicing mode, so it is written the way Orca's own code is: C++17, CMake, no exotic
dependencies.

## Rule
Every step is proved against the Python output on the reference files (`tools/regress.py`, `tools/check.py`):
- byte-identical G-code is the target;
- where floating point can differ in the last bit (numpy's pairwise sums, KD-tree internals), the fallback bar is: the
  same bands, swaps and line order, and `tools/check.py` equal to the Python run (state per mm, swap blocks, retraction
  balance, arrivals, cooling, collisions). Any other difference is a bug until explained.

## Layout
```
cpp/
  CMakeLists.txt
  core/            library, one file per Python module: reader, model, parse, geometry, plan, staggered, tower,
                   writer, estimate, summary, config
  cli/             color-batching in.gcode -o out.gcode --set section.key=value  (same options as python -m color_batching)
  app/             desktop shell: the existing viewer/index.html in a native webview, talking to the core directly
  tests/           runs the CLI on the reference files and compares with the Python outputs
```
Dependencies: nanoflann (header-only KD tree, BSD) for the nearest-plastic queries; toml++ (header-only) for
colorstack.toml; webview/webview (MIT) for the app shell. Nothing else.

## Order
1. reader + model: read the reference files, dump the event stream, compare with the Python reader's (exact).
2. parse: dump layers / paths / lead-ins / state, compare with Python's (exact).
3. writer with a pass-through plan (every layer one band, original order): compare with the Python writer given the
   same plan. This settles formatting (`_fmt`, `E%.5f`) and all travel / retract / swap block / cooling logic.
4. estimate + summary (M73, header rewrite): compare times and stamped lines.
5. geometry (sampling, split, interface mask, height map) and plan (bands, nearest-plastic answers cache): compare
   the bands (layers, unit order, ceded mm) per file, then whole outputs.
6. staggered, tower compensation, variants in parallel (threads instead of processes), cancellation.
7. CLI parity with `python -m color_batching`; then the app shell (viewer + settings + Optimize + export).
8. CI: GitHub Actions builds on Windows, macOS and Linux runners; artifacts per OS. macOS signing / notarization
   needs an Apple developer account (user's decision).

Each step ends with its comparison in `tools/` and a line in STATUS.md (what was compared, the result).

## Measured starting point (Python, 2026-10-27)
Stitch 20-24 s, Vegeta ~115-150 s (two variants in parallel processes); per variant on Vegeta: plan ~50 s,
write ~41 s, estimate ~30 s, parse ~20 s.
