"""Owner site and scan-target management.."""

from __future__ import annotations

from .database import *


def _owner_site(user: User, *, required: bool = True) -> dict[str, Any] | None:
    with conn() as database:
        row = database.execute("SELECT * FROM service_navigator_sites WHERE owner_user_id=?", (user.id,)).fetchone()
    if row is None and required:
        raise ToolboxError("SITE_NOT_FOUND", "尚未创建服务导航站", status_code=404, tool_id=TOOL_ID)
    return _row(row) if row else None

def get_site(user: User) -> dict[str, Any] | None:
    site = _owner_site(user, required=False)
    if not site:
        return None
    return site_detail(site["id"], user)

def create_site(payload: dict[str, Any], user: User) -> dict[str, Any]:
    title = str(payload.get("title") or "").strip()[:100]
    if not title:
        raise ToolboxError("INVALID_SITE", "站点标题不能为空", status_code=400, tool_id=TOOL_ID)
    slug = normalize_slug(str(payload.get("slug") or ""))
    description = str(payload.get("description") or "").strip()[:500]
    theme = _normalise_site_theme(payload.get("theme", "auto"))
    accent_color = _normalise_accent_color(payload.get("accentColor", "#4f7cff"))
    card_opacity = _normalise_card_opacity(payload.get("cardOpacity", 84))
    card_blur = _normalise_card_blur(payload.get("cardBlur", True))
    background_overlay_opacity = _normalise_background_overlay_opacity(payload.get("backgroundOverlayOpacity", 50))
    now = now_iso()
    with conn() as database:
        if database.execute("SELECT 1 FROM service_navigator_sites WHERE owner_user_id=?", (user.id,)).fetchone():
            raise ToolboxError("SITE_EXISTS", "每个账号只能创建一个服务导航站", status_code=409, tool_id=TOOL_ID)
        try:
            site_id = uuid4().hex
            database.execute("""INSERT INTO service_navigator_sites
                (id,owner_user_id,title,slug,description,visibility,theme,accent_color,card_opacity,card_blur,background_overlay_opacity,created_at,updated_at)
                VALUES(?,?,?,?,?,'private',?,?,?,?,?,?)""", (site_id, user.id, title, slug, description, theme, accent_color, card_opacity, card_blur, background_overlay_opacity, now, now))
            database.execute("INSERT INTO service_navigator_health_settings(site_id,updated_at) VALUES(?,?)", (site_id, now))
            database.commit()
        except sqlite3.IntegrityError as exc:
            raise ToolboxError("SLUG_EXISTS", "该站点地址已被占用", status_code=409, tool_id=TOOL_ID) from exc
    return get_site(user) or {}

def update_site(payload: dict[str, Any], user: User) -> dict[str, Any]:
    site = _owner_site(user)
    title = str(payload.get("title", site["title"]) or "").strip()[:100]
    if not title:
        raise ToolboxError("INVALID_SITE", "站点标题不能为空", status_code=400, tool_id=TOOL_ID)
    slug = normalize_slug(str(payload.get("slug", site["slug"]) or ""))
    description = str(payload.get("description", site["description"]) or "").strip()[:500]
    theme = _normalise_site_theme(payload.get("theme", site.get("theme", "auto")))
    accent_color = _normalise_accent_color(payload.get("accentColor", site.get("accent_color", "#4f7cff")))
    card_opacity = _normalise_card_opacity(payload.get("cardOpacity", site.get("card_opacity", 84)))
    card_blur = _normalise_card_blur(payload.get("cardBlur", site.get("card_blur", 1)))
    background_overlay_opacity = _normalise_background_overlay_opacity(payload.get("backgroundOverlayOpacity", site.get("background_overlay_opacity", 50)))
    with conn() as database:
        try:
            database.execute("UPDATE service_navigator_sites SET title=?,slug=?,description=?,theme=?,accent_color=?,card_opacity=?,card_blur=?,background_overlay_opacity=?,updated_at=? WHERE id=?", (title, slug, description, theme, accent_color, card_opacity, card_blur, background_overlay_opacity, now_iso(), site["id"]))
            database.commit()
        except sqlite3.IntegrityError as exc:
            raise ToolboxError("SLUG_EXISTS", "该站点地址已被占用", status_code=409, tool_id=TOOL_ID) from exc
    return get_site(user) or {}

def delete_site(user: User) -> None:
    site = _owner_site(user)
    with conn() as database:
        filenames = [row[0] for row in database.execute("SELECT favicon_filename FROM service_navigator_services s JOIN service_navigator_targets t ON t.id=s.target_id WHERE t.site_id=?", (site["id"],)).fetchall()]
        navigation_filenames = [filename for row in database.execute("""SELECT icon_filename,external_favicon_filename
            FROM service_navigator_nav_icons WHERE site_id=?""", (site["id"],)).fetchall() for filename in row if filename]
        background_filename = database.execute("SELECT background_filename FROM service_navigator_sites WHERE id=?", (site["id"],)).fetchone()[0]
        database.execute("DELETE FROM service_navigator_sites WHERE id=?", (site["id"],))
        database.commit()
    for filename in filenames:
        _remove_icon(filename)
    for filename in navigation_filenames:
        _remove_navigation_icon(filename)
    _remove_background(background_filename)

