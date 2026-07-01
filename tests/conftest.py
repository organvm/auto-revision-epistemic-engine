"""Test configuration"""

import hashlib
import os
import shutil
import sys
import tempfile
import types
from pathlib import Path

import pytest


ROOT_DIR = Path(__file__).resolve().parents[1]
SRC_DIR = ROOT_DIR / "src"

if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))


try:
    import blake3  # noqa: F401
except ModuleNotFoundError:
    class _Blake3TestHash:
        """Small hash object matching the subset of blake3 used by tests."""

        def __init__(self, data: bytes = b""):
            self._hash = hashlib.blake2b(digest_size=32)
            if data:
                self.update(data)

        def update(self, data: bytes):
            self._hash.update(data)
            return self

        def digest(self, length=None):
            digest = self._hash.digest()
            return digest if length is None else digest[:length]

        def hexdigest(self, length=None):
            digest = self._hash.hexdigest()
            return digest if length is None else digest[: length * 2]

    def _blake3(data: bytes = b"", **_kwargs):
        return _Blake3TestHash(data)

    sys.modules["blake3"] = types.SimpleNamespace(blake3=_blake3)


@pytest.fixture
def temp_dir():
    """Create temporary directory for tests"""
    tmpdir = tempfile.mkdtemp()
    yield tmpdir
    shutil.rmtree(tmpdir, ignore_errors=True)


@pytest.fixture
def engine_config(temp_dir):
    """Provide test engine configuration"""
    return {
        "pipeline_id": "test_pipeline",
        "random_seed": 42,
        "audit_log_dir": os.path.join(temp_dir, "audit"),
        "state_dir": os.path.join(temp_dir, "state"),
    }
