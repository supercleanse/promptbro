"""PrompterView: the scrolling script card (DESIGN.md 7.3).

Inputs: a script.Script (or, for the demo and tests, a list[str] or any object with
.lines (str or .text) and .units (.line, .char_end)), and an aligner.Position
(.line, .unit = global unit index or -1/None, .state).
"""
from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("Gsk", "4.0")
gi.require_version("Graphene", "1.0")
gi.require_version("Pango", "1.0")
from gi.repository import Gdk, GLib, Graphene, Gsk, Gtk, Pango  # noqa: E402

from .config import WindowConfig  # noqa: E402
from .legend import LEGEND_ALPHA, LEGEND_SCALE, LEGEND_STRIP_PX, LEGEND_TEXT  # noqa: E402
from .theme import load_palette  # noqa: E402

GAP = 10
PAD = 16
EDGE = 12
RADIUS = 14
TOAST_MS = 3000
EMPTY_TEXT = "No script — promptbro open <file> or promptbro paste"

_AMBER = "#f59e0b"


def rgba(hexstr: str, alpha: float = 1.0) -> Gdk.RGBA:
    c = Gdk.RGBA()
    if not c.parse(hexstr):
        c.parse("#888888")
    c.alpha = alpha
    return c


def ease_out_cubic(t: float) -> float:
    return 1 - (1 - t) ** 3


def _text_of(line) -> str:
    return line if isinstance(line, str) else getattr(line, "text", str(line))