def site_detail(site_id: str, user: User) -> dict[str, Any]:
    # Imported here to keep site/target CRUD independent of navigation loading.
    from .navigation import _navigation_detail

    with conn() as database:
        site_row = database.execute("SELECT * FROM service_navigator_sites WHERE id=? AND owner_user_id=?", (site_id, user.id)).fetchone()
        if not site_row:
            raise ToolboxError("SITE_NOT_FOUND", "服务导航站不存在", status_code=404, tool_id=TOOL_ID)
        targets = database.execute("SELECT * FROM service_navigator_targets WHERE site_id=? ORDER BY label,address", (site_id,)).fetchall()
        services = database.execute("SELECT s.* FROM service_navigator_services s JOIN service_navigator_targets t ON t.id=s.target_id WHERE t.site_id=? ORDER BY s.display_name,s.port,s.id", (site_id,)).fetchall()
        runs = database.execute("SELECT * FROM service_navigator_scan_runs WHERE site_id=? ORDER BY requested_at DESC LIMIT 20", (site_id,)).fetchall()
    return {"site": _site_public(site_row), "targets": [_target_public(row) for row in targets], "services": [_service_public(row) for row in services], "runs": [_run_public(row) for row in runs], "navigation": _navigation_detail(site_id)}

def add_target(payload: dict[str, Any], user: User) -> dict[str, Any]:
    site = _owner_site(user)
    label = str(payload.get("label") or "").strip()[:100]
    address = normalize_address(str(payload.get("address") or ""))
    if not label:
        label = address
    custom_ports = normalize_ports(str(payload.get("customPorts") or ""))
    with conn() as database:
        count = database.execute("SELECT COUNT(*) FROM service_navigator_targets WHERE site_id=?", (site["id"],)).fetchone()[0]
        if count >= MAX_TARGETS:
            raise ToolboxError("TARGET_LIMIT", f"每个站点最多配置 {MAX_TARGETS} 个目标", status_code=400, tool_id=TOOL_ID)
        now = now_iso()
        order = int(database.execute("SELECT COALESCE(MAX(sort_order),-1)+1 FROM service_navigator_targets WHERE site_id=?", (site["id"],)).fetchone()[0])
        target_id = uuid4().hex
        try:
            database.execute("INSERT INTO service_navigator_targets(id,site_id,label,address,custom_ports,sort_order,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)", (target_id, site["id"], label, address, custom_ports, order, now, now))
            database.commit()
        except sqlite3.IntegrityError as exc:
            raise ToolboxError("TARGET_EXISTS", "该目标已存在", status_code=409, tool_id=TOOL_ID) from exc
        row = database.execute("SELECT * FROM service_navigator_targets WHERE id=?", (target_id,)).fetchone()
    return _target_public(row)

def _owned_target(database: sqlite3.Connection, site_id: str, target_id: str) -> sqlite3.Row:
    row = database.execute("SELECT * FROM service_navigator_targets WHERE id=? AND site_id=?", (target_id, site_id)).fetchone()
    if row is None:
        raise ToolboxError("TARGET_NOT_FOUND", "目标不存在", status_code=404, tool_id=TOOL_ID)
    return row

def update_target(target_id: str, payload: dict[str, Any], user: User) -> dict[str, Any]:
    site = _owner_site(user)
    with conn() as database:
        old = _owned_target(database, site["id"], target_id)
        label = str(payload.get("label", old["label"]) or "").strip()[:100]
        address = normalize_address(str(payload.get("address", old["address"]) or ""))
        if not label:
            label = address
        ports = normalize_ports(str(payload.get("customPorts", old["custom_ports"]) or ""))
        show_in_navigation = 1 if payload.get("showInNavigation", bool(old["show_in_navigation"])) else 0
        try:
            database.execute("UPDATE service_navigator_targets SET label=?,address=?,custom_ports=?,show_in_navigation=?,updated_at=? WHERE id=?", (label, address, ports, show_in_navigation, now_iso(), target_id))
            database.commit()
        except sqlite3.IntegrityError as exc:
            raise ToolboxError("TARGET_EXISTS", "该目标已存在", status_code=409, tool_id=TOOL_ID) from exc
        row = database.execute("SELECT * FROM service_navigator_targets WHERE id=?", (target_id,)).fetchone()
    return _target_public(row)

def delete_target(target_id: str, user: User) -> None:
    site = _owner_site(user)
    with conn() as database:
        _owned_target(database, site["id"], target_id)
        filenames = [row[0] for row in database.execute("SELECT favicon_filename FROM service_navigator_services WHERE target_id=?", (target_id,)).fetchall()]
        database.execute("DELETE FROM service_navigator_targets WHERE id=?", (target_id,))
        navigation_filenames = _cleanup_empty_navigation_items(database, site["id"])
        database.commit()
    for filename in filenames:
        _remove_icon(filename)
    for filename in navigation_filenames:
        _remove_navigation_icon(filename)

def list_targets(user: User) -> list[dict[str, Any]]:
    site = _owner_site(user)
    with conn() as database:
        rows = database.execute("SELECT * FROM service_navigator_targets WHERE site_id=? ORDER BY sort_order,label,address", (site["id"],)).fetchall()
    return [_target_public(row) for row in rows]

def reorder_targets(target_ids: list[str], user: User) -> list[dict[str, Any]]:
    site = _owner_site(user)
    with conn() as database:
        existing = [row[0] for row in database.execute("SELECT id FROM service_navigator_targets WHERE site_id=? ORDER BY sort_order,label,address", (site["id"],)).fetchall()]
        if set(existing) != set(target_ids) or len(existing) != len(target_ids) or any(not target_id for target_id in target_ids):
            raise ToolboxError("INVALID_TARGET_ORDER", "固定页面排序数据不完整", status_code=400, tool_id=TOOL_ID)
        for order, target_id in enumerate(target_ids):
            database.execute("UPDATE service_navigator_targets SET sort_order=?,updated_at=? WHERE id=?", (order, now_iso(), target_id))
        database.commit()
    return list_targets(user)

__all__ = ['_owned_target', '_owner_site', 'add_target', 'create_site', 'delete_site', 'delete_target', 'get_site', 'list_targets', 'reorder_targets', 'site_detail', 'update_site', 'update_target']
