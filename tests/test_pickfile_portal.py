"""Portal path of promptbro.pickfile against a FAKE org.freedesktop.portal.Desktop on a private
session bus (dbus-daemon, never the user's bus, never a real dialog). The fake emits
Request.Response as a UNICAST signal addressed to the caller, as the real portal does; a
separate-connection listener (the old gdbus-monitor design) never sees it."""
import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

FAKE = textwrap.dedent('''
    import sys, os
    import gi
    gi.require_version("Gio", "2.0")
    from gi.repository import Gio, GLib
    mode, logf = sys.argv[1], sys.argv[2]
    XML = """<node><interface name="org.freedesktop.portal.FileChooser">
      <method name="OpenFile"><arg type="s" direction="in"/><arg type="s" direction="in"/>
      <arg type="a{sv}" direction="in"/><arg type="o" direction="out"/></method></interface>
      <interface name="org.freedesktop.portal.Request"><method name="Close"/></interface></node>"""
    info = Gio.DBusNodeInfo.new_for_xml(XML)
    loop = GLib.MainLoop()
    def log(s):
        open(logf, "a").write(s + "\\n")
    def respond(conn, dest, path, code, results):
        conn.emit_signal(dest, path, "org.freedesktop.portal.Request", "Response",
            GLib.Variant.new_tuple(GLib.Variant("u", code), GLib.Variant("a{sv}", results)))
    def on_call(conn, sender, path, iface, method, params, inv):
        if method == "Close":
            log("Close " + path); inv.return_value(None); return
        opts = params.unpack()[2]
        token = opts["handle_token"]
        n = sender.lstrip(":").replace(".", "_")
        real = "/org/freedesktop/portal/desktop/request/%s/%s" % (n, token)
        handle = "/org/freedesktop/portal/desktop/request/%s/other" % n if mode == "mismatch" else real
        log("handle " + handle)
        conn.register_object(handle, info.interfaces[1], on_call, None, None)
        inv.return_value(GLib.Variant("(o)", (handle,)))
        if mode == "timeout":
            return
        def go():
            if mode == "cancel":
                respond(conn, sender, handle, 1, {})
            else:
                uri = "file:///tmp/My%20Notes/caf%C3%A9%20script.md"
                respond(conn, sender, handle, 0, {"uris": GLib.Variant("as", [uri])})
            return False
        GLib.timeout_add(100, go)
    def on_bus(conn, name):
        conn.register_object("/org/freedesktop/portal/desktop", info.interfaces[0], on_call, None, None)
    Gio.bus_own_name(Gio.BusType.SESSION, "org.freedesktop.portal.Desktop",
                     Gio.BusNameOwnerFlags.NONE, on_bus, None, None)
    loop.run()
''')

CLIENT = textwrap.dedent('''
    import sys
    sys.path.insert(0, %r)
    from promptbro import pickfile
    ran, path = pickfile._try_portal("T", timeout=float(sys.argv[1]))
    print(repr((ran, path)))
''' % ROOT)


@unittest.skipUnless(shutil.which("dbus-daemon") and os.path.exists("/usr/bin/python3"), "needs dbus-daemon")
class TestPortalUnicast(unittest.TestCase):
    def _run(self, mode, timeout=5):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        log = os.path.join(tmp, "log")
        fake = os.path.join(tmp, "fake.py")
        cli = os.path.join(tmp, "cli.py")
        open(fake, "w").write(FAKE)
        open(cli, "w").write(CLIENT)
        script = (f'/usr/bin/python3 -I {fake} {mode} {log} & FP=$!; sleep 1; '
                  f'/usr/bin/python3 -I {cli} {timeout}; kill $FP')
        r = subprocess.run(["dbus-run-session", "--", "bash", "-c", script],
                           capture_output=True, text=True, timeout=30)
        logged = open(log).read() if os.path.exists(log) else ""
        return r.stdout.strip().splitlines()[-1] if r.stdout.strip() else r.stderr, logged

    def test_chosen_file_with_spaces_and_unicode(self):
        out, _ = self._run("choose")
        self.assertEqual(out, repr((True, "/tmp/My Notes/café script.md")))

    def test_cancel(self):
        out, _ = self._run("cancel")
        self.assertEqual(out, repr((True, "")))

    def test_timeout_calls_close(self):
        out, log = self._run("timeout", timeout=1)
        self.assertEqual(out, repr((False, "")))
        self.assertIn("Close /org/freedesktop/portal/desktop/request/", log)

    def test_handle_path_mismatch_still_accepted(self):
        out, log = self._run("mismatch")
        self.assertEqual(out, repr((True, "/tmp/My Notes/café script.md")))


if __name__ == "__main__":
    unittest.main()
