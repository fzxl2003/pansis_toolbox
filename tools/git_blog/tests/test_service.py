from __future__ import annotations

import pytest

from backend.app.core.errors import ToolboxError
from backend.app.services.auth_service import User
from tools.git_blog.backend import service


@pytest.fixture
def owner() -> User:
    return User(id="git-blog-owner", username="writer", display_name="Writer")


@pytest.fixture
def isolated_storage(monkeypatch: pytest.MonkeyPatch, tmp_path):
    monkeypatch.setattr(service, "_root", lambda: tmp_path)
    return tmp_path


def test_frontmatter_and_config_precedence() -> None:
    metadata, body = service._frontmatter("---\npublished: false\ntags: [article]\n---\n# Hello\n\nSummary")
    assert metadata == {"published": False, "tags": ["article"]}
    assert body.startswith("# Hello")
    ui = service._normalise_config({"defaults": {"published": False}})
    repo = {"site": {}, "defaults": {"published": True}}
    assert service._merge_config(ui, repo)["defaults"] == {"published": True, "author": "", "cover": ""}


def test_blog_crud_is_owner_isolated(isolated_storage, owner: User) -> None:
    blog = service.create_blog({"name": "Notes", "slug": "writer-notes", "repoUrl": "https://github.com/acme/notes", "branch": "main"}, owner)
    assert blog["repoUrl"] == "https://github.com/acme/notes.git"
    assert blog["tokenConfigured"] is False
    assert service.get_blog(blog["id"], owner)["slug"] == "writer-notes"
    stranger = User(id="other", username="other", display_name="Other")
    with pytest.raises(ToolboxError) as error:
        service.get_blog(blog["id"], stranger)
    assert error.value.status_code == 404
    updated = service.update_blog(blog["id"], {"slug": "renamed-notes", "name": "Renamed"}, owner)
    assert updated["slug"] == "renamed-notes"
    service.delete_blog(blog["id"], owner)
    assert service.list_blogs(owner) == []


@pytest.mark.parametrize("value", ["git@github.com:a/b.git", "https://example.com/a/b", "https://github.com/a/b?token=x"])
def test_repository_validation_rejects_non_github_https(value: str) -> None:
    with pytest.raises(ToolboxError):
        service._normalize_repo(value)


def test_deploy_key_probe_uses_the_selected_key_and_returns_branches(monkeypatch: pytest.MonkeyPatch, owner: User) -> None:
    captured: dict[str, object] = {}
    monkeypatch.setattr(service.github_key_service, "get_private_key", lambda key_id, user: "private-key")
    seen_hosts: list[str] = []
    monkeypatch.setattr(service.proxy_service, "get_proxy_url_for_host", lambda host: seen_hosts.append(host) or "http://proxy:7890")
    def fake_git(args, **kwargs):
        captured["args"] = args
        captured.update(kwargs)
        return "abc\trefs/heads/main\ndef\trefs/heads/docs\n"
    monkeypatch.setattr(service, "_git", fake_git)
    result = service.probe_repository("https://github.com/acme/notes", owner, "key-1")
    assert result["branches"] == ["docs", "main"]
    assert captured["private_key"] == "private-key"
    assert captured["proxy_url"] == "http://proxy:7890"
    assert captured["args"] == ["ls-remote", "--heads", "git@ssh.github.com:acme/notes.git"]
    assert seen_hosts == ["ssh.github.com"]


def test_article_slug_rejects_path_traversal() -> None:
    assert service._article_slug("nested/post.md", None) == "nested/post"
    with pytest.raises(ToolboxError):
        service._article_slug("post.md", "../secret")


def test_public_directory_and_tag_counts_use_only_indexed_articles(isolated_storage, owner: User) -> None:
    blog = service.create_blog({"slug": "docs", "repoUrl": "https://github.com/acme/docs"}, owner)
    with service._conn() as conn:
        conn.executemany(
            "INSERT INTO git_blog_articles(blog_id,slug,source_path,title,summary,author,published_at,updated_at,tags_json,html,plain_text) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            [(blog["id"], "guide/install", "guide/install.md", "Install", "", "", "2026-01-02", "", '["guide", "setup"]', "", ""), (blog["id"], "readme", "README.md", "Readme", "", "", "2026-01-01", "", '["guide"]', "", "")],
        )
    directory = service.public_directory(blog["id"])
    assert [item["name"] for item in directory["children"]] == ["guide", "README"]
    assert service.public_tag_counts(blog["id"]) == [{"tag": "guide", "count": 2}, {"tag": "setup", "count": 1}]
