from __future__ import annotations

import subprocess

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


def test_git_error_message_is_not_truncated(monkeypatch: pytest.MonkeyPatch) -> None:
    message = "git failed\n" + "detail" * 500
    monkeypatch.setattr(
        service.subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args[0], 1, stdout="", stderr=message),
    )

    with pytest.raises(ToolboxError) as error:
        service._git(["status"])

    assert error.value.message == message


def test_sync_run_stores_the_complete_error(monkeypatch: pytest.MonkeyPatch, isolated_storage, owner: User) -> None:
    blog = service.create_blog({"slug": "full-error", "repoUrl": "https://github.com/acme/docs"}, owner)
    message = "sync failed\n" + "detail" * 500
    monkeypatch.setattr(service, "_git", lambda *args, **kwargs: (_ for _ in ()).throw(ToolboxError("GIT_FAILED", message, status_code=400)))
    monkeypatch.setattr(service.proxy_service, "get_proxy_url_for_host", lambda host: "")

    service.sync_blog(blog["id"])

    run = service.list_runs(blog["id"], owner)[0]
    assert run["message"] == message
    assert service.get_blog(blog["id"], owner)["lastError"] == message


def test_snapshot_retention_keeps_only_the_latest_five(isolated_storage, owner: User) -> None:
    blog = service.create_blog({"slug": "snapshots", "repoUrl": "https://github.com/acme/docs"}, owner)
    commits = [str(index) * 40 for index in range(6)]
    with service._conn() as conn:
        for index, commit in enumerate(commits):
            service._snapshot_dir(blog["id"], commit).mkdir(parents=True)
            conn.execute(
                "INSERT INTO git_blog_snapshots(blog_id,commit_hash,created_at) VALUES(?,?,?)",
                (blog["id"], commit, f"2026-01-0{index + 1}T00:00:00+00:00"),
            )
        conn.commit()

    snapshots = service.list_snapshots(blog["id"], owner)

    assert [snapshot["commit"] for snapshot in snapshots] == list(reversed(commits[1:]))
    assert not service._snapshot_dir(blog["id"], commits[0]).exists()


def test_rollback_reindexes_snapshot_and_disables_auto_sync(monkeypatch: pytest.MonkeyPatch, isolated_storage, owner: User) -> None:
    blog = service.create_blog(
        {"slug": "rollback", "repoUrl": "https://github.com/acme/docs", "config": {"defaults": {"published": True}}},
        owner,
    )
    commit = "a" * 40
    snapshot = service._snapshot_dir(blog["id"], commit)
    snapshot.mkdir(parents=True)
    (snapshot / "post.md").write_text("# Restored post\n\nA sufficiently long paragraph that can be used as an article summary after rollback.", encoding="utf-8")
    with service._conn() as conn:
        conn.execute(
            "INSERT INTO git_blog_snapshots(blog_id,commit_hash,created_at) VALUES(?,?,?)",
            (blog["id"], commit, "2026-01-01T00:00:00+00:00"),
        )
        conn.commit()
    monkeypatch.setattr(service, "_git", lambda *args, **kwargs: "2026-01-01T00:00:00+00:00\n")
    monkeypatch.setattr(service, "_render", lambda markdown, **kwargs: f"<h1>{markdown.splitlines()[0][2:]}</h1>")

    rolled_back = service.rollback_snapshot(blog["id"], commit, owner)

    assert rolled_back["currentCommit"] == commit
    assert rolled_back["autoSyncEnabled"] is False
    assert service.public_article(blog["id"], "post")["title"] == "Restored post"
    assert "自动同步已关闭" in service.list_runs(blog["id"], owner)[0]["message"]


def test_manual_sync_still_queues_when_auto_sync_is_disabled(monkeypatch: pytest.MonkeyPatch, isolated_storage, owner: User) -> None:
    blog = service.create_blog(
        {"slug": "manual-sync", "repoUrl": "https://github.com/acme/docs", "autoSyncEnabled": False},
        owner,
    )
    synced: list[str] = []
    monkeypatch.setattr(service, "list_users", lambda: [owner])
    monkeypatch.setattr(service, "can_access_tool", lambda tool_id, user: True)
    monkeypatch.setattr(service, "sync_blog", lambda blog_id: synced.append(blog_id))

    service.sync_due_blogs()
    assert synced == []

    service.request_sync(blog["id"], owner)
    service.sync_due_blogs()
    assert synced == [blog["id"]]


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
