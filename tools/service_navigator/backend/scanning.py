"""TCP discovery, lightweight fingerprinting, and scan persistence.."""

from __future__ import annotations

from .catalog import default_command
from .database import *
from .sites import _owned_target, _owner_site


def request_scan(user: User, target_id: str | None = None) -> dict[str, Any]:
    site = _owner_site(user)
    with conn() as database:
        if target_id:
            _owned_target(database, site["id"], target_id)
        exists = database.execute("SELECT 1 FROM service_navigator_scan_runs WHERE site_id=? AND status IN ('queued','running')", (site["id"],)).fetchone()
        if exists:
            raise ToolboxError("SCAN_IN_PROGRESS", "该站点已有扫描任务正在进行", status_code=409, tool_id=TOOL_ID)
        run_id = uuid4().hex
        target_count = 1 if target_id else database.execute("SELECT COUNT(*) FROM service_navigator_targets WHERE site_id=?", (site["id"],)).fetchone()[0]
        summary = json.dumps({"targetCount": target_count, "completedTargetCount": 0, "successCount": 0}, ensure_ascii=False)
        database.execute("INSERT INTO service_navigator_scan_runs(id,site_id,target_id,status,requested_at,summary_json) VALUES(?,?,?,'queued',?,?)", (run_id, site["id"], target_id, now_iso(), summary))
        database.commit()
    return {"id": run_id, "status": "queued"}

def get_scan(run_id: str, user: User) -> dict[str, Any]:
    site = _owner_site(user)
    with conn() as database:
        row = database.execute("SELECT * FROM service_navigator_scan_runs WHERE id=? AND site_id=?", (run_id, site["id"])).fetchone()
    if row is None:
        raise ToolboxError("SCAN_NOT_FOUND", "扫描任务不存在", status_code=404, tool_id=TOOL_ID)
    return _run_public(row)

def run_scan(run_id: str) -> None:
    with ACTIVE_LOCK:
        if run_id in ACTIVE_RUNS:
            return
        ACTIVE_RUNS.add(run_id)
    try:
        with conn() as database:
            run = database.execute("SELECT * FROM service_navigator_scan_runs WHERE id=?", (run_id,)).fetchone()
            if not run or run["status"] != "queued":
                return
            database.execute("UPDATE service_navigator_scan_runs SET status='running',started_at=? WHERE id=?", (now_iso(), run_id))
            targets = database.execute("SELECT * FROM service_navigator_targets WHERE site_id=?" + (" AND id=?" if run["target_id"] else "") + " ORDER BY label,address", ((run["site_id"], run["target_id"]) if run["target_id"] else (run["site_id"],))).fetchall()
            database.commit()
        if not targets:
            _finish_run(run_id, "failed", "没有可扫描的目标", {"targetCount": 0, "completedTargetCount": 0, "successCount": 0})
            return
        results: list[dict[str, Any]] = []
        _update_run_progress(run_id, completed=0, total=len(targets), successes=0)
        with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
            futures = [executor.submit(_scan_target, dict(target)) for target in targets]
            for future in concurrent.futures.as_completed(futures):
                try:
                    results.append(future.result())
                except Exception as exc:  # defensive: individual failures should not discard other targets
                    results.append({"ok": False, "error": str(exc)[:500], "targetId": ""})
                _update_run_progress(run_id, completed=len(results), total=len(targets), successes=sum(1 for item in results if item.get("ok")))
        successes = sum(1 for item in results if item.get("ok"))
        status = "success" if successes == len(results) else ("partial" if successes else "failed")
        error = "" if status == "success" else "部分目标扫描失败" if status == "partial" else "所有目标扫描失败"
        _finish_run(run_id, status, error, {"targets": results, "successCount": successes, "targetCount": len(results), "completedTargetCount": len(results)})
    except Exception as exc:  # noqa: BLE001
        _finish_run(run_id, "failed", str(exc)[:500], {})
    finally:
        with ACTIVE_LOCK:
            ACTIVE_RUNS.discard(run_id)

def _finish_run(run_id: str, status: str, error: str, summary: dict[str, Any]) -> None:
    with conn() as database:
        database.execute("UPDATE service_navigator_scan_runs SET status=?,finished_at=?,error=?,summary_json=? WHERE id=?", (status, now_iso(), error, json.dumps(summary, ensure_ascii=False), run_id))
        database.commit()

def _update_run_progress(run_id: str, *, completed: int, total: int, successes: int) -> None:
    summary = {"targetCount": total, "completedTargetCount": completed, "successCount": successes}
    with conn() as database:
        database.execute("UPDATE service_navigator_scan_runs SET summary_json=? WHERE id=? AND status='running'", (json.dumps(summary, ensure_ascii=False), run_id))
        database.commit()

