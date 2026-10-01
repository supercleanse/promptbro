"""promptbro.pickfile tests: gdbus/zenity/kdialog are all mocked -- never
opens a real dialog. See tests/live/test_live.py for the live-gated check,
which only confirms the portal interface is introspectable (never opens a
dialog on the user's screen either). Nothing here opens a real dialog."""
import unittest
from unittest import mock

from promptbro import pickfile


def _which_only(*names):
    names = set(names)
    return lambda n: f"/usr/bin/{n}" if n in names else None


class TestPortalCallHandle(unittest.TestCase):
    def test_extracts_handle_from_gdbus_output(self):
        out = ("(objectpath '/org/freedesktop/portal/desktop/request/1_2/t0',)\n")
        run = mock.Mock(return_value=mock.Mock(returncode=0, stdout=out))
        handle = pickfile._portal_call_handle("Choose", run=run)
        self.assertEqual(handle, "/org/freedesktop/portal/desktop/request/1_2/t0")

    def test_nonzero_exit_is_none(self):
        run = mock.Mock(return_value=mock.Mock(returncode=1, stdout=""))
        self.assertIsNone(pickfile._portal_call_handle("Choose", run=run))

    def test_oserror_is_none(self):
        run = mock.Mock(side_effect=OSError("no gdbus"))
        self.assertIsNone(pickfile._portal_call_handle("Choose", run=run))


class TestPortalWaitResponse(unittest.TestCase):
    def _popen_with_lines(self, lines):
        proc = mock.Mock()
        proc.stdout = iter(lines)
        proc.poll.return_value = 0  # already exited, so no terminate() needed
        return mock.Mock(return_value=proc)

    def test_success_extracts_path(self):
        handle = "/org/.../request/1/t0"
        line = (f"{handle}: org.freedesktop.portal.Request.Response "
                 "(uint32 0, {'uris': <['file:///home/morgan/Downloads/a.md']>})\n")
        popen = self._popen_with_lines([line])
        result = pickfile._portal_wait_response(handle, popen=popen, timeout=5)
        self.assertEqual(result, "/home/morgan/Downloads/a.md")

    def test_canceled_returns_empty_string(self):
        handle = "/org/.../request/1/t0"
        line = f"{handle}: org.freedesktop.portal.Request.Response (uint32 1, {{}})\n"
        popen = self._popen_with_lines([line])
        result = pickfile._portal_wait_response(handle, popen=popen, timeout=5)
        self.assertEqual(result, "")

    def test_unrelated_lines_are_ignored(self):
        handle = "/org/.../request/1/t0"
        lines = [
            "/some/other/handle: org.freedesktop.portal.Request.Response (uint32 0, {})\n",
            f"{handle}: org.freedesktop.portal.Request.Response (uint32 0, "
            "{'uris': <['file:///tmp/x']>})\n",
        ]
        popen = self._popen_with_lines(lines)
        result = pickfile._portal_wait_response(handle, popen=popen, timeout=5)
        self.assertEqual(result, "/tmp/x")

    def test_no_response_line_ever_is_none(self):
        popen = self._popen_with_lines([])
        result = pickfile._portal_wait_response("/x", popen=popen, timeout=5)
        self.assertIsNone(result)

    def test_oserror_launching_monitor_is_none(self):
        popen = mock.Mock(side_effect=OSError("no gdbus"))
        self.assertIsNone(pickfile._portal_wait_response("/x", popen=popen))

    def test_response_with_no_uris_is_none(self):
        handle = "/x"
        line = f"{handle}: org.freedesktop.portal.Request.Response (uint32 0, {{}})\n"
        popen = self._popen_with_lines([line])
        result = pickfile._portal_wait_response(handle, popen=popen, timeout=5)
        self.assertIsNone(result)


class TestExactArgv(unittest.TestCase):
    def test_portal_call_argv(self):
        run = mock.Mock(return_value=mock.Mock(returncode=0, stdout="(objectpath '/h',)\n"))
        pickfile._portal_call_handle("Choose a script", run=run)
        cmd = run.call_args.args[0]
        self.assertEqual(cmd[:8], ["gdbus", "call", "--session", "--dest", "org.freedesktop.portal.Desktop",
                                   "--object-path", "/org/freedesktop/portal/desktop", "--method"])
        self.assertEqual(cmd[8:11], ["org.freedesktop.portal.FileChooser.OpenFile", "", "Choose a script"])
        opts = cmd[11]
        self.assertIn("'multiple': <false>", opts)
        self.assertNotIn("'directory'", opts)
        self.assertNotIn("current_folder", opts)
        for pat in ("*.md", "*.txt", "*.markdown"):
            self.assertIn(f"(uint32 0, '{pat}')", opts)
        self.assertIn("('All files', [(uint32 0, '*')])", opts)
        self.assertIn("'current_filter': <('Scripts'", opts)

    def test_zenity_argv(self):
        run = mock.Mock(return_value=mock.Mock(returncode=0, stdout="/x.md\n"))
        pickfile._try_zenity("T", run=run)
        self.assertEqual(run.call_args.args[0],
                         ["zenity", "--file-selection", "--title=T", "--file-filter=Scripts | *.md *.txt *.markdown"])
        self.assertNotIn("--directory", run.call_args.args[0])
        self.assertEqual(run.call_args.kwargs["timeout"], 600.0)

    def test_kdialog_argv(self):
        run = mock.Mock(return_value=mock.Mock(returncode=0, stdout="/x.md\n"))
        pickfile._try_kdialog("T", run=run)
        self.assertEqual(run.call_args.args[0],
                         ["kdialog", "--getopenfilename", "~", "*.md *.txt *.markdown", "--title=T"])

    def test_wait_timeout_is_ten_minutes(self):
        self.assertEqual(pickfile.WAIT_TIMEOUT_S, 600)


