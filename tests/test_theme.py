import logging

from promptbro import theme


def test_reads_accent(tmp_path):
    f = tmp_path / "colors.toml"
    f.write_text('accent = "#123456"\n')
    pal = theme.load_palette(f)
    assert pal["accent"] == "#123456"
    assert pal["background"] == theme.FALLBACK["background"]


def test_missing_file_fallback_one_warning(tmp_path, caplog):
    theme._warned = False
    with caplog.at_level(logging.WARNING, logger="promptbro.theme"):
        a = theme.load_palette(tmp_path / "nope.toml")
        b = theme.load_palette(tmp_path / "nope.toml")
    assert a == b == theme.FALLBACK
    assert a["accent"] == "#e68e0d"
    assert len([r for r in caplog.records if r.levelno == logging.WARNING]) == 1


def test_css_defines():
    css = theme.css_defines({"accent": "#123456", "mode": "dark"})
    assert "@define-color tp_accent #123456;" in css
    assert "mode" not in css
