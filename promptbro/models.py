"""Model registry, download, verify and path resolution for the ASR engines."""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import shutil
import sys
import tarfile
import tempfile
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path, PureWindowsPath

from . import paths

_GH = "https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/"


@dataclass(frozen=True)
class ModelSpec:
    name: str
    url: str
    files: tuple
    size_mb: int
    sha256: str
    engine: str = "sherpa-onnx"
    uppercase: bool = False


@dataclass(frozen=True)
class ModelPaths:
    name: str
    dir: Path
    tokens: Path
    encoder: Path
    decoder: Path
    joiner: Path
    uppercase: bool = False


REGISTRY = {
    s.name: s
    for s in (
        ModelSpec(
            "sherpa-onnx-nemo-streaming-fast-conformer-transducer-en-80ms-int8",
            _GH + "sherpa-onnx-nemo-streaming-fast-conformer-transducer-en-80ms-int8.tar.bz2",
            ("encoder.int8.onnx", "decoder.int8.onnx", "joiner.int8.onnx", "tokens.txt"),
            103,
            "7bd33a914e93370a1ba9c2066d9e841bdcad8613fa2a00537c1ae15d851a14d8",
        ),
        ModelSpec(
            "sherpa-onnx-streaming-zipformer-en-2023-06-26",
            _GH + "sherpa-onnx-streaming-zipformer-en-2023-06-26.tar.bz2",
            (
                "encoder-epoch-99-avg-1-chunk-16-left-128.int8.onnx",
                "decoder-epoch-99-avg-1-chunk-16-left-128.int8.onnx",
                "joiner-epoch-99-avg-1-chunk-16-left-128.int8.onnx",
                "tokens.txt",
            ),
            120,
            "639e25b578e9e997131402199419c13a941f8e4e198e2da1ce57dbf5cf401282",
            uppercase=True,
        ),
        ModelSpec(
            "sherpa-onnx-nemotron-speech-streaming-en-0.6b-160ms-int8-2026-04-25",
            _GH + "sherpa-onnx-nemotron-speech-streaming-en-0.6b-160ms-int8-2026-04-25.tar.bz2",
            ("encoder.int8.onnx", "decoder.int8.onnx", "joiner.int8.onnx", "tokens.txt"),
            464,
            "0ae73a41cd51599dc7cac9ac083d9d35de53d762ca45923505fde47a3751814b",
        ),
        ModelSpec(
            "sherpa-onnx-nemotron-speech-streaming-en-0.6b-80ms-int8-2026-04-25",
            _GH + "sherpa-onnx-nemotron-speech-streaming-en-0.6b-80ms-int8-2026-04-25.tar.bz2",
            ("encoder.int8.onnx", "decoder.int8.onnx", "joiner.int8.onnx", "tokens.txt"),
            464,
            "caaf92069dbd1ca054f8e17cab179813bc28b4585f5c392540357ece4722333d",
        ),
        ModelSpec(
            "vosk-model-small-en-us-0.15",
            "https://alphacephei.com/vosk/models/vosk-model-small-en-us-0.15.zip",
            ("am/final.mdl", "conf/model.conf"),
            40,
            "30f26242c4eb449f948e42cb302dd7a686cb29a3423a8367f99ff41780942498",
            engine="vosk",
        ),
    )
}


# The default engine model (DESIGN 5.1, BUILD_LOG "Snappiness"): 600M Nemotron streaming RNNT at
# 160 ms chunks. The former default, the 114M NeMo fast-conformer 80 ms, stays selectable.
DEFAULT_MODEL = "sherpa-onnx-nemotron-speech-streaming-en-0.6b-160ms-int8-2026-04-25"
PREVIOUS_MODEL = "sherpa-onnx-nemo-streaming-fast-conformer-transducer-en-80ms-int8"


def default_models_dir() -> Path:
    return paths.models_dir()


def _spec(name: str) -> ModelSpec:
    if name not in REGISTRY:
        raise KeyError(f"unknown model {name!r}; known: {', '.join(REGISTRY)}")
    return REGISTRY[name]