def _scan_target(target: dict[str, Any]) -> dict[str, Any]:
    target_id, address = target["id"], target["address"]
    try:
        addresses = resolve_addresses(address)
    except OSError as exc:
        return {"targetId": target_id, "address": address, "ok": False, "error": f"域名解析失败：{exc}"}

    discovered: dict[int, dict[str, Any]] = {}
    phase_errors: list[str] = []
    ports = ports_for_target(target.get("custom_ports", ""))
    # This semaphore covers the complete target operation (not just a child
    # process), so separate site scans cannot exceed three active targets.
    with SCAN_SEMAPHORE:
        for concrete_address in addresses:
            try:
                for item in _scan_address(concrete_address, ports):
                    port = int(item["port"])
                    current = discovered.get(port)
                    resolved = set(current.get("resolvedAddresses", set())) if current else set()
                    if current is None or _fingerprint_score(item) > _fingerprint_score(current):
                        discovered[port] = item
                    resolved.add(concrete_address)
                    discovered[port]["resolvedAddresses"] = resolved
            except ToolboxError as exc:
                phase_errors.append(exc.message)
    if phase_errors:
        return {"targetId": target_id, "address": address, "ok": False, "error": "; ".join(phase_errors)[:500], "openPorts": sorted(discovered)}
    services: list[dict[str, Any]] = []
    for port, item in discovered.items():
        item["resolvedAddresses"] = sorted(item.get("resolvedAddresses", set()))
        item.update(_enrich_web_service(address, item))
        services.append(item)
    _persist_target_scan(target, services)
    return {"targetId": target_id, "address": address, "ok": True, "openPorts": sorted(discovered), "serviceCount": len(services)}

def resolve_addresses(address: str) -> list[str]:
    try:
        return [str(ipaddress.ip_address(address))]
    except ValueError:
        resolved: list[str] = []
        for result in socket.getaddrinfo(address, None, type=socket.SOCK_STREAM):
            candidate = result[4][0]
            if candidate not in resolved:
                resolved.append(candidate)
            if len(resolved) >= MAX_DNS_ADDRESSES:
                break
        if not resolved:
            raise OSError("未找到 A 或 AAAA 记录")
        return resolved

def ports_for_target(custom_ports: str) -> tuple[int, ...]:
    """Return the built-in TCP set plus validated, persisted custom ports."""
    custom: set[int] = set()
    for value in custom_ports.split(","):
        if not value:
            continue
        if "-" in value:
            start, end = (int(part) for part in value.split("-", 1))
            custom.update(range(start, end + 1))
        else:
            custom.add(int(value))
    return tuple(sorted(set(COMMON_TCP_PORTS).union(custom)))

def _scan_address(address: str, ports: tuple[int, ...]) -> list[dict[str, Any]]:
    """Connect-scan one concrete IP without executing external programs."""
    deadline = time.monotonic() + HOST_SCAN_TIMEOUT
    discovered: list[dict[str, Any]] = []
    executor = concurrent.futures.ThreadPoolExecutor(max_workers=PORT_SCAN_WORKERS)
    iterator = iter(ports)
    futures: set[concurrent.futures.Future[dict[str, Any] | None]] = set()

    def submit_next() -> bool:
        try:
            futures.add(executor.submit(_scan_open_port, address, next(iterator)))
            return True
        except StopIteration:
            return False

    for _ in range(PORT_SCAN_WORKERS):
        if not submit_next():
            break
    try:
        while futures:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise concurrent.futures.TimeoutError
            done, _ = concurrent.futures.wait(futures, timeout=remaining, return_when=concurrent.futures.FIRST_COMPLETED)
            if not done:
                raise concurrent.futures.TimeoutError
            for future in done:
                futures.remove(future)
                item = future.result()
                if item:
                    discovered.append(item)
                submit_next()
    except concurrent.futures.TimeoutError as exc:
        raise ToolboxError("SCAN_TIMEOUT", "目标端口扫描超时", status_code=504, tool_id=TOOL_ID) from exc
    finally:
        # A socket operation has its own short timeout.  Do not block a task
        # past the host deadline waiting for any straggling worker.
        executor.shutdown(wait=False, cancel_futures=True)
    return discovered

def _scan_open_port(address: str, port: int) -> dict[str, Any] | None:
    if not _tcp_connects(address, port):
        return None
    return _identify_service(address, port)

def _tcp_connects(address: str, port: int) -> bool:
    for attempt in range(2):
        try:
            with socket.create_connection((address, port), timeout=PORT_CONNECT_TIMEOUT):
                return True
        except ConnectionRefusedError:
            return False
        except (socket.timeout, TimeoutError):
            if attempt == 0:
                continue
            return False
        except OSError:
            return False
    return False

