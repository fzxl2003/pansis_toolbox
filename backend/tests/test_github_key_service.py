from __future__ import annotations

import uuid

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from backend.app.db import database
from backend.app.services import github_key_service
from backend.app.services.auth_service import User


@pytest.fixture(autouse=True)
def isolated_platform_db(monkeypatch, tmp_path):
    monkeypatch.setattr(database.get_settings(), "platform_db_path", tmp_path / "platform.db")


def owner() -> User:
    return User(id="owner", username="owner", display_name="Owner")


def test_created_key_uses_openssh_private_key_format() -> None:
    created = github_key_service.create_key("blog", owner())
    private_key = github_key_service.get_private_key(created["id"], owner())
    assert private_key.startswith("-----BEGIN OPENSSH PRIVATE KEY-----")
    serialization.load_ssh_private_key(private_key.encode(), password=None)


def test_legacy_pkcs8_key_is_converted_when_used() -> None:
    database.init_database()
    legacy = Ed25519PrivateKey.generate().private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    ).decode()
    key_id = uuid.uuid4().hex
    with database.get_connection() as conn:
        conn.execute(
            "INSERT INTO platform_github_keys(id,owner_user_id,name,private_key_encrypted,public_key,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",
            (key_id, "owner", "legacy", github_key_service._fernet().encrypt(legacy.encode()).decode(), "public", "now", "now"),
        )
    converted = github_key_service.get_private_key(key_id, owner())
    assert converted.startswith("-----BEGIN OPENSSH PRIVATE KEY-----")
    serialization.load_ssh_private_key(converted.encode(), password=None)
