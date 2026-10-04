import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.api.deps import get_store
from app.api.main import app
from app.db.memory import InMemoryDB

FIXTURE = Path(__file__).parent / "fixtures" / "data.json"


FakeStore = InMemoryDB


@pytest.fixture
def data() -> dict[str, list[dict[str, Any]]]:
    return json.loads(FIXTURE.read_text())


@pytest.fixture
def store(data) -> FakeStore:
    return FakeStore(data)


@pytest.fixture
def client(store):
    app.dependency_overrides[get_store] = lambda: store
    yield TestClient(app)
    app.dependency_overrides.clear()


def pytest_pyfunc_call(pyfuncitem):
    """Run `async def` tests without a plugin dependency."""
    import asyncio
    import inspect

    if inspect.iscoroutinefunction(pyfuncitem.obj):
        args = {a: pyfuncitem.funcargs[a] for a in pyfuncitem._fixtureinfo.argnames}
        asyncio.run(pyfuncitem.obj(**args))
        return True


@pytest.fixture(autouse=True)
def _isolate_tests_from_real_env(monkeypatch):
    """Tests must not read the developer's real .env or shell secrets (regression: with real keys set,
    tests that expect 'no key configured' failed). Tests that need a setting pass it explicitly."""
    from app.config.settings import Settings

    monkeypatch.setitem(Settings.model_config, "env_file", None)
    for name in Settings.model_fields:
        monkeypatch.delenv(name.upper(), raising=False)
    monkeypatch.delenv("DASHBOARD_MODE", raising=False)  # tests always use the offline mock world
