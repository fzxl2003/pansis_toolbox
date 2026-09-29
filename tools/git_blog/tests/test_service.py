from __future__ import annotations

import json
import subprocess
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace

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


def test_accent_color_config_accepts_hex_and_rejects_invalid_values() -> None:
    configured = service._normalise_config({"site": {"accentColor": " #7C3AED ", "accentColorEnabled": True}})
    invalid = service._normalise_config({"site": {"accentColor": "rebeccapurple", "accentColorEnabled": True}})

    assert configured["site"]["accentColor"] == "#7c3aed"
    assert configured["site"]["accentColorEnabled"] is True
    assert invalid["site"]["accentColor"] == "#42b983"


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


def _publish_test_article(blog: dict, slug: str = "guide/intro", title: str = "Intro") -> None:
    with service._conn() as conn:
        conn.execute("UPDATE git_blog_blogs SET current_commit=? WHERE id=?", ("a" * 40, blog["id"]))
        conn.execute(
            "INSERT INTO git_blog_articles(blog_id,slug,source_path,title,summary,author,published_at,updated_at,tags_json,html,plain_text) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (blog["id"], slug, f"{slug}.md", title, "Summary", "", service._now(), service._now(), "[]", f"<h1>{title}</h1>", "Summary"),
        )
        conn.commit()


def test_blog_access_supports_invited_users_and_revocable_password_sessions(monkeypatch: pytest.MonkeyPatch, isolated_storage, owner: User) -> None:
    invited = User(id="reader", username="reader", display_name="Reader")
    platform_admin = User(id="platform-admin", username="boss", display_name="Boss", role="admin")
    monkeypatch.setattr(service, "list_users", lambda: [owner, invited, platform_admin])
    blog = service.create_blog({"slug": "private-notes", "repoUrl": "https://github.com/acme/docs"}, owner)
    service.set_blog_visibility(blog["id"], "private", owner)
    private_blog = service.get_blog(blog["id"], owner)

    assert service.blog_access(private_blog, owner)["canShare"] is True
    assert service.blog_access(private_blog, platform_admin)["allowed"] is False

    service.add_access_user(blog["id"], "reader", True, owner)
    invited_access = service.blog_access(private_blog, invited)
    assert invited_access == {"allowed": True, "canShare": True, "kind": "user", "label": "reader"}

    password = service.add_access_password(blog["id"], "Team", "secret", False, owner)
    service.unlock_blog(private_blog, "secret", "browser-one")
    assert service.blog_access(private_blog, None, "browser-one")["allowed"] is True
    service.update_access_password(blog["id"], password["id"], {"label": "Team", "password": "new-secret", "canShare": False, "enabled": True}, owner)
    assert service.blog_access(private_blog, None, "browser-one")["allowed"] is False


def test_document_share_bypasses_private_blog_but_full_share_requires_blog_access(monkeypatch: pytest.MonkeyPatch, isolated_storage, owner: User) -> None:
    monkeypatch.setattr(service, "list_users", lambda: [owner])
    monkeypatch.setattr(service, "can_access_tool", lambda _tool_id, _user: True)
    blog = service.create_blog({"slug": "share-modes", "repoUrl": "https://github.com/acme/docs"}, owner)
    _publish_test_article(blog)
    service.set_blog_visibility(blog["id"], "private", owner)
    service.set_sharing_enabled(blog["id"], True, owner)

    document = service.create_share("share-modes", "guide/intro", {"mode": "document", "expiresAt": None}, owner, "owner-browser")
    _share, _blog, item = service.open_share(document["token"], None, "anonymous-browser")
    assert item["title"] == "Intro"

    full = service.create_share("share-modes", "guide/intro", {"mode": "full", "expiresAt": None}, owner, "owner-browser")
    with pytest.raises(ToolboxError) as error:
        service.open_share(full["token"], None, "other-browser")
    assert error.value.code == "BLOG_LOGIN_REQUIRED"
    assert service.open_share(full["token"], owner, "owner-browser")[2]["slug"] == "guide/intro"


def test_public_blog_allows_anonymous_visitors_to_create_shares(monkeypatch: pytest.MonkeyPatch, isolated_storage, owner: User) -> None:
    monkeypatch.setattr(service, "list_users", lambda: [owner])
    monkeypatch.setattr(service, "can_access_tool", lambda _tool_id, _user: True)
    blog = service.create_blog({"slug": "public-sharing", "repoUrl": "https://github.com/acme/docs"}, owner)
    _publish_test_article(blog)
    service.set_sharing_enabled(blog["id"], True, owner)

    public_blog = service.public_blog("public-sharing")
    assert service.blog_access(public_blog, None)["canShare"] is True
    shared = service.create_share("public-sharing", "guide/intro", {"expiresAt": None}, None, "anonymous-browser")
    assert shared["createdByType"] == "anonymous"
    assert shared["url"].startswith("/blog/share/")


