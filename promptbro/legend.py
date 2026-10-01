"""Hotkey legend: one source for the text, shared with any later plugin build.

Keys match the Hyprland bindings printed by --print-hypr-snippet. Shift is a
glyph; Super is spelled out because Linux has no command key.
"""

KEYS = (
    ("Super⇧T", "hide"),
    ("Super⇧[ ]", "line"),
    ("Super⇧\\", "pause"),
    ("Super⇧Home", "top"),
)
LEGEND_TEXT = "  ·  ".join(f"{k} {a}" for k, a in KEYS)

LEGEND_SCALE = 0.65      # legend font size as a fraction of window.other_pt
LEGEND_ALPHA = 0.30      # brightness only (colorblind-safe); past lines run 0.15 to 0.50
LEGEND_STRIP_PX = 24     # extra window height reserved at the bottom for the legend
