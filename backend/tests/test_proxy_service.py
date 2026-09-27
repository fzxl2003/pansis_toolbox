from __future__ import annotations

import pytest

from backend.app.core.errors import ToolboxError
from backend.app.db import database
from backend.app.services import proxy_service


@pytest.fixture(autouse=True)
def isolated_platform_db(monkeypatch, tmp_path):
    monkeypatch.setattr(database.get_settings(), "platform_db_path", tmp_path / "platform.db")


def test_global_proxy_only_applies_to_enabled_domains() -> None:
    proxy_service.save_settings({
        "protocol": "socks5", "host": "127.0.0.1", "port": 1080,
        "selectedDomains": ["github.com"], "customDomains": ["example.com"],
    })
    assert proxy_service.get_proxy_url_for_host("github.com") == "socks5://127.0.0.1:1080"
    assert proxy_service.get_proxy_url_for_host("api.github.com") == "socks5://127.0.0.1:1080"
    assert proxy_service.get_proxy_url_for_host("example.com") == ""


def test_custom_domain_must_be_valid() -> None:
    with pytest.raises(ToolboxError, match="域名"):
        proxy_service.save_settings({
            "protocol": "http", "host": "proxy.internal", "port": 7890,
            "selectedDomains": [], "customDomains": ["not a hostname"],
        })
