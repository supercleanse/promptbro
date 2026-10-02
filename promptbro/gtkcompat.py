"""Workarounds for known GTK bugs, applied before the GDK display opens (stdlib only).

GTK 4.22.4 and older (and 4.23.0 to 4.23.2) crash with SIGSEGV when a Wayland compositor
re-sends the default linux-dmabuf feedback while the app is running. Hyprland does that
on every monitor hotplug (a dock re-enumerating is enough). GDK's format_table handler
munmap()s the heap-allocated DmabufFormats struct instead of the mmap()ed format table;
when that heap pointer happens to be page-aligned the munmap succeeds, the page holding
the struct disappears, and the following `done` event dereferences it:

    #0  dmabuf_formats_free / linux_dmabuf_done   (libgtk-4, zwp_linux_dmabuf_feedback_v1.done)
    #4  wl_closure_invoke                         (libwayland-client)
    #7  wl_display_dispatch_queue_pending

Fixed upstream by GTK commit a8a5692c ("wayland: Fix munmap() of the wrong pointer in the
dmabuf format table", MR !10166), released in 4.22.5 and 4.23.3.

The workaround tells GDK not to bind zwp_linux_dmabuf_v1 (GDK_WAYLAND_DISABLE), so no
feedback object exists and the buggy handler never runs. GDK only uses that global for
dmabuf texture import and graphics offload; the GL/Vulkan renderers present through
EGL/Vulkan WSI, which bind their own. PromptBro draws text, so nothing visible changes.

It must run before the first `from gi.repository import Gtk` (PyGObject opens the display
at import), which is why __main__ calls it before importing anything else.
"""
from __future__ import annotations

import ctypes
import os
from typing import MutableMapping, Optional, Tuple

DMABUF_GLOBAL = "zwp_linux_dmabuf_v1"
ENV = "GDK_WAYLAND_DISABLE"

Version = Tuple[int, int, int]


def gtk_runtime_version() -> Optional[Version]:
    """The libgtk-4 version this process will load, without initializing GTK."""
    try:
        lib = ctypes.CDLL("libgtk-4.so.1")
        return (int(lib.gtk_get_major_version()), int(lib.gtk_get_minor_version()),
                int(lib.gtk_get_micro_version()))
    except (OSError, AttributeError):
        return None


def dmabuf_feedback_bug(version: Optional[Version]) -> bool:
    """True if this GTK has the dmabuf format-table munmap bug (or the version is unknown)."""
    if version is None:
        return True
    if version[0] != 4:
        return False
    if version[1] == 23:
        return version < (4, 23, 3)
    return version < (4, 22, 5)


def apply_dmabuf_workaround(environ: Optional[MutableMapping[str, str]] = None,
                            version: Optional[Version] = None, *,
                            detect: bool = True) -> bool:
    """Add zwp_linux_dmabuf_v1 to GDK_WAYLAND_DISABLE when this GTK needs it.

    Keeps any interfaces already listed. Idempotent. Returns True if the global is
    disabled after the call.
    """
    env = os.environ if environ is None else environ
    if version is None and detect:
        version = gtk_runtime_version()
    current = env.get(ENV, "")
    items = [p for p in current.replace(";", ":").replace(",", ":").replace(" ", ":")
             .replace("\t", ":").split(":") if p]
    if DMABUF_GLOBAL in items:
        return True
    if not dmabuf_feedback_bug(version):
        return False
    env[ENV] = ":".join(items + [DMABUF_GLOBAL])
    return True
