"""HTTP health snapshots, alert state, and scheduled checks.."""

from __future__ import annotations

from .database import *
from .sites import _owner_site


def _health_settings_public(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "checkIntervalSeconds": int(row["check_interval_seconds"]),
        "emailRecipients": json.loads(row["email_recipients_json"] or "[]"),
        "confirmCount": int(row["confirm_count"]),
        "repeatIntervalSeconds": int(row["repeat_interval_seconds"]),
        "maxRepeatCount": int(row["max_repeat_count"]),
        "lastCheckedAt": row["last_checked_at"],
        "updatedAt": row["updated_at"],
    }

def _ensure_health_settings(database: sqlite3.Connection, site_id: str) -> sqlite3.Row:
    database.execute(
        "INSERT OR IGNORE INTO service_navigator_health_settings(site_id,updated_at) VALUES(?,?)",
        (site_id, now_iso()),
    )
    row = database.execute("SELECT * FROM service_navigator_health_settings WHERE site_id=?", (site_id,)).fetchone()
    assert row is not None
    return row

def _clean_recipients(value: Any) -> list[str]:
    raw = value if isinstance(value, list) else re.split(r"[,\n]", str(value or ""))
    recipients: list[str] = []
    for candidate in raw:
        email = str(candidate).strip()
        if not email:
            continue
        if len(email) > 254 or not re.fullmatch(r"[^\s@,]+@[^\s@,]+\.[^\s@,]+", email):
            raise ToolboxError("INVALID_HEALTH_RECIPIENT", "收件人邮箱格式不合法", status_code=400, tool_id=TOOL_ID)
        if email.lower() not in {item.lower() for item in recipients}:
            recipients.append(email)
    return recipients[:100]

def get_health_settings(user: User) -> dict[str, Any]:
    site = _owner_site(user)
    with conn() as database:
        row = _ensure_health_settings(database, site["id"])
        database.commit()
    return _health_settings_public(row)

def update_health_settings(payload: dict[str, Any], user: User) -> dict[str, Any]:
    site = _owner_site(user)
    with conn() as database:
        old = _ensure_health_settings(database, site["id"])
        interval = int(payload.get("checkIntervalSeconds", old["check_interval_seconds"]) or 0)
        confirm = int(payload.get("confirmCount", old["confirm_count"]) or 0)
        repeat = int(payload.get("repeatIntervalSeconds", old["repeat_interval_seconds"]) or 0)
        maximum = int(payload.get("maxRepeatCount", old["max_repeat_count"]) or 0)
        if not HEALTH_MIN_INTERVAL <= interval <= HEALTH_MAX_INTERVAL:
            raise ToolboxError("INVALID_HEALTH_INTERVAL", f"检测间隔必须在 {HEALTH_MIN_INTERVAL}–{HEALTH_MAX_INTERVAL} 秒之间", status_code=400, tool_id=TOOL_ID)
        if not 1 <= confirm <= 20:
            raise ToolboxError("INVALID_HEALTH_CONFIRM", "连续失败确认次数必须在 1–20 之间", status_code=400, tool_id=TOOL_ID)
        if repeat < 0 or repeat > HEALTH_MAX_INTERVAL * 30:
            raise ToolboxError("INVALID_HEALTH_REPEAT", "重复告警冷却时间不合法", status_code=400, tool_id=TOOL_ID)
        if maximum < 0 or maximum > 100000:
            raise ToolboxError("INVALID_HEALTH_REPEAT_MAX", "最大重复告警次数不合法", status_code=400, tool_id=TOOL_ID)
        recipients = _clean_recipients(payload.get("emailRecipients", json.loads(old["email_recipients_json"] or "[]")))
        database.execute("""UPDATE service_navigator_health_settings
            SET check_interval_seconds=?,email_recipients_json=?,confirm_count=?,repeat_interval_seconds=?,max_repeat_count=?,updated_at=?
            WHERE site_id=?""", (interval, json.dumps(recipients, ensure_ascii=False), confirm, repeat, maximum, now_iso(), site["id"]))
        database.commit()
        row = database.execute("SELECT * FROM service_navigator_health_settings WHERE site_id=?", (site["id"],)).fetchone()
    return _health_settings_public(row)

def _health_url(service_row: sqlite3.Row | dict[str, Any]) -> str:
    item = _row(service_row) if isinstance(service_row, sqlite3.Row) else service_row
    return str(item.get("health_url") or item.get("navigation_url") or item.get("detected_url") or "")

