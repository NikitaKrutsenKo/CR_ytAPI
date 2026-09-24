import pytest
from research.storage.db import DatabaseStorageAdapter


@pytest.fixture(autouse=True)
def clean_postgres_db():
    """Autouse fixture to ensure a clean database state for tests."""
    try:
        adapter = DatabaseStorageAdapter()
        adapter.clean_tables()
    except Exception:
        pass
    yield
    try:
        adapter = DatabaseStorageAdapter()
        adapter.clean_tables()
    except Exception:
        pass


@pytest.fixture
def pg_db():
    """Fixture providing a DatabaseStorageAdapter instance with clean state."""
    adapter = DatabaseStorageAdapter()
    adapter.clean_tables()
    return adapter