def _verify_checksum(archive: Path, expected_sha256: str) -> bool:
    """Verify file SHA256 checksum."""
    sha256_hash = hashlib.sha256()
    with open(archive, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            sha256_hash.update(chunk)
    return sha256_hash.hexdigest() == expected_sha256


def _is_safe_path(path: str) -> bool:
    """Check if a path is safe to extract (no .., no absolute paths)."""
    if PureWindowsPath(path).drive or PureWindowsPath(path).root:
        return False
    parts = Path(path).parts
    for part in parts:
        if part == ".." or part.startswith("/"):
            return False
    return True


def _extract_safe(archive: Path, extract_to: Path, is_zip: bool) -> None:
    """Safely extract archive, rejecting dangerous paths."""
    if is_zip:
        with zipfile.ZipFile(archive) as z:
            for member in z.namelist():
                if not _is_safe_path(member):
                    raise RuntimeError(f"unsafe path in archive: {member}")
            z.extractall(extract_to)
    else:
        with tarfile.open(archive) as t:
            for member in t.getmembers():
                if not _is_safe_path(member.name):
                    raise RuntimeError(f"unsafe path in archive: {member.name}")
            t.extractall(extract_to, filter="data")


MANIFEST = ".promptbro-install.json"
TMP_PREFIX = ".promptbro-dl-"
OLD_PREFIX = ".promptbro-old-"
NET_TIMEOUT_S = 30


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _write_manifest(root: Path, spec: ModelSpec) -> None:
    files = {}
    for rel in spec.files:
        p = root / rel
        files[rel] = {"size": p.stat().st_size, "sha256": _sha256_file(p)}
    (root / MANIFEST).write_text(
        json.dumps({"archive_sha256": spec.sha256, "files": files}, indent=1), encoding="utf-8")


def _read_manifest(d: Path):
    try:
        m = json.loads((d / MANIFEST).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return m if isinstance(m, dict) and isinstance(m.get("files"), dict) else None


def has_manifest(name: str, models_dir) -> bool:
    return _read_manifest(Path(models_dir).expanduser() / name) is not None


def verify(name: str, models_dir, deep: bool = False) -> bool:
    """Cheap by default (presence, and sizes against the install manifest when there is one).

    deep=True rehashes every file against the manifest. A model with no manifest (a legacy
    install) stays valid on the cheap check."""
    spec = _spec(name)
    d = Path(models_dir).expanduser() / name
    if not all((d / f).is_file() and (d / f).stat().st_size > 0 for f in spec.files):
        return False
    man = _read_manifest(d)
    if man is None:
        return True
    if man.get("archive_sha256") != spec.sha256:
        return False
    for rel in spec.files:
        ent = man["files"].get(rel)
        if not isinstance(ent, dict) or (d / rel).stat().st_size != ent.get("size"):
            return False
        if deep and _sha256_file(d / rel) != ent.get("sha256"):
            return False
    return True


def stale_tmp_dirs(models_dir) -> list:
    """Old-style tmpXXXXXX leftovers from interrupted installs (listed by setup, never deleted)."""
    md = Path(models_dir).expanduser()
    try:
        return sorted(p.name for p in md.iterdir() if p.is_dir() and p.name.startswith("tmp"))
    except OSError:
        return []


def download(name: str, models_dir, log=print, force: bool = False) -> Path:
    spec = _spec(name)
    models_dir = Path(models_dir).expanduser()
    dest = models_dir / name
    if not force and verify(name, models_dir):
        log(f"{name}: already present at {dest}")
        return dest
    models_dir.mkdir(parents=True, exist_ok=True)
    with open(models_dir / ".install.lock", "a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        # Whoever held the lock before us may have just installed it.
        if not force and verify(name, models_dir):
            log(f"{name}: already present at {dest}")
            return dest
        for p in models_dir.glob(TMP_PREFIX + "*"):
            shutil.rmtree(p, ignore_errors=True)
        for p in models_dir.glob(OLD_PREFIX + "*"):
            shutil.rmtree(p, ignore_errors=True)
        tmp = Path(tempfile.mkdtemp(prefix=TMP_PREFIX, dir=models_dir))
        try:
            archive = tmp / Path(spec.url).name
            log(f"downloading {spec.url} (~{spec.size_mb} MB)")
            req = urllib.request.Request(spec.url, headers={"User-Agent": "promptbro/0.2"})
            with urllib.request.urlopen(req, timeout=NET_TIMEOUT_S) as r, open(archive, "wb") as f:
                shutil.copyfileobj(r, f)
            log("verifying checksum")
            if not _verify_checksum(archive, spec.sha256):
                raise RuntimeError(f"{name}: checksum mismatch; archive may be corrupted")
            ex = tmp / "x"
            ex.mkdir()
            _extract_safe(archive, ex, archive.suffix == ".zip")
            tops = [p for p in ex.iterdir()]
            src = tops[0] if len(tops) == 1 and tops[0].is_dir() else ex
            missing = [f for f in spec.files if not (src / f).is_file() or (src / f).stat().st_size == 0]
            if missing:
                raise RuntimeError(f"{name}: archive is missing required files: {', '.join(missing)}")
            _write_manifest(src, spec)
            old = None
            if dest.exists() or dest.is_symlink():
                old = models_dir / f"{OLD_PREFIX}{os.urandom(4).hex()}"
                os.rename(dest, old)
            os.rename(src, dest)
            if old is not None:
                shutil.rmtree(old, ignore_errors=True)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
    if not verify(name, models_dir):
        raise RuntimeError(f"{name}: download finished but required files are missing in {dest}")
    log(f"{name}: installed to {dest}")
    return dest


def resolve_dir(d) -> ModelPaths:
    """Paths for a transducer directory that is not in the registry (benchmarks, experiments)."""
    d = Path(d).expanduser()

    def pick(stem: str) -> Path:
        c = sorted(d.glob(f"{stem}*.int8.onnx")) or sorted(d.glob(f"{stem}*.onnx"))
        if not c:
            raise FileNotFoundError(f"no {stem}*.onnx in {d}")
        return c[0]

    tok = d / "tokens.txt"
    if not tok.is_file():
        raise FileNotFoundError(f"no tokens.txt in {d}")
    upper = any(ln[:1].isupper() for ln in tok.read_text(encoding="utf-8").splitlines()[5:50])
    return ModelPaths(d.name, d, tok, pick("encoder"), pick("decoder"), pick("joiner"), upper)


def resolve(cfg) -> ModelPaths:
    """cfg is an ASRConfig (or anything with .model and .models_dir)."""
    name = cfg.model
    spec = _spec(name)
    if spec.engine != "sherpa-onnx":
        raise ValueError(f"{name} is not a sherpa-onnx model")
    d = Path(cfg.models_dir).expanduser() / name
    if not verify(name, cfg.models_dir):
        raise FileNotFoundError(
            f"model {name} not found in {d}; run: python -m promptbro.models download {name}"
        )
    enc, dec, joi, tok = (d / f for f in spec.files)
    return ModelPaths(name, d, tok, enc, dec, joi, spec.uppercase)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="promptbro.models")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list")
    for c in ("download", "verify"):
        p = sub.add_parser(c)
        p.add_argument("name", nargs="?", default=DEFAULT_MODEL)
        p.add_argument("--models-dir", default=str(default_models_dir()))
    a = ap.parse_args(argv)
    if a.cmd == "list":
        for s in REGISTRY.values():
            print(f"{s.name}\t{s.engine}\t~{s.size_mb} MB")
        return 0
    try:
        if a.cmd == "download":
            download(a.name, a.models_dir)
            return 0
        ok = verify(a.name, a.models_dir)
        print(f"{a.name}: {'ok' if ok else 'missing'}")
        return 0 if ok else 1
    except Exception as e:  # noqa: BLE001
        print(f"error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
