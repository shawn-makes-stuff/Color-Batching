"""Make a test variant of an Orca 3MF: override project settings, optionally leave objects off the plate, and slice it
with the installed OrcaSlicer.

python tools/variant.py in.3mf out_dir NAME [--drop OBJECT_NAME ...] [--slice] key=value ...
A value with commas sets a per-filament list ("0.02,0.035,0.05,0.04"); NAME.3mf (and NAME.gcode with --slice) go to
out_dir.
"""
import argparse
import json
import re
import shutil
import subprocess
import tempfile
import zipfile
from pathlib import Path

ORCA = r"C:\Program Files\OrcaSlicer\orca-slicer.exe"


def make(src, dst, overrides, drop):
    with zipfile.ZipFile(src) as zin, zipfile.ZipFile(dst, "w", zipfile.ZIP_DEFLATED) as zout:
        model_cfg = zin.read("Metadata/model_settings.config").decode()
        # object ids (model_settings) of the objects to leave off
        ids = [m.group(1) for m in re.finditer(r'<object id="(\d+)"[^>]*>\s*<metadata key="name" value="([^"]+)"',
                                                model_cfg) if m.group(2) in drop]
        for item in zin.infolist():
            data = zin.read(item.filename)
            if item.filename == "Metadata/project_settings.config":
                cfg = json.loads(data)
                for k, v in overrides.items():
                    if k not in cfg:
                        raise SystemExit(f"unknown setting {k}")
                    cfg[k] = v.split(",") if isinstance(cfg[k], list) and isinstance(v, str) else v
                # the command line reloads the presets and keeps only the keys listed as changed (one entry per
                # preset: process, each filament, printer); list the key everywhere, Orca takes it where it belongs
                diff = cfg.get("different_settings_to_system", [])
                for n, entry in enumerate(diff):
                    keys = [x for x in entry.split(";") if x]
                    diff[n] = ";".join(sorted(set(keys) | set(overrides)))
                data = json.dumps(cfg, indent=4).encode()
            elif item.filename == "3D/3dmodel.model" and ids:
                text = data.decode()
                for i in ids:      # removing the object breaks Orca's plate data; not printable leaves it off
                    text = re.sub(rf'(<item objectid="{i}"[^>]*printable=")1"', r'\g<1>0"', text)
                data = text.encode()
            zout.writestr(item, data)


def slice_3mf(path, out_dir):
    """Slice plate 1 with OrcaSlicer's command line; returns the G-code path."""
    with tempfile.TemporaryDirectory() as tmp:
        subprocess.run([ORCA, "--slice", "1", "--outputdir", tmp, str(Path(path).resolve())], check=True, timeout=3600)
        found = list(Path(tmp).glob("*.gcode"))
        if not found:
            raise SystemExit("OrcaSlicer wrote no G-code")
        dst = Path(out_dir) / (Path(path).stem + ".gcode")
        shutil.move(found[0], dst)
        return dst


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("src")
    ap.add_argument("out_dir")
    ap.add_argument("name")
    ap.add_argument("--drop", action="append", default=[])
    ap.add_argument("--slice", action="store_true")
    ap.add_argument("settings", nargs="*", metavar="key=value")
    a = ap.parse_args()
    overrides = dict(s.split("=", 1) for s in a.settings)
    out = Path(a.out_dir) / (a.name + ".3mf")
    make(a.src, out, overrides, set(a.drop))
    print("wrote", out)
    if a.slice:
        print("sliced", slice_3mf(out, a.out_dir))


if __name__ == "__main__":
    main()
