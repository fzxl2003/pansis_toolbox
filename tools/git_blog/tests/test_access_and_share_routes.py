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
    service.set_sharing_enabled(blog["id"], True, owner, False)
    service.add_access_password(blog["id"], "Readers", "open-sesame", False, owner)
    client = TestClient(_app())

    gated = client.get("/blog/route-notes/posts/docs/intro")
    assert gated.status_code == 401
    assert "此博客为私密博客" in gated.text
    assert "form.hasAttribute('data-platform-login')?'/api/auth/login':form.dataset.endpoint" in gated.text
    assert "form.dataset.platformLogin?'/api/auth/login'" not in gated.text
    unlocked = client.post("/blog/route-notes/unlock", json={"password": "open-sesame"})
    assert unlocked.status_code == 200
    article = client.get("/blog/route-notes/posts/docs/intro")
    assert article.status_code == 200
    assert 'data-blog-identity' in article.text
    assert '>Guest</span>' in article.text
    assert 'data-logout-endpoint="/blog/route-notes/logout"' in article.text
    assert 'data-share-root' not in article.text
    assert client.post("/blog/route-notes/logout").json()["authenticated"] is False
    assert client.get("/blog/route-notes/posts/docs/intro").status_code == 401
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
    assert 'data-blog-identity' in article_page.text
    assert '>writer</span>' in article_page.text
    assert 'data-logout-endpoint="/api/auth/logout"' in article_page.text
    assert "data-share-dialog" in article_page.text
    assert "data-share-auth-dialog" not in article_page.text
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
    assert 'data-share-root' in article_page.text
    assert 'data-can-share="true"' in article_page.text
    assert 'data-share-auth-dialog' not in article_page.text
    assert 'data-blog-identity' not in article_page.text


def test_public_anonymous_share_button_authenticates_before_opening_share(monkeypatch, tmp_path) -> None:
    owner = User(id="route-owner", username="writer", display_name="Writer")
    monkeypatch.setattr(service, "_root", lambda: tmp_path)
    monkeypatch.setattr(service, "list_users", lambda: [owner])
    monkeypatch.setattr(service, "can_access_tool", lambda _tool_id, _user: True)
    monkeypatch.setattr(public, "get_optional_user", lambda _request: None)
    blog = _published_blog(owner)
    service.set_sharing_enabled(blog["id"], True, owner, False)
    service.add_access_password(blog["id"], "Share team", "share-secret", True, owner)
    client = TestClient(_app())

    access = client.get("/blog/route-notes/share-access")
    assert access.status_code == 200
    assert access.json()["canShare"] is False
    article = client.get("/blog/route-notes/posts/docs/intro")
    assert article.status_code == 200
    assert 'data-share-root' in article.text
    assert 'data-can-share="false"' in article.text
    assert 'data-share-auth-dialog' in article.text
    share_dialog = re.search(r'<dialog class="blog-share-dialog" data-share-dialog>(.*?)</dialog>', article.text)
    assert share_dialog is not None
    assert "平台用户名" not in share_dialog.group(1)
    assert "博客访问密码" not in share_dialog.group(1)

    unlocked = client.post("/blog/route-notes/unlock", json={"password": "share-secret"})
    assert unlocked.status_code == 200
    assert unlocked.json()["canShare"] is True
    authenticated_article = client.get("/blog/route-notes/posts/docs/intro")
    assert 'data-can-share="true"' in authenticated_article.text
    assert 'data-share-auth-dialog' not in authenticated_article.text
    assert '>Guest</span>' in authenticated_article.text


def test_public_blog_hides_share_for_authenticated_user_without_permission(monkeypatch, tmp_path) -> None:
    owner = User(id="route-owner", username="writer", display_name="Writer")
    reader = User(id="route-reader", username="reader", display_name="Reader")
    monkeypatch.setattr(service, "_root", lambda: tmp_path)
    monkeypatch.setattr(service, "list_users", lambda: [owner, reader])
    monkeypatch.setattr(service, "can_access_tool", lambda _tool_id, _user: True)
    monkeypatch.setattr(public, "get_optional_user", lambda _request: reader)
    blog = _published_blog(owner)
    service.set_sharing_enabled(blog["id"], True, owner, False)
    client = TestClient(_app())

    article = client.get("/blog/route-notes/posts/docs/intro")

    assert article.status_code == 200
    assert 'data-share-root' not in article.text
    assert 'data-blog-identity' in article.text
    assert '>reader</span>' in article.text
    assert 'data-logout-endpoint="/api/auth/logout"' in article.text
