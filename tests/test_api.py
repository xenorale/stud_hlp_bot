import pytest
import asyncio
from fastapi.testclient import TestClient
from api.server import app
from database.db import init_db

client = TestClient(app)

@pytest.fixture(autouse=True)
def setup_db():
    asyncio.run(init_db())

def test_health():
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}

def test_get_faq():
    response = client.get("/api/faq")
    assert response.status_code == 200
    assert isinstance(response.json(), list)

def test_get_profile_not_found():
    response = client.get("/api/profile/999999999")
    assert response.status_code == 404

def test_brs_no_credentials():
    response = client.get("/api/brs?telegram_id=999999999")
    assert response.status_code == 503
    assert response.json()["detail"] == "BRS credentials not configured for user"