"""Visitor-safe public navigation data and static assets.."""

from __future__ import annotations

from .access import public_site
from .catalog import _service_public
from .database import *
from .navigation import _navigation_detail
from .scanning import _read_limited


def public_services(site: dict[str, Any]) -> list[dict[str, Any]]:
    with conn() as database:
        rows = database.execute("""SELECT s.*,t.label AS target_label,t.address AS target_address FROM service_navigator_services s
            JOIN service_navigator_targets t ON t.id=s.target_id WHERE t.site_id=? ORDER BY t.label,t.address,s.display_name,s.port,s.id""", (site["id"],)).fetchall()
    output = []
    for row in rows:
        item = _service_public(row, include_fingerprint=False)
        # The public page gets a coarse health label only. URLs, status codes,
        # timings, errors and historical samples remain owner-only data.
        item["healthMonitored"] = bool(row["health_enabled"])
        for private_key in ("healthEnabled", "healthUrl", "lastHealthCheckedAt", "lastHealthStatusCode", "lastHealthLatencyMs", "lastHealthError"):
            item.pop(private_key, None)
        item.update({
            "targetLabel": row["target_label"], "targetAddress": row["target_address"],
            "name": row["display_name"] or row["http_title"] or row["service_name"],
            "url": (row["navigation_url"] or row["detected_url"]) if row["service_type"] == "http" else "",
            "commandDescription": row["command_description"] or row["connection_command"] or default_command(row["service_name"], row["target_address"], int(row["port"])),
        })
        output.append(item)
    return output

def _responsive_layouts(items: list[dict[str, Any]]) -> dict[str, dict[str, dict[str, int]]]:
    """Derive narrow layouts from the single editable 16-column canvas."""
    output: dict[str, dict[str, dict[str, int]]] = {item["id"]: {"16": item.get("layouts", {}).get("16", {"x": 0, "y": 0})} for item in items}
    ordered = sorted(items, key=lambda item: (item.get("layouts", {}).get("16", {}).get("y", 0), item.get("layouts", {}).get("16", {}).get("x", 0), item["id"]))
    for columns in (12, 8, 4):
        occupied: set[tuple[int, int]] = set()
        for item in ordered:
            width, height = NAV_SIZES[item["size"]]
            for y in range(1000):
                found = False
                for x in range(columns - width + 1):
                    cells = {(column, row) for column in range(x, x + width) for row in range(y, y + height)}
                    if not occupied & cells:
                        occupied |= cells
                        output[item["id"]][str(columns)] = {"x": x, "y": y}
                        found = True
                        break
                if found:
                    break
    return output

def public_icon(service_id: str, site: dict[str, Any]) -> Path | None:
    with conn() as database:
        row = database.execute("""SELECT s.favicon_filename FROM service_navigator_services s JOIN service_navigator_targets t ON t.id=s.target_id WHERE s.id=? AND t.site_id=?""", (service_id, site["id"])).fetchone()
    if not row or not row["favicon_filename"]:
        return None
    path = (icon_dir() / Path(row["favicon_filename"]).name).resolve()
    return path if path.is_file() and path.parent == icon_dir().resolve() else None

