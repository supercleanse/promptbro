"""Entry for the commands that never touch GTK or the single-instance bus. Imported by
bin/promptbro-lite under `python3 -I`; everything it loads is standard library only."""
from __future__ import annotations

import sys

COMMANDS = ("pick-file", "doctor", "setup", "migrate", "uninstall-data")


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] in ("-h", "--help"):
        print("usage: promptbro-lite {" + "|".join(COMMANDS) + "} [args]")
        return 0 if argv else 2
    cmd, rest = argv[0], argv[1:]
    if cmd == "pick-file":
        from . import pickfile
        return pickfile.main(rest)
    if cmd == "doctor":
        from . import doctor
        return doctor.main(rest)
    if cmd == "setup":
        from . import setup
        return setup.main(rest)
    if cmd == "migrate":
        from . import migrate
        return migrate.main(rest)
    if cmd == "uninstall-data":
        from . import setup
        return setup.uninstall_main(rest)
    print(f"promptbro-lite: unknown command {cmd!r}", file=sys.stderr)
    return 2
