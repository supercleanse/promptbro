try:
    from ctypes import CDLL
    CDLL("libgtk4-layer-shell.so.0")  # must load before libwayland-client (libgtk-4 pulls it in)
except OSError:
    pass

# Before anything imports Gtk (PyGObject opens the display at import): work around the
# GTK dmabuf-feedback crash on monitor hotplug. See promptbro/gtkcompat.py.
from .gtkcompat import apply_dmabuf_workaround  # noqa: E402

apply_dmabuf_workaround()

from .app import main as _main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(_main())