def _identify_service(address: str, port: int) -> dict[str, Any]:
    hint = PORT_SERVICE_HINTS.get(port, "unknown")
    item = {
        "port": port,
        "protocol": "tcp",
        "serviceName": hint,
        "product": "",
        "version": "",
        "extraInfo": "",
        "tunnel": "ssl" if hint == "https" else "",
    }
    try:
        with socket.create_connection((address, port), timeout=PORT_PROBE_TIMEOUT) as connection:
            connection.settimeout(PORT_PROBE_TIMEOUT)
            banner = connection.recv(512)
    except (OSError, socket.timeout):
        return item
    return _apply_banner_fingerprint(item, banner)

def _apply_banner_fingerprint(item: dict[str, Any], banner: bytes) -> dict[str, Any]:
    text = banner.decode("utf-8", errors="replace").replace("\x00", " ").strip()
    port = int(item["port"])
    if text.startswith("SSH-"):
        item["serviceName"] = "ssh"
        identity = text.split(None, 1)[0]
        match = re.search(r"(?:OpenSSH|dropbear)[_-]?([^\s]+)?", identity, re.IGNORECASE)
        item["product"] = "OpenSSH" if "openssh" in identity.lower() else "Dropbear" if "dropbear" in identity.lower() else "SSH"
        item["version"] = (match.group(1) or "")[:80] if match else ""
    elif banner[:1] == b"\x0a" and port == 3306:
        item["serviceName"] = "mysql"
        item["product"] = "MySQL"
        item["version"] = banner[1:].split(b"\x00", 1)[0].decode("ascii", errors="replace")[:80]
    elif text.startswith("HTTP/"):
        item["serviceName"] = "http"
        item["product"] = "HTTP"
    elif text.startswith("+PONG"):
        item["serviceName"] = "redis"
        item["product"] = "Redis"
    elif text.startswith("220"):
        if port == 21:
            item["serviceName"] = "ftp"
        elif port in {25, 465, 587}:
            item["serviceName"] = "smtp"
        item["extraInfo"] = re.sub(r"\s+", " ", text)[:160]
    return item

def _fingerprint_score(item: dict[str, Any]) -> int:
    return sum(bool(item.get(key)) for key in ("serviceName", "product", "version", "extraInfo"))

def _is_web(item: dict[str, Any]) -> bool:
    name = str(item.get("serviceName") or "").lower()
    return "http" in name or int(item.get("port") or 0) in WEB_PORTS

def _url_for(address: str, port: int, scheme: str) -> str:
    host = f"[{address}]" if ":" in address and not address.startswith("[") else address
    return f"{scheme}://{host}:{port}/"

def _enrich_web_service(address: str, item: dict[str, Any]) -> dict[str, Any]:
    if not _is_web(item):
        return {"httpTitle": "", "detectedUrl": "", "faviconFilename": ""}
    name = str(item.get("serviceName") or "").lower()
    first = "https" if item.get("tunnel") == "ssl" or "https" in name or int(item["port"]) in {443, 444, 8443} else "http"
    schemes = [first, "http" if first == "https" else "https"]
    for scheme in schemes:
        url = _url_for(address, int(item["port"]), scheme)
        metadata = fetch_web_metadata(url)
        if metadata:
            return {"httpTitle": metadata["title"], "detectedUrl": metadata["url"], "faviconFilename": metadata["faviconFilename"]}
    return {"httpTitle": "", "detectedUrl": _url_for(address, int(item["port"]), first), "faviconFilename": ""}

def fetch_web_metadata(url: str) -> dict[str, str] | None:
    try:
        response = _open_web_url(url)
        with response:
            if not 200 <= response.getcode() < 300:
                return None
            content_type = response.headers.get_content_type()
            if content_type not in {"text/html", "application/xhtml+xml"}:
                return None
            content = _read_limited(response, HTTP_METADATA_LIMIT)
            if content is None:
                return None
            text = content.decode(response.headers.get_content_charset() or "utf-8", errors="replace")
            final_url = response.geturl()
        title_match = re.search(r"<title[^>]*>(.*?)</title>", text, re.IGNORECASE | re.DOTALL)
        title = re.sub(r"\s+", " ", html.unescape(title_match.group(1))).strip()[:160] if title_match else ""
        icon_match = re.search(r"<link[^>]+rel=[\"'][^\"']*icon[^\"']*[\"'][^>]+href=[\"']([^\"']+)[\"']", text, re.IGNORECASE)
        icon_url = urljoin(final_url, icon_match.group(1)) if icon_match else urljoin(final_url, "/favicon.ico")
        filename = _download_favicon(icon_url)
        return {"title": title, "url": final_url, "faviconFilename": filename}
    except (HTTPError, URLError, OSError, ValueError, ssl.SSLError):
        return None