def _check_http_health(url: str) -> dict[str, Any]:
    """Perform a small direct GET request; body data is never retained."""
    started = time.monotonic()
    request = Request(url, headers={"User-Agent": "Pansis-Service-Navigator-Health/1.0", "Accept": "*/*"})
    try:
        # Health checks deliberately validate TLS. A broken certificate is an
        # outage for a browser-facing HTTPS endpoint and is reported as such.
        opener = build_opener(_SafeRedirectHandler(), HTTPSHandler(context=ssl.create_default_context()))
        with opener.open(request, timeout=HEALTH_TIMEOUT) as response:
            status_code = int(response.getcode())
            final_url = str(response.geturl())[:1000]
        latency = max(0, round((time.monotonic() - started) * 1000))
        return {"status": "healthy" if 200 <= status_code < 400 else "unhealthy", "statusCode": status_code, "latencyMs": latency, "finalUrl": final_url, "error": "" if 200 <= status_code < 400 else f"HTTP {status_code}"}
    except HTTPError as exc:
        latency = max(0, round((time.monotonic() - started) * 1000))
        healthy = 200 <= int(exc.code) < 400
        return {"status": "healthy" if healthy else "unhealthy", "statusCode": int(exc.code), "latencyMs": latency, "finalUrl": str(exc.geturl() or url)[:1000], "error": "" if healthy else f"HTTP {exc.code}"}
    except Exception as exc:  # URL/TLS/socket/timeout errors all mean unhealthy
        latency = max(0, round((time.monotonic() - started) * 1000))
        message = str(exc) or type(exc).__name__
        return {"status": "unhealthy", "statusCode": None, "latencyMs": latency, "finalUrl": url[:1000], "error": message[:500]}

def _event(database: sqlite3.Connection, site_id: str, service_id: str, event_type: str, message: str, details: dict[str, Any]) -> None:
    database.execute("INSERT INTO service_navigator_health_events(id,site_id,service_id,event_type,message,details_json,created_at) VALUES(?,?,?,?,?,?,?)", (uuid4().hex, site_id, service_id, event_type, message[:500], json.dumps(details, ensure_ascii=False), now_iso()))

def _parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        result = datetime.fromisoformat(value)
        return result if result.tzinfo else result.replace(tzinfo=timezone.utc)
    except ValueError:
        return None

def _alert_email(site: dict[str, Any], service: dict[str, Any], sample: dict[str, Any], recipients: list[str], *, recovery: bool, repeated: bool) -> None:
    name = service.get("display_name") or service.get("http_title") or service.get("service_name") or "HTTP 服务"
    url = sample.get("finalUrl") or _health_url(service)
    timestamp = now_iso().replace("T", " ").replace("+00:00", " UTC")
    if recovery:
        subject = f"【服务导航】{site['title']}：{name} 已恢复"
        body = f"服务已恢复健康。\n\n站点：{site['title']}\n服务：{name}\n健康检查 URL：{url}\n状态码：{sample.get('statusCode') or '—'}\n耗时：{sample.get('latencyMs')} ms\n时间：{timestamp}"
    else:
        prefix = "持续异常提醒" if repeated else "服务异常"
        subject = f"【服务导航】{site['title']}：{name} {prefix}"
        body = f"HTTP 健康检查发现服务异常。\n\n站点：{site['title']}\n服务：{name}\n健康检查 URL：{url}\n失败原因：{sample.get('error') or '健康检查失败'}\n状态码：{sample.get('statusCode') or '—'}\n耗时：{sample.get('latencyMs')} ms\n时间：{timestamp}"
    platform_send_email(recipients, subject, body)

