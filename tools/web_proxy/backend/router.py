from __future__ import annotations

import json
import os
import secrets
import shutil
import subprocess
import threading
import time
from pathlib import Path
from urllib.error import URLError
from urllib.parse import urlencode
from urllib.request import Request as UrlRequest
from urllib.request import urlopen

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from backend.app.core.config import get_settings
from backend.app.core.errors import ToolboxError
from backend.app.core.security import get_optional_user, require_user, require_user_tool_data_dir

router = APIRouter()

TOOL_ID = "web_proxy"
SIDECAR_HOST = "127.0.0.1"
SIDECAR_PORT = 8787
SIDECAR_CROSS_PORT = 8788
SIDECAR_BASE_URL = f"http://{SIDECAR_HOST}:{SIDECAR_PORT}"
ROOT = Path(__file__).resolve().parents[1]
VENDOR_DIR = ROOT / "vendor" / "rammerhead"
SESSION_META = "session.json"
_sidecar_process: subprocess.Popen | None = None
_sidecar_lock = threading.Lock()
_sidecar_log_handle = None
_sidecar_public_info: tuple[str, int, str] | None = None


@router.get("", response_model=None)
@router.get("/", response_model=None)
def open_proxy(request: Request, url: str | None = None) -> HTMLResponse | RedirectResponse:
    user = get_optional_user(request)
    if user is None:
        return _login_page(request, url)
    if not url:
        return _direct_entry_page()

    data_dir = require_user_tool_data_dir(request, TOOL_ID)
    target_url = _normalize_target_url(url)
    session_id = _get_or_create_session(data_dir, request)
    _edit_session(session_id, enable_shuffling=False)
    return RedirectResponse(f"{_external_origin(request)}/{session_id}/{target_url}", status_code=302)


@router.get("/session")
def session_status(request: Request) -> dict[str, str | bool | None]:
    data_dir = require_user_tool_data_dir(request, TOOL_ID)
    session_id = _read_session_id(data_dir)
    return {
        "active": bool(session_id and _sidecar_session_exists(session_id, request)),
        "sessionId": session_id,
        "sidecarUrl": SIDECAR_BASE_URL,
    }


@router.post("/session/clear")
def clear_session(request: Request) -> dict[str, bool]:
    require_user(request)
    data_dir = require_user_tool_data_dir(request, TOOL_ID)
    session_id = _read_session_id(data_dir)
    if session_id:
        _delete_sidecar_session(session_id)
    _session_meta_path(data_dir).unlink(missing_ok=True)
    return {"cleared": True}


def _get_or_create_session(data_dir: Path, request: Request | None = None) -> str:
    session_id = _read_session_id(data_dir)
    if session_id and _sidecar_session_exists(session_id, request):
        return session_id

    session_id = _create_sidecar_session(request)
    _write_session_id(data_dir, session_id)
    return session_id


def _create_sidecar_session(request: Request | None = None) -> str:
    _ensure_sidecar(request)
    password = _sidecar_password()
    query = urlencode({"pwd": password})
    session_id = _sidecar_get(f"/newsession?{query}").strip()
    if not session_id:
        raise ToolboxError("WEB_PROXY_SESSION_FAILED", "代理会话创建失败", status_code=502, tool_id=TOOL_ID)
    return session_id


def _edit_session(session_id: str, enable_shuffling: bool) -> None:
    password = _sidecar_password()
    query = urlencode({"id": session_id, "enableShuffling": "1" if enable_shuffling else "0", "pwd": password})
    response = _sidecar_get(f"/editsession?{query}").strip()
    if response != "Success":
        raise ToolboxError("WEB_PROXY_SESSION_FAILED", "代理会话配置失败", status_code=502, tool_id=TOOL_ID)


def _delete_sidecar_session(session_id: str) -> None:
    _ensure_sidecar()
    password = _sidecar_password()
    query = urlencode({"id": session_id, "pwd": password})
    _sidecar_get(f"/deletesession?{query}")


def _sidecar_session_exists(session_id: str, request: Request | None = None) -> bool:
    _ensure_sidecar(request)
    query = urlencode({"id": session_id})
    return _sidecar_get(f"/sessionexists?{query}").strip() == "exists"