class TestMainExitCodes(unittest.TestCase):
    def test_chosen_prints_path(self):
        with mock.patch("promptbro.pickfile.pick_file", return_value=(0, "/a/b.md")), \
             mock.patch("builtins.print") as pr:
            self.assertEqual(pickfile.main([]), 0)
        pr.assert_called_once_with("/a/b.md")

    def test_canceled_is_1(self):
        with mock.patch("promptbro.pickfile.pick_file", return_value=(1, "")):
            self.assertEqual(pickfile.main([]), 1)

    def test_no_picker_is_2(self):
        with mock.patch("promptbro.pickfile.pick_file", return_value=(2, "")):
            self.assertEqual(pickfile.main(["--title", "x"]), 2)


class TestPathFromUri(unittest.TestCase):
    def test_plain_path(self):
        self.assertEqual(pickfile._path_from_uri("file:///home/morgan/Scripts"), "/home/morgan/Scripts")

    def test_url_encoded_space(self):
        self.assertEqual(pickfile._path_from_uri("file:///home/morgan/My%20Notes"),
                          "/home/morgan/My Notes")


class TestZenityFallback(unittest.TestCase):
    def test_success(self):
        run = mock.Mock(return_value=mock.Mock(returncode=0, stdout="/home/morgan/Downloads/a.md\n"))
        ran, path = pickfile._try_zenity("Choose", run=run)
        self.assertTrue(ran)
        self.assertEqual(path, "/home/morgan/Downloads/a.md")

    def test_cancel_exit_code_1(self):
        run = mock.Mock(return_value=mock.Mock(returncode=1, stdout=""))
        ran, path = pickfile._try_zenity("Choose", run=run)
        self.assertTrue(ran)
        self.assertEqual(path, "")

    def test_not_installed_oserror(self):
        run = mock.Mock(side_effect=OSError("no zenity"))
        ran, path = pickfile._try_zenity("Choose", run=run)
        self.assertFalse(ran)


class TestKdialogFallback(unittest.TestCase):
    def test_success(self):
        run = mock.Mock(return_value=mock.Mock(returncode=0, stdout="/home/morgan/Scripts\n"))
        ran, path = pickfile._try_kdialog("Choose", run=run)
        self.assertTrue(ran)
        self.assertEqual(path, "/home/morgan/Scripts")

    def test_cancel(self):
        run = mock.Mock(return_value=mock.Mock(returncode=1, stdout=""))
        ran, path = pickfile._try_kdialog("Choose", run=run)
        self.assertTrue(ran)
        self.assertEqual(path, "")


class TestPickFileDispatch(unittest.TestCase):
    def test_no_tool_at_all_exits_2(self):
        with mock.patch("shutil.which", return_value=None):
            code, path = pickfile.pick_file("Choose")
        self.assertEqual(code, 2)
        self.assertEqual(path, "")

    def test_portal_success_short_circuits_others(self):
        with mock.patch("shutil.which", _which_only("gdbus", "zenity")), \
             mock.patch("promptbro.pickfile._try_portal", return_value=(True, "/home/morgan/Calls")), \
             mock.patch("promptbro.pickfile._try_zenity") as zenity_mock:
            code, path = pickfile.pick_file("Choose")
        self.assertEqual((code, path), (0, "/home/morgan/Calls"))
        zenity_mock.assert_not_called()

    def test_portal_cancel_does_not_fall_back(self):
        with mock.patch("shutil.which", _which_only("gdbus", "zenity")), \
             mock.patch("promptbro.pickfile._try_portal", return_value=(True, "")), \
             mock.patch("promptbro.pickfile._try_zenity") as zenity_mock:
            code, path = pickfile.pick_file("Choose")
        self.assertEqual((code, path), (1, ""))
        zenity_mock.assert_not_called()

    def test_portal_unusable_falls_back_to_zenity(self):
        with mock.patch("shutil.which", _which_only("gdbus", "zenity")), \
             mock.patch("promptbro.pickfile._try_portal", return_value=(False, "")), \
             mock.patch("promptbro.pickfile._try_zenity", return_value=(True, "/home/morgan/Calls")):
            code, path = pickfile.pick_file("Choose")
        self.assertEqual((code, path), (0, "/home/morgan/Calls"))

    def test_falls_through_to_kdialog(self):
        with mock.patch("shutil.which", _which_only("zenity", "kdialog")), \
             mock.patch("promptbro.pickfile._try_zenity", return_value=(False, "")), \
             mock.patch("promptbro.pickfile._try_kdialog", return_value=(True, "/home/morgan/Calls")):
            code, path = pickfile.pick_file("Choose")
        self.assertEqual((code, path), (0, "/home/morgan/Calls"))

    def test_all_unusable_exits_2(self):
        with mock.patch("shutil.which", _which_only("gdbus", "zenity", "kdialog")), \
             mock.patch("promptbro.pickfile._try_portal", return_value=(False, "")), \
             mock.patch("promptbro.pickfile._try_zenity", return_value=(False, "")), \
             mock.patch("promptbro.pickfile._try_kdialog", return_value=(False, "")):
            code, path = pickfile.pick_file("Choose")
        self.assertEqual((code, path), (2, ""))


if __name__ == "__main__":
    unittest.main()
