"""Tests for model download hardening (T10): checksums and safe extraction."""
import hashlib
import json
import tarfile
import tempfile
import zipfile
from pathlib import Path
from unittest import mock

import pytest

from promptbro import models


@pytest.fixture
def tiny_tar_archive(tmp_path):
    """Create a tiny valid tar.bz2 archive with required files."""
    archive = tmp_path / "test_model.tar.bz2"
    extract_dir = tmp_path / "extract"
    extract_dir.mkdir()

    # Create required files
    model_dir = extract_dir / "model"
    model_dir.mkdir()
    (model_dir / "encoder.int8.onnx").write_bytes(b"encoder")
    (model_dir / "decoder.int8.onnx").write_bytes(b"decoder")
    (model_dir / "joiner.int8.onnx").write_bytes(b"joiner")
    (model_dir / "tokens.txt").write_bytes(b"tokens")

    # Create tar.bz2
    with tarfile.open(archive, "w:bz2") as tar:
        tar.add(model_dir, arcname="model")

    return archive


@pytest.fixture
def tiny_zip_archive(tmp_path):
    """Create a tiny valid zip archive with required files."""
    archive = tmp_path / "test_model.zip"
    extract_dir = tmp_path / "extract_zip"
    extract_dir.mkdir()

    # Create required files
    model_dir = extract_dir / "model"
    model_dir.mkdir()
    (model_dir / "encoder.int8.onnx").write_bytes(b"encoder")
    (model_dir / "decoder.int8.onnx").write_bytes(b"decoder")
    (model_dir / "joiner.int8.onnx").write_bytes(b"joiner")
    (model_dir / "tokens.txt").write_bytes(b"tokens")

    # Create zip
    with zipfile.ZipFile(archive, "w") as z:
        z.write(model_dir / "encoder.int8.onnx", "model/encoder.int8.onnx")
        z.write(model_dir / "decoder.int8.onnx", "model/decoder.int8.onnx")
        z.write(model_dir / "joiner.int8.onnx", "model/joiner.int8.onnx")
        z.write(model_dir / "tokens.txt", "model/tokens.txt")

    return archive


def compute_sha256(path: Path) -> str:
    """Compute SHA256 of a file."""
    sha256_hash = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            sha256_hash.update(chunk)
    return sha256_hash.hexdigest()


class TestChecksumVerification:
    """Test checksum verification."""

    def test_verify_checksum_valid(self, tiny_tar_archive):
        """Valid checksum passes verification."""
        sha256 = compute_sha256(tiny_tar_archive)
        assert models._verify_checksum(tiny_tar_archive, sha256)

    def test_verify_checksum_mismatch(self, tiny_tar_archive):
        """Bad checksum fails verification."""
        bad_sha256 = "0" * 64
        assert not models._verify_checksum(tiny_tar_archive, bad_sha256)

    def test_download_bad_checksum(self, tmp_path, tiny_tar_archive, monkeypatch):
        """Download fails if checksum doesn't match."""
        models_dir = tmp_path / "models"
        models_dir.mkdir()

        # Mock urllib to return our tiny archive
        def mock_urlopen(req):
            from io import BytesIO

            with open(tiny_tar_archive, "rb") as f:
                content = f.read()
            return mock.MagicMock(
                read=mock.MagicMock(side_effect=[content, b""]),
                __enter__=lambda self: self,
                __exit__=lambda *args: None,
            )

        monkeypatch.setattr("urllib.request.urlopen", mock_urlopen)

        # Register a test model with bad checksum
        test_spec = models.ModelSpec(
            "test-model",
            "http://example.com/test.tar.bz2",
            ("encoder.int8.onnx", "decoder.int8.onnx", "joiner.int8.onnx", "tokens.txt"),
            1,
            "0" * 64,
        )
        with mock.patch.dict(models.REGISTRY, {test_spec.name: test_spec}):
            with pytest.raises(RuntimeError, match="checksum mismatch"):
                models.download("test-model", models_dir, log=lambda x: None)


