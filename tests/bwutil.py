"""Broadway display helpers shared by the tests that render or run the app.

`gtk4-broadwayd :N` listens on broadway<N+1>.socket and leaves the socket behind when it is
killed, and concurrent test runs used to fight over fixed numbers. Each test now takes a free
number and removes the sockets afterward."""
import os
import random


def _rt() -> str:
    return os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")


def free_display() -> int:
    while True:
        n = random.randint(100, 900)
        if not any(os.path.exists(f"{_rt()}/broadway{k}.socket") for k in (n, n + 1)):
            return n


def stop_display(srv, disp: int) -> None:
    srv.terminate()
    try:
        srv.wait(5)
    except Exception:  # noqa: BLE001
        srv.kill()
    for k in (disp, disp + 1):
        try:
            os.unlink(f"{_rt()}/broadway{k}.socket")
        except OSError:
            pass