def test_share_view_limit_counts_each_browser_once_and_keeps_existing_visitors(monkeypatch: pytest.MonkeyPatch, isolated_storage, owner: User) -> None:
    monkeypatch.setattr(service, "list_users", lambda: [owner])
    monkeypatch.setattr(service, "can_access_tool", lambda _tool_id, _user: True)
    blog = service.create_blog({"slug": "limited-share", "repoUrl": "https://github.com/acme/docs"}, owner)
    _publish_test_article(blog)
    service.set_sharing_enabled(blog["id"], True, owner)
    shared = service.create_share("limited-share", "guide/intro", {"maxViews": 1, "expiresAt": None}, owner, "owner-browser")

    service.open_share(shared["token"], None, "browser-a")
    service.open_share(shared["token"], None, "browser-a")
    with pytest.raises(ToolboxError) as error:
        service.open_share(shared["token"], None, "browser-b")
    assert error.value.code == "SHARE_VIEW_LIMIT"
    assert service.get_sharing_settings(blog["id"], owner)["shares"][0]["views"] == 1


def test_password_protected_share_unlock_is_invalidated_when_password_changes(monkeypatch: pytest.MonkeyPatch, isolated_storage, owner: User) -> None:
    monkeypatch.setattr(service, "list_users", lambda: [owner])
    monkeypatch.setattr(service, "can_access_tool", lambda _tool_id, _user: True)
    blog = service.create_blog({"slug": "protected-share", "repoUrl": "https://github.com/acme/docs"}, owner)
    _publish_test_article(blog)
    service.set_sharing_enabled(blog["id"], True, owner)
    shared = service.create_share("protected-share", "guide/intro", {"password": "first", "expiresAt": None}, owner, "owner-browser")

    with pytest.raises(ToolboxError) as required:
        service.open_share(shared["token"], None, "browser-a")
    assert required.value.code == "SHARE_PASSWORD_REQUIRED"
    service.unlock_share(shared["token"], "first", "browser-a")
    service.open_share(shared["token"], None, "browser-a")
    service.update_share(blog["id"], shared["id"], {"passwordAction": "replace", "password": "second"}, owner)
    with pytest.raises(ToolboxError) as invalidated:
        service.open_share(shared["token"], None, "browser-a")
    assert invalidated.value.code == "SHARE_PASSWORD_REQUIRED"


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


def test_sync_skips_clone_when_remote_commit_is_current(monkeypatch: pytest.MonkeyPatch, isolated_storage, owner: User) -> None:
    blog = service.create_blog({"slug": "current", "repoUrl": "https://github.com/acme/docs"}, owner)
    commit = "a" * 40
    service._snapshot_dir(blog["id"], commit).mkdir(parents=True)
    with service._conn() as conn:
        conn.execute("UPDATE git_blog_blogs SET current_commit=? WHERE id=?", (commit, blog["id"]))
        row = conn.execute("SELECT * FROM git_blog_blogs WHERE id=?", (blog["id"],)).fetchone()
        conn.execute("UPDATE git_blog_blogs SET render_fingerprint=? WHERE id=?", (service._render_fingerprint(row), blog["id"]))
        conn.commit()
    calls: list[list[str]] = []

    def fake_git(args, **kwargs):
        calls.append(args)
        return f"{commit}\trefs/heads/main\n"

    monkeypatch.setattr(service, "_git", fake_git)
    monkeypatch.setattr(service.proxy_service, "get_proxy_url_for_host", lambda host: "")

    service.sync_blog(blog["id"])

    assert calls == [["ls-remote", "--heads", "https://github.com/acme/docs.git", "refs/heads/main"]]
    run = service.list_runs(blog["id"], owner)[0]
    assert run["status"] == "success"
    assert run["commit_hash"] == commit
    assert run["message"] == "当前已是最新版本"


def test_sync_clones_when_remote_commit_changed(monkeypatch: pytest.MonkeyPatch, isolated_storage, owner: User) -> None:
    blog = service.create_blog({"slug": "changed", "repoUrl": "https://github.com/acme/docs"}, owner)
    old_commit, new_commit = "a" * 40, "b" * 40
    with service._conn() as conn:
        conn.execute("UPDATE git_blog_blogs SET current_commit=? WHERE id=?", (old_commit, blog["id"]))
        conn.commit()
    calls: list[list[str]] = []
    stages: list[str] = []
    set_status = service._set_sync_status

    def fake_git(args, **kwargs):
        calls.append(args)
        if args[0] == "ls-remote":
            return f"{new_commit}\trefs/heads/main\n"
        if args[0] == "clone":
            Path(args[-1]).mkdir(parents=True)
            return ""
        if args[:2] == ["rev-parse", "HEAD"]:
            return f"{new_commit}\n"
        raise AssertionError(args)

    monkeypatch.setattr(service, "_git", fake_git)
    monkeypatch.setattr(service.proxy_service, "get_proxy_url_for_host", lambda host: "")
    monkeypatch.setattr(service, "_prepare_snapshot", lambda blog_row, target: (service._normalise_config(None), [], []))
    monkeypatch.setattr(service, "_set_sync_status", lambda blog_id, status: (stages.append(status), set_status(blog_id, status))[1])

    service.sync_blog(blog["id"])

    assert [args[0] for args in calls] == ["ls-remote", "clone", "rev-parse"]
    assert stages == ["syncing_cloning", "syncing_rendering", "syncing_publishing"]
    assert service.get_blog(blog["id"], owner)["currentCommit"] == new_commit
    assert service.list_runs(blog["id"], owner)[0]["message"] == "远端发现新提交，已同步 0 篇文章"


