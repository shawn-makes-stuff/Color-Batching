"""The parsed print: layers of extrusion paths, with the slicer's settings and the state each path printed under."""
import math
from collections import Counter
from dataclasses import dataclass, field

VISIBLE_TYPES = {"Outer wall", "Overhang wall", "Top surface", "Bottom surface", "Ironing"}

NEUTRAL_TYPES = {"Prime tower"}  # printed by whichever tool is loaded

# pause / filament change: everything below must be printed first, nothing above, so no band crosses the layer
BARRIERS = {"M600", "M601", "M0", "M1", "M25", "M125", "PAUSE"}


@dataclass
class Path:
    tool: int | None          # None = prime tower
    type: str
    width: float
    z: float
    pts: list                 # [(x, y), ...], one more than segments
    e: list                   # filament mm per segment
    f: list                   # mm/min per segment
    accel: float | None
    fans: dict                # {"": S, "P2": S, ...} as set before the path
    obj: str | None           # EXCLUDE_OBJECT name
    seq: int = 0              # index in the slicer's path order
    retract_before: bool = True
    hop_before: bool = True
    head: bool = True         # starts where the slicer's path started (False for a later piece of a split)
    tail: bool = True
    tower: str | None = None  # prime tower part: "wipe", "grid" or "wall"
    block: int = -1           # wipe block number (one per tool change)
    scv: float | None = None  # Klipper square corner velocity set before the path
    temp: float | None = None  # nozzle temperature the slicer printed it at
    vlim: tuple = ()          # other SET_VELOCITY_LIMIT parameters in force, ((name, value), ...)
    height: float | None = None   # line height the slicer wrote for it (;HEIGHT: after ;TYPE:), None = the layer's

    @property
    def visible(self):
        return self.type in VISIBLE_TYPES

    def length(self):
        return sum(math.dist(self.pts[k], self.pts[k + 1]) for k in range(len(self.e)))


@dataclass
class Layer:
    index: int
    z: float
    height: float
    paths: list = field(default_factory=list)
    cmds: list = field(default_factory=list)   # emitted at the layer's start

    @property
    def barrier(self):
        return any(c.split()[0].upper() in BARRIERS for c in self.cmds)


@dataclass
class Program:
    settings: dict            # the slicer's "; key = value" config block
    preamble: list
    postamble: list
    layers: list
    first_tool: int
    toolchange_extra: list    # what the slicer emits around a tool change (filament start/end G-code)
    header: list
    tool_format: str = "T{n}"     # the file's own tool change line, {purge} for PURGE_LENGTH
    abs_e: bool = False           # start G-code leaves M82
    stats_info: bool = False      # file uses SET_PRINT_STATS_INFO
    accel_format: str = "SET_VELOCITY_LIMIT ACCEL={a}"
    first_temp: float | None = None   # nozzle temperature when the layers start
    temps: dict = field(default_factory=dict)    # tool -> nozzle temperature per layer index (see temp_at)
    fans: dict = field(default_factory=dict)     # (tool, layer index, type) -> fans the slicer set for it
    travel: dict = field(default_factory=dict)   # retract, retract_f, deretract_f, z_hop, travel_f, min_travel
    unknown: Counter = field(default_factory=Counter)

    def temp_at(self, tool, index):
        """Nozzle temperature for tool printing layer index: what the slicer printed that tool at there (a layer
        the tool had nothing on takes the nearest one below, else above). None if unknown."""
        col = self.temps.get(tool)
        return col[index] if col else None

    def setting_list(self, key, cast=float, default=None):
        if key not in self.settings:
            return default
        try:
            return [cast(v) for v in self.settings[key].split(",")]
        except ValueError:
            return default


def first_setting(settings, key, default):
    """First value of a per-filament / per-mode setting."""
    try:
        return float(settings[key].split(",")[0])
    except (KeyError, ValueError):
        return default