class _SafeRedirectHandler(HTTPRedirectHandler):
    max_redirections = 5

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[no-untyped-def]
        if urlparse(newurl).scheme not in {"http", "https"}:
            return None
        return super().redirect_request(req, fp, code, msg, headers, newurl)

def _open_web_url(url: str):  # type: ignore[no-untyped-def]
    request = Request(url, headers={"User-Agent": "Pansis-Service-Navigator/1.0"})
    # The probe collects only public page metadata.  It must tolerate the
    # self-signed certificates common on private service dashboards.
    context = ssl.create_default_context()
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    opener = build_opener(_SafeRedirectHandler(), HTTPSHandler(context=context))
    return opener.open(request, timeout=3.0)

def _download_favicon(url: str) -> str:
    try:
        with _open_web_url(url) as response:
            if not 200 <= response.getcode() < 300:
                return ""
            content = _read_limited(response, FAVICON_LIMIT)
            content_type = response.headers.get_content_type().lower()
        allowed = {"image/x-icon": ".ico", "image/vnd.microsoft.icon": ".ico", "image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp"}
        if content is None or content_type not in allowed:
            return ""
        filename = f"{uuid4().hex}{allowed[content_type]}"
        (icon_dir() / filename).write_bytes(content)
        return filename
    except (HTTPError, URLError, OSError, ValueError, ssl.SSLError):
        return ""

def _read_limited(response: Any, limit: int) -> bytes | None:
    content = response.read(limit + 1)
    return content if len(content) <= limit else None

def _remove_icon(filename: str) -> None:
    if not filename:
        return
    path = (icon_dir() / Path(filename).name).resolve()
    if path.parent == icon_dir().resolve():
        path.unlink(missing_ok=True)

def _persist_target_scan(target: dict[str, Any], services: list[dict[str, Any]]) -> None:
    now = now_iso()
    old_icons: list[str] = []
    with conn() as database:
        existing = {int(row["port"]): row for row in database.execute("SELECT * FROM service_navigator_services WHERE target_id=? AND protocol='tcp'", (target["id"],)).fetchall()}
        seen: set[int] = set()
        for detected in services:
            port = int(detected["port"])
            seen.add(port)
            old = existing.get(port)
            if old:
                old_icon = old["favicon_filename"]
                new_icon = detected.get("faviconFilename") or old_icon
                if detected.get("faviconFilename") and old_icon and old_icon != new_icon:
                    old_icons.append(old_icon)
                database.execute("""UPDATE service_navigator_services SET state='online',service_name=?,product=?,version=?,extra_info=?,resolved_addresses_json=?,http_title=?,favicon_filename=?,detected_url=?,last_seen_at=?,updated_at=? WHERE id=?""", (detected.get("serviceName", "unknown"), detected.get("product", ""), detected.get("version", ""), detected.get("extraInfo", ""), json.dumps(detected.get("resolvedAddresses", [])), detected.get("httpTitle", ""), new_icon, detected.get("detectedUrl", ""), now, now, old["id"]))
            else:
                service_name = str(detected.get("serviceName") or "unknown")
                host = target["address"]
                command = default_command(service_name, host, port)
                health_enabled = 1 if _is_web(detected) else 0
                service_type = "http" if _is_web(detected) else "port"
                database.execute("""INSERT INTO service_navigator_services(id,target_id,protocol,port,state,service_name,product,version,extra_info,resolved_addresses_json,http_title,favicon_filename,detected_url,connection_command,service_type,visible,health_enabled,first_seen_at,last_seen_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (uuid4().hex, target["id"], "tcp", port, "online", service_name, detected.get("product", ""), detected.get("version", ""), detected.get("extraInfo", ""), json.dumps(detected.get("resolvedAddresses", [])), detected.get("httpTitle", ""), detected.get("faviconFilename", ""), detected.get("detectedUrl", ""), command, service_type, 1, health_enabled, now, now, now))
        for port, old in existing.items():
            if port not in seen:
                database.execute("UPDATE service_navigator_services SET state='offline',updated_at=? WHERE id=?", (now, old["id"]))
        database.commit()
    for filename in old_icons:
        _remove_icon(filename)

__all__ = ['_SafeRedirectHandler', '_apply_banner_fingerprint', '_download_favicon', '_enrich_web_service', '_fingerprint_score', '_finish_run', '_identify_service', '_is_web', '_open_web_url', '_persist_target_scan', '_read_limited', '_remove_icon', '_scan_address', '_scan_open_port', '_scan_target', '_tcp_connects', '_update_run_progress', '_url_for', 'fetch_web_metadata', 'get_scan', 'ports_for_target', 'request_scan', 'resolve_addresses', 'run_scan']