def _record_health_result(site: dict[str, Any], service: dict[str, Any], sample: dict[str, Any], *, manual: bool) -> dict[str, Any]:
    checked_at = now_iso()
    with conn() as database:
        database.execute("""INSERT INTO service_navigator_health_snapshots(id,service_id,checked_at,status,status_code,latency_ms,final_url,error,manual)
            VALUES(?,?,?,?,?,?,?,?,?)""", (uuid4().hex, service["id"], checked_at, sample["status"], sample.get("statusCode"), sample.get("latencyMs"), sample.get("finalUrl", ""), sample.get("error", ""), 1 if manual else 0))
        database.execute("""UPDATE service_navigator_services SET health_status=?,last_health_checked_at=?,last_health_status_code=?,last_health_latency_ms=?,last_health_error=?,updated_at=? WHERE id=?""", (sample["status"], checked_at, sample.get("statusCode"), sample.get("latencyMs"), sample.get("error", ""), checked_at, service["id"]))
        database.execute("UPDATE service_navigator_health_settings SET last_checked_at=? WHERE site_id=?", (checked_at, site["id"]))
        if manual:
            database.commit()
            return sample
        settings = _ensure_health_settings(database, site["id"])
        alert = database.execute("SELECT * FROM service_navigator_health_alert_states WHERE service_id=?", (service["id"],)).fetchone()
        old_failures = int(alert["consecutive_failures"]) if alert else 0
        was_alerting = bool(alert["is_alerting"]) if alert else False
        repeat_count = int(alert["repeat_count"]) if alert else 0
        exhausted = bool(alert["repeat_exhausted"]) if alert else False
        last_alerted = alert["last_alerted_at"] if alert else None
        should_email = False
        recovery = False
        repeated = False
        if sample["status"] == "healthy":
            recovery = was_alerting
            failures, is_alerting, repeat_count, exhausted, last_alerted = 0, False, 0, False, None
        else:
            failures = old_failures + 1
            is_alerting = was_alerting
            if failures >= int(settings["confirm_count"]):
                last_time = _parse_time(last_alerted)
                interval_passed = not last_time or datetime.now(timezone.utc) - last_time >= timedelta(seconds=int(settings["repeat_interval_seconds"]))
                limit_ok = int(settings["max_repeat_count"]) == 0 or repeat_count < int(settings["max_repeat_count"])
                if not was_alerting:
                    should_email, is_alerting, repeated = True, True, False
                elif interval_passed and limit_ok and not exhausted:
                    should_email, repeated = True, True
                if should_email:
                    repeat_count += 1
                    last_alerted = checked_at
                    exhausted = int(settings["max_repeat_count"]) > 0 and repeat_count >= int(settings["max_repeat_count"])
                is_alerting = True
        database.execute("""INSERT INTO service_navigator_health_alert_states(service_id,consecutive_failures,is_alerting,last_alerted_at,repeat_count,repeat_exhausted,updated_at)
            VALUES(?,?,?,?,?,?,?) ON CONFLICT(service_id) DO UPDATE SET consecutive_failures=excluded.consecutive_failures,is_alerting=excluded.is_alerting,last_alerted_at=excluded.last_alerted_at,repeat_count=excluded.repeat_count,repeat_exhausted=excluded.repeat_exhausted,updated_at=excluded.updated_at""", (service["id"], failures, 1 if is_alerting else 0, last_alerted, repeat_count, 1 if exhausted else 0, checked_at))
        if recovery:
            _event(database, site["id"], service["id"], "recovered", "服务已恢复健康", sample)
        elif should_email:
            _event(database, site["id"], service["id"], "alert_triggered" if not repeated else "alert_repeated", "服务健康检查异常", sample)
        database.commit()
        recipients = json.loads(settings["email_recipients_json"] or "[]")
    # SMTP is intentionally outside the SQLite transaction. A mail failure
    # must never lose a health snapshot or block other services.
    if (should_email or recovery) and recipients:
        try:
            _alert_email(site, service, sample, recipients, recovery=recovery, repeated=repeated)
            with conn() as database:
                _event(database, site["id"], service["id"], "recovery_email_sent" if recovery else "alert_email_sent", f"邮件已发送给 {len(recipients)} 个收件人", {"recipients": recipients})
                database.commit()
        except Exception as exc:  # noqa: BLE001
            logger.warning("Service navigator health email failed: %s", exc)
            with conn() as database:
                _event(database, site["id"], service["id"], "recovery_email_failed" if recovery else "alert_email_failed", f"邮件发送失败：{str(exc)[:350]}", {"recipients": recipients, "error": str(exc)[:500]})
                database.commit()
    return sample

def _check_service_health(site: dict[str, Any], service: dict[str, Any], *, manual: bool) -> dict[str, Any]:
    url = _health_url(service)
    if not url:
        sample = {"status": "unknown", "statusCode": None, "latencyMs": None, "finalUrl": "", "error": "未配置可用的健康检查 URL"}
        # A web service discovered without a working URL remains unknown rather
        # than producing a false outage alert.
        return _record_health_result(site, service, sample, manual=True)
    with HEALTH_SEMAPHORE:
        sample = _check_http_health(url)
    return _record_health_result(site, service, sample, manual=manual)

def check_health(service_id: str, user: User) -> dict[str, Any]:
    site = _owner_site(user)
    with conn() as database:
        row = database.execute("SELECT s.* FROM service_navigator_services s JOIN service_navigator_targets t ON t.id=s.target_id WHERE s.id=? AND t.site_id=?", (service_id, site["id"])).fetchone()
    if not row:
        raise ToolboxError("SERVICE_NOT_FOUND", "服务不存在", status_code=404, tool_id=TOOL_ID)
    # The persisted toggle is authoritative. Older discoveries can have a
    # weak/unknown fingerprint even though the owner explicitly enabled HTTP
    # health checks in the editor.
    if row["service_type"] != "http" or not row["health_enabled"]:
        raise ToolboxError("HEALTH_NOT_ENABLED", "该服务未启用 HTTP 健康检测", status_code=400, tool_id=TOOL_ID)
    return _check_service_health(site, _row(row), manual=True)