def _ensure_sidecar(request: Request | None = None) -> None:
    global _sidecar_log_handle, _sidecar_process
    global _sidecar_public_info
    with _sidecar_lock:
        public_info = _public_server_info(request)
        if (
            _sidecar_process is not None
            and _sidecar_process.poll() is None
            and _sidecar_ready()
            and (_sidecar_public_info is None or _sidecar_public_info == public_info)
        ):
            return
        if _sidecar_process is not None and _sidecar_process.poll() is None:
            _sidecar_process.terminate()
            try:
                _sidecar_process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                _sidecar_process.kill()

        _ensure_node_dependencies()
        _ensure_sidecar_build()
        data_dir = _global_data_dir()
        (data_dir / "sessions").mkdir(parents=True, exist_ok=True)
        (data_dir / "cache-js").mkdir(parents=True, exist_ok=True)

        env = os.environ.copy()
        env.update(
            {
                "PANSIS_WEB_PROXY_DATA_DIR": str(data_dir),
                "PANSIS_WEB_PROXY_HOST": SIDECAR_HOST,
                "PANSIS_WEB_PROXY_PORT": str(SIDECAR_PORT),
                "PANSIS_WEB_PROXY_CROSS_PORT": str(SIDECAR_CROSS_PORT),
                "PANSIS_WEB_PROXY_PUBLIC_HOST": public_info[0],
                "PANSIS_WEB_PROXY_PUBLIC_PORT": str(public_info[1]),
                "PANSIS_WEB_PROXY_PUBLIC_PROTOCOL": public_info[2],
                "PANSIS_WEB_PROXY_PASSWORD": _sidecar_password(),
            }
        )
        if _sidecar_log_handle is None or _sidecar_log_handle.closed:
            _sidecar_log_handle = (_global_data_dir() / "sidecar.log").open("ab")
        _sidecar_process = subprocess.Popen(
            _runtime_command("node", "src/server.js"),
            cwd=VENDOR_DIR,
            env=env,
            stdout=_sidecar_log_handle,
            stderr=_sidecar_log_handle,
        )
        _sidecar_public_info = public_info
        _wait_for_sidecar()


def _ensure_node_dependencies() -> None:
    if (VENDOR_DIR / "node_modules" / "testcafe-hammerhead").exists() and (VENDOR_DIR / "node_modules" / "dotenv-flow").exists():
        return
    subprocess.run(
        _runtime_command("npm", "install", "--ignore-scripts"),
        cwd=VENDOR_DIR,
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=180,
    )


def _ensure_sidecar_build() -> None:
    if (VENDOR_DIR / "src" / "client" / "hammerhead.min.js").exists():
        return
    subprocess.run(
        _runtime_command("npm", "run", "build"),
        cwd=VENDOR_DIR,
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=180,
    )


def _runtime_command(name: str, *args: str) -> list[str]:
    if shutil.which(name):
        return [name, *args]
    conda = shutil.which("conda")
    if conda:
        return [conda, "run", "-n", "pansis_toolbox", name, *args]
    raise ToolboxError(
        "WEB_PROXY_RUNTIME_MISSING",
        f"未找到 {name}，请在 pansis_toolbox 环境中安装 Node.js",
        status_code=500,
        tool_id=TOOL_ID,
    )


def _wait_for_sidecar() -> None:
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        if _sidecar_process is not None and _sidecar_process.poll() is not None:
            break
        if _sidecar_ready():
            return
        time.sleep(0.25)
    raise ToolboxError("WEB_PROXY_START_FAILED", "网页代理进程启动失败", status_code=502, tool_id=TOOL_ID)


def _sidecar_ready() -> bool:
    try:
        _sidecar_get("/needpassword", timeout=1)
        return True
    except ToolboxError:
        return False


