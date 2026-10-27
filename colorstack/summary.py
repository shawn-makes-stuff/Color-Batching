"""Rewrite the file's own summary (times, colour changes, filament used) and progress lines (M73)."""
import re

from .estimate import fmt_duration, parse_duration


def stamp_header(lines, o, p):
    """The slicer's summary lines with the new figures, for the printer's screen: the times (other modes scaled
    like normal), the filament change count and the filament used. Filament per tool: the original's, plus the
    change in what the file extrudes, with the flush it counted beyond that scaled to the swaps into that tool."""
    ratio = p["total_s"] / o["total_s"] if o["total_s"] else 1.0
    lengths = {}            # tool -> (original mm, new mm)

    def used_mm(values):
        out = []
        for t, mm in enumerate(values):
            flush = max(0.0, mm - o["extruded"].get(t, 0.0))
            into_o, into_p = o["swaps_into"].get(t, 0), p["swaps_into"].get(t, 0)
            new = mm + p["extruded"].get(t, 0.0) - o["extruded"].get(t, 0.0) - flush * (1 - into_p / into_o if into_o else 0)
            out.append(max(0.0, new) if mm else 0.0)
            lengths[t] = (mm, out[-1])
        return out

    def per_tool(values):      # cm3, g, cost: in proportion to the length
        return [v * lengths[t][1] / lengths[t][0] if lengths.get(t, (0,))[0] else v for t, v in enumerate(values)]

    rules = [
        (r"^(; estimated printing time \(normal mode\) = ).*$", lambda m: m.group(1) + fmt_duration(p["total_s"])),
        (r"^(; estimated printing time \((?:silent|sport) mode\) = )(.*)$",
         lambda m: m.group(1) + fmt_duration(parse_duration(m.group(2)) * ratio)),
        (r"^(;TIME:)\d+", lambda m: m.group(1) + str(int(round(p["total_s"])))),
        (r"^(; total filament change = )\d+", lambda m: m.group(1) + str(p["swaps"])),
        (r"^(; filament used \[mm\] = )(.*)$", lambda m: m.group(1) + _floats(used_mm(_numbers(m.group(2))))),
        (r"^(; filament used \[(?:cm3|g)\] = |; filament cost = )(.*)$",
         lambda m: m.group(1) + _floats(per_tool(_numbers(m.group(2))))),
    ]
    for k, ln in enumerate(lines):
        if not ln.startswith(";"):        # every rule matches a comment line
            continue
        for pattern, new in rules:
            m = re.match(pattern, ln)
            if m:
                lines[k] = new(m)
                break
    # totals from the per-tool lines just written
    sums = {}
    for ln in lines:
        m = ln.startswith("; ") and re.match(r"^; (filament used \[g\]|filament cost) = (.*)$", ln)
        if m:
            sums[m.group(1)] = sum(_numbers(m.group(2)))
    for k, ln in enumerate(lines):
        m = ln.startswith("; total") and re.match(r"^(; total (filament used \[g\]|filament cost) = )", ln)
        if m and m.group(2) in sums:
            lines[k] = m.group(1) + f"{sums[m.group(2)]:.2f}"


def _numbers(text):
    return [float(x) for x in text.split(",")]


def _floats(values):
    return ", ".join(f"{v:.2f}" for v in values)

_NUM = re.compile(r"([A-Za-z]+)(\d+(?:\.\d+)?)")


def stamp_progress(lines, steps, each, total):
    """Progress lines (M73) from our timeline, in the file's own format. The file's M73 lines before the first
    move are the templates: P/Q get our percent, every other number (R, Anycubic QuietR/SportR/NormalR, Prusa S)
    is scaled from its start value by our remaining time. A line follows each change of percent or remaining
    minute; the closing lines after the last move stay. Returns the new lines."""
    at = [k for k, ln in enumerate(lines) if ln.startswith("M73 ")]
    if not at or total <= 0:
        return lines
    first_move = next((k for k, _ in steps), len(lines))
    templates = [lines[k] for k in at if k < first_move] or [lines[at[0]]]
    ref = next((float(m.group(2)) for t in templates for m in _NUM.finditer(t) if m.group(1) == "R"), None)
    if not ref:
        return lines

    def render(pct, rem_s, start):
        scale = rem_s / 60 / ref

        def num(m, in_code):
            name, val = m.group(1), m.group(2)
            if name == "M":
                return m.group(0)
            if name in ("P", "Q"):
                return f"{name}{pct}"
            x = float(val) * scale
            dec = len(val.split(".")[1]) if "." in val else 0
            if dec and (start or not in_code):     # the slicer writes decimals only on the first line
                return f"{name}{x:.{dec}f}"
            return f"{name}{int(x)}"
        out = []
        for t in templates:
            code, sep, comment = t.partition(";")
            out.append(_NUM.sub(lambda m: num(m, True), code) + sep + _NUM.sub(lambda m: num(m, False), comment))
        return out

    after = {}
    elapsed, last = 0.0, (0, int(total // 60))
    for k, sec in steps:
        elapsed += each if sec is None else sec
        rem = max(0.0, total - elapsed)
        key = (min(99, int(elapsed / total * 100)), int(rem // 60))
        if key != last:
            after[k] = render(key[0], rem, False)
            last = key
    end = steps[-1][0] if steps else len(lines)
    out = []
    for k, ln in enumerate(lines):
        if k == at[0]:
            out += render(0, total, True)
        elif not ln.startswith("M73 ") or k > end:
            out.append(ln)
        out += after.get(k, [])
    return out