class TestSafePathValidation:
    """Test safe path validation."""

    def test_safe_path_normal(self):
        """Normal paths pass validation."""
        assert models._is_safe_path("model/encoder.onnx")
        assert models._is_safe_path("file.txt")
        assert models._is_safe_path("a/b/c/d.txt")

    def test_unsafe_path_dotdot(self):
        """Paths with .. are rejected."""
        assert not models._is_safe_path("../../../etc/passwd")
        assert not models._is_safe_path("model/../../../etc/passwd")
        assert not models._is_safe_path("a/../b/../c/../../d.txt")

    def test_unsafe_path_absolute(self):
        """Absolute paths are rejected."""
        assert not models._is_safe_path("/etc/passwd")
        assert not models._is_safe_path("/home/user/file.txt")

    def test_unsafe_path_windows_absolute(self):
        """Windows-style absolute paths are rejected."""
        # On Windows, paths starting with a drive letter are absolute
        assert not models._is_safe_path("C:/Windows/System32")


class TestSafeExtraction:
    """Test safe archive extraction."""

    def test_extract_tar_valid(self, tmp_path, tiny_tar_archive):
        """Valid tar.bz2 extracts successfully."""
        extract_dir = tmp_path / "extracted"
        extract_dir.mkdir()
        models._extract_safe(tiny_tar_archive, extract_dir, is_zip=False)

        # Verify files exist
        assert (extract_dir / "model" / "encoder.int8.onnx").exists()
        assert (extract_dir / "model" / "decoder.int8.onnx").exists()
        assert (extract_dir / "model" / "joiner.int8.onnx").exists()
        assert (extract_dir / "model" / "tokens.txt").exists()

    def test_extract_zip_valid(self, tmp_path, tiny_zip_archive):
        """Valid zip extracts successfully."""
        extract_dir = tmp_path / "extracted"
        extract_dir.mkdir()
        models._extract_safe(tiny_zip_archive, extract_dir, is_zip=True)

        # Verify files exist
        assert (extract_dir / "model" / "encoder.int8.onnx").exists()
        assert (extract_dir / "model" / "decoder.int8.onnx").exists()

    def test_extract_tar_path_traversal(self, tmp_path):
        """Tar with .. paths is rejected."""
        archive = tmp_path / "bad.tar.bz2"

        # Create a tar with a path traversal attempt
        extract_dir = tmp_path / "to_tar"
        extract_dir.mkdir()
        (extract_dir / "file.txt").write_bytes(b"test")

        with tarfile.open(archive, "w:bz2") as tar:
            # Add file with .. in path
            tar.add(extract_dir / "file.txt", arcname="../../../etc/passwd")

        dest = tmp_path / "dest"
        dest.mkdir()
        with pytest.raises(RuntimeError, match="unsafe path"):
            models._extract_safe(archive, dest, is_zip=False)

    def test_extract_zip_path_traversal(self, tmp_path):
        """Zip with .. paths is rejected."""
        archive = tmp_path / "bad.zip"

        with zipfile.ZipFile(archive, "w") as z:
            z.writestr("../../../etc/passwd", "evil")

        dest = tmp_path / "dest"
        dest.mkdir()
        with pytest.raises(RuntimeError, match="unsafe path"):
            models._extract_safe(archive, dest, is_zip=True)

    def test_extract_zip_absolute_path(self, tmp_path):
        """Zip with absolute paths is rejected."""
        archive = tmp_path / "bad.zip"

        with zipfile.ZipFile(archive, "w") as z:
            z.writestr("/etc/passwd", "evil")

        dest = tmp_path / "dest"
        dest.mkdir()
        with pytest.raises(RuntimeError, match="unsafe path"):
            models._extract_safe(archive, dest, is_zip=True)


