from __future__ import annotations

import json
import re
import shutil
import subprocess
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.app.core.config import Settings
from backend.app.db import database
from backend.app.services.auth_service import User
from tools.airplay_subscription_manager.backend import service
from tools.airplay_subscription_manager.backend.router import _subscription_details_html, mount_extra


@pytest.mark.parametrize("already_open", [False, True])
@pytest.mark.parametrize("request_ok", [False, True])
def test_public_access_button_script(already_open: bool, request_ok: bool) -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is required to execute the generated page script")
    deadline = "2026-11-05T10:05:00+00:00"
    page = _subscription_details_html("token", {"name": "protected"}, access={
        "passwordRequired": True, "publicAccessOpen": already_open,
        "publicAccessUntil": deadline if already_open else None,
    })
    script = re.search(r"<script>(.*?)</script>", page, re.S).group(1)
    # Execute the rendered script, since Python string escaping can break all handlers.
    harness = r'''
const assert = require("node:assert/strict");
const vm = require("node:vm");
const input = JSON.parse(require("node:fs").readFileSync(0, "utf8"));
const handlers = {};
const button = {disabled: false, addEventListener: (type, handler) => handlers[type] = handler};
const status = {textContent: "closed", getAttribute: () => input.alreadyOpen ? input.deadline : ""};
const requests = [];
let reloaded = false, cleared = false;
const context = {
  document: {
    querySelectorAll: () => [], getElementById: () => null,
    querySelector: selector => ({"[data-open-public-access]": button, "[data-access-status]": status})[selector] || null
  },
  Date: class extends Date { static now() { return Date.parse("2026-11-05T10:00:00+00:00"); } },
  window: {
    location: {pathname: "/sub/airplay/details/token", reload: () => reloaded = true},
    setTimeout: () => 1, clearTimeout: () => cleared = true
  },
  fetch: (url, options) => {
    requests.push({url, method: options.method});
    return Promise.resolve({ok: input.requestOk, json: () => Promise.resolve({openUntil: input.deadline})});
  }
};
vm.runInNewContext(input.script, context);
assert.equal(status.textContent, input.alreadyOpen ? "剩余 05:00" : "closed");
assert.equal(typeof handlers.click, "function");
handlers.click();
assert.equal(button.disabled, true);
assert.equal(status.textContent, "正在开放…");
assert.deepEqual(requests, [{url: "/sub/airplay/details/token/open", method: "POST"}]);
assert.equal(cleared, input.alreadyOpen);
setImmediate(() => {
  assert.equal(reloaded, input.requestOk);
  if (!input.requestOk) {
    assert.equal(button.disabled, false);
    assert.equal(status.textContent, "操作失败");
  }
});
'''
    result = subprocess.run([node, "-e", harness], input=json.dumps({
        "script": script, "alreadyOpen": already_open, "requestOk": request_ok, "deadline": deadline,
    }), text=True, capture_output=True, timeout=10)
    assert result.returncode == 0, result.stderr


def test_public_access_login_open_renew_and_expire(tmp_path, monkeypatch) -> None:
    settings = Settings(storage_dir=tmp_path / "storage", platform_db_path=tmp_path / "storage" / "platform.db", session_secret="test-secret")
    monkeypatch.setattr(service, "get_settings", lambda: settings)
    monkeypatch.setattr(database, "get_settings", lambda: settings)
    service._initialized.clear()
    now = datetime(2026, 11, 5, 10, tzinfo=timezone.utc)

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return now.astimezone(tz)

    monkeypatch.setattr(service, "datetime", Clock)
    monkeypatch.setattr(service.time, "time", lambda: now.timestamp())
    user = User(id="public-access-user", username="access", display_name="Access")
    rule_set = service.save_rule_set({"name": "direct", "rules": ["MATCH,DIRECT"]}, user)
    profile = service.create_profile({
        "name": "protected", "ruleSetId": rule_set["id"],
        "passwordRequired": True, "accessPassword": "test-password",
    }, user)
    published = service.publish_profile(profile["id"], user)
    token = published["subscriptionToken"]
    details_url = f"/sub/airplay/details/{token}"
    download_url = f"/sub/airplay/{token}"
    app = FastAPI()
    mount_extra(app)
    with TestClient(app) as client:
        assert client.get(details_url).status_code == 401
        assert client.post(details_url + "/open").status_code == 404
        assert client.get(download_url).status_code == 404
        assert client.post(details_url + "/login", json={"password": "wrong"}).status_code == 401
        assert client.post(details_url + "/login", json={"password": "test-password"}).status_code == 200
        assert "开放 5 分钟" in client.get(details_url).text
        response = client.post(details_url + "/open")
        assert response.status_code == 200
        assert datetime.fromisoformat(response.json()["openUntil"]) == now + timedelta(minutes=5)
        assert client.get(download_url).status_code == 200
        assert "续期 5 分钟" in client.get(details_url).text
        now += timedelta(minutes=4)
        renewed = client.post(details_url + "/open")
        assert datetime.fromisoformat(renewed.json()["openUntil"]) == now + timedelta(minutes=5)
        now += timedelta(minutes=1)
        assert client.get(download_url).status_code == 200
        now += timedelta(minutes=4)
        assert client.get(download_url).status_code == 404
        assert "开放 5 分钟" in client.get(details_url).text
