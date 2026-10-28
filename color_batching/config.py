"""Settings: defaults, color-batching.toml, and the label / unit / help the app shows for each."""
import copy
import tomllib
from pathlib import Path

CONFIG_PATH = Path(__file__).resolve().parent.parent / "color-batching.toml"
if not CONFIG_PATH.exists() and CONFIG_PATH.with_name("colorstack.toml").exists():     # 0.1.0's name
    CONFIG_PATH = CONFIG_PATH.with_name("colorstack.toml")

DEFAULTS = {
    "nozzle": {
        "tip_diameter": 1.0,
        "cone_angle": 45.0,
        "clear_height": 2.0,
        "contact": 0.2,      # one layer: what every measured result and the first real print used
    },
    "stack": {
        "max_band_layers": 10,
        "surface_tolerance": 0.0,
        "min_piece": 2.0,
        "per_object": True,        # plan with and without, keep the faster
        "staggered": True,         # also plan each colour on its own schedule, keep the faster
        "free_support": True,      # support the slicer printed with any filament may change filament (STATUS 12)
        "resolution": 0.1,
    },
    "cooling": {
        "respect_min_layer_time": True,    # outer walls keep their speed (tools/cooling_options.py)
        "stacked_fan": True,
        "lift_wait": False,
        "min_layer_time": 0.0,     # seconds; 0 = the file's own per filament
        "tower_between": True,
    },
    "swap": {
        "lift": 1.0,
    },
    "export": {
        "printer_host": "",
    },
    "printer": {                  # only for files without the slicer's machine limits
        "max_velocity": 500.0,
        "max_accel": 20000.0,
        "square_corner_velocity": 9.0,
        "max_z_velocity": 20.0,
        "max_z_accel": 500.0,
    },
}


def load_config(path=CONFIG_PATH):
    cfg = copy.deepcopy(DEFAULTS)
    if Path(path).exists():
        with open(path, "rb") as f:
            for section, values in tomllib.load(f).items():
                if section in cfg:          # sections of removed features are dropped
                    cfg[section].update(values)
    return cfg


def apply(cfg, values):
    """Merge {section: {key: value}} over cfg, every value converted to its default's type."""
    for section, defaults in DEFAULTS.items():
        for key, default in defaults.items():
            v = values.get(section, {}).get(key, cfg[section].get(key, default))
            cfg[section][key] = (v is True or str(v).lower() == "true") if isinstance(default, bool) else type(default)(v)
    return cfg


def save_config(cfg, path=CONFIG_PATH):
    lines = ["# Color Batching settings (written by the app; safe to edit by hand)"]
    for section, values in cfg.items():
        lines.append(f"\n[{section}]")
        for k, v in values.items():
            if isinstance(v, bool):
                v = str(v).lower()
            elif isinstance(v, str):
                v = '"' + v.replace("\\", "\\\\").replace('"', '\\"') + '"'
            lines.append(f"{k} = {v}")
    Path(path).write_text("\n".join(lines) + "\n", encoding="utf-8")

# key -> (label, unit, help); the README's settings table says the same
HELP = {
    "nozzle.tip_diameter": ("Tip diameter", "mm", "Flat face at the nozzle tip."),
    "nozzle.cone_angle": ("Cone angle", "°", "Cone half-angle from vertical."),
    "nozzle.clear_height": ("Clear height", "mm", "How far the tip sticks out below the heater block / sock / duct."),
    "nozzle.contact": ("Edge contact", "mm", "How much taller a neighbouring colour's edge may be while the nozzle "
                       "face slides past it. 0.2 = two layers per colour, 0.4 = three."),
    "stack.max_band_layers": ("Max layers per colour", "", "Most layers one colour prints before the next swap."),
    "stack.surface_tolerance": ("Edge shift", "mm", "How far a colour edge on a visible surface may move. 0 = never."),
    "stack.min_piece": ("Min piece", "mm", "Shorter leftovers between handed-over pieces are handed over too."),
    "stack.per_object": ("Per-object batching", "", "Finish one object's block before moving to the next, where "
                         "that is faster (both ways are planned and the faster kept)."),
    "stack.staggered": ("Staggered colours", "", "Each colour on its own schedule: fewer swaps where 3+ colours "
                        "share layers. Both ways are planned, the faster kept. Slower processing on those files."),
    "stack.free_support": ("Free support filament", "", "Support the slicer printed with whatever filament was loaded "
                           "(support filament 'Default') may be printed by another filament of the same material, so "
                           "it stacks. Support (or interface) with a chosen filament keeps it."),
    "stack.resolution": ("Resolution", "mm", "Sampling step for the collision planning."),
    "cooling.respect_min_layer_time": ("Respect minimum layer time", "", "Slow regions printed again straight on top "
                                       "of themselves down to the minimum layer time, as the slicer does. Outer walls "
                                       "keep their sliced speed. Off: keep all sliced speeds (the fan rule still "
                                       "applies)."),
    "cooling.stacked_fan": ("Fan for stacked regions", "", "Raise the part fan on regions printed again straight on "
                            "top of themselves, by the slicer's own fan rule."),
    "cooling.lift_wait": ("Lift and wait", "", "If a stacked region is still under the minimum layer time at "
                          "minimum speed, lift clear and wait out the rest. Can leave ooze strings."),
    "cooling.min_layer_time": ("Min layer time", "s", "Minimum layer time for stacked regions. 0 = the file's own "
                               "(per filament, where its slow-down is on)."),
    "cooling.tower_between": ("Tower between layers", "", "Print the prime tower's walls and grid between a colour's "
                              "stacked layers, as cooling time. The wipe still comes right after the swap."),
    "swap.lift": ("Swap lift", "mm", "Before each colour change, lift this far above the tallest point printed so "
                  "far (at least the slicer's Z hop), so the trip to the purge tray clears every stack."),
    "export.printer_host": ("Printer", "", "Moonraker address for Send to printer, e.g. 192.168.1.20 or "
                            "192.168.1.20:7125."),
    "printer.max_velocity": ("Max velocity", "mm/s", "Fallback for the time estimate, for files without the "
                             "slicer's machine limits."),
    "printer.max_accel": ("Max accel", "mm/s²", "Fallback for the time estimate."),
    "printer.square_corner_velocity": ("Square corner velocity", "mm/s", "Fallback for the time estimate."),
    "printer.max_z_velocity": ("Max Z velocity", "mm/s", "Fallback for the time estimate."),
    "printer.max_z_accel": ("Max Z accel", "mm/s²", "Fallback for the time estimate."),
}