def public_navigation(site: dict[str, Any], *, include_icons: bool = False) -> dict[str, Any]:
    """Return visitor-safe placements, with the icon library for the owner only."""
    services = {item["id"]: item for item in public_services(site)}
    detail = _navigation_detail(site["id"])
    pages: list[dict[str, Any]] = []
    for page in detail["pages"]:
        responsive_layouts = _responsive_layouts(page["items"])
        items: list[dict[str, Any]] = []
        for item in page["items"]:
            linked = [services[service_id] for service_id in item["serviceIds"] if service_id in services]
            if not linked:
                continue
            service_types = {entry["serviceType"] for entry in linked}
            if len(service_types) != 1:
                continue
            source = item["iconSource"]
            if source == "custom" and item["iconFilename"]:
                icon_url = f"/service-nav/navigation-icon/{item['iconId']}"
            elif source == "favicon" and item["faviconServiceId"] in services:
                icon_url = services[item["faviconServiceId"]].get("faviconUrl", "")
            else:
                icon_url = ""
            items.append({
                "id": item["id"], "iconId": item["iconId"], "name": item["name"], "size": item["size"], "iconUrl": icon_url,
                "iconSource": source, "iconText": item.get("iconText", ""), "iconColor": item.get("iconColor", "#4f7cff"),
                "serviceType": next(iter(service_types)),
                "layouts": responsive_layouts.get(item["id"], {}), "services": linked,
            })
        pages.append({"id": page["id"], "name": page["name"], "visible": page.get("visible", True), "items": items})
    with conn() as database:
        targets = database.execute("SELECT id,label,address,custom_ports,show_in_navigation,sort_order FROM service_navigator_targets WHERE site_id=? ORDER BY sort_order,label,address", (site["id"],)).fetchall()
    target_pages = [{"id": f"target:{row['id']}", "targetId": row["id"], "name": row["label"], "address": row["address"], "customPorts": row["custom_ports"], "visible": bool(row["show_in_navigation"])} for row in targets]
    output = {
        "breakpoints": list(NAV_BREAKPOINTS),
        "targetPages": target_pages,
        "pages": pages,
        "services": list(services.values()),
    }
    if include_icons:
        output["icons"] = detail["icons"]
    return output

def public_site_for_icon(service_id: str) -> dict[str, Any] | None:
    with conn() as database:
        row = database.execute("""SELECT site.slug FROM service_navigator_sites site JOIN service_navigator_targets t ON t.site_id=site.id JOIN service_navigator_services s ON s.target_id=t.id WHERE s.id=?""", (service_id,)).fetchone()
    return public_site(row["slug"]) if row else None

def public_site_for_navigation_icon(icon_id: str) -> dict[str, Any] | None:
    with conn() as database:
        row = database.execute("""SELECT site.slug FROM service_navigator_sites site
            JOIN service_navigator_nav_icons icon ON icon.site_id=site.id WHERE icon.id=?""", (icon_id,)).fetchone()
    return public_site(row["slug"]) if row else None

def public_navigation_icon(icon_id: str, site: dict[str, Any]) -> Path | None:
    with conn() as database:
        row = database.execute("SELECT icon_filename FROM service_navigator_nav_icons WHERE id=? AND site_id=?", (icon_id, site["id"])).fetchone()
    if not row or not row["icon_filename"]:
        return None
    path = (navigation_icon_dir() / Path(row["icon_filename"]).name).resolve()
    return path if path.is_file() and path.parent == navigation_icon_dir().resolve() else None

def public_background(site: dict[str, Any]) -> Path | None:
    source = str(site.get("background_source") or "default")
    if source == "custom":
        path = (background_dir() / Path(str(site.get("background_filename") or "")).name).resolve()
        return path if path.is_file() and path.parent == background_dir().resolve() else None
    if source != "bing":
        return None
    day = datetime.now(timezone.utc).strftime("%Y%m%d")
    cached = background_dir() / f"bing-{day}.jpg"
    if cached.is_file():
        return cached
    try:
        request = Request("https://www.bing.com/HPImageArchive.aspx?format=js&idx=0&n=1&mkt=zh-CN", headers={"User-Agent": "Pansis-Service-Navigator/1.0"})
        with urlopen(request, timeout=5) as response:
            metadata = json.loads(_read_limited(response, 256 * 1024) or b"{}")
        image = (metadata.get("images") or [{}])[0]
        image_url = str(image.get("url") or "")
        if not image_url.startswith("/"):
            return None
        with urlopen(Request(f"https://www.bing.com{image_url}", headers={"User-Agent": "Pansis-Service-Navigator/1.0"}), timeout=10) as response:
            content = _read_limited(response, BACKGROUND_LIMIT)
        if not content:
            return None
        cached.write_bytes(content)
        return cached
    except (OSError, ValueError, json.JSONDecodeError):
        return None

__all__ = ['_responsive_layouts', 'public_background', 'public_icon', 'public_navigation', 'public_navigation_icon', 'public_services', 'public_site_for_icon', 'public_site_for_navigation_icon']