def _sidecar_get(path: str, timeout: int = 10) -> str:
    request = UrlRequest(f"{SIDECAR_BASE_URL}{path}", headers={"User-Agent": "pansis-toolbox-web-proxy"})
    try:
        with urlopen(request, timeout=timeout) as response:
            return response.read().decode("utf-8", errors="replace")
    except URLError as exc:
        raise ToolboxError("WEB_PROXY_UNAVAILABLE", "网页代理进程不可用", status_code=502, tool_id=TOOL_ID) from exc


def _read_session_id(data_dir: Path) -> str | None:
    path = _session_meta_path(data_dir)
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    session_id = payload.get("sessionId")
    return session_id if isinstance(session_id, str) and session_id else None


def _write_session_id(data_dir: Path, session_id: str) -> None:
    _session_meta_path(data_dir).write_text(json.dumps({"sessionId": session_id}, indent=2), encoding="utf-8")


def _session_meta_path(data_dir: Path) -> Path:
    return data_dir / SESSION_META


def _global_data_dir() -> Path:
    return (get_settings().storage_dir / TOOL_ID).resolve()


def _sidecar_password() -> str:
    path = _global_data_dir() / "sidecar_password.txt"
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        return path.read_text(encoding="utf-8").strip()
    password = secrets.token_urlsafe(32)
    path.write_text(password, encoding="utf-8")
    return password


def _normalize_target_url(url: str) -> str:
    value = url.strip()
    if not value:
        raise ToolboxError("WEB_PROXY_INVALID_URL", "请输入网址", status_code=400, tool_id=TOOL_ID)
    if value.startswith(("http://", "https://")):
        return value
    if "://" in value:
        raise ToolboxError("WEB_PROXY_INVALID_URL", "仅支持 http 和 https 网址", status_code=400, tool_id=TOOL_ID)
    return f"https://{value}"


def _external_origin(request: Request) -> str:
    proto = (request.headers.get("x-forwarded-proto") or request.url.scheme or "http").split(",")[0].strip()
    host = (request.headers.get("x-forwarded-host") or request.headers.get("host") or request.url.netloc).split(",")[0].strip()
    return f"{proto}://{host}"


def _public_server_info(request: Request | None) -> tuple[str, int, str]:
    if request is None:
        return (SIDECAR_HOST, SIDECAR_PORT, "http:")
    proto = (request.headers.get("x-forwarded-proto") or request.url.scheme or "http").split(",")[0].strip()
    host = (request.headers.get("x-forwarded-host") or request.headers.get("host") or request.url.netloc).split(",")[0].strip()
    port = 443 if proto == "https" else 80
    hostname = host
    if host.startswith("[") and "]" in host:
        hostname, _, tail = host.partition("]")
        hostname = f"{hostname}]"
        if tail.startswith(":") and tail[1:].isdigit():
            port = int(tail[1:])
    elif ":" in host:
        hostname, maybe_port = host.rsplit(":", 1)
        if maybe_port.isdigit():
            port = int(maybe_port)
    return (hostname, port, f"{proto}:")


def _login_page(request: Request, url: str | None) -> HTMLResponse:
    query = f"?{urlencode({'url': url})}" if url else ""
    login_url = f"/login?redirect=/web-proxy{query}"
    return HTMLResponse(
        f"""<!doctype html>
<html lang="zh-CN">
<head><meta charset="utf-8"><title>需要登录</title></head>
<body style="font-family: -apple-system, BlinkMacSystemFont, sans-serif; padding: 32px;">
  <h1>需要登录后使用网页代理</h1>
  <p>目标网站登录态会绑定到当前 toolbox 用户。</p>
  <p><a href="{login_url}">前往登录</a></p>
</body>
</html>""",
        status_code=401,
    )


def _direct_entry_page() -> HTMLResponse:
    return HTMLResponse(
        """<!doctype html>
<html lang="zh-CN">
<head><meta charset="utf-8"><title>网页代理</title></head>
<body style="font-family: -apple-system, BlinkMacSystemFont, sans-serif; padding: 32px;">
  <form method="get" action="/web-proxy">
    <input name="url" placeholder="https://example.com" style="min-width: 360px; padding: 10px;">
    <button type="submit" style="padding: 10px 14px;">打开</button>
  </form>
</body>
</html>"""
    )
