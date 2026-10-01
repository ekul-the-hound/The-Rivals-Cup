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
