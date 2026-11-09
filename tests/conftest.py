from __future__ import annotations

import pytest

from .support import APPROVER, Harness, generate_key


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture(scope="session")
def keys() -> dict:
    return {"approver": generate_key(), "attacker": generate_key()}


@pytest.fixture
def harness(tmp_path, monkeypatch, keys) -> Harness:
    return Harness(tmp_path, monkeypatch, keys, APPROVER_ADDRESS=APPROVER)


@pytest.fixture
def make_harness(tmp_path, monkeypatch, keys):
    def build(**overrides: str) -> Harness:
        return Harness(tmp_path, monkeypatch, keys, **overrides)

    return build