def test_config_change_rebuilds_current_snapshot_without_clone_when_auto_sync_is_off(monkeypatch: pytest.MonkeyPatch, isolated_storage, owner: User) -> None:
    blog = service.create_blog(
        {"slug": "local-rebuild", "repoUrl": "https://github.com/acme/docs", "autoSyncEnabled": False},
        owner,
    )
    commit = "c" * 40
    service._snapshot_dir(blog["id"], commit).mkdir(parents=True)
    with service._conn() as conn:
        conn.execute("UPDATE git_blog_blogs SET current_commit=?,sync_status='success' WHERE id=?", (commit, blog["id"]))
        row = conn.execute("SELECT * FROM git_blog_blogs WHERE id=?", (blog["id"],)).fetchone()
        conn.execute("UPDATE git_blog_blogs SET render_fingerprint=? WHERE id=?", (service._render_fingerprint(row), blog["id"]))
        conn.commit()

    updated = service.update_blog(
        blog["id"],
        {"config": {"defaults": {"published": True}}},
        owner,
    )
    assert updated["syncStatus"] == "queued"
    assert updated["autoSyncEnabled"] is False

    calls: list[list[str]] = []
    stages: list[str] = []
    set_status = service._set_sync_status
    def fake_git(args, **kwargs):
        calls.append(args)
        return f"{commit}\trefs/heads/main\n"

    monkeypatch.setattr(service, "_git", fake_git)
    monkeypatch.setattr(service.proxy_service, "get_proxy_url_for_host", lambda host: "")
    monkeypatch.setattr(service, "_prepare_snapshot", lambda blog_row, target: (service._normalise_config(json.loads(blog_row["config_json"])), [], []))
    monkeypatch.setattr(service, "_set_sync_status", lambda blog_id, status: (stages.append(status), set_status(blog_id, status))[1])

    service.sync_blog(blog["id"])

    assert calls == [["ls-remote", "--heads", "https://github.com/acme/docs.git", "refs/heads/main"]]
    assert stages == ["syncing_rebuilding", "syncing_publishing"]
    refreshed = service.get_blog(blog["id"], owner)
    assert refreshed["syncStatus"] == "success"
    assert refreshed["renderFingerprint"] != blog["renderFingerprint"]
    assert service.list_runs(blog["id"], owner)[0]["message"] == "仓库版本未变化，已应用博客配置更新，共 0 篇文章"


def test_typora_theme_crud_preserves_css_and_resets_using_blogs(isolated_storage, owner: User) -> None:
    raw = b'@charset "UTF-8";\n#write { color: #123456; }\n'
    theme = service.save_theme(SimpleNamespace(filename="misty-light.css", file=BytesIO(raw)), owner)

    assert theme["name"] == "misty-light"
    assert theme["size"] == len(raw)
    assert service.theme_css(theme["id"], owner) == raw
    assert service.list_themes(owner) == [theme]

    blog = service.create_blog(
        {
            "slug": "themed",
            "repoUrl": "https://github.com/acme/docs",
            "config": {"site": {"customThemeId": theme["id"]}},
        },
        owner,
    )
    public_blog = {**blog, "ownerUserId": owner.id}
    assert service.public_theme_css(public_blog) == raw

    assert service.delete_theme(theme["id"], owner) == 1
    assert service.get_blog(blog["id"], owner)["config"]["site"]["customThemeId"] == ""
    assert service.list_themes(owner) == []


def test_blog_rejects_another_users_custom_theme(isolated_storage, owner: User) -> None:
    theme = service.save_theme(SimpleNamespace(filename="private.css", file=BytesIO(b"#write {}")), owner)
    stranger = User(id="theme-stranger", username="stranger", display_name="Stranger")

    with pytest.raises(ToolboxError) as error:
        service.create_blog(
            {
                "slug": "stolen-theme",
                "repoUrl": "https://github.com/acme/docs",
                "config": {"site": {"customThemeId": theme["id"]}},
            },
            stranger,
        )

    assert error.value.code == "THEME_NOT_FOUND"


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


def test_sync_due_does_not_restart_an_active_phase(monkeypatch: pytest.MonkeyPatch, isolated_storage, owner: User) -> None:
    blog = service.create_blog({"slug": "active-sync", "repoUrl": "https://github.com/acme/docs"}, owner)
    with service._conn() as conn:
        conn.execute("UPDATE git_blog_blogs SET sync_status='syncing_rendering' WHERE id=?", (blog["id"],))
        conn.commit()
    synced: list[str] = []
    monkeypatch.setattr(service, "list_users", lambda: [owner])
    monkeypatch.setattr(service, "can_access_tool", lambda tool_id, user: True)
    monkeypatch.setattr(service, "sync_blog", lambda blog_id: synced.append(blog_id))

    service.sync_due_blogs()

    assert synced == []


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