class PrompterView(Gtk.Widget):
    __gtype_name__ = "PromptBroView"

    def __init__(self, cfg: WindowConfig | None = None, palette: dict | None = None):
        super().__init__()
        self.cfg = cfg or WindowConfig()
        self.pal = palette or load_palette()
        self.lines: list[str] = []
        self.units: list = []
        self.current = 0
        self._unit = None
        self._state = "holding"
        self._offset = 0.0
        self._anim_from = 0.0
        self._anim_start = None
        self._tick_id = 0
        self._cache: dict = {}
        self._cache_w = 0
        self._toast = ""
        self._toast_id = 0
        self.set_hexpand(True)
        self.set_vexpand(True)
        self.set_overflow(Gtk.Overflow.HIDDEN)

    # ---- public API -------------------------------------------------------
    def set_script(self, script) -> None:
        if isinstance(script, (list, tuple)):
            self.lines = [_text_of(x) for x in script]
            self.units = []
        else:
            self.lines = [_text_of(x) for x in script.lines]
            self.units = list(getattr(script, "units", []))
        self.current, self._unit = 0, None
        self._stop_anim()
        self._cache.clear()
        self.queue_draw()

    def set_position(self, pos) -> None:
        self._unit = getattr(pos, "unit", None)
        self._state = self._state_name(getattr(pos, "state", self._state))
        line = max(0, min(len(self.lines) - 1, pos.line)) if self.lines else 0
        if line != self.current:
            self._move_to(line, animate=True)
        else:
            self.queue_draw()

    def jump_to(self, line: int) -> None:
        self._unit = None
        if self.lines:
            self._move_to(max(0, min(len(self.lines) - 1, line)), animate=False)

    def settle(self) -> None:
        """Finish any scroll animation now (offscreen renders: Broadway's clock may not tick)."""
        self._stop_anim()
        self.queue_draw()

    def clear_toast(self) -> None:
        if self._toast_id:
            GLib.source_remove(self._toast_id)
        self._clear_toast()

    def set_palette(self, palette: dict) -> None:
        self.pal = palette
        self._cache.clear()
        self.queue_draw()

    def set_status(self, state) -> None:
        self._state = self._state_name(state)
        self.queue_draw()

    def toast(self, text: str) -> None:
        self._toast = text
        if self._toast_id:
            GLib.source_remove(self._toast_id)
        self._toast_id = GLib.timeout_add(TOAST_MS, self._clear_toast)
        self.queue_draw()

    # ---- internals --------------------------------------------------------
    @staticmethod
    def _state_name(state) -> str:
        return str(getattr(state, "value", getattr(state, "name", state))).lower()

    def _clear_toast(self):
        self._toast, self._toast_id = "", 0
        self.queue_draw()
        return GLib.SOURCE_REMOVE

    def _stop_anim(self):
        if self._tick_id:
            self.remove_tick_callback(self._tick_id)
        self._tick_id, self._offset, self._anim_start = 0, 0.0, None

    def _text_width(self) -> int:
        return max(1, (self.get_width() or self.cfg.width) - 2 * PAD)

    def _layout(self, i: int, cur: bool) -> Pango.Layout:
        w = self._text_width()
        if w != self._cache_w:
            self._cache.clear()
            self._cache_w = w
        key = (i, w, cur)
        lay = self._cache.get(key)
        if lay is None:
            lay = self.create_pango_layout(self.lines[i])
            fd = Pango.FontDescription.from_string(
                f"{self.cfg.font}, Inter, Noto Sans {'Bold' if cur else ''}")
            fd.set_absolute_size((self.cfg.current_pt if cur else self.cfg.other_pt) * Pango.SCALE)
            lay.set_font_description(fd)
            lay.set_width(w * Pango.SCALE)
            lay.set_wrap(Pango.WrapMode.WORD_CHAR)
            lay.set_alignment(Pango.Alignment.CENTER)
            self._cache[key] = lay
        return lay

    def _height(self, i: int, cur: bool) -> int:
        return self._layout(i, cur).get_pixel_size()[1]

    def _move_to(self, new: int, animate: bool) -> None:
        old = self.current
        reduced = not Gtk.Settings.get_default().get_property("gtk-enable-animations")
        animate = animate and not reduced and self.get_width() > 0 and new != old
        if animate:
            lo, hi = sorted((old, new))
            dist = (self._height(old, True) / 2 + GAP
                    + sum(self._height(i, False) + GAP for i in range(lo + 1, hi))
                    + self._height(new, True) / 2)
            self.current = new
            self._anim_from = self._offset + (dist if new > old else -dist)
            self._offset = self._anim_from
            self._anim_start = None
            if not self._tick_id:
                self._tick_id = self.add_tick_callback(self._on_tick)
        else:
            self._stop_anim()
            self.current = new
        self.queue_draw()

    def _on_tick(self, _w, clock):
        now = clock.get_frame_time()
        if self._anim_start is None:
            self._anim_start = now
        t = min(1.0, (now - self._anim_start) / (self.cfg.animation_ms * 1000))
        self._offset = self._anim_from * (1 - ease_out_cubic(t))
        self.queue_draw()
        if t >= 1.0:
            self._offset, self._tick_id = 0.0, 0
            return GLib.SOURCE_REMOVE
        return GLib.SOURCE_CONTINUE

    def _spoken_chars(self) -> int:
        u = self._unit
        if u is None or u < 0 or u >= len(self.units):
            return 0
        unit = self.units[u]
        if unit.line == self.current:
            return unit.char_end
        return len(self.lines[self.current]) if unit.line > self.current else 0

    def _current_layout(self) -> Pango.Layout:
        lay = self._layout(self.current, True)
        text = self.lines[self.current]
        n = self._spoken_chars()
        split = len(text[:n].encode("utf-8"))
        attrs = Pango.AttrList()
        for start, end, col in ((0, split, self.pal["accent"]),
                                (split, len(text.encode("utf-8")), self.pal["foreground"])):
            if end > start:
                c = rgba(col)
                a = Pango.attr_foreground_new(int(c.red * 65535), int(c.green * 65535),
                                              int(c.blue * 65535))
                a.start_index, a.end_index = start, end
                attrs.insert(a)
        lay.set_attributes(attrs)
        return lay

    def _dot_color(self) -> str:
        s = self._state
        if s == "tracking":
            return self.pal["accent"]
        if s in ("holding", "hold", "adlib"):
            return self.pal["muted"]
        if s == "paused":
            return self.pal.get("bright_blue") or _AMBER
        return self.pal["red"]

    # ---- drawing ----------------------------------------------------------
    def do_snapshot(self, snap):
        W, H = self.get_width(), self.get_height()
        if W <= 0 or H <= 0:
            return
        pal = self.pal
        card = Gsk.RoundedRect()
        card.init_from_rect(Graphene.Rect().init(0, 0, W, H), RADIUS)
        snap.push_rounded_clip(card)
        snap.append_color(rgba(pal["background"], self.cfg.opacity), Graphene.Rect().init(0, 0, W, H))

        strip = LEGEND_STRIP_PX if self.cfg.show_legend else 0
        if not self.lines:
            self._draw_empty(snap, W, H - strip)
        else:
            self._draw_stack(snap, W, H - strip)
        if strip:
            self._draw_legend(snap, W, H, strip)
        snap.pop()

        border = rgba(pal["foreground"], 0.12)
        snap.append_border(card, [1, 1, 1, 1], [border] * 4)
        self._draw_status(snap, W)

    def _draw_text(self, snap, layout, x, y, color):
        snap.save()
        snap.translate(Graphene.Point().init(x, y))
        snap.append_layout(layout, color)
        snap.restore()

    def _draw_empty(self, snap, W, H):
        lay = self.create_pango_layout(EMPTY_TEXT)
        fd = Pango.FontDescription.from_string(self.cfg.font)
        fd.set_absolute_size(self.cfg.other_pt * Pango.SCALE)
        lay.set_font_description(fd)
        lay.set_width((W - 2 * PAD) * Pango.SCALE)
        lay.set_wrap(Pango.WrapMode.WORD_CHAR)
        lay.set_alignment(Pango.Alignment.CENTER)
        _, h = lay.get_pixel_size()
        self._draw_text(snap, lay, PAD, (H - h) / 2, rgba(self.pal["light_foreground"]))

    def _draw_stack(self, snap, W, H):
        pal = self.pal
        fg = pal["foreground"]
        # 12 px alpha mask at top and bottom so text never sits on the rounded corners
        snap.push_mask(Gsk.MaskMode.ALPHA)
        stops = []
        for off, a in ((0.0, 0.0), (EDGE / H, 1.0), (1 - EDGE / H, 1.0), (1.0, 0.0)):
            s = Gsk.ColorStop()
            s.offset, s.color = off, rgba("#000000", a)
            stops.append(s)
        snap.append_linear_gradient(Graphene.Rect().init(0, 0, W, H),
                                    Graphene.Point().init(0, 0), Graphene.Point().init(0, H), stops)
        snap.pop()

        cur = self._current_layout()
        ch = cur.get_pixel_size()[1]
        y_cur = (H - ch) / 2 + self._offset
        self._draw_text(snap, cur, PAD, y_cur, rgba(fg))

        y, i = y_cur, self.current - 1
        while i >= 0 and y > 0:
            lay = self._layout(i, False)
            h = lay.get_pixel_size()[1]
            y -= h + GAP
            f = max(0.0, min(1.0, (y + h) / (H / 2)))
            self._draw_text(snap, lay, PAD, y, rgba(pal["light_foreground"], 0.15 + 0.35 * f))
            i -= 1
        y, i = y_cur + ch + GAP, self.current + 1
        while i < len(self.lines) and y < H:
            lay = self._layout(i, False)
            h = lay.get_pixel_size()[1]
            f = max(0.0, min(1.0, (H - y) / (H / 2)))
            self._draw_text(snap, lay, PAD, y, rgba(fg, 0.25 + 0.55 * f))
            y += h + GAP
            i += 1
        snap.pop()  # mask

    def _draw_legend(self, snap, W, H, strip):
        # Brightness only (no hue): light_foreground at low alpha, well under the past lines.
        lay = self.create_pango_layout(LEGEND_TEXT)
        fd = Pango.FontDescription.from_string(f"{self.cfg.font}, Inter, Noto Sans")
        fd.set_absolute_size(round(self.cfg.other_pt * LEGEND_SCALE) * Pango.SCALE)
        lay.set_font_description(fd)
        lay.set_width((W - 2 * PAD) * Pango.SCALE)
        lay.set_alignment(Pango.Alignment.CENTER)
        lay.set_ellipsize(Pango.EllipsizeMode.END)
        _, lh = lay.get_pixel_size()
        y = H - strip + (strip - lh) / 2 - 2
        self._draw_text(snap, lay, PAD, y, rgba(self.pal["light_foreground"], LEGEND_ALPHA))

    def _draw_status(self, snap, W):
        r = 4
        cx, cy = W - PAD, PAD + 4
        rect = Graphene.Rect().init(cx - r, cy - r, 2 * r, 2 * r)
        rr = Gsk.RoundedRect()
        rr.init_from_rect(rect, r)
        snap.push_rounded_clip(rr)
        snap.append_color(rgba(self._dot_color()), rect)
        snap.pop()
        if self._toast:
            lay = self.create_pango_layout(self._toast)
            fd = Pango.FontDescription.from_string(self.cfg.font)
            fd.set_absolute_size(12 * Pango.SCALE)
            lay.set_font_description(fd)
            lw, lh = lay.get_pixel_size()
            self._draw_text(snap, lay, W - PAD - lw, cy + r + 4, rgba(self.pal["light_foreground"]))
