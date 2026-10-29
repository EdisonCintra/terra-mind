import pytest
from fastapi.testclient import TestClient

from base_agricola.main import app


@pytest.fixture
def client():
    return TestClient(app)
