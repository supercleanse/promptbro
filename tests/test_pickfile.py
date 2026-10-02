"""promptbro.pickfile tests: zenity/kdialog are mocked; the portal path is covered by test_pickfile_portal.py -- never
opens a real dialog. See tests/live/test_live.py for the live-gated check,
which only confirms the portal interface is introspectable (never opens a
dialog on the user's screen either). Nothing here opens a real dialog."""
import unittest
from unittest import mock

from promptbro import pickfile


def _which_only(*names):
    names = set(names)
    return lambda n: f"/usr/bin/{n}" if n in names else None


class TestExactArgv(unittest.TestCase):
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
        with mock.patch("shutil.which", return_value=None), \
             mock.patch("promptbro.pickfile._try_portal", return_value=(False, "")):
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
             mock.patch("promptbro.pickfile._try_portal", return_value=(False, "")), \
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


class TestTrailingNewline(unittest.TestCase):
    def test_only_one_newline_removed(self):
        run = mock.Mock(return_value=mock.Mock(returncode=0, stdout="/tmp/a b \n"))
        self.assertEqual(pickfile._try_zenity("T", run=run), (True, "/tmp/a b "))


if __name__ == "__main__":
    unittest.main()
