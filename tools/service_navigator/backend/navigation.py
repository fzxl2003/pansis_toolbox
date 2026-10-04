"""Navigation icon library, pages, placements, and background.."""

from __future__ import annotations

from .catalog import _service_public
from .database import *
from .sites import _owner_site


def _navigation_detail(site_id: str) -> dict[str, Any]:
    """Return the global icon library plus pages that reference its icons."""
    with conn() as database:
        pages = database.execute("SELECT * FROM service_navigator_nav_pages WHERE site_id=? ORDER BY sort_order,id", (site_id,)).fetchall()
        items = database.execute("""SELECT i.id,i.page_id,i.size,i.created_at,i.updated_at,
                icon.id AS icon_id,icon.name,icon.icon_source,icon.icon_filename,icon.icon_text,
                icon.icon_color,icon.favicon_service_id,icon.preference_revision
            FROM service_navigator_nav_items i
            JOIN service_navigator_nav_pages page ON page.id=i.page_id
            JOIN service_navigator_nav_icons icon ON icon.id=i.icon_id
            WHERE page.site_id=? ORDER BY i.created_at,i.id""", (site_id,)).fetchall()
        icons = database.execute("SELECT * FROM service_navigator_nav_icons WHERE site_id=? ORDER BY detected_service_id IS NULL,detected_service_id,sort_order,name,id", (site_id,)).fetchall()
        services = database.execute("""SELECT s.*,t.label AS target_label,t.address AS target_address
            FROM service_navigator_services s JOIN service_navigator_targets t ON t.id=s.target_id
            WHERE t.site_id=? ORDER BY s.display_name,s.port,s.id""", (site_id,)).fetchall()
        links = database.execute("""SELECT l.icon_id,l.service_id FROM service_navigator_nav_icon_services l
            JOIN service_navigator_nav_icons icon ON icon.id=l.icon_id WHERE icon.site_id=?""", (site_id,)).fetchall()
        layouts = database.execute("""SELECT l.* FROM service_navigator_nav_item_layouts l
            JOIN service_navigator_nav_items i ON i.id=l.item_id
            JOIN service_navigator_nav_pages p ON p.id=i.page_id WHERE p.site_id=?""", (site_id,)).fetchall()
        targets = database.execute("SELECT id,label,address,custom_ports,show_in_navigation,sort_order FROM service_navigator_targets WHERE site_id=? ORDER BY sort_order,label,address", (site_id,)).fetchall()
    service_by_id = {row["id"]: _service_public(row) for row in services}
    service_ids: dict[str, list[str]] = {}
    for link in links:
        service_ids.setdefault(link["icon_id"], []).append(link["service_id"])
    layouts_by_item: dict[str, dict[str, dict[str, int]]] = {}
    for layout in layouts:
        layouts_by_item.setdefault(layout["item_id"], {})[str(layout["breakpoint"])] = {"x": int(layout["grid_x"]), "y": int(layout["grid_y"])}

    def icon_public(row: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
        selected = [service_by_id[service_id] for service_id in service_ids.get(row["id"], []) if service_id in service_by_id]
        return {
            "id": row["id"], "name": row["name"], "iconSource": row["icon_source"],
            "iconFilename": row["icon_filename"], "iconText": row["icon_text"],
            "iconColor": _normalise_accent_color(row["icon_color"]),
            "faviconServiceId": row["favicon_service_id"] or "",
            "detectedServiceId": dict(row).get("detected_service_id") or "",
            "preferenceRevision": int(row["preference_revision"]),
            "serviceIds": [entry["id"] for entry in selected], "services": selected,
            "iconUrl": f"/service-nav/navigation-icon/{row['id']}" if row["icon_source"] == "custom" and row["icon_filename"] else "",
            "createdAt": row["created_at"], "updatedAt": row["updated_at"],
        }

    icon_by_id = {row["id"]: icon_public(row) for row in icons}
    item_by_page: dict[str, list[dict[str, Any]]] = {}
    for row in items:
        icon = icon_by_id[row["icon_id"]]
        item_by_page.setdefault(row["page_id"], []).append({
            "id": row["id"], "pageId": row["page_id"], "iconId": row["icon_id"], "size": row["size"],
            **{key: value for key, value in icon.items() if key not in {"id", "detectedServiceId", "iconUrl"}},
            "layouts": layouts_by_item.get(row["id"], {}), "createdAt": row["created_at"], "updatedAt": row["updated_at"],
        })
    return {
        "breakpoints": [16],
        "pages": [{"id": row["id"], "name": row["name"], "sortOrder": int(row["sort_order"]), "visible": bool(row["visible"]), "items": item_by_page.get(row["id"], [])} for row in pages],
        "icons": list(icon_by_id.values()),
        "targetPages": [{"id": f"target:{row['id']}", "targetId": row["id"], "name": row["label"], "address": row["address"], "customPorts": row["custom_ports"], "visible": bool(row["show_in_navigation"])} for row in targets],
        "services": list(service_by_id.values()),
    }

def get_navigation(user: User) -> dict[str, Any]:
    return _navigation_detail(_owner_site(user)["id"])

def _owned_icon(database: sqlite3.Connection, site_id: str, icon_id: str) -> sqlite3.Row:
    row = database.execute("SELECT * FROM service_navigator_nav_icons WHERE id=? AND site_id=?", (icon_id, site_id)).fetchone()
    if not row:
        raise ToolboxError("NAV_ICON_NOT_FOUND", "导航图标不存在", status_code=404, tool_id=TOOL_ID)
    return row

def _library_source(value: Any, favicon_service_id: str, service_ids: list[str]) -> str:
    source = str(value or "text")
    if source not in {"text", "favicon", "custom"}:
        raise ToolboxError("INVALID_NAV_ICON", "图标来源不合法", status_code=400, tool_id=TOOL_ID)
    if source == "favicon" and favicon_service_id not in service_ids:
        raise ToolboxError("INVALID_NAV_ICON", "favicon 必须来自已关联服务", status_code=400, tool_id=TOOL_ID)
    return source

def list_nav_icons(user: User) -> list[dict[str, Any]]:
    return _navigation_detail(_owner_site(user)["id"])["icons"]

def create_nav_icon(payload: dict[str, Any], user: User) -> dict[str, Any]:
    site = _owner_site(user)
    detected_service_id = str(payload.get("detectedServiceId") or "")
    service_ids = list(payload.get("serviceIds") or ([detected_service_id] if detected_service_id else []))
    with conn() as database:
        service_ids, _kind = _validate_item_services(database, site["id"], service_ids)
        if detected_service_id and service_ids != [detected_service_id]:
            raise ToolboxError("INVALID_NAV_SERVICES", "已探测服务图标只能关联当前服务", status_code=400, tool_id=TOOL_ID)
        favicon_service_id = str(payload.get("faviconServiceId") or "")
        source = _library_source(payload.get("iconSource", "text"), favicon_service_id, service_ids)
        icon_id, now = uuid4().hex, now_iso()
        database.execute("""INSERT INTO service_navigator_nav_icons(id,site_id,name,icon_source,icon_filename,icon_text,
            icon_color,favicon_service_id,detected_service_id,preference_revision,sort_order,created_at,updated_at)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""", (icon_id, site["id"], _clean_nav_name(payload.get("name"), "图标名称"),
            source, "", str(payload.get("iconText") or "")[:4], _normalise_accent_color(payload.get("iconColor", "#4f7cff")),
            favicon_service_id or None, detected_service_id or None, 1, 0, now, now))
        database.executemany("INSERT INTO service_navigator_nav_icon_services(icon_id,service_id) VALUES(?,?)", [(icon_id, service_id) for service_id in service_ids])
        database.commit()
    return _navigation_icon_for_user(icon_id, user)

def _navigation_icon_for_user(icon_id: str, user: User) -> dict[str, Any]:
    for icon in get_navigation(user)["icons"]:
        if icon["id"] == icon_id:
            return icon
    raise ToolboxError("NAV_ICON_NOT_FOUND", "导航图标不存在", status_code=404, tool_id=TOOL_ID)

def update_nav_icon(icon_id: str, payload: dict[str, Any], user: User) -> dict[str, Any]:
    site = _owner_site(user)
    with conn() as database:
        old = _owned_icon(database, site["id"], icon_id)
        detected_service_id = str(old["detected_service_id"] or payload.get("detectedServiceId") or "")
        if "serviceIds" in payload or "detectedServiceId" in payload:
            service_ids = list(payload.get("serviceIds") or ([detected_service_id] if detected_service_id else []))
            service_ids, _kind = _validate_item_services(database, site["id"], service_ids)
            if detected_service_id and service_ids != [detected_service_id]:
                raise ToolboxError("INVALID_NAV_SERVICES", "已探测服务图标只能关联当前服务", status_code=400, tool_id=TOOL_ID)
        else:
            service_ids = [row["service_id"] for row in database.execute("SELECT service_id FROM service_navigator_nav_icon_services WHERE icon_id=?", (icon_id,)).fetchall()]
            _validate_item_services(database, site["id"], service_ids)
        favicon_service_id = str(payload.get("faviconServiceId", old["favicon_service_id"] or "") or "")
        source = _library_source(payload.get("iconSource", old["icon_source"]), favicon_service_id, service_ids)
        database.execute("""UPDATE service_navigator_nav_icons SET name=?,icon_source=?,icon_text=?,icon_color=?,
            favicon_service_id=?,preference_revision=preference_revision+1,updated_at=? WHERE id=?""",
            (_clean_nav_name(payload.get("name", old["name"]), "图标名称"), source,
             str(payload.get("iconText", old["icon_text"]) or "")[:4],
             _normalise_accent_color(payload.get("iconColor", old["icon_color"])), favicon_service_id or None, now_iso(), icon_id))
        if "serviceIds" in payload:
            database.execute("DELETE FROM service_navigator_nav_icon_services WHERE icon_id=?", (icon_id,))
            database.executemany("INSERT INTO service_navigator_nav_icon_services(icon_id,service_id) VALUES(?,?)", [(icon_id, service_id) for service_id in service_ids])
        database.commit()
    return _navigation_icon_for_user(icon_id, user)

def delete_nav_icon(icon_id: str, user: User) -> None:
    site = _owner_site(user)
    with conn() as database:
        old = _owned_icon(database, site["id"], icon_id)
        if old["detected_service_id"]:
            raise ToolboxError("DETECTED_NAV_ICON_LOCKED", "已探测服务图标不能删除", status_code=400, tool_id=TOOL_ID)
        database.execute("DELETE FROM service_navigator_nav_icons WHERE id=?", (icon_id,))
        database.commit()
    _remove_navigation_icon(old["icon_filename"])

def create_nav_item(payload: dict[str, Any], user: User) -> dict[str, Any]:
    site = _owner_site(user)
    with conn() as database:
        page = _owned_page(database, site["id"], str(payload.get("pageId") or ""))
        icon = _owned_icon(database, site["id"], str(payload.get("iconId") or ""))
        size = _nav_size(payload.get("size"))
        item_id, now = uuid4().hex, now_iso()
        database.execute("INSERT INTO service_navigator_nav_items(id,page_id,icon_id,name,size,created_at,updated_at) VALUES(?,?,?,?,?,?,?)", (item_id, page["id"], icon["id"], icon["name"], size, now, now))
        x, y = _first_available_layout(database, page["id"], 16, size)
        database.execute("INSERT INTO service_navigator_nav_item_layouts(item_id,breakpoint,grid_x,grid_y) VALUES(?,?,?,?)", (item_id, 16, x, y))
        database.commit()
    return _navigation_item_for_user(item_id, user)

def _navigation_item_for_user(item_id: str, user: User) -> dict[str, Any]:
    for page in get_navigation(user)["pages"]:
        for item in page["items"]:
            if item["id"] == item_id:
                return item
    raise ToolboxError("NAV_ITEM_NOT_FOUND", "页面图标不存在", status_code=404, tool_id=TOOL_ID)

def update_nav_item(item_id: str, payload: dict[str, Any], user: User) -> dict[str, Any]:
    site = _owner_site(user)
    with conn() as database:
        item = _owned_item(database, site["id"], item_id)
        if not item["icon_id"]:
            raise ToolboxError("NAV_ITEM_NOT_FOUND", "页面图标缺少全局图标", status_code=400, tool_id=TOOL_ID)
        size = _nav_size(payload.get("size", item["size"]))
        database.execute("UPDATE service_navigator_nav_items SET size=?,updated_at=? WHERE id=?", (size, now_iso(), item_id))
        database.commit()
    return _navigation_item_for_user(item_id, user)

def delete_nav_item(item_id: str, user: User) -> None:
    site = _owner_site(user)
    with conn() as database:
        _owned_item(database, site["id"], item_id)
        database.execute("DELETE FROM service_navigator_nav_items WHERE id=?", (item_id,))
        database.commit()

def update_nav_custom_icon(icon_id: str, filename: str, content: bytes, user: User) -> dict[str, Any]:
    suffix = Path(filename).suffix.lower()
    if suffix not in NAV_ICON_SUFFIXES:
        raise ToolboxError("INVALID_NAV_ICON", "图标仅支持 PNG、JPEG、WebP 或 ICO", status_code=400, tool_id=TOOL_ID)
    if not content or len(content) > NAV_ICON_LIMIT:
        raise ToolboxError("INVALID_NAV_ICON", "图标不能超过 1MB", status_code=400, tool_id=TOOL_ID)
    site = _owner_site(user)
    with conn() as database:
        icon = _owned_icon(database, site["id"], icon_id)
        saved = f"{icon_id}-{uuid4().hex[:8]}{suffix}"
        (navigation_icon_dir() / saved).write_bytes(content)
        database.execute("UPDATE service_navigator_nav_icons SET icon_source='custom',icon_filename=?,updated_at=? WHERE id=?", (saved, now_iso(), icon_id))
        database.commit()
    _remove_navigation_icon(icon["icon_filename"])
    return _navigation_icon_for_user(icon_id, user)

def clear_nav_custom_icon(icon_id: str, user: User) -> dict[str, Any]:
    site = _owner_site(user)
    with conn() as database:
        icon = _owned_icon(database, site["id"], icon_id)
        database.execute("UPDATE service_navigator_nav_icons SET icon_source='text',icon_filename='',updated_at=? WHERE id=?", (now_iso(), icon_id))
        database.commit()
    _remove_navigation_icon(icon["icon_filename"])
    return _navigation_icon_for_user(icon_id, user)

def _clean_nav_name(value: Any, label: str = "名称") -> str:
    result = str(value or "").strip()[:120]
    if not result:
        raise ToolboxError("INVALID_NAVIGATION", f"{label}不能为空", status_code=400, tool_id=TOOL_ID)
    return result

def _owned_page(database: sqlite3.Connection, site_id: str, page_id: str) -> sqlite3.Row:
    page = database.execute("SELECT * FROM service_navigator_nav_pages WHERE id=? AND site_id=?", (page_id, site_id)).fetchone()
    if not page:
        raise ToolboxError("NAV_PAGE_NOT_FOUND", "导航页面不存在", status_code=404, tool_id=TOOL_ID)
    return page

def _owned_item(database: sqlite3.Connection, site_id: str, item_id: str) -> sqlite3.Row:
    item = database.execute("""SELECT i.* FROM service_navigator_nav_items i JOIN service_navigator_nav_pages p ON p.id=i.page_id
        WHERE i.id=? AND p.site_id=?""", (item_id, site_id)).fetchone()
    if not item:
        raise ToolboxError("NAV_ITEM_NOT_FOUND", "导航图标不存在", status_code=404, tool_id=TOOL_ID)
    return item

def create_nav_page(payload: dict[str, Any], user: User) -> dict[str, Any]:
    site = _owner_site(user)
    name, now = _clean_nav_name(payload.get("name"), "页面名称"), now_iso()
    with conn() as database:
        order = int(database.execute("SELECT COALESCE(MAX(sort_order),-1)+1 FROM service_navigator_nav_pages WHERE site_id=?", (site["id"],)).fetchone()[0])
        page_id = uuid4().hex
        database.execute("INSERT INTO service_navigator_nav_pages(id,site_id,name,sort_order,visible,created_at,updated_at) VALUES(?,?,?,?,1,?,?)", (page_id, site["id"], name, order, now, now))
        database.commit()
    return next(page for page in get_navigation(user)["pages"] if page["id"] == page_id)

def update_nav_page(page_id: str, payload: dict[str, Any], user: User) -> dict[str, Any]:
    site = _owner_site(user)
    with conn() as database:
        old = _owned_page(database, site["id"], page_id)
        name = _clean_nav_name(payload.get("name", old["name"]), "页面名称")
        visible = 1 if payload.get("visible", bool(old["visible"])) else 0
        database.execute("UPDATE service_navigator_nav_pages SET name=?,visible=?,updated_at=? WHERE id=?", (name, visible, now_iso(), page_id))
        database.commit()
    return next(page for page in get_navigation(user)["pages"] if page["id"] == page_id)

def delete_nav_page(page_id: str, user: User) -> None:
    site = _owner_site(user)
    with conn() as database:
        _owned_page(database, site["id"], page_id)
        filenames = [row[0] for row in database.execute("SELECT icon_filename FROM service_navigator_nav_items WHERE page_id=? AND icon_filename<>''", (page_id,)).fetchall()]
        database.execute("DELETE FROM service_navigator_nav_pages WHERE id=?", (page_id,))
        database.commit()
    for filename in filenames:
        _remove_navigation_icon(filename)

def reorder_nav_pages(page_ids: list[str], user: User) -> dict[str, Any]:
    site = _owner_site(user)
    with conn() as database:
        existing = [row[0] for row in database.execute("SELECT id FROM service_navigator_nav_pages WHERE site_id=? ORDER BY sort_order,id", (site["id"],)).fetchall()]
        if set(existing) != set(page_ids) or len(existing) != len(page_ids):
            raise ToolboxError("INVALID_NAVIGATION_ORDER", "页面排序数据不完整", status_code=400, tool_id=TOOL_ID)
        for order, page_id in enumerate(page_ids):
            database.execute("UPDATE service_navigator_nav_pages SET sort_order=?,updated_at=? WHERE id=?", (order, now_iso(), page_id))
        database.commit()
    return get_navigation(user)

def _nav_size(value: Any) -> str:
    size = str(value or "small")
    if size not in NAV_SIZES:
        raise ToolboxError("INVALID_NAV_SIZE", "图标尺寸不合法", status_code=400, tool_id=TOOL_ID)
    return size

def _validate_item_services(database: sqlite3.Connection, site_id: str, service_ids: Any) -> tuple[list[str], str]:
    if not isinstance(service_ids, list) or not service_ids:
        raise ToolboxError("INVALID_NAV_SERVICES", "至少关联一个服务", status_code=400, tool_id=TOOL_ID)
    ids = list(dict.fromkeys(str(item) for item in service_ids if str(item)))
    placeholders = ",".join("?" for _ in ids)
    rows = database.execute(f"""SELECT s.id,s.service_type FROM service_navigator_services s JOIN service_navigator_targets t ON t.id=s.target_id
        WHERE t.site_id=? AND s.id IN ({placeholders})""", [site_id, *ids]).fetchall()
    if len(rows) != len(ids):
        raise ToolboxError("INVALID_NAV_SERVICES", "关联服务不存在或不属于当前站点", status_code=400, tool_id=TOOL_ID)
    kinds = {row["service_type"] for row in rows}
    if len(kinds) != 1:
        raise ToolboxError("MIXED_NAV_SERVICES", "一个图标不能混用 HTTP 服务和端口服务", status_code=400, tool_id=TOOL_ID)
    return ids, next(iter(kinds))

def _first_available_layout(database: sqlite3.Connection, page_id: str, breakpoint: int, size: str) -> tuple[int, int]:
    width, height = NAV_SIZES[size]
    rows = database.execute("""SELECT i.size,l.grid_x,l.grid_y FROM service_navigator_nav_item_layouts l
        JOIN service_navigator_nav_items i ON i.id=l.item_id WHERE i.page_id=? AND l.breakpoint=?""", (page_id, breakpoint)).fetchall()
    occupied: set[tuple[int, int]] = set()
    for row in rows:
        old_width, old_height = NAV_SIZES[row["size"]]
        occupied.update((x, y) for x in range(row["grid_x"], row["grid_x"] + old_width) for y in range(row["grid_y"], row["grid_y"] + old_height))
    for y in range(1000):
        for x in range(breakpoint - width + 1):
            if all((cell_x, cell_y) not in occupied for cell_x in range(x, x + width) for cell_y in range(y, y + height)):
                return x, y
    raise ToolboxError("NAV_LAYOUT_FULL", "导航布局已满", status_code=400, tool_id=TOOL_ID)

def save_nav_layout(page_id: str, breakpoint: int, placements: list[dict[str, Any]], user: User) -> dict[str, Any]:
    if breakpoint != 16:
        raise ToolboxError("INVALID_NAV_BREAKPOINT", "导航布局仅需编辑 16 列，其他档位会自动生成", status_code=400, tool_id=TOOL_ID)
    site = _owner_site(user)
    with conn() as database:
        _owned_page(database, site["id"], page_id)
        items = {row["id"]: row for row in database.execute("SELECT * FROM service_navigator_nav_items WHERE page_id=?", (page_id,)).fetchall()}
        placement_by_id = {str(item.get("itemId") or ""): item for item in placements if isinstance(item, dict)}
        if set(placement_by_id) != set(items) or len(placements) != len(items):
            raise ToolboxError("INVALID_NAV_LAYOUT", "布局必须包含页面内所有图标", status_code=400, tool_id=TOOL_ID)
        occupied: set[tuple[int, int]] = set()
        validated: list[tuple[str, int, int]] = []
        for item_id, item in items.items():
            try:
                x, y = int(placement_by_id[item_id].get("x")), int(placement_by_id[item_id].get("y"))
            except (TypeError, ValueError):
                raise ToolboxError("INVALID_NAV_LAYOUT", "布局坐标必须为整数", status_code=400, tool_id=TOOL_ID) from None
            width, height = NAV_SIZES[item["size"]]
            if x < 0 or y < 0 or x + width > breakpoint or y > 1000:
                raise ToolboxError("INVALID_NAV_LAYOUT", "图标超出当前网格范围", status_code=400, tool_id=TOOL_ID)
            cells = {(column, row) for column in range(x, x + width) for row in range(y, y + height)}
            if occupied & cells:
                raise ToolboxError("INVALID_NAV_LAYOUT", "图标不能重叠", status_code=400, tool_id=TOOL_ID)
            occupied |= cells
            validated.append((item_id, x, y))
        for item_id, x, y in validated:
            database.execute("INSERT INTO service_navigator_nav_item_layouts(item_id,breakpoint,grid_x,grid_y) VALUES(?,?,?,?) ON CONFLICT(item_id,breakpoint) DO UPDATE SET grid_x=excluded.grid_x,grid_y=excluded.grid_y", (item_id, breakpoint, x, y))
        database.commit()
    return get_navigation(user)


def save_nav_canvas(page_id: str, canvas_items: list[dict[str, Any]], user: User) -> dict[str, Any]:
    """Atomically persist every size and position on a page's editable canvas."""
    site = _owner_site(user)
    with conn() as database:
        _owned_page(database, site["id"], page_id)
        stored = {
            row["id"]: row
            for row in database.execute(
                "SELECT * FROM service_navigator_nav_items WHERE page_id=?", (page_id,)
            ).fetchall()
        }
        submitted = {
            str(entry.get("itemId") or ""): entry
            for entry in canvas_items
            if isinstance(entry, dict)
        }
        if set(submitted) != set(stored) or len(canvas_items) != len(stored):
            raise ToolboxError("INVALID_NAV_LAYOUT", "画布必须包含页面内所有图标", status_code=400, tool_id=TOOL_ID)
        occupied: set[tuple[int, int]] = set()
        validated: list[tuple[str, str, int, int]] = []
        for item_id in stored:
            entry = submitted[item_id]
            try:
                size = _nav_size(entry.get("size"))
                x, y = int(entry.get("x")), int(entry.get("y"))
            except (TypeError, ValueError):
                raise ToolboxError("INVALID_NAV_LAYOUT", "尺寸和画布坐标必须有效", status_code=400, tool_id=TOOL_ID) from None
            width, height = NAV_SIZES[size]
            if x < 0 or y < 0 or x + width > 16 or y > 1000:
                raise ToolboxError("INVALID_NAV_LAYOUT", "图标超出当前网格范围", status_code=400, tool_id=TOOL_ID)
            cells = {
                (column, row)
                for column in range(x, x + width)
                for row in range(y, y + height)
            }
            if occupied & cells:
                raise ToolboxError("INVALID_NAV_LAYOUT", "图标不能重叠", status_code=400, tool_id=TOOL_ID)
            occupied |= cells
            validated.append((item_id, size, x, y))
        now = now_iso()
        for item_id, size, x, y in validated:
            database.execute(
                "UPDATE service_navigator_nav_items SET size=?,updated_at=? WHERE id=?",
                (size, now, item_id),
            )
            database.execute(
                """INSERT INTO service_navigator_nav_item_layouts(item_id,breakpoint,grid_x,grid_y)
                VALUES(?,?,?,?) ON CONFLICT(item_id,breakpoint) DO UPDATE SET
                grid_x=excluded.grid_x,grid_y=excluded.grid_y""",
                (item_id, 16, x, y),
            )
        database.commit()
    return get_navigation(user)

def _remove_navigation_icon(filename: str) -> None:
    if not filename:
        return
    path = (navigation_icon_dir() / Path(filename).name).resolve()
    if path.parent == navigation_icon_dir().resolve():
        path.unlink(missing_ok=True)

def update_background_source(source: str, user: User) -> dict[str, Any]:
    if source not in {"default", "custom", "bing"}:
        raise ToolboxError("INVALID_BACKGROUND", "背景来源不合法", status_code=400, tool_id=TOOL_ID)
    site = _owner_site(user)
    with conn() as database:
        current = database.execute("SELECT background_filename FROM service_navigator_sites WHERE id=?", (site["id"],)).fetchone()
        if source == "custom" and not current["background_filename"]:
            raise ToolboxError("BACKGROUND_REQUIRED", "请先上传背景图片", status_code=400, tool_id=TOOL_ID)
        database.execute("UPDATE service_navigator_sites SET background_source=?,updated_at=? WHERE id=?", (source, now_iso(), site["id"]))
        database.commit()
    return _site_public(_owner_site(user))

def set_all_services_visible(visible: bool, user: User) -> dict[str, Any]:
    site = _owner_site(user)
    with conn() as database:
        database.execute("UPDATE service_navigator_sites SET show_all_services=?,updated_at=? WHERE id=?", (1 if visible else 0, now_iso(), site["id"]))
        database.commit()
    return _site_public(_owner_site(user))

def update_custom_background(filename: str, content: bytes, user: User) -> dict[str, Any]:
    suffix = Path(filename).suffix.lower()
    if suffix not in BACKGROUND_SUFFIXES:
        raise ToolboxError("INVALID_BACKGROUND", "背景仅支持 PNG、JPEG 或 WebP", status_code=400, tool_id=TOOL_ID)
    if not content or len(content) > BACKGROUND_LIMIT:
        raise ToolboxError("INVALID_BACKGROUND", "背景图片不能超过 6MB", status_code=400, tool_id=TOOL_ID)
    site = _owner_site(user)
    saved = f"{site['id']}-{uuid4().hex[:8]}{suffix}"
    (background_dir() / saved).write_bytes(content)
    with conn() as database:
        old = database.execute("SELECT background_filename FROM service_navigator_sites WHERE id=?", (site["id"],)).fetchone()["background_filename"]
        database.execute("UPDATE service_navigator_sites SET background_source='custom',background_filename=?,updated_at=? WHERE id=?", (saved, now_iso(), site["id"]))
        database.commit()
    _remove_background(old)
    return _site_public(_owner_site(user))

def _remove_background(filename: str) -> None:
    if not filename:
        return
    path = (background_dir() / Path(filename).name).resolve()
    if path.parent == background_dir().resolve():
        path.unlink(missing_ok=True)

def _cleanup_empty_navigation_items(database: sqlite3.Connection, site_id: str) -> list[str]:
    """Placement and icon cleanup is explicit; deleting services keeps library icons."""
    return []

__all__ = ['_clean_nav_name', '_cleanup_empty_navigation_items', '_first_available_layout', '_library_source', '_nav_size', '_navigation_detail', '_navigation_icon_for_user', '_navigation_item_for_user', '_owned_icon', '_owned_item', '_owned_page', '_remove_background', '_remove_navigation_icon', '_validate_item_services', 'clear_nav_custom_icon', 'create_nav_icon', 'create_nav_item', 'create_nav_page', 'delete_nav_icon', 'delete_nav_item', 'delete_nav_page', 'get_navigation', 'list_nav_icons', 'reorder_nav_pages', 'save_nav_canvas', 'save_nav_layout', 'set_all_services_visible', 'update_background_source', 'update_custom_background', 'update_nav_custom_icon', 'update_nav_icon', 'update_nav_item', 'update_nav_page']
