"""The compatibility variants of the user's 3MF (spinner + TerrorCube; Vegeta left off)."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
from variant import make, slice_3mf

HERE = Path(__file__).resolve().parent.parent / "samples" / "compat"   # the user's 3MF (gitignored)
SRC, OUT = HERE / "not_colliding.3mf", HERE / "v"
DROP = {"majin_vegeta_bust.stl"}
MODEL = "0.04,1,1000\n0.035,8,1000\n0.03,20,1000\n0.035,1,10000\n0.03,8,10000\n0.025,20,10000"  # >= 2 flows per accel
VARIANTS = {
    # per-filament differences: PA, flow, volumetric limit, retraction, hop, outer wall slow-down, layer time, temperature
    "filaments": {"pressure_advance": ["0.02", "0.035", "0.05", "0.04"],
                  "filament_flow_ratio": ["0.95", "0.98", "1.0", "1.02"],
                  "filament_max_volumetric_speed": ["21", "8", "21", "15"],
                  "filament_retraction_length": ["0.5", "nil", "1.2", "nil"],
                  "filament_z_hop": ["nil", "0.6", "nil", "nil"],
                  "dont_slow_down_outer_wall": ["1", "0", "0", "0"],
                  "slow_down_layer_time": ["4", "8", "4", "6"],
                  "nozzle_temperature": ["210", "220", "215", "225"]},
    "adaptive_pa": {"adaptive_pressure_advance": ["1", "1", "1", "1"],
                    "adaptive_pressure_advance_model": [MODEL] * 4,
                    "adaptive_pressure_advance_overhangs": ["1", "1", "1", "1"]},
    "travel": {"reduce_crossing_wall": "1", "z_hop_types": ["Spiral Lift"], "wipe_on_loops": "1",
               "wipe_before_external_loop": "1", "retract_restart_extra": ["0.1"], "role_based_wipe_speed": "1"},
    "scarf_arcs": {"seam_slope_type": "all", "seam_slope_conditional": "0", "enable_arc_fitting": "1"},
    "fw_retract": {"use_firmware_retraction": "1", "wipe": ["0"]},
    "surface": {"ironing_type": "top", "fuzzy_skin": "external", "fan_kickstart": "0.3"},
}

if __name__ == "__main__":
    for name in sys.argv[1:] or VARIANTS:
        dst = OUT / f"{name}.3mf"
        make(SRC, dst, {k: v for k, v in VARIANTS[name].items()}, DROP)
        print(name, slice_3mf(dst, OUT), flush=True)
