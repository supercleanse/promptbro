"""T5: recent.json (CONTRACT 3.4)."""
import json
import os
import stat

from promptbro import recent


def mk(tmp_path, n):
    out = []
    for i in range(n):
        p = tmp_path / f"s{i}.md"
        p.write_text("x")
        out.append(p)
    return out


def test_add_newest_first_dedup_and_cap(tmp_path):
    f = tmp_path / "state" / "recent.json"
    files = mk(tmp_path, 10)
    for i, p in enumerate(files):
        recent.add(p, now=100 + i, file=f)
    got = recent.load(f)
    assert len(got) == recent.MAX_RECENT == 8
    assert got[0]["path"] == str(files[9]) and got[-1]["path"] == str(files[2])
    recent.add(files[5], now=500, file=f)
    got = recent.load(f)
    assert [e["path"] for e in got][0] == str(files[5]) and len(got) == 8
    assert [e["path"] for e in got].count(str(files[5])) == 1
    assert got[0]["opened_at"] == 500


def test_dedup_by_resolved_path(tmp_path):
    f = tmp_path / "recent.json"
    (real,) = mk(tmp_path, 1)
    link = tmp_path / "link.md"
    link.symlink_to(real)
    recent.add(real, file=f)
    recent.add(link, file=f)
    assert [e["path"] for e in recent.load(f)] == [str(real.resolve())]


def test_missing_paths_dropped_on_write(tmp_path):
    f = tmp_path / "recent.json"
    a, b = mk(tmp_path, 2)
    recent.add(a, file=f)
    recent.add(b, file=f)
    a.unlink()
    c = tmp_path / "c.md"
    c.write_text("x")
    recent.add(c, file=f)
    assert [e["path"] for e in recent.load(f)] == [str(c), str(b)]


def test_remove_and_first_existing(tmp_path):
    f = tmp_path / "recent.json"
    a, b = mk(tmp_path, 2)
    recent.add(a, file=f)
    recent.add(b, file=f)
    assert recent.first_existing(f) == str(b)
    b.unlink()
    assert recent.first_existing(f) == str(a)          # reading never rewrites
    assert len(recent.load(f)) == 2
    recent.remove(b, file=f)                           # a failed open drops the path
    assert [e["path"] for e in recent.load(f)] == [str(a)]
    recent.remove(tmp_path / "nope.md", file=f)        # unknown path: no change, no error
    assert [e["path"] for e in recent.load(f)] == [str(a)]


def test_garbage_tolerant_and_file_mode(tmp_path):
    f = tmp_path / "recent.json"
    f.write_text("not json")
    assert recent.load(f) == [] and recent.first_existing(f) is None
    f.write_text(json.dumps({"schema": 1, "recent": ["x", {"path": 3}, {"path": ""}, {"nope": 1}]}))
    assert recent.load(f) == []
    (p,) = mk(tmp_path, 1)
    recent.add(p, file=f)
    assert stat.S_IMODE(f.stat().st_mode) == 0o600
    assert json.loads(f.read_text())["schema"] == 1
    assert sorted(x for x in os.listdir(tmp_path) if x.startswith(".")) == []  # temp file gone


def test_default_location_follows_xdg_state(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "st"))
    (p,) = mk(tmp_path, 1)
    recent.add(p)
    assert (tmp_path / "st" / "promptbro" / "recent.json").is_file()
