try:
    from ctypes import CDLL
    CDLL("libgtk4-layer-shell.so.0")
except OSError:
    pass

from .app import main as _main

if __name__ == "__main__":
    raise SystemExit(_main())