class TestDownloadIntegration:
    """Integration tests for download with checksums."""

    def test_download_valid_model(self, tmp_path, tiny_tar_archive, monkeypatch):
        """Download and extraction succeeds with valid checksum."""
        models_dir = tmp_path / "models"
        models_dir.mkdir()

        sha256 = compute_sha256(tiny_tar_archive)

        # Create a mock response
        class MockResponse:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                pass

            def __init__(self):
                self._buf = open(tiny_tar_archive, "rb")

            def read(self, size=-1):
                return self._buf.read(size)

        def mock_urlopen(req):
            return MockResponse()

        monkeypatch.setattr("urllib.request.urlopen", mock_urlopen)

        # Register test model with correct checksum
        test_spec = models.ModelSpec(
            "test-model",
            "http://example.com/test.tar.bz2",
            ("encoder.int8.onnx", "decoder.int8.onnx", "joiner.int8.onnx", "tokens.txt"),
            1,
            sha256,
        )
        with mock.patch.dict(models.REGISTRY, {test_spec.name: test_spec}):
            result = models.download("test-model", models_dir, log=lambda x: None)

            # Verify model installed
            assert result == models_dir / "test-model"
            assert models.verify("test-model", models_dir)
            assert (models_dir / "test-model" / "encoder.int8.onnx").exists()

    def test_download_already_present(self, tmp_path, tiny_tar_archive):
        """Download skips if model already present."""
        models_dir = tmp_path / "models"
        model_dir = models_dir / "test-model"
        model_dir.mkdir(parents=True)

        # Create the required files
        (model_dir / "encoder.int8.onnx").write_bytes(b"encoder")
        (model_dir / "decoder.int8.onnx").write_bytes(b"decoder")
        (model_dir / "joiner.int8.onnx").write_bytes(b"joiner")
        (model_dir / "tokens.txt").write_bytes(b"tokens")

        sha256 = compute_sha256(tiny_tar_archive)

        test_spec = models.ModelSpec(
            "test-model",
            "http://example.com/test.tar.bz2",
            ("encoder.int8.onnx", "decoder.int8.onnx", "joiner.int8.onnx", "tokens.txt"),
            1,
            sha256,
        )
        with mock.patch.dict(models.REGISTRY, {test_spec.name: test_spec}):
            # Download should skip since model already exists
            log_messages = []
            result = models.download(
                "test-model", models_dir, log=lambda x: log_messages.append(x)
            )
            assert result == model_dir
            assert any("already present" in msg for msg in log_messages)


EXPECTED_SHA256 = {
    "sherpa-onnx-nemo-streaming-fast-conformer-transducer-en-80ms-int8":
        "7bd33a914e93370a1ba9c2066d9e841bdcad8613fa2a00537c1ae15d851a14d8",
    "sherpa-onnx-streaming-zipformer-en-2023-06-26":
        "639e25b578e9e997131402199419c13a941f8e4e198e2da1ce57dbf5cf401282",
    "sherpa-onnx-nemotron-speech-streaming-en-0.6b-160ms-int8-2026-04-25":
        "0ae73a41cd51599dc7cac9ac083d9d35de53d762ca45923505fde47a3751814b",
    "sherpa-onnx-nemotron-speech-streaming-en-0.6b-80ms-int8-2026-04-25":
        "caaf92069dbd1ca054f8e17cab179813bc28b4585f5c392540357ece4722333d",
    "vosk-model-small-en-us-0.15":
        "30f26242c4eb449f948e42cb302dd7a686cb29a3423a8367f99ff41780942498",
}


class TestRegistryChecksums:
    """Guard against placeholder digests in the real registry."""

    def test_expected_digests_pinned(self):
        actual = {n: s.sha256 for n, s in models.REGISTRY.items()}
        assert actual == EXPECTED_SHA256

    def test_digests_are_64_hex_and_not_placeholders(self):
        placeholders = {hashlib.sha256(str(i).encode()).hexdigest() for i in range(100)}
        placeholders |= {hashlib.sha256(b"").hexdigest()}
        for name, spec in models.REGISTRY.items():
            assert len(spec.sha256) == 64 and all(c in "0123456789abcdef" for c in spec.sha256), name
            assert spec.sha256 not in placeholders, name
            assert len(set(spec.sha256)) > 8, name
