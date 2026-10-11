from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_registration_and_login():
    response = client.post("/auth/register", json={"name": "Alice", "email": "alice@example.com", "password": "secret123"})
    assert response.status_code == 201
    login = client.post("/auth/login", json={"email": "alice@example.com", "password": "secret123"})
    assert login.status_code == 200
    data = login.json()
    assert "access_token" in data


def test_get_me_requires_auth():
    response = client.get("/auth/me")
    assert response.status_code == 401


def test_invalid_document_type():
    user = client.post("/auth/register", json={"name": "Bob", "email": "bob@example.com", "password": "secret123"})
    token = client.post("/auth/login", json={"email": "bob@example.com", "password": "secret123"}).json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    payload = b"not-a-valid-file"
    response = client.post("/documents", headers=headers, files={"file": ("bad.txt", payload, "text/plain")})
    assert response.status_code == 400


def test_chat_does_not_require_documents():
    user = client.post("/auth/register", json={"name": "Cara", "email": "cara@example.com", "password": "secret123"})
    token = client.post("/auth/login", json={"email": "cara@example.com", "password": "secret123"}).json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    response = client.post("/chat", headers=headers, json={"question": "What is the answer?"})
    assert response.status_code == 200
    assert "answer" in response.json()
