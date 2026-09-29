from __future__ import annotations

import re

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.app.core.errors import ToolboxError, toolbox_error_handler
from backend.app.services.auth_service import User
from tools.git_blog.backend import public, service


def _app() -> FastAPI:
    app = FastAPI()
    app.add_exception_handler(ToolboxError, toolbox_error_handler)
    public.mount_extra(app)
    return app


def _published_blog(owner: User) -> dict:
    blog = service.create_blog({"slug": "route-notes", "repoUrl": "https://github.com/acme/docs"}, owner)
    with service._conn() as conn:
        conn.execute("UPDATE git_blog_blogs SET current_commit=? WHERE id=?", ("a" * 40, blog["id"]))
        conn.execute(
            "INSERT INTO git_blog_articles(blog_id,slug,source_path,title,summary,author,published_at,updated_at,tags_json,html,plain_text) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (blog["id"], "docs/intro", "docs/intro.md", "Intro", "Summary", "", service._now(), service._now(), "[]", "<h1>Intro</h1>", "Summary"),
        )
        conn.commit()
    return service.get_blog(blog["id"], owner)


def test_private_blog_gate_password_and_private_feeds(monkeypatch, tmp_path) -> None:
    owner = User(id="route-owner", username="writer", display_name="Writer")
    monkeypatch.setattr(service, "_root", lambda: tmp_path)
    monkeypatch.setattr(service, "list_users", lambda: [owner])
    monkeypatch.setattr(service, "can_access_tool", lambda _tool_id, _user: True)
    monkeypatch.setattr(public, "get_optional_user", lambda _request: None)
    blog = _published_blog(owner)
    service.set_blog_visibility(blog["id"], "private", owner)
    service.add_access_password(blog["id"], "Readers", "open-sesame", False, owner)
    client = TestClient(_app())

    gated = client.get("/blog/route-notes/posts/docs/intro")
    assert gated.status_code == 401
    assert "此博客为私密博客" in gated.text
    unlocked = client.post("/blog/route-notes/unlock", json={"password": "open-sesame"})
    assert unlocked.status_code == 200
    assert client.get("/blog/route-notes/posts/docs/intro").status_code == 200
    assert client.get("/blog/route-notes/feed.xml").status_code == 404
    assert client.get("/blog/route-notes/atom.xml").status_code == 404
    assert client.get("/blog/route-notes/sitemap.xml").status_code == 404


def test_share_routes_apply_document_and_full_private_rules(monkeypatch, tmp_path) -> None:
    owner = User(id="route-owner", username="writer", display_name="Writer")
    monkeypatch.setattr(service, "_root", lambda: tmp_path)
    monkeypatch.setattr(service, "list_users", lambda: [owner])
    monkeypatch.setattr(service, "can_access_tool", lambda _tool_id, _user: True)
    monkeypatch.setattr(public, "get_optional_user", lambda request: owner if request.headers.get("x-blog-owner") else None)
    blog = _published_blog(owner)
    service.set_blog_visibility(blog["id"], "private", owner)
    service.set_sharing_enabled(blog["id"], True, owner)
    document = service.create_share("route-notes", "docs/intro", {"mode": "document", "expiresAt": None}, owner, "creator")
    full = service.create_share("route-notes", "docs/intro", {"mode": "full", "expiresAt": None}, owner, "creator")
    client = TestClient(_app())

    document_response = client.get(document["url"])
    assert document_response.status_code == 200
    assert "Intro" in document_response.text
    assert "首页" not in document_response.text
    full_gate = client.get(full["url"])
    assert full_gate.status_code == 401
    assert "此分享来自私密博客" in full_gate.text
    full_page = client.get(full["url"], headers={"x-blog-owner": "1"})
    assert full_page.status_code == 200
    assert client.get("/blog/route-notes/share-access").json()["canShare"] is False
    assert client.get("/blog/route-notes/share-access", headers={"x-blog-owner": "1"}).json()["canShare"] is True
    article_page = client.get("/blog/route-notes/posts/docs/intro", headers={"x-blog-owner": "1"})
    assert "data-share-root" in article_page.text
    assert 'data-can-share="true"' in article_page.text
    assert "data-share-dialog" in article_page.text
    assert "data-share-auth-dialog" in article_page.text
    assert "data-share-close" in article_page.text
    share_dialog = re.search(r'<dialog class="blog-share-dialog" data-share-dialog>(.*?)</dialog>', article_page.text)
    assert share_dialog is not None
    assert "平台用户名" not in share_dialog.group(1)
    assert "博客访问密码" not in share_dialog.group(1)


def test_public_blog_share_needs_no_authorization(monkeypatch, tmp_path) -> None:
    owner = User(id="route-owner", username="writer", display_name="Writer")
    monkeypatch.setattr(service, "_root", lambda: tmp_path)
    monkeypatch.setattr(service, "list_users", lambda: [owner])
    monkeypatch.setattr(service, "can_access_tool", lambda _tool_id, _user: True)
    monkeypatch.setattr(public, "get_optional_user", lambda _request: None)
    blog = _published_blog(owner)
    service.set_sharing_enabled(blog["id"], True, owner)
    client = TestClient(_app())

    access = client.get("/blog/route-notes/share-access")
    assert access.status_code == 200
    assert access.json()["canShare"] is True
    article_page = client.get("/blog/route-notes/posts/docs/intro")
    assert article_page.status_code == 200
    assert 'data-can-share="true"' in article_page.text