def check_site_health(user: User) -> list[dict[str, Any]]:
    site = _owner_site(user)
    return _collect_site_health(site, manual=True)

def _collect_site_health(site: dict[str, Any], *, manual: bool) -> list[dict[str, Any]]:
    with conn() as database:
        rows = database.execute("SELECT s.* FROM service_navigator_services s JOIN service_navigator_targets t ON t.id=s.target_id WHERE t.site_id=? AND s.health_enabled=1", (site["id"],)).fetchall()
    services = [_row(row) for row in rows]
    if not services:
        return []
    with concurrent.futures.ThreadPoolExecutor(max_workers=min(10, len(services))) as executor:
        futures = [executor.submit(_check_service_health, site, item, manual=manual) for item in services]
        return [future.result() for future in concurrent.futures.as_completed(futures)]

def list_health_snapshots(service_id: str, user: User, limit: int = 100) -> list[dict[str, Any]]:
    site = _owner_site(user)
    with conn() as database:
        owned = database.execute("SELECT 1 FROM service_navigator_services s JOIN service_navigator_targets t ON t.id=s.target_id WHERE s.id=? AND t.site_id=?", (service_id, site["id"])).fetchone()
        if not owned:
            raise ToolboxError("SERVICE_NOT_FOUND", "服务不存在", status_code=404, tool_id=TOOL_ID)
        rows = database.execute("SELECT * FROM service_navigator_health_snapshots WHERE service_id=? ORDER BY checked_at DESC LIMIT ?", (service_id, max(1, min(limit, 500)))).fetchall()
    return [{"id": row["id"], "checkedAt": row["checked_at"], "status": row["status"], "statusCode": row["status_code"], "latencyMs": row["latency_ms"], "finalUrl": row["final_url"], "error": row["error"], "manual": bool(row["manual"])} for row in rows]

def list_health_events(user: User, limit: int = 100) -> list[dict[str, Any]]:
    site = _owner_site(user)
    with conn() as database:
        rows = database.execute("SELECT e.*,s.display_name,s.service_name FROM service_navigator_health_events e JOIN service_navigator_services s ON s.id=e.service_id WHERE e.site_id=? ORDER BY e.created_at DESC LIMIT ?", (site["id"], max(1, min(limit, 500)))).fetchall()
    return [{"id": row["id"], "serviceId": row["service_id"], "serviceName": row["display_name"] or row["service_name"], "eventType": row["event_type"], "message": row["message"], "details": json.loads(row["details_json"] or "{}"), "createdAt": row["created_at"]} for row in rows]

def _prune_health_data() -> None:
    cutoff = (datetime.now(timezone.utc) - timedelta(days=HEALTH_RETENTION_DAYS)).isoformat()
    with conn() as database:
        database.execute("DELETE FROM service_navigator_health_snapshots WHERE checked_at<?", (cutoff,))
        database.execute("DELETE FROM service_navigator_health_events WHERE created_at<?", (cutoff,))
        database.commit()

def collect_due_health_checks() -> None:
    """Scheduler entry point. A broken site or SMTP must not starve peers."""
    _prune_health_data()
    users = {item.id: item for item in list_users() if not item.disabled}
    with conn() as database:
        rows = database.execute("""SELECT site.*,settings.last_checked_at,settings.check_interval_seconds
            FROM service_navigator_sites site JOIN service_navigator_health_settings settings ON settings.site_id=site.id""").fetchall()
    current = datetime.now(timezone.utc)
    for row in rows:
        site = _row(row)
        owner = users.get(site["owner_user_id"])
        if not owner or not can_access_tool(TOOL_ID, owner):
            continue
        last = _parse_time(row["last_checked_at"])
        if last and current - last < timedelta(seconds=int(row["check_interval_seconds"])):
            continue
        with HEALTH_ACTIVE_LOCK:
            if site["id"] in HEALTH_ACTIVE_SITES:
                continue
            HEALTH_ACTIVE_SITES.add(site["id"])
        try:
            _collect_site_health(site, manual=False)
        except Exception:
            logger.exception("Health collection failed for site %s", site["id"])
        finally:
            with HEALTH_ACTIVE_LOCK:
                HEALTH_ACTIVE_SITES.discard(site["id"])

__all__ = ['_alert_email', '_check_http_health', '_check_service_health', '_clean_recipients', '_collect_site_health', '_ensure_health_settings', '_event', '_health_settings_public', '_health_url', '_parse_time', '_prune_health_data', '_record_health_result', 'check_health', 'check_site_health', 'collect_due_health_checks', 'get_health_settings', 'list_health_events', 'list_health_snapshots', 'update_health_settings']
