"""
Docker 多租户管理工具 - 核心服务层
支持：服务器管理、镜像管理、容器管理、卷管理、模板管理
"""
from __future__ import annotations

import base64
import io
import json
import os
import secrets
import shlex
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Generator

from cryptography.fernet import Fernet, InvalidToken

from backend.app.core.config import get_settings
from backend.app.core.errors import ToolboxError
from backend.app.db.database import get_connection
from backend.app.services.auth_service import User

TOOL_ID = "docker_manager"

# 模板 MD 文件存储目录
TEMPLATES_DIR = Path(__file__).parent.parent / "templates"
TEMPLATES_DIR.mkdir(exist_ok=True)


# ==============================================================
# 加密工具（与 server_monitor 共享同一密钥派生方式）
# ==============================================================

def _get_fernet() -> Fernet:
    secret = get_settings().session_secret
    key = base64.urlsafe_b64encode(secret.encode("utf-8").ljust(32)[:32])
    return Fernet(key)


def _encrypt(plain: str) -> str:
    return _get_fernet().encrypt(plain.encode("utf-8")).decode("utf-8")


def _decrypt(cipher: str) -> str:
    try:
        return _get_fernet().decrypt(cipher.encode("utf-8")).decode("utf-8")
    except InvalidToken as exc:
        raise ToolboxError("DECRYPT_ERROR", "凭证解密失败", status_code=500, tool_id=TOOL_ID) from exc


# ==============================================================
# 数据库初始化
# ==============================================================

_DB_INITIALIZED = False
_DB_LOCK = threading.Lock()


def init_docker_database() -> None:
    global _DB_INITIALIZED
    with _DB_LOCK:
        if _DB_INITIALIZED:
            return
        with get_connection() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS docker_servers (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    host TEXT NOT NULL,
                    port INTEGER NOT NULL DEFAULT 22,
                    ssh_username TEXT NOT NULL,
                    ssh_password_encrypted TEXT NOT NULL,
                    created_by TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    cuda_available INTEGER NOT NULL DEFAULT 0,
                    gpu_count INTEGER NOT NULL DEFAULT 0,
                    gpu_info TEXT NOT NULL DEFAULT '[]'
                );

                -- 旧的粗粒度权限表（保留兼容）
                CREATE TABLE IF NOT EXISTS docker_server_permissions (
                    server_id TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    level TEXT NOT NULL DEFAULT 'none',
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY(server_id, user_id),
                    FOREIGN KEY(server_id) REFERENCES docker_servers(id)
                );

                -- 新的细粒度权限表（替代旧 quotas 表）
                CREATE TABLE IF NOT EXISTS docker_user_perms (
                    server_id TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    -- 服务器可见性
                    server_visible INTEGER NOT NULL DEFAULT 0,
                    -- 镜像权限
                    img_pull INTEGER NOT NULL DEFAULT 0,
                    img_delete INTEGER NOT NULL DEFAULT 0,
                    img_copy INTEGER NOT NULL DEFAULT 0,
                    -- 容器权限
                    ctr_view_own INTEGER NOT NULL DEFAULT 0,
                    ctr_view_all INTEGER NOT NULL DEFAULT 0,
                    ctr_create_run INTEGER NOT NULL DEFAULT 0,
                    ctr_create_compose INTEGER NOT NULL DEFAULT 0,
                    ctr_create_template INTEGER NOT NULL DEFAULT 0,
                    ctr_manage_own INTEGER NOT NULL DEFAULT 0,
                    ctr_manage_all INTEGER NOT NULL DEFAULT 0,
                    ctr_path_whitelist TEXT NOT NULL DEFAULT '[]',
                    -- 卷权限
                    vol_create INTEGER NOT NULL DEFAULT 0,
                    vol_delete_own INTEGER NOT NULL DEFAULT 0,
                    vol_delete_all INTEGER NOT NULL DEFAULT 0,
                    vol_copy INTEGER NOT NULL DEFAULT 0,
                    vol_quota_gb REAL NOT NULL DEFAULT 0,
                    -- 模板权限
                    tpl_use INTEGER NOT NULL DEFAULT 0,
                    tpl_create INTEGER NOT NULL DEFAULT 0,
                    tpl_edit INTEGER NOT NULL DEFAULT 0,
                    -- CUDA 权限（可用显卡序号列表，JSON 数组）
                    cuda_gpu_indices TEXT NOT NULL DEFAULT '[]',
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY(server_id, user_id),
                    FOREIGN KEY(server_id) REFERENCES docker_servers(id)
                );

                CREATE TABLE IF NOT EXISTS docker_templates (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    description TEXT NOT NULL DEFAULT '',
                    category TEXT NOT NULL DEFAULT 'general',
                    creator_id TEXT NOT NULL,
                    doc_file TEXT NOT NULL DEFAULT '',
                    config_json TEXT NOT NULL DEFAULT '{}',
                    is_public INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS docker_volumes_meta (
                    id TEXT PRIMARY KEY,
                    volume_name TEXT NOT NULL,
                    server_id TEXT NOT NULL,
                    owner_user_id TEXT NOT NULL,
                    size_gb REAL NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    UNIQUE(volume_name, server_id),
                    FOREIGN KEY(server_id) REFERENCES docker_servers(id)
                );

                -- 镜像所有权元数据（记录平台侧的镜像归属，按 repo:tag 或 image_id 索引）
                CREATE TABLE IF NOT EXISTS docker_images_meta (
                    id TEXT PRIMARY KEY,
                    image_ref TEXT NOT NULL,       -- 镜像标识：repo:tag 或 image_id（sha256前缀）
                    server_id TEXT NOT NULL,
                    owner_user_id TEXT NOT NULL,
                    assigned_at TEXT NOT NULL,
                    UNIQUE(image_ref, server_id),
                    FOREIGN KEY(server_id) REFERENCES docker_servers(id)
                );

                -- 容器所有权元数据（记录平台侧的容器归属，按容器 ID 或名称索引）
                CREATE TABLE IF NOT EXISTS docker_containers_meta (
                    id TEXT PRIMARY KEY,
                    container_ref TEXT NOT NULL,   -- 容器名称或短 ID（docker ps 中的 Names 字段）
                    server_id TEXT NOT NULL,
                    owner_user_id TEXT NOT NULL,
                    assigned_at TEXT NOT NULL,
                    UNIQUE(container_ref, server_id),
                    FOREIGN KEY(server_id) REFERENCES docker_servers(id)
                );

                -- 资源多角色关系表（多所有者、查看者、创建者、配额占用者）
                -- resource_type: container | image | volume
                -- role: owner | viewer | creator | quota_holder
                -- quota_holder: 配额占用者，必须同时具备 owner 角色
                CREATE TABLE IF NOT EXISTS docker_resource_roles (
                    id TEXT PRIMARY KEY,
                    server_id TEXT NOT NULL,
                    resource_type TEXT NOT NULL,
                    resource_ref TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    role TEXT NOT NULL,          -- owner | viewer | creator | quota_holder
                    assigned_at TEXT NOT NULL,
                    UNIQUE(server_id, resource_type, resource_ref, user_id, role),
                    FOREIGN KEY(server_id) REFERENCES docker_servers(id)
                );

                -- 资源配额模式表（记录每个服务器每种资源类型的默认配额模式）
                -- quota_mode: shared（所有者均分）| exclusive（配额占用者独占）
                CREATE TABLE IF NOT EXISTS docker_server_quota_mode (
                    server_id TEXT NOT NULL,
                    resource_type TEXT NOT NULL,   -- container | image | volume
                    quota_mode TEXT NOT NULL DEFAULT 'shared',
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY(server_id, resource_type),
                    FOREIGN KEY(server_id) REFERENCES docker_servers(id)
                );
                """
            )
            # 兼容迁移：为旧版 docker_user_perms 表添加 vol_copy 列（如果不存在）
            cols = {r[1] for r in conn.execute("PRAGMA table_info(docker_user_perms)").fetchall()}
            if "vol_copy" not in cols:
                conn.execute("ALTER TABLE docker_user_perms ADD COLUMN vol_copy INTEGER NOT NULL DEFAULT 0")
            if "cuda_gpu_indices" not in cols:
                conn.execute("ALTER TABLE docker_user_perms ADD COLUMN cuda_gpu_indices TEXT NOT NULL DEFAULT '[]'")
            # 兼容迁移：为旧版 docker_servers 表添加 CUDA 字段
            srv_cols = {r[1] for r in conn.execute("PRAGMA table_info(docker_servers)").fetchall()}
            if "cuda_available" not in srv_cols:
                conn.execute("ALTER TABLE docker_servers ADD COLUMN cuda_available INTEGER NOT NULL DEFAULT 0")
            if "gpu_count" not in srv_cols:
                conn.execute("ALTER TABLE docker_servers ADD COLUMN gpu_count INTEGER NOT NULL DEFAULT 0")
            if "gpu_info" not in srv_cols:
                conn.execute("ALTER TABLE docker_servers ADD COLUMN gpu_info TEXT NOT NULL DEFAULT '[]'")
            # 兼容迁移：为旧版 docker_volumes_meta 表添加 creator_user_id 列
            vol_cols = {r[1] for r in conn.execute("PRAGMA table_info(docker_volumes_meta)").fetchall()}
            if "creator_user_id" not in vol_cols:
                conn.execute("ALTER TABLE docker_volumes_meta ADD COLUMN creator_user_id TEXT NOT NULL DEFAULT ''")
            # 兼容迁移：为旧版 docker_images_meta 添加 creator_user_id 列
            img_cols = {r[1] for r in conn.execute("PRAGMA table_info(docker_images_meta)").fetchall()}
            if "creator_user_id" not in img_cols:
                conn.execute("ALTER TABLE docker_images_meta ADD COLUMN creator_user_id TEXT NOT NULL DEFAULT ''")
            # 兼容迁移：为旧版 docker_containers_meta 添加 creator_user_id 列
            ctr_cols = {r[1] for r in conn.execute("PRAGMA table_info(docker_containers_meta)").fetchall()}
            if "creator_user_id" not in ctr_cols:
                conn.execute("ALTER TABLE docker_containers_meta ADD COLUMN creator_user_id TEXT NOT NULL DEFAULT ''")
        _DB_INITIALIZED = True


# ==============================================================
# 工具函数
# ==============================================================

def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_id() -> str:
    return secrets.token_hex(12)


def _permission_level(level: str) -> int:
    """旧粗粒度级别数字化（兼容用）"""
    return {"manage": 4, "use": 3, "view": 2, "none": 0}.get(level, 0)


# ── 新细粒度权限 ──────────────────────────────────────────────

_PERMS_DEFAULTS: dict[str, Any] = {
    "server_visible": False,
    "img_pull": False,
    "img_delete": False,
    "img_copy": False,
    "ctr_view_own": False,
    "ctr_view_all": False,
    "ctr_create_run": False,
    "ctr_create_compose": False,
    "ctr_create_template": False,
    "ctr_manage_own": False,
    "ctr_manage_all": False,
    "ctr_path_whitelist": [],
    "vol_create": False,
    "vol_delete_own": False,
    "vol_delete_all": False,
    "vol_copy": False,
    "vol_quota_gb": 0.0,
    "tpl_use": False,
    "tpl_create": False,
    "tpl_edit": False,
    "cuda_gpu_indices": [],
}

# 管理员拥有所有权限的完整集合
_ADMIN_PERMS: dict[str, Any] = {k: (True if isinstance(v, bool) else ([] if isinstance(v, list) else 999.0)) for k, v in _PERMS_DEFAULTS.items()}
_ADMIN_PERMS["ctr_path_whitelist"] = []   # 管理员路径不受限
_ADMIN_PERMS["vol_quota_gb"] = 0.0        # 管理员配额无限制（0=不限）


def _row_to_perms(row: sqlite3.Row) -> dict[str, Any]:
    """将数据库行转换为权限字典"""
    keys = set(row.keys())
    return {
        "server_visible": bool(row["server_visible"]),
        "img_pull": bool(row["img_pull"]),
        "img_delete": bool(row["img_delete"]),
        "img_copy": bool(row["img_copy"]),
        "ctr_view_own": bool(row["ctr_view_own"]),
        "ctr_view_all": bool(row["ctr_view_all"]),
        "ctr_create_run": bool(row["ctr_create_run"]),
        "ctr_create_compose": bool(row["ctr_create_compose"]),
        "ctr_create_template": bool(row["ctr_create_template"]),
        "ctr_manage_own": bool(row["ctr_manage_own"]),
        "ctr_manage_all": bool(row["ctr_manage_all"]),
        "ctr_path_whitelist": json.loads(row["ctr_path_whitelist"]),
        "vol_create": bool(row["vol_create"]),
        "vol_delete_own": bool(row["vol_delete_own"]),
        "vol_delete_all": bool(row["vol_delete_all"]),
        "vol_copy": bool(row["vol_copy"]) if "vol_copy" in keys else False,
        "vol_quota_gb": float(row["vol_quota_gb"]),
        "tpl_use": bool(row["tpl_use"]),
        "tpl_create": bool(row["tpl_create"]),
        "tpl_edit": bool(row["tpl_edit"]),
        "cuda_gpu_indices": json.loads(row["cuda_gpu_indices"]) if "cuda_gpu_indices" in keys else [],
    }


def _get_user_perms(conn: sqlite3.Connection, server_id: str, user: User) -> dict[str, Any]:
    """获取用户对服务器的细粒度权限（管理员返回全满权限）"""
    if user.role == "admin":
        return dict(_ADMIN_PERMS)
    row = conn.execute(
        "SELECT * FROM docker_user_perms WHERE server_id = ? AND user_id = ?",
        (server_id, user.id),
    ).fetchone()
    if row:
        return _row_to_perms(row)
    return dict(_PERMS_DEFAULTS)


def _get_user_permission(conn: sqlite3.Connection, server_id: str, user: User) -> str:
    """兼容旧调用：从新权限表推断出粗粒度 level"""
    if user.role == "admin":
        return "manage"
    perms = _get_user_perms(conn, server_id, user)
    if not perms["server_visible"]:
        return "none"
    # 判断是否拥有管理类权限
    manage_flags = ["ctr_manage_all", "vol_delete_all", "tpl_edit"]
    if any(perms.get(f) for f in manage_flags):
        return "manage"
    # 判断是否有创建/操作类权限
    use_flags = ["img_pull", "ctr_create_run", "ctr_create_compose", "ctr_create_template",
                 "ctr_manage_own", "vol_create", "tpl_use"]
    if any(perms.get(f) for f in use_flags):
        return "use"
    return "view"


def _require_permission(conn: sqlite3.Connection, server_id: str, user: User, min_level: str) -> None:
    level = _get_user_permission(conn, server_id, user)
    if _permission_level(level) < _permission_level(min_level):
        raise ToolboxError(
            "PERMISSION_DENIED",
            f"权限不足，需要 {min_level} 权限",
            status_code=403,
            tool_id=TOOL_ID,
        )


def _require_perm(conn: sqlite3.Connection, server_id: str, user: User, perm: str) -> None:
    """校验指定细粒度权限位"""
    if user.role == "admin":
        return
    perms = _get_user_perms(conn, server_id, user)
    if not perms.get(perm, False):
        label_map = {
            "img_pull": "拉取镜像", "img_delete": "删除镜像", "img_copy": "复制镜像",
            "ctr_view_own": "查看容器", "ctr_view_all": "查看全部容器",
            "ctr_create_run": "创建容器(run)", "ctr_create_compose": "创建容器(compose)",
            "ctr_create_template": "从模板创建容器",
            "ctr_manage_own": "管理自己的容器", "ctr_manage_all": "管理全部容器",
            "vol_create": "创建卷", "vol_delete_own": "删除自己的卷", "vol_delete_all": "删除全部卷", "vol_copy": "复制卷",
            "tpl_use": "使用模板", "tpl_create": "创建模板", "tpl_edit": "编辑模板",
        }
        raise ToolboxError(
            "PERMISSION_DENIED",
            f"您没有权限：{label_map.get(perm, perm)}",
            status_code=403,
            tool_id=TOOL_ID,
        )


def _get_quota(conn: sqlite3.Connection, server_id: str, user_id: str) -> dict[str, Any]:
    """兼容旧调用，从新 perms 表读取"""
    if user_id == "admin":
        return {"volumeTotalGb": 0.0, "pathWhitelist": [], "canCreateContainer": True, "canManageContainer": True}
    row = conn.execute(
        "SELECT * FROM docker_user_perms WHERE server_id = ? AND user_id = ?",
        (server_id, user_id),
    ).fetchone()
    if row:
        p = _row_to_perms(row)
        return {
            "volumeTotalGb": p["vol_quota_gb"],
            "pathWhitelist": p["ctr_path_whitelist"],
            "canCreateContainer": p["ctr_create_run"] or p["ctr_create_compose"] or p["ctr_create_template"],
            "canManageContainer": p["ctr_manage_own"] or p["ctr_manage_all"],
        }
    return {"volumeTotalGb": 0.0, "pathWhitelist": [], "canCreateContainer": False, "canManageContainer": False}


def _public_server(row: sqlite3.Row) -> dict[str, Any]:
    keys = set(row.keys())
    return {
        "id": row["id"],
        "name": row["name"],
        "host": row["host"],
        "port": row["port"],
        "sshUsername": row["ssh_username"],
        "createdBy": row["created_by"],
        "createdAt": row["created_at"],
        "updatedAt": row["updated_at"],
        "cudaAvailable": bool(row["cuda_available"]) if "cuda_available" in keys else False,
        "gpuCount": int(row["gpu_count"]) if "gpu_count" in keys else 0,
        "gpuInfo": json.loads(row["gpu_info"]) if "gpu_info" in keys else [],
    }


# ==============================================================
# SSH 执行工具
# ==============================================================

def _ssh_connect(server_row: sqlite3.Row):
    """建立 paramiko SSH 连接"""
    try:
        import paramiko
    except ImportError as exc:
        raise ToolboxError("MISSING_DEP", "缺少 paramiko 依赖", status_code=500, tool_id=TOOL_ID) from exc

    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    password = _decrypt(server_row["ssh_password_encrypted"])
    try:
        client.connect(
            hostname=server_row["host"],
            port=server_row["port"],
            username=server_row["ssh_username"],
            password=password,
            timeout=15,
        )
    except Exception as exc:
        raise ToolboxError("SSH_CONNECT_FAILED", f"SSH 连接失败: {exc}", status_code=502, tool_id=TOOL_ID) from exc
    return client


def _ssh_exec(client, cmd: str, timeout: int = 60) -> tuple[str, str, int]:
    """执行命令，返回 (stdout, stderr, exit_code)"""
    stdin, stdout, stderr = client.exec_command(cmd, timeout=timeout)
    exit_code = stdout.channel.recv_exit_status()
    return stdout.read().decode("utf-8", errors="replace"), stderr.read().decode("utf-8", errors="replace"), exit_code


def _get_server_row(conn: sqlite3.Connection, server_id: str) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM docker_servers WHERE id = ?", (server_id,)).fetchone()
    if row is None:
        raise ToolboxError("SERVER_NOT_FOUND", "服务器不存在", status_code=404, tool_id=TOOL_ID)
    return row


# ==============================================================
# 服务器管理
# ==============================================================

def add_server(payload: dict[str, Any], user: User) -> dict[str, Any]:
    """添加服务器（管理员）：验证 SSH + Docker 权限后保存"""
    init_docker_database()
    if user.role != "admin":
        raise ToolboxError("ADMIN_REQUIRED", "只有管理员可以添加服务器", status_code=403, tool_id=TOOL_ID)

    host = payload["host"].strip()
    port = int(payload.get("port", 22))
    ssh_username = payload["sshUsername"].strip()
    ssh_password = payload["sshPassword"]
    name = payload["name"].strip()

    # 验证 SSH 连接和 Docker 权限
    try:
        import paramiko
    except ImportError as exc:
        raise ToolboxError("MISSING_DEP", "缺少 paramiko 依赖", status_code=500, tool_id=TOOL_ID) from exc

    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        client.connect(hostname=host, port=port, username=ssh_username, password=ssh_password, timeout=15)
    except Exception as exc:
        raise ToolboxError("SSH_CONNECT_FAILED", f"SSH 连接失败: {exc}", status_code=502, tool_id=TOOL_ID) from exc

    cuda_available = False
    gpu_count = 0
    gpu_info: list[dict[str, Any]] = []
    try:
        stdout_data, stderr_data, exit_code = _ssh_exec(client, "docker info", timeout=20)
        if exit_code != 0:
            if "permission denied" in stderr_data.lower() or "permission denied" in stdout_data.lower():
                raise ToolboxError(
                    "DOCKER_PERMISSION_DENIED",
                    f"用户 {ssh_username} 没有 Docker 权限，请将该用户加入 docker 用户组（sudo usermod -aG docker {ssh_username}）",
                    status_code=403,
                    tool_id=TOOL_ID,
                )
            raise ToolboxError(
                "DOCKER_NOT_AVAILABLE",
                f"无法执行 docker 命令: {stderr_data.strip()}",
                status_code=502,
                tool_id=TOOL_ID,
            )

        # 检测 CUDA/nvidia-smi 支持
        cuda_out, _, cuda_rc = _ssh_exec(
            client,
            "docker run --rm --gpus all nvidia/cuda:12.0.0-base-ubuntu22.04 nvidia-smi --query-gpu=index,name,memory.total --format=csv,noheader 2>/dev/null || "
            "nvidia-smi --query-gpu=index,name,memory.total --format=csv,noheader 2>/dev/null",
            timeout=30,
        )
        if cuda_rc == 0 and cuda_out.strip():
            cuda_available = True
            for line in cuda_out.strip().splitlines():
                parts = [p.strip() for p in line.split(",")]
                if len(parts) >= 3:
                    gpu_info.append({
                        "index": int(parts[0]) if parts[0].isdigit() else len(gpu_info),
                        "name": parts[1],
                        "memoryTotal": parts[2],
                    })
            gpu_count = len(gpu_info)
    finally:
        client.close()

    server_id = _new_id()
    encrypted_pw = _encrypt(ssh_password)
    now = _now()

    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO docker_servers (id, name, host, port, ssh_username, ssh_password_encrypted, created_by, created_at, updated_at, cuda_available, gpu_count, gpu_info)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (server_id, name, host, port, ssh_username, encrypted_pw, user.id, now, now,
             1 if cuda_available else 0, gpu_count, json.dumps(gpu_info)),
        )
    with get_connection() as conn:
        row = conn.execute("SELECT * FROM docker_servers WHERE id = ?", (server_id,)).fetchone()
    return _public_server(row)


def list_servers(user: User) -> list[dict[str, Any]]:
    """列出用户有权限查看的服务器"""
    init_docker_database()
    with get_connection() as conn:
        all_rows = conn.execute("SELECT * FROM docker_servers ORDER BY name").fetchall()
        result = []
        for row in all_rows:
            level = _get_user_permission(conn, row["id"], user)
            if _permission_level(level) >= _permission_level("view"):
                srv = _public_server(row)
                srv["permissionLevel"] = level
                result.append(srv)
    return result


def get_server(server_id: str, user: User, min_level: str = "view") -> dict[str, Any]:
    """获取单个服务器信息（含权限校验）"""
    init_docker_database()
    with get_connection() as conn:
        row = _get_server_row(conn, server_id)
        _require_permission(conn, server_id, user, min_level)
        level = _get_user_permission(conn, server_id, user)
        srv = _public_server(row)
        srv["permissionLevel"] = level
    return srv


def delete_server(server_id: str, user: User) -> None:
    """删除服务器（管理员）"""
    init_docker_database()
    if user.role != "admin":
        raise ToolboxError("ADMIN_REQUIRED", "只有管理员可以删除服务器", status_code=403, tool_id=TOOL_ID)
    with get_connection() as conn:
        _get_server_row(conn, server_id)
        conn.execute("DELETE FROM docker_server_permissions WHERE server_id = ?", (server_id,))
        conn.execute("DELETE FROM docker_user_perms WHERE server_id = ?", (server_id,))
        conn.execute("DELETE FROM docker_volumes_meta WHERE server_id = ?", (server_id,))
        conn.execute("DELETE FROM docker_servers WHERE id = ?", (server_id,))


def list_server_permissions(server_id: str, user: User) -> list[dict[str, Any]]:
    """列出服务器的用户权限概览（管理员或 manage 级别用户），兼容旧接口"""
    init_docker_database()
    with get_connection() as conn:
        _require_permission(conn, server_id, user, "manage")
        rows = conn.execute(
            """
            SELECT u.id, u.username, u.display_name, u.role
            FROM users u
            WHERE u.disabled = 0
            ORDER BY u.role = 'admin' DESC, u.username
            """,
        ).fetchall()
        result = []
        for row in rows:
            perms = _get_user_perms(conn, server_id, type('_U', (), {'id': row['id'], 'role': row['role']})())
            level = _get_user_permission(conn, server_id, type('_U', (), {'id': row['id'], 'role': row['role']})())
            result.append({
                "userId": row["id"],
                "username": row["username"],
                "displayName": row["display_name"],
                "role": row["role"],
                "level": level,
                "perms": perms,
            })
    return result


def get_user_perms_for_user(server_id: str, target_user_id: str, user: User) -> dict[str, Any]:
    """获取指定用户在服务器上的细粒度权限（管理员或 manage 级别）"""
    init_docker_database()
    with get_connection() as conn:
        _require_permission(conn, server_id, user, "manage")
        _get_server_row(conn, server_id)
        target = conn.execute("SELECT * FROM users WHERE id = ?", (target_user_id,)).fetchone()
        if not target:
            raise ToolboxError("USER_NOT_FOUND", "目标用户不存在", status_code=404, tool_id=TOOL_ID)
        perms = _get_user_perms(conn, server_id, type('_U', (), {'id': target_user_id, 'role': target['role']})())
    return {"serverId": server_id, "userId": target_user_id, "perms": perms}


def set_user_perms(server_id: str, target_user_id: str, perms: dict[str, Any], user: User) -> dict[str, Any]:
    """设置用户在服务器上的细粒度权限（管理员或 manage 级别）"""
    init_docker_database()
    with get_connection() as conn:
        _require_permission(conn, server_id, user, "manage")
        _get_server_row(conn, server_id)
        target = conn.execute("SELECT * FROM users WHERE id = ?", (target_user_id,)).fetchone()
        if not target:
            raise ToolboxError("USER_NOT_FOUND", "目标用户不存在", status_code=404, tool_id=TOOL_ID)
        if target["role"] == "admin":
            raise ToolboxError("ADMIN_IMMUTABLE", "管理员权限不可修改", status_code=400, tool_id=TOOL_ID)

        def b(k: str) -> int:
            return 1 if perms.get(k, False) else 0

        now = _now()
        path_whitelist_json = json.dumps(perms.get("ctr_path_whitelist", []))
        vol_quota_gb = float(perms.get("vol_quota_gb", 0))
        cuda_gpu_indices_json = json.dumps(perms.get("cuda_gpu_indices", []))
        conn.execute(
            """
            INSERT INTO docker_user_perms (
                server_id, user_id, server_visible,
                img_pull, img_delete, img_copy,
                ctr_view_own, ctr_view_all,
                ctr_create_run, ctr_create_compose, ctr_create_template,
                ctr_manage_own, ctr_manage_all, ctr_path_whitelist,
                vol_create, vol_delete_own, vol_delete_all, vol_copy, vol_quota_gb,
                tpl_use, tpl_create, tpl_edit,
                cuda_gpu_indices,
                updated_at
            ) VALUES (
                ?, ?, ?,
                ?, ?, ?,
                ?, ?,
                ?, ?, ?,
                ?, ?, ?,
                ?, ?, ?, ?, ?,
                ?, ?, ?,
                ?,
                ?
            )
            ON CONFLICT(server_id, user_id) DO UPDATE SET
                server_visible = excluded.server_visible,
                img_pull = excluded.img_pull,
                img_delete = excluded.img_delete,
                img_copy = excluded.img_copy,
                ctr_view_own = excluded.ctr_view_own,
                ctr_view_all = excluded.ctr_view_all,
                ctr_create_run = excluded.ctr_create_run,
                ctr_create_compose = excluded.ctr_create_compose,
                ctr_create_template = excluded.ctr_create_template,
                ctr_manage_own = excluded.ctr_manage_own,
                ctr_manage_all = excluded.ctr_manage_all,
                ctr_path_whitelist = excluded.ctr_path_whitelist,
                vol_create = excluded.vol_create,
                vol_delete_own = excluded.vol_delete_own,
                vol_delete_all = excluded.vol_delete_all,
                vol_copy = excluded.vol_copy,
                vol_quota_gb = excluded.vol_quota_gb,
                tpl_use = excluded.tpl_use,
                tpl_create = excluded.tpl_create,
                tpl_edit = excluded.tpl_edit,
                cuda_gpu_indices = excluded.cuda_gpu_indices,
                updated_at = excluded.updated_at
            """,
            (
                server_id, target_user_id, b("server_visible"),
                b("img_pull"), b("img_delete"), b("img_copy"),
                b("ctr_view_own"), b("ctr_view_all"),
                b("ctr_create_run"), b("ctr_create_compose"), b("ctr_create_template"),
                b("ctr_manage_own"), b("ctr_manage_all"), path_whitelist_json,
                b("vol_create"), b("vol_delete_own"), b("vol_delete_all"), b("vol_copy"), vol_quota_gb,
                b("tpl_use"), b("tpl_create"), b("tpl_edit"),
                cuda_gpu_indices_json,
                now,
            ),
        )
        row = conn.execute(
            "SELECT * FROM docker_user_perms WHERE server_id = ? AND user_id = ?",
            (server_id, target_user_id),
        ).fetchone()
    return {"serverId": server_id, "userId": target_user_id, "perms": _row_to_perms(row)}


def set_user_permission(server_id: str, target_user_id: str, level: str, user: User) -> dict[str, Any]:
    """兼容旧接口：按粗粒度 level 批量设置权限"""
    init_docker_database()
    if level not in {"manage", "use", "view", "none"}:
        raise ToolboxError("INVALID_LEVEL", "无效的权限级别", status_code=400, tool_id=TOOL_ID)

    # 按级别映射到细粒度权限预设
    preset: dict[str, Any] = dict(_PERMS_DEFAULTS)
    if level == "none":
        pass  # all False
    elif level == "view":
        preset.update({"server_visible": True, "ctr_view_own": True, "tpl_use": False})
    elif level == "use":
        preset.update({
            "server_visible": True,
            "img_pull": True,
            "ctr_view_own": True,
            "ctr_create_run": True, "ctr_create_compose": True, "ctr_create_template": True,
            "ctr_manage_own": True,
            "vol_create": True, "vol_delete_own": True, "vol_copy": True,
            "tpl_use": True,
        })
    elif level == "manage":
        preset = {k: (True if isinstance(v, bool) else ([] if isinstance(v, list) else 0.0))
                  for k, v in _PERMS_DEFAULTS.items()}

    return set_user_perms(server_id, target_user_id, preset, user)


def set_user_quota(server_id: str, target_user_id: str, quota: dict[str, Any], user: User) -> dict[str, Any]:
    """兼容旧接口：仅更新配额相关字段"""
    init_docker_database()
    with get_connection() as conn:
        _require_permission(conn, server_id, user, "manage")
        existing_row = conn.execute(
            "SELECT * FROM docker_user_perms WHERE server_id = ? AND user_id = ?",
            (server_id, target_user_id),
        ).fetchone()
        existing = _row_to_perms(existing_row) if existing_row else dict(_PERMS_DEFAULTS)

    # 合并配额字段
    existing["vol_quota_gb"] = float(quota.get("volumeTotalGb", existing["vol_quota_gb"]))
    existing["ctr_path_whitelist"] = quota.get("pathWhitelist", existing["ctr_path_whitelist"])
    if quota.get("canCreateContainer") is not None:
        for k in ["ctr_create_run", "ctr_create_compose", "ctr_create_template"]:
            existing[k] = bool(quota["canCreateContainer"])
    if quota.get("canManageContainer") is not None:
        existing["ctr_manage_own"] = bool(quota["canManageContainer"])

    return set_user_perms(server_id, target_user_id, existing, user)


def get_my_quota(server_id: str, user: User) -> dict[str, Any]:
    """获取当前用户在服务器上的细粒度权限与配额信息"""
    init_docker_database()
    with get_connection() as conn:
        _require_permission(conn, server_id, user, "view")
        perms = _get_user_perms(conn, server_id, user)
        # 计算已用卷空间
        used = conn.execute(
            "SELECT COALESCE(SUM(size_gb), 0) as used FROM docker_volumes_meta WHERE server_id = ? AND owner_user_id = ?",
            (server_id, user.id),
        ).fetchone()["used"]
        perms["volumeUsedGb"] = used
    return perms


# ==============================================================
# 镜像管理
# ==============================================================

def list_images(server_id: str, user: User) -> list[dict[str, Any]]:
    """列出服务器上的 Docker 镜像，按所有权/角色过滤"""
    init_docker_database()
    with get_connection() as conn:
        perms = _get_user_perms(conn, server_id, user)
        # 至少需要 ctr_view_own 或 ctr_view_all 之一（或管理员）
        if user.role != "admin" and not perms.get("ctr_view_own") and not perms.get("ctr_view_all"):
            raise ToolboxError("PERMISSION_DENIED", "您没有查看镜像的权限", status_code=403, tool_id=TOOL_ID)
        row = _get_server_row(conn, server_id)
        # 获取镜像所有权元数据（旧表兼容）
        meta_rows = conn.execute(
            "SELECT image_ref, owner_user_id FROM docker_images_meta WHERE server_id = ?", (server_id,)
        ).fetchall()
        meta_map = {r["image_ref"]: r["owner_user_id"] for r in meta_rows}
        # 新角色表：查询当前用户拥有任意角色（owner/viewer/creator）的所有镜像 ref
        user_accessible_refs: set[str] = set()
        for r in conn.execute(
            "SELECT resource_ref FROM docker_resource_roles WHERE server_id=? AND resource_type='image' AND user_id=?",
            (server_id, user.id),
        ).fetchall():
            user_accessible_refs.add(r["resource_ref"])
        view_all = user.role == "admin" or perms.get("ctr_view_all", False)

    client = _ssh_connect(row)
    try:
        stdout, stderr, code = _ssh_exec(
            client,
            'docker images --format \'{"id":"{{.ID}}","repo":"{{.Repository}}","tag":"{{.Tag}}","size":"{{.Size}}","created":"{{.CreatedAt}}"}\' ',
        )
    finally:
        client.close()

    images = []
    for line in stdout.strip().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            img = json.loads(line)
        except json.JSONDecodeError:
            continue

        # 查找所有者：优先用 repo:tag 匹配，其次用 image id 匹配
        ref_full = f"{img.get('repo', '')}:{img.get('tag', '')}"
        owner = meta_map.get(ref_full) or meta_map.get(img.get("id", ""))
        img["ownerUserId"] = owner  # 兼容旧字段（第一个所有者）
        img["platformManaged"] = owner is not None or ref_full in user_accessible_refs

        # 访问过滤：view_all 看全部；否则看有角色关联的（owner/viewer/creator 皆可）
        if view_all:
            images.append(img)
        elif ref_full in user_accessible_refs or owner == user.id:
            images.append(img)

    return images


def pull_image(server_id: str, image_ref: str, user: User) -> dict[str, Any]:
    """在服务器上拉取 Docker 镜像"""
    init_docker_database()
    with get_connection() as conn:
        _require_permission(conn, server_id, user, "use")
        row = _get_server_row(conn, server_id)

    client = _ssh_connect(row)
    try:
        stdout, stderr, code = _ssh_exec(client, f"docker pull {shlex.quote(image_ref)}", timeout=300)
    finally:
        client.close()

    if code != 0:
        raise ToolboxError("PULL_FAILED", f"镜像拉取失败: {stderr.strip()}", status_code=502, tool_id=TOOL_ID)
    return {"success": True, "output": stdout + stderr}


def delete_image(server_id: str, image_ref: str, user: User, force: bool = False) -> dict[str, Any]:
    """删除服务器上的 Docker 镜像（需 img_delete 权限，且须是所有者或 ctr_manage_all）"""
    init_docker_database()
    with get_connection() as conn:
        if user.role != "admin":
            _require_perm(conn, server_id, user, "img_delete")
            perms = _get_user_perms(conn, server_id, user)
            # 检查所有权（非 manage_all 时只能删自己的）
            if not perms.get("ctr_manage_all"):
                meta = conn.execute(
                    "SELECT owner_user_id FROM docker_images_meta WHERE server_id = ? AND image_ref = ?",
                    (server_id, image_ref),
                ).fetchone()
                if meta and meta["owner_user_id"] != user.id:
                    raise ToolboxError("PERMISSION_DENIED", "您只能删除自己拥有的镜像", status_code=403, tool_id=TOOL_ID)
        row = _get_server_row(conn, server_id)

    client = _ssh_connect(row)
    try:
        flag = "-f " if force else ""
        stdout, stderr, code = _ssh_exec(client, f"docker rmi {flag}{shlex.quote(image_ref)}")
    finally:
        client.close()

    if code != 0:
        raise ToolboxError("DELETE_IMAGE_FAILED", f"删除镜像失败: {stderr.strip()}", status_code=502, tool_id=TOOL_ID)

    # 清除平台元数据
    with get_connection() as conn:
        conn.execute(
            "DELETE FROM docker_images_meta WHERE server_id = ? AND image_ref = ?",
            (server_id, image_ref),
        )
    return {"success": True, "output": stdout + stderr}


def copy_image(src_server_id: str, dst_server_id: str, image_ref: str, user: User) -> dict[str, Any]:
    """
    跨服务器复制镜像：docker save | gzip -> 平台中转 -> gunzip | docker load
    整个过程在平台服务器内存中流式处理，避免落盘大文件
    """
    init_docker_database()
    with get_connection() as conn:
        _require_permission(conn, src_server_id, user, "view")
        _require_permission(conn, dst_server_id, user, "use")
        src_row = _get_server_row(conn, src_server_id)
        dst_row = _get_server_row(conn, dst_server_id)

    try:
        import paramiko
    except ImportError as exc:
        raise ToolboxError("MISSING_DEP", "缺少 paramiko 依赖", status_code=500, tool_id=TOOL_ID) from exc

    src_client = _ssh_connect(src_row)
    dst_client = _ssh_connect(dst_row)

    try:
        # 在源端执行 docker save | gzip
        src_transport = src_client.get_transport()
        dst_transport = dst_client.get_transport()

        src_chan = src_transport.open_session()
        dst_chan = dst_transport.open_session()

        src_chan.exec_command(f"docker save {shlex.quote(image_ref)} | gzip")
        dst_chan.exec_command("gunzip | docker load")

        # 流式传输数据
        total_bytes = 0
        while True:
            data = src_chan.recv(65536)
            if not data:
                break
            total_bytes += len(data)
            dst_chan.sendall(data)

        dst_chan.shutdown_write()
        dst_exit = dst_chan.recv_exit_status()
        src_exit = src_chan.recv_exit_status()

        if src_exit != 0:
            src_err = src_chan.recv_stderr(4096).decode("utf-8", errors="replace")
            raise ToolboxError("COPY_SRC_FAILED", f"源端保存镜像失败: {src_err}", status_code=502, tool_id=TOOL_ID)
        if dst_exit != 0:
            dst_err = dst_chan.recv_stderr(4096).decode("utf-8", errors="replace")
            raise ToolboxError("COPY_DST_FAILED", f"目标端加载镜像失败: {dst_err}", status_code=502, tool_id=TOOL_ID)

        return {
            "success": True,
            "imageRef": image_ref,
            "transferredBytes": total_bytes,
        }
    finally:
        src_client.close()
        dst_client.close()


# ==============================================================
# 容器管理
# ==============================================================

def list_containers(server_id: str, user: User, all_containers: bool = True) -> list[dict[str, Any]]:
    """列出服务器上的容器，按所有权/角色过滤"""
    init_docker_database()
    with get_connection() as conn:
        perms = _get_user_perms(conn, server_id, user)
        if user.role != "admin" and not perms.get("ctr_view_own") and not perms.get("ctr_view_all"):
            raise ToolboxError("PERMISSION_DENIED", "您没有查看容器的权限", status_code=403, tool_id=TOOL_ID)
        row = _get_server_row(conn, server_id)
        # 获取容器所有权元数据（旧表兼容）
        meta_rows = conn.execute(
            "SELECT container_ref, owner_user_id FROM docker_containers_meta WHERE server_id = ?", (server_id,)
        ).fetchall()
        meta_map: dict[str, str] = {r["container_ref"]: r["owner_user_id"] for r in meta_rows}
        # 新角色表：查询当前用户拥有任意角色的所有容器 ref
        user_accessible_refs: set[str] = set()
        for r in conn.execute(
            "SELECT resource_ref FROM docker_resource_roles WHERE server_id=? AND resource_type='container' AND user_id=?",
            (server_id, user.id),
        ).fetchall():
            user_accessible_refs.add(r["resource_ref"])
        view_all = user.role == "admin" or perms.get("ctr_view_all", False)

    client = _ssh_connect(row)
    try:
        flag = "-a " if all_containers else ""
        stdout, stderr, code = _ssh_exec(
            client,
            f'docker ps {flag}--format \'{{{{json .}}}}\' ',
        )
    finally:
        client.close()

    containers = []
    for line in stdout.strip().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            ctr = json.loads(line)
        except json.JSONDecodeError:
            continue

        # 查找所有者：先用 Names 匹配，再用 ID 前缀匹配
        ctr_name = ctr.get("Names", "").lstrip("/")
        ctr_id_short = ctr.get("ID", "")[:12]
        ref = ctr_name or ctr_id_short
        owner = meta_map.get(ctr_name) or meta_map.get(ctr_id_short)
        ctr["ownerUserId"] = owner  # 兼容旧字段
        ctr["platformManaged"] = owner is not None or ref in user_accessible_refs

        if view_all:
            containers.append(ctr)
        elif ref in user_accessible_refs or owner == user.id:
            containers.append(ctr)

    return containers


def _validate_path_whitelist(mount_paths: list[str], whitelist: list[str]) -> None:
    """校验挂载路径是否在白名单内（前缀匹配）"""
    if not whitelist:
        return  # 没有设置白名单时不做限制（仅在有挂载路径时才需要白名单）
    for path in mount_paths:
        host_path = path.split(":")[0] if ":" in path else path
        allowed = any(host_path.startswith(w.rstrip("/")) for w in whitelist)
        if not allowed:
            raise ToolboxError(
                "PATH_NOT_ALLOWED",
                f"路径 {host_path} 不在允许的挂载白名单中",
                status_code=403,
                tool_id=TOOL_ID,
            )


def create_container_run(server_id: str, params: dict[str, Any], user: User) -> dict[str, Any]:
    """
    通过 docker run 参数创建容器。
    params 结构：
    {
        "name": str,
        "image": str,
        "command": str,
        "ports": ["8080:80", ...],
        "volumes": ["/host:/container", ...],
        "envs": ["KEY=VALUE", ...],
        "network": str,
        "restart": str,
        "gpus": str,   // e.g. "all" or ""
        "extra_args": str   // 额外原始参数字符串
    }
    """
    init_docker_database()
    with get_connection() as conn:
        level = _get_user_permission(conn, server_id, user)
        if user.role != "admin" and _permission_level(level) < _permission_level("manage"):
            quota = _get_quota(conn, server_id, user.id)
            if not quota["canCreateContainer"]:
                raise ToolboxError(
                    "NO_CREATE_PERMISSION", "您没有在此服务器创建容器的权限", status_code=403, tool_id=TOOL_ID
                )
            # 校验挂载路径白名单
            volumes = params.get("volumes", [])
            if volumes:
                whitelist = quota["pathWhitelist"]
                if whitelist:
                    _validate_path_whitelist(volumes, whitelist)
        row = _get_server_row(conn, server_id)

    # 构建 docker run 命令
    cmd_parts = ["docker", "run", "-d"]

    if params.get("name"):
        cmd_parts += ["--name", shlex.quote(params["name"])]

    if params.get("restart"):
        cmd_parts += ["--restart", shlex.quote(params["restart"])]

    for port in params.get("ports", []):
        cmd_parts += ["-p", shlex.quote(port)]

    for vol in params.get("volumes", []):
        cmd_parts += ["-v", shlex.quote(vol)]

    for env in params.get("envs", []):
        cmd_parts += ["-e", shlex.quote(env)]

    if params.get("network"):
        cmd_parts += ["--network", shlex.quote(params["network"])]

    if params.get("gpus"):
        cmd_parts += ["--gpus", shlex.quote(params["gpus"])]

    if params.get("extra_args"):
        # 安全追加额外参数
        cmd_parts += shlex.split(params["extra_args"])

    cmd_parts.append(shlex.quote(params["image"]))

    if params.get("command"):
        cmd_parts += shlex.split(params["command"])

    full_cmd = " ".join(cmd_parts)

    client = _ssh_connect(row)
    try:
        stdout, stderr, code = _ssh_exec(client, full_cmd, timeout=60)
    finally:
        client.close()

    if code != 0:
        raise ToolboxError(
            "CREATE_CONTAINER_FAILED", f"创建容器失败: {stderr.strip()}", status_code=502, tool_id=TOOL_ID
        )
    return {"success": True, "containerId": stdout.strip(), "command": full_cmd}


def create_container_run_raw(server_id: str, command: str, user: User) -> dict[str, Any]:
    """
    直接在服务器上执行用户提供的完整 docker run 命令（命令行模式）。
    安全校验：
    - 命令必须以 `docker run` 开头（大小写不敏感、允许前缀空格）
    - 用户必须拥有创建容器的权限
    """
    init_docker_database()
    cmd = command.strip()
    # 安全性：只允许 docker run 命令
    if not cmd.lower().startswith("docker run"):
        raise ToolboxError("INVALID_COMMAND", "命令必须以 'docker run' 开头", status_code=400, tool_id=TOOL_ID)

    with get_connection() as conn:
        level = _get_user_permission(conn, server_id, user)
        if user.role != "admin" and _permission_level(level) < _permission_level("manage"):
            quota = _get_quota(conn, server_id, user.id)
            if not quota["canCreateContainer"]:
                raise ToolboxError(
                    "NO_CREATE_PERMISSION", "您没有在此服务器创建容器的权限", status_code=403, tool_id=TOOL_ID
                )
        row = _get_server_row(conn, server_id)

    client = _ssh_connect(row)
    try:
        stdout, stderr, code = _ssh_exec(client, cmd, timeout=120)
    finally:
        client.close()

    if code != 0:
        raise ToolboxError(
            "CREATE_CONTAINER_FAILED", f"创建容器失败: {stderr.strip()}", status_code=502, tool_id=TOOL_ID
        )
    return {"success": True, "containerId": stdout.strip(), "command": cmd}


def create_container_compose(server_id: str, yaml_content: str, user: User, project_name: str = "") -> dict[str, Any]:
    """
    通过 docker compose 创建容器：将 YAML 上传至服务器临时目录执行
    """
    init_docker_database()
    with get_connection() as conn:
        level = _get_user_permission(conn, server_id, user)
        if user.role != "admin" and _permission_level(level) < _permission_level("manage"):
            quota = _get_quota(conn, server_id, user.id)
            if not quota["canCreateContainer"]:
                raise ToolboxError(
                    "NO_CREATE_PERMISSION", "您没有在此服务器创建容器的权限", status_code=403, tool_id=TOOL_ID
                )
        row = _get_server_row(conn, server_id)

    tmp_dir = f"/tmp/.docker_manager_{uuid.uuid4().hex[:8]}"
    compose_file = f"{tmp_dir}/docker-compose.yml"

    try:
        import paramiko
    except ImportError as exc:
        raise ToolboxError("MISSING_DEP", "缺少 paramiko 依赖", status_code=500, tool_id=TOOL_ID) from exc

    client = _ssh_connect(row)
    try:
        sftp = client.open_sftp()
        try:
            client.exec_command(f"mkdir -p {shlex.quote(tmp_dir)}")[1].channel.recv_exit_status()
            with sftp.file(compose_file, "w") as f:
                f.write(yaml_content)
        finally:
            sftp.close()

        project_flag = f"-p {shlex.quote(project_name)}" if project_name else ""
        cmd = f"docker compose {project_flag} -f {shlex.quote(compose_file)} up -d"
        stdout, stderr, code = _ssh_exec(client, cmd, timeout=300)

        # 无论成功与否，清理临时目录
        client.exec_command(f"rm -rf {shlex.quote(tmp_dir)}")
    finally:
        client.close()

    if code != 0:
        raise ToolboxError(
            "COMPOSE_FAILED", f"docker compose 失败: {stderr.strip()}", status_code=502, tool_id=TOOL_ID
        )
    return {"success": True, "output": stdout + stderr, "projectName": project_name}


def container_action(server_id: str, container_id: str, action: str, user: User) -> dict[str, Any]:
    """
    容器生命周期操作：start/stop/restart/remove
    需要 ctr_manage_own（自己的容器）或 ctr_manage_all（任意容器）
    """
    init_docker_database()
    if action not in {"start", "stop", "restart", "remove"}:
        raise ToolboxError("INVALID_ACTION", "无效的容器操作", status_code=400, tool_id=TOOL_ID)

    with get_connection() as conn:
        if user.role != "admin":
            perms = _get_user_perms(conn, server_id, user)
            can_manage_all = perms.get("ctr_manage_all", False)
            can_manage_own = perms.get("ctr_manage_own", False)
            if not can_manage_all and not can_manage_own:
                raise ToolboxError("PERMISSION_DENIED", "您没有管理容器的权限", status_code=403, tool_id=TOOL_ID)
            # 如果只有 manage_own，检查所有权
            if not can_manage_all:
                meta = conn.execute(
                    "SELECT owner_user_id FROM docker_containers_meta WHERE server_id = ? AND container_ref = ?",
                    (server_id, container_id),
                ).fetchone()
                if meta and meta["owner_user_id"] != user.id:
                    raise ToolboxError("PERMISSION_DENIED", "您只能管理自己拥有的容器", status_code=403, tool_id=TOOL_ID)
        row = _get_server_row(conn, server_id)

    docker_cmd = "rm -f" if action == "remove" else action
    cmd = f"docker {docker_cmd} {shlex.quote(container_id)}"

    client = _ssh_connect(row)
    try:
        stdout, stderr, code = _ssh_exec(client, cmd, timeout=60)
    finally:
        client.close()

    if code != 0:
        raise ToolboxError(
            "CONTAINER_ACTION_FAILED",
            f"容器操作 {action} 失败: {stderr.strip()}",
            status_code=502,
            tool_id=TOOL_ID,
        )
    return {"success": True, "action": action, "containerId": container_id}


def update_restart_policy(server_id: str, container_id: str, policy: str, user: User) -> dict[str, Any]:
    """
    更新容器重启策略（docker update --restart）。
    支持：no / always / unless-stopped / on-failure / on-failure:N
    权限：ctr_manage_own（自己的容器）或 ctr_manage_all / admin（任意容器）。
    """
    allowed = {"no", "always", "unless-stopped", "on-failure"}
    base = policy.split(":")[0] if ":" in policy else policy
    if base not in allowed:
        raise ToolboxError("INVALID_POLICY", "无效的重启策略", status_code=400, tool_id=TOOL_ID)

    init_docker_database()
    with get_connection() as conn:
        if user.role != "admin":
            perms = _get_user_perms(conn, server_id, user)
            can_manage_all = perms.get("ctr_manage_all", False)
            can_manage_own = perms.get("ctr_manage_own", False)
            if not can_manage_all and not can_manage_own:
                raise ToolboxError("PERMISSION_DENIED", "您没有管理容器的权限", status_code=403, tool_id=TOOL_ID)
            if not can_manage_all:
                meta = conn.execute(
                    "SELECT owner_user_id FROM docker_containers_meta WHERE server_id = ? AND container_ref = ?",
                    (server_id, container_id),
                ).fetchone()
                if meta and meta["owner_user_id"] != user.id:
                    raise ToolboxError("PERMISSION_DENIED", "您只能管理自己拥有的容器", status_code=403, tool_id=TOOL_ID)
        row = _get_server_row(conn, server_id)

    cmd = f"docker update --restart {shlex.quote(policy)} {shlex.quote(container_id)}"
    client = _ssh_connect(row)
    try:
        stdout, stderr, code = _ssh_exec(client, cmd, timeout=30)
    finally:
        client.close()

    if code != 0:
        raise ToolboxError(
            "UPDATE_RESTART_FAILED",
            f"重启策略更新失败: {stderr.strip()}",
            status_code=502,
            tool_id=TOOL_ID,
        )
    return {"success": True, "restartPolicy": policy}


def get_container_detail(server_id: str, container_id: str, user: User) -> dict[str, Any]:
    """
    获取容器详情（docker inspect），返回基础信息、环境变量、端口映射、卷挂载、网络等。
    权限：ctr_view_own（仅自己的）或 ctr_view_all / admin（所有容器）。
    """
    init_docker_database()
    with get_connection() as conn:
        if user.role != "admin":
            perms = _get_user_perms(conn, server_id, user)
            view_all = perms.get("ctr_view_all", False)
            view_own = perms.get("ctr_view_own", False)
            if not view_all and not view_own:
                raise ToolboxError("PERMISSION_DENIED", "您没有查看容器详情的权限", status_code=403, tool_id=TOOL_ID)
            if not view_all:
                # 检查是否是自己的容器（新角色表或旧 meta 表）
                meta = conn.execute(
                    "SELECT owner_user_id FROM docker_containers_meta WHERE server_id=? AND container_ref=?",
                    (server_id, container_id),
                ).fetchone()
                accessible_roles = conn.execute(
                    "SELECT 1 FROM docker_resource_roles WHERE server_id=? AND resource_type='container' AND resource_ref=? AND user_id=?",
                    (server_id, container_id, user.id),
                ).fetchone()
                if not accessible_roles and (not meta or meta["owner_user_id"] != user.id):
                    raise ToolboxError("PERMISSION_DENIED", "您没有权限查看此容器的详情", status_code=403, tool_id=TOOL_ID)

        # 查询平台元数据
        meta_row = conn.execute(
            "SELECT owner_user_id, assigned_at FROM docker_containers_meta WHERE server_id=? AND container_ref=?",
            (server_id, container_id),
        ).fetchone()
        # 查询显示端口配置（从 display_ports 列，若存在）
        display_port_cols = {r[1] for r in conn.execute("PRAGMA table_info(docker_containers_meta)").fetchall()}
        display_ports_raw = None
        if "display_ports" in display_port_cols:
            dp_row = conn.execute(
                "SELECT display_ports FROM docker_containers_meta WHERE server_id=? AND container_ref=?",
                (server_id, container_id),
            ).fetchone()
            if dp_row:
                display_ports_raw = dp_row["display_ports"]

        row = _get_server_row(conn, server_id)

    client = _ssh_connect(row)
    try:
        stdout, stderr, code = _ssh_exec(
            client,
            f"docker inspect {shlex.quote(container_id)}",
            timeout=30,
        )
    finally:
        client.close()

    if code != 0 or not stdout.strip():
        raise ToolboxError("INSPECT_FAILED", f"获取容器详情失败: {stderr.strip()}", status_code=502, tool_id=TOOL_ID)

    try:
        inspect_list = json.loads(stdout.strip())
        if not inspect_list:
            raise ToolboxError("NOT_FOUND", "容器不存在", status_code=404, tool_id=TOOL_ID)
        d = inspect_list[0]
    except (json.JSONDecodeError, IndexError) as exc:
        raise ToolboxError("PARSE_ERROR", "解析 docker inspect 输出失败", status_code=502, tool_id=TOOL_ID) from exc

    cfg = d.get("Config", {})
    host_cfg = d.get("HostConfig", {})
    net_settings = d.get("NetworkSettings", {})
    state = d.get("State", {})
    mounts = d.get("Mounts", [])

    # 端口映射
    port_bindings = host_cfg.get("PortBindings") or {}
    ports_list: list[dict[str, Any]] = []
    for container_port, bindings in port_bindings.items():
        if bindings:
            for b in bindings:
                ports_list.append({
                    "containerPort": container_port,
                    "hostIp": b.get("HostIp", ""),
                    "hostPort": b.get("HostPort", ""),
                })
        else:
            ports_list.append({"containerPort": container_port, "hostIp": "", "hostPort": ""})

    # 卷挂载
    mounts_list: list[dict[str, Any]] = []
    for m in mounts:
        mounts_list.append({
            "type": m.get("Type", ""),
            "source": m.get("Source", ""),
            "destination": m.get("Destination", ""),
            "mode": m.get("Mode", ""),
            "rw": m.get("RW", True),
            "name": m.get("Name", ""),
        })

    # 网络
    networks: list[dict[str, Any]] = []
    for net_name, net_info in (net_settings.get("Networks") or {}).items():
        networks.append({
            "name": net_name,
            "ipAddress": net_info.get("IPAddress", ""),
            "gateway": net_info.get("Gateway", ""),
            "macAddress": net_info.get("MacAddress", ""),
        })

    # SSH 端口预留：从端口映射中查找宿主机侧映射到容器 22 端口的
    ssh_host_port: str | None = None
    for pb in ports_list:
        cp = pb.get("containerPort", "")
        if cp in ("22/tcp", "22"):
            hp = pb.get("hostPort", "")
            if hp:
                ssh_host_port = hp
                break

    return {
        "id": d.get("Id", ""),
        "shortId": d.get("Id", "")[:12],
        "name": d.get("Name", "").lstrip("/"),
        "image": cfg.get("Image", ""),
        "imageId": d.get("Image", ""),
        "status": state.get("Status", ""),
        "running": state.get("Running", False),
        "paused": state.get("Paused", False),
        "restarting": state.get("Restarting", False),
        "startedAt": state.get("StartedAt", ""),
        "finishedAt": state.get("FinishedAt", ""),
        "exitCode": state.get("ExitCode", 0),
        "created": d.get("Created", ""),
        "restartPolicy": host_cfg.get("RestartPolicy", {}).get("Name", ""),
        "platform": d.get("Platform", ""),
        "hostname": cfg.get("Hostname", ""),
        "cmd": cfg.get("Cmd") or [],
        "entrypoint": cfg.get("Entrypoint") or [],
        "workingDir": cfg.get("WorkingDir", ""),
        "user": cfg.get("User", ""),
        "envs": cfg.get("Env") or [],
        "ports": ports_list,
        "mounts": mounts_list,
        "networks": networks,
        "sshHostPort": ssh_host_port,
        "serverHost": row["host"],
        "serverSshUsername": row["ssh_username"],
        "platformMeta": {
            "ownerUserId": meta_row["owner_user_id"] if meta_row else None,
            "assignedAt": meta_row["assigned_at"] if meta_row else None,
            "displayPorts": json.loads(display_ports_raw) if display_ports_raw else None,
        },
    }


def get_container_logs(server_id: str, container_id: str, user: User, tail: int = 200) -> dict[str, Any]:
    """获取容器日志（需 ctr_view_own 或 ctr_view_all）"""
    init_docker_database()
    with get_connection() as conn:
        if user.role != "admin":
            perms = _get_user_perms(conn, server_id, user)
            view_all = perms.get("ctr_view_all", False)
            view_own = perms.get("ctr_view_own", False)
            if not view_all and not view_own:
                raise ToolboxError("PERMISSION_DENIED", "您没有查看容器日志的权限", status_code=403, tool_id=TOOL_ID)
            if not view_all:
                meta = conn.execute(
                    "SELECT owner_user_id FROM docker_containers_meta WHERE server_id = ? AND container_ref = ?",
                    (server_id, container_id),
                ).fetchone()
                if meta and meta["owner_user_id"] != user.id:
                    raise ToolboxError("PERMISSION_DENIED", "您只能查看自己容器的日志", status_code=403, tool_id=TOOL_ID)
        row = _get_server_row(conn, server_id)

    client = _ssh_connect(row)
    try:
        stdout, stderr, code = _ssh_exec(
            client,
            f"docker logs --tail {tail} {shlex.quote(container_id)} 2>&1",
            timeout=30,
        )
    finally:
        client.close()

    if code != 0:
        raise ToolboxError("LOGS_FAILED", f"获取日志失败: {stderr.strip()}", status_code=502, tool_id=TOOL_ID)
    return {"logs": stdout, "containerId": container_id}


# ==============================================================
# 模板管理
# ==============================================================

def create_template(payload: dict[str, Any], user: User) -> dict[str, Any]:
    """创建容器模板（管理员）"""
    init_docker_database()
    if user.role != "admin":
        raise ToolboxError("ADMIN_REQUIRED", "只有管理员可以创建模板", status_code=403, tool_id=TOOL_ID)

    template_id = _new_id()
    name = payload.get("name", "").strip()
    if not name:
        raise ToolboxError("INVALID_NAME", "模板名称不能为空", status_code=400, tool_id=TOOL_ID)

    description = payload.get("description", "")
    category = payload.get("category", "general")
    doc_content = payload.get("docContent", "")
    config = payload.get("config", {})
    is_public = bool(payload.get("isPublic", True))

    # 保存 MD 文档到文件
    doc_file = ""
    if doc_content:
        doc_file = f"{template_id}.md"
        doc_path = TEMPLATES_DIR / doc_file
        doc_path.write_text(doc_content, encoding="utf-8")

    now = _now()
    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO docker_templates (id, name, description, category, creator_id, doc_file, config_json, is_public, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                template_id, name, description, category, user.id,
                doc_file, json.dumps(config), 1 if is_public else 0, now, now,
            ),
        )

    return {
        "id": template_id,
        "name": name,
        "description": description,
        "category": category,
        "docFile": doc_file,
        "config": config,
        "isPublic": is_public,
        "createdAt": now,
        "updatedAt": now,
    }


def update_template(template_id: str, payload: dict[str, Any], user: User) -> dict[str, Any]:
    """更新容器模板（管理员）"""
    init_docker_database()
    if user.role != "admin":
        raise ToolboxError("ADMIN_REQUIRED", "只有管理员可以修改模板", status_code=403, tool_id=TOOL_ID)

    with get_connection() as conn:
        row = conn.execute("SELECT * FROM docker_templates WHERE id = ?", (template_id,)).fetchone()
        if not row:
            raise ToolboxError("TEMPLATE_NOT_FOUND", "模板不存在", status_code=404, tool_id=TOOL_ID)

        name = payload.get("name", row["name"]).strip() or row["name"]
        description = payload.get("description", row["description"])
        category = payload.get("category", row["category"])
        config = payload.get("config", json.loads(row["config_json"]))
        is_public = bool(payload.get("isPublic", bool(row["is_public"])))

        doc_file = row["doc_file"]
        if "docContent" in payload:
            if not doc_file:
                doc_file = f"{template_id}.md"
            doc_path = TEMPLATES_DIR / doc_file
            doc_path.write_text(payload["docContent"], encoding="utf-8")

        now = _now()
        conn.execute(
            """
            UPDATE docker_templates SET name=?, description=?, category=?, doc_file=?, config_json=?, is_public=?, updated_at=?
            WHERE id=?
            """,
            (name, description, category, doc_file, json.dumps(config), 1 if is_public else 0, now, template_id),
        )

    return {"id": template_id, "name": name, "description": description, "category": category,
            "docFile": doc_file, "config": config, "isPublic": is_public, "updatedAt": now}


def delete_template(template_id: str, user: User) -> None:
    """删除模板（管理员）"""
    init_docker_database()
    if user.role != "admin":
        raise ToolboxError("ADMIN_REQUIRED", "只有管理员可以删除模板", status_code=403, tool_id=TOOL_ID)

    with get_connection() as conn:
        row = conn.execute("SELECT * FROM docker_templates WHERE id = ?", (template_id,)).fetchone()
        if not row:
            raise ToolboxError("TEMPLATE_NOT_FOUND", "模板不存在", status_code=404, tool_id=TOOL_ID)

        # 删除 MD 文件
        if row["doc_file"]:
            doc_path = TEMPLATES_DIR / row["doc_file"]
            if doc_path.exists():
                doc_path.unlink()

        conn.execute("DELETE FROM docker_templates WHERE id = ?", (template_id,))


def list_templates(user: User) -> list[dict[str, Any]]:
    """列出模板"""
    init_docker_database()
    with get_connection() as conn:
        if user.role == "admin":
            rows = conn.execute("SELECT * FROM docker_templates ORDER BY category, name").fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM docker_templates WHERE is_public = 1 ORDER BY category, name"
            ).fetchall()

    return [
        {
            "id": row["id"],
            "name": row["name"],
            "description": row["description"],
            "category": row["category"],
            "creatorId": row["creator_id"],
            "hasDoc": bool(row["doc_file"]),
            "config": json.loads(row["config_json"]),
            "isPublic": bool(row["is_public"]),
            "createdAt": row["created_at"],
            "updatedAt": row["updated_at"],
        }
        for row in rows
    ]


def get_template(template_id: str, user: User) -> dict[str, Any]:
    """获取模板详情，包含 MD 文档内容"""
    init_docker_database()
    with get_connection() as conn:
        row = conn.execute("SELECT * FROM docker_templates WHERE id = ?", (template_id,)).fetchone()
        if not row:
            raise ToolboxError("TEMPLATE_NOT_FOUND", "模板不存在", status_code=404, tool_id=TOOL_ID)
        if not row["is_public"] and user.role != "admin":
            raise ToolboxError("TEMPLATE_NOT_FOUND", "模板不存在", status_code=404, tool_id=TOOL_ID)

    doc_content = ""
    if row["doc_file"]:
        doc_path = TEMPLATES_DIR / row["doc_file"]
        if doc_path.exists():
            doc_content = doc_path.read_text(encoding="utf-8")

    return {
        "id": row["id"],
        "name": row["name"],
        "description": row["description"],
        "category": row["category"],
        "creatorId": row["creator_id"],
        "docContent": doc_content,
        "config": json.loads(row["config_json"]),
        "isPublic": bool(row["is_public"]),
        "createdAt": row["created_at"],
        "updatedAt": row["updated_at"],
    }


def create_from_template(
    server_id: str,
    template_id: str,
    overrides: dict[str, Any],
    user: User,
    gpus: str = "",
) -> dict[str, Any]:
    """从模板创建容器。gpus 为 GPU 挂载参数，优先级高于模板自身配置（若非空）"""
    init_docker_database()
    template = get_template(template_id, user)
    config = template["config"].copy()

    # 合并用户覆盖参数
    for key, val in overrides.items():
        config[key] = val

    # 若调用方传入了 gpus 参数，则覆盖模板配置中的 gpus
    if gpus:
        config["gpus"] = gpus

    deploy_type = config.get("type", "run")
    if deploy_type == "compose":
        yaml_content = config.get("composeYaml", "")
        project_name = config.get("projectName", template["name"].lower().replace(" ", "_"))
        return create_container_compose(server_id, yaml_content, user, project_name)
    else:
        return create_container_run(server_id, config, user)


# ==============================================================
# 卷管理
# ==============================================================

def list_volumes(server_id: str, user: User) -> dict[str, Any]:
    """列出服务器上的卷，附加平台元数据"""
    init_docker_database()
    with get_connection() as conn:
        _require_permission(conn, server_id, user, "view")
        row = _get_server_row(conn, server_id)

        # 获取当前用户配额信息
        if user.role == "admin":
            quota = {"volumeTotalGb": None, "volumeUsedGb": None}
        else:
            quota_data = _get_quota(conn, server_id, user.id)
            used = conn.execute(
                "SELECT COALESCE(SUM(size_gb), 0) as used FROM docker_volumes_meta WHERE server_id = ? AND owner_user_id = ?",
                (server_id, user.id),
            ).fetchone()["used"]
            quota = {"volumeTotalGb": quota_data["volumeTotalGb"], "volumeUsedGb": used}

        # 查询平台记录的卷元数据
        meta_rows = conn.execute(
            "SELECT * FROM docker_volumes_meta WHERE server_id = ?", (server_id,)
        ).fetchall()
        meta_map = {r["volume_name"]: dict(r) for r in meta_rows}
        # 新角色表：查询当前用户拥有任意角色的卷 ref
        user_accessible_vols: set[str] = set()
        if user.role != "admin":
            for r in conn.execute(
                "SELECT resource_ref FROM docker_resource_roles WHERE server_id=? AND resource_type='volume' AND user_id=?",
                (server_id, user.id),
            ).fetchall():
                user_accessible_vols.add(r["resource_ref"])

    # 从服务器获取实际卷列表
    client = _ssh_connect(row)
    try:
        stdout, stderr, code = _ssh_exec(
            client,
            'docker volume ls --format \'{"name":"{{.Name}}","driver":"{{.Driver}}","mountpoint":"{{.Mountpoint}}"}\'',
        )
    finally:
        client.close()

    volumes = []
    for line in stdout.strip().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            vol = json.loads(line)
        except json.JSONDecodeError:
            continue

        vname = vol.get("name", "")
        if vname in meta_map:
            m = meta_map[vname]
            vol["ownerUserId"] = m["owner_user_id"]  # 兼容旧字段
            vol["sizeGb"] = m["size_gb"]
            vol["createdAt"] = m["created_at"]
            vol["platformManaged"] = True
        else:
            vol["ownerUserId"] = None
            vol["platformManaged"] = vname in user_accessible_vols

        # 访问过滤：管理员看全部；普通用户看自己有角色关联的
        if user.role != "admin":
            legacy_owner = vol.get("ownerUserId")
            if legacy_owner and legacy_owner != user.id and vname not in user_accessible_vols:
                continue
            if not legacy_owner and vname not in user_accessible_vols:
                continue

        volumes.append(vol)

    return {"volumes": volumes, "quota": quota}


def get_volume_detail(server_id: str, volume_name: str, user: User) -> dict[str, Any]:
    """
    获取卷详情：包含平台角色信息（creator/owner/viewer）以及挂载该卷的容器列表。
    容器列表按当前用户权限过滤：
      - 有查看权限（viewer/owner/creator/ctr_view_all）的容器：返回完整信息
      - 无查看权限的容器：仅返回数量
    """
    init_docker_database()
    with get_connection() as conn:
        _require_permission(conn, server_id, user, "view")
        row = _get_server_row(conn, server_id)

        # 卷平台元数据
        meta = conn.execute(
            "SELECT * FROM docker_volumes_meta WHERE server_id=? AND volume_name=?",
            (server_id, volume_name),
        ).fetchone()

        # 新角色表：卷的多角色
        roles = _get_resource_roles(conn, server_id, "volume", volume_name)

        # 当前用户细粒度权限（容器可见性判断用）
        perms = _get_user_perms(conn, server_id, user)
        view_all_ctrs = user.role == "admin" or perms.get("ctr_view_all", False)

        # 当前用户可访问的容器 ref 集合（新角色表）
        user_accessible_ctrs: set[str] = set()
        if not view_all_ctrs:
            for r in conn.execute(
                "SELECT resource_ref FROM docker_resource_roles WHERE server_id=? AND resource_type='container' AND user_id=?",
                (server_id, user.id),
            ).fetchall():
                user_accessible_ctrs.add(r["resource_ref"])
            # 旧表兼容：owner 也算可见
            for r in conn.execute(
                "SELECT container_ref FROM docker_containers_meta WHERE server_id=? AND owner_user_id=?",
                (server_id, user.id),
            ).fetchall():
                user_accessible_ctrs.add(r["container_ref"])

    # 通过 SSH 查询挂载该卷的所有容器（docker ps -a --filter volume=<name>）
    client = _ssh_connect(row)
    try:
        stdout, _, _ = _ssh_exec(
            client,
            f"docker ps -a --filter volume={shlex.quote(volume_name)} --format '{{{{json .}}}}'",
            timeout=30,
        )
    finally:
        client.close()

    visible_containers: list[dict[str, Any]] = []
    hidden_count = 0
    for line in stdout.strip().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            ctr = json.loads(line)
        except json.JSONDecodeError:
            continue
        ctr_name = ctr.get("Names", "").lstrip("/")
        ctr_id_short = ctr.get("ID", "")[:12]
        ref = ctr_name or ctr_id_short
        if view_all_ctrs or ref in user_accessible_ctrs:
            visible_containers.append({
                "id": ctr.get("ID", ""),
                "name": ctr_name or ctr_id_short,
                "image": ctr.get("Image", ""),
                "status": ctr.get("Status", ""),
                "state": ctr.get("State", ""),
            })
        else:
            hidden_count += 1

    # 组装角色用户名（便于前端直接展示）
    with get_connection() as conn:
        def _get_user_basic(uid: str | None) -> dict | None:
            if not uid:
                return None
            r = conn.execute("SELECT id, username, display_name FROM users WHERE id=?", (uid,)).fetchone()
            return {"userId": r["id"], "username": r["username"], "displayName": r["display_name"]} if r else {"userId": uid, "username": uid, "displayName": uid}

        creator_info = _get_user_basic(roles.get("creatorUserId"))
        owner_infos = [_get_user_basic(uid) for uid in roles.get("ownerUserIds", []) if uid]
        viewer_infos = [_get_user_basic(uid) for uid in roles.get("viewerUserIds", []) if uid]

    return {
        "serverId": server_id,
        "name": volume_name,
        "sizeGb": meta["size_gb"] if meta else None,
        "createdAt": meta["created_at"] if meta else None,
        "platformManaged": meta is not None or bool(roles.get("ownerUserIds") or roles.get("creatorUserId")),
        "roles": {
            "creatorUserId": roles.get("creatorUserId"),
            "creator": creator_info,
            "ownerUserIds": roles.get("ownerUserIds", []),
            "owners": [o for o in owner_infos if o],
            "viewerUserIds": roles.get("viewerUserIds", []),
            "viewers": [v for v in viewer_infos if v],
        },
        "mountedContainers": visible_containers,
        "hiddenContainerCount": hidden_count,
    }


def create_volume(server_id: str, name: str, size_gb: float, user: User) -> dict[str, Any]:
    """创建 Docker 卷（含配额校验）"""
    init_docker_database()
    with get_connection() as conn:
        level = _get_user_permission(conn, server_id, user)
        if user.role != "admin" and _permission_level(level) < _permission_level("manage"):
            quota = _get_quota(conn, server_id, user.id)
            used_row = conn.execute(
                "SELECT COALESCE(SUM(size_gb), 0) as used FROM docker_volumes_meta WHERE server_id = ? AND owner_user_id = ?",
                (server_id, user.id),
            ).fetchone()
            used = used_row["used"]
            if quota["volumeTotalGb"] > 0 and used + size_gb > quota["volumeTotalGb"]:
                raise ToolboxError(
                    "QUOTA_EXCEEDED",
                    f"卷空间配额不足，已用 {used:.2f} GB，配额 {quota['volumeTotalGb']:.2f} GB，请求 {size_gb:.2f} GB",
                    status_code=403,
                    tool_id=TOOL_ID,
                )
        row = _get_server_row(conn, server_id)

    client = _ssh_connect(row)
    try:
        stdout, stderr, code = _ssh_exec(client, f"docker volume create {shlex.quote(name)}")
    finally:
        client.close()

    if code != 0:
        raise ToolboxError("CREATE_VOLUME_FAILED", f"创建卷失败: {stderr.strip()}", status_code=502, tool_id=TOOL_ID)

    # 记录平台元数据
    now = _now()
    vol_id = _new_id()
    with get_connection() as conn:
        conn.execute(
            """
            INSERT OR IGNORE INTO docker_volumes_meta (id, volume_name, server_id, owner_user_id, size_gb, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (vol_id, name, server_id, user.id, size_gb, now),
        )

    return {"success": True, "volumeName": name, "serverId": server_id, "sizeGb": size_gb, "createdAt": now}


def delete_volume(server_id: str, volume_name: str, user: User) -> dict[str, Any]:
    """删除 Docker 卷（需 vol_delete_own 或 vol_delete_all 权限）"""
    init_docker_database()
    with get_connection() as conn:
        if user.role != "admin":
            perms = _get_user_perms(conn, server_id, user)
            can_delete_all = perms.get("vol_delete_all", False)
            can_delete_own = perms.get("vol_delete_own", False)
            if not can_delete_all and not can_delete_own:
                raise ToolboxError("PERMISSION_DENIED", "您没有删除卷的权限", status_code=403, tool_id=TOOL_ID)
            if not can_delete_all:
                meta = conn.execute(
                    "SELECT * FROM docker_volumes_meta WHERE server_id = ? AND volume_name = ?",
                    (server_id, volume_name),
                ).fetchone()
                if meta and meta["owner_user_id"] != user.id:
                    raise ToolboxError("PERMISSION_DENIED", "您只能删除自己创建的卷", status_code=403, tool_id=TOOL_ID)
        row = _get_server_row(conn, server_id)

    client = _ssh_connect(row)
    try:
        stdout, stderr, code = _ssh_exec(client, f"docker volume rm {shlex.quote(volume_name)}")
    finally:
        client.close()

    if code != 0:
        raise ToolboxError("DELETE_VOLUME_FAILED", f"删除卷失败: {stderr.strip()}", status_code=502, tool_id=TOOL_ID)

    # 清除平台元数据
    with get_connection() as conn:
        conn.execute(
            "DELETE FROM docker_volumes_meta WHERE server_id = ? AND volume_name = ?",
            (server_id, volume_name),
        )

    return {"success": True, "volumeName": volume_name}


def copy_volume(
    src_server_id: str,
    src_volume_name: str,
    dst_server_id: str,
    dst_volume_name: str,
    user: User,
) -> dict[str, Any]:
    """
    跨服务器（或同服务器）复制卷数据：
      源端: docker run --rm -v <src>:/src:ro alpine tar -czC /src .
      目标端: docker volume create <dst> && docker run --rm -i -v <dst>:/dst alpine sh -c 'tar -xzC /dst'
    整个流程在平台内存中流式中转，不在任何服务器落盘。
    """
    init_docker_database()
    with get_connection() as conn:
        # 源：需要 vol_copy 权限
        _require_perm(conn, src_server_id, user, "vol_copy")
        # 目标：需要 vol_create 权限
        _require_perm(conn, dst_server_id, user, "vol_create")

        src_row = _get_server_row(conn, src_server_id)
        dst_row = _get_server_row(conn, dst_server_id)

        # 目标卷配额检查
        if user.role != "admin":
            dst_perms = _get_user_perms(conn, dst_server_id, user)
            used_row = conn.execute(
                "SELECT COALESCE(SUM(size_gb), 0) as used FROM docker_volumes_meta WHERE server_id = ? AND owner_user_id = ?",
                (dst_server_id, user.id),
            ).fetchone()
            used = used_row["used"]
            quota_gb = dst_perms.get("vol_quota_gb", 0.0)
            if quota_gb > 0 and used >= quota_gb:
                raise ToolboxError(
                    "QUOTA_EXCEEDED",
                    f"目标服务器卷配额不足，已用 {used:.2f} GB，配额 {quota_gb:.2f} GB",
                    status_code=403,
                    tool_id=TOOL_ID,
                )

        # 获取源卷元数据（用于继承 size_gb）
        src_meta = conn.execute(
            "SELECT * FROM docker_volumes_meta WHERE server_id = ? AND volume_name = ?",
            (src_server_id, src_volume_name),
        ).fetchone()

    try:
        import paramiko
    except ImportError as exc:
        raise ToolboxError("MISSING_DEP", "缺少 paramiko 依赖", status_code=500, tool_id=TOOL_ID) from exc

    same_server = src_server_id == dst_server_id
    src_client = _ssh_connect(src_row)
    dst_client = src_client if same_server else _ssh_connect(dst_row)

    try:
        # 先在目标服务器创建目标卷
        _, stderr, code = _ssh_exec(
            dst_client,
            f"docker volume create {shlex.quote(dst_volume_name)}",
            timeout=30,
        )
        if code != 0:
            raise ToolboxError(
                "CREATE_DST_VOL_FAILED",
                f"创建目标卷失败: {stderr.strip()}",
                status_code=502,
                tool_id=TOOL_ID,
            )

        src_transport = src_client.get_transport()
        dst_transport = dst_client.get_transport()

        src_chan = src_transport.open_session()
        dst_chan = dst_transport.open_session()

        # 源端：把卷内容打包成 tar.gz 输出到 stdout（只读挂载）
        src_cmd = (
            f"docker run --rm -v {shlex.quote(src_volume_name)}:/src:ro alpine "
            f"tar -czC /src ."
        )
        # 目标端：从 stdin 解压到目标卷
        dst_cmd = (
            f"docker run --rm -i -v {shlex.quote(dst_volume_name)}:/dst alpine "
            f"sh -c 'tar -xzC /dst'"
        )

        src_chan.exec_command(src_cmd)
        dst_chan.exec_command(dst_cmd)

        # 流式中转，不落盘
        total_bytes = 0
        while True:
            data = src_chan.recv(65536)
            if not data:
                break
            total_bytes += len(data)
            dst_chan.sendall(data)

        dst_chan.shutdown_write()
        dst_exit = dst_chan.recv_exit_status()
        src_exit = src_chan.recv_exit_status()

        if src_exit != 0:
            src_err = src_chan.recv_stderr(4096).decode("utf-8", errors="replace")
            raise ToolboxError(
                "COPY_SRC_FAILED",
                f"源卷打包失败（exit {src_exit}）: {src_err}",
                status_code=502,
                tool_id=TOOL_ID,
            )
        if dst_exit != 0:
            dst_err = dst_chan.recv_stderr(4096).decode("utf-8", errors="replace")
            raise ToolboxError(
                "COPY_DST_FAILED",
                f"目标卷解压失败（exit {dst_exit}）: {dst_err}",
                status_code=502,
                tool_id=TOOL_ID,
            )

    finally:
        src_client.close()
        if not same_server:
            dst_client.close()

    # 记录目标卷平台元数据
    now = _now()
    vol_id = _new_id()
    size_gb = float(src_meta["size_gb"]) if src_meta else 0.0
    with get_connection() as conn:
        conn.execute(
            """
            INSERT OR IGNORE INTO docker_volumes_meta (id, volume_name, server_id, owner_user_id, size_gb, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (vol_id, dst_volume_name, dst_server_id, user.id, size_gb, now),
        )

    return {
        "success": True,
        "srcServerId": src_server_id,
        "srcVolumeName": src_volume_name,
        "dstServerId": dst_server_id,
        "dstVolumeName": dst_volume_name,
        "transferredBytes": total_bytes,
        "createdAt": now,
    }


# ==============================================================
# 资源多角色管理（管理员专用）
# ==============================================================

def _get_resource_roles(conn: sqlite3.Connection, server_id: str, resource_type: str, resource_ref: str) -> dict[str, Any]:
    """
    从 docker_resource_roles 表中查询一个资源的所有角色信息。
    返回：{ "ownerUserIds": [...], "viewerUserIds": [...], "creatorUserId": str|None, "quotaHolderUserIds": [...] }
    """
    rows = conn.execute(
        "SELECT user_id, role FROM docker_resource_roles WHERE server_id=? AND resource_type=? AND resource_ref=?",
        (server_id, resource_type, resource_ref),
    ).fetchall()
    owner_ids: list[str] = []
    viewer_ids: list[str] = []
    quota_holder_ids: list[str] = []
    creator_id: str | None = None
    for r in rows:
        if r["role"] == "owner":
            owner_ids.append(r["user_id"])
        elif r["role"] == "viewer":
            viewer_ids.append(r["user_id"])
        elif r["role"] == "creator":
            creator_id = r["user_id"]
        elif r["role"] == "quota_holder":
            quota_holder_ids.append(r["user_id"])
    return {
        "ownerUserIds": owner_ids,
        "viewerUserIds": viewer_ids,
        "creatorUserId": creator_id,
        "quotaHolderUserIds": quota_holder_ids,
    }


def _user_has_resource_access(conn: sqlite3.Connection, server_id: str, resource_type: str, resource_ref: str, user_id: str) -> bool:
    """
    判断用户是否能访问某资源：是 creator/owner/viewer 之一即可。
    注意：creator 默认具备 owner 权限（除非被剥夺 owner 角色）。
    """
    row = conn.execute(
        """SELECT COUNT(*) as cnt FROM docker_resource_roles
           WHERE server_id=? AND resource_type=? AND resource_ref=? AND user_id=?""",
        (server_id, resource_type, resource_ref, user_id),
    ).fetchone()
    return bool(row["cnt"] > 0)


def list_server_resources(server_id: str, user: User) -> dict[str, Any]:
    """
    列出服务器上的全部容器、镜像、卷，并附上平台侧多角色信息（仅管理员）。
    通过 SSH 实时拉取列表，再与本地元数据合并。
    """
    init_docker_database()
    if user.role != "admin":
        raise ToolboxError("ADMIN_REQUIRED", "仅管理员可以查看资源所有者信息", status_code=403, tool_id=TOOL_ID)

    with get_connection() as conn:
        row = _get_server_row(conn, server_id)

        # 拉取新的多角色表数据
        role_rows = conn.execute(
            "SELECT resource_type, resource_ref, user_id, role FROM docker_resource_roles WHERE server_id=?",
            (server_id,),
        ).fetchall()
        # 构建 roles_map: { (resource_type, resource_ref) -> {owner:[...], viewer:[...], creator:str, quota_holder:[...]} }
        roles_map: dict[tuple[str,str], dict] = {}
        for r in role_rows:
            key = (r["resource_type"], r["resource_ref"])
            if key not in roles_map:
                roles_map[key] = {"ownerUserIds": [], "viewerUserIds": [], "creatorUserId": None, "quotaHolderUserIds": []}
            if r["role"] == "owner":
                roles_map[key]["ownerUserIds"].append(r["user_id"])
            elif r["role"] == "viewer":
                roles_map[key]["viewerUserIds"].append(r["user_id"])
            elif r["role"] == "creator":
                roles_map[key]["creatorUserId"] = r["user_id"]
            elif r["role"] == "quota_holder":
                roles_map[key]["quotaHolderUserIds"].append(r["user_id"])

        # 读取各资源类型的配额模式
        quota_mode_rows = conn.execute(
            "SELECT resource_type, quota_mode FROM docker_server_quota_mode WHERE server_id=?",
            (server_id,),
        ).fetchall()
        quota_mode_map: dict[str, str] = {r["resource_type"]: r["quota_mode"] for r in quota_mode_rows}

        # 兼容旧数据：从旧 _meta 表读取 owner（迁移显示）
        img_meta = {r["image_ref"]: r["owner_user_id"] for r in
                    conn.execute("SELECT image_ref, owner_user_id FROM docker_images_meta WHERE server_id=?", (server_id,)).fetchall()}
        ctr_meta = {r["container_ref"]: r["owner_user_id"] for r in
                    conn.execute("SELECT container_ref, owner_user_id FROM docker_containers_meta WHERE server_id=?", (server_id,)).fetchall()}
        vol_meta = {r["volume_name"]: r["owner_user_id"] for r in
                    conn.execute("SELECT volume_name, owner_user_id FROM docker_volumes_meta WHERE server_id=?", (server_id,)).fetchall()}

    def _merge_roles(rtype: str, ref: str, legacy_owner: str | None) -> dict:
        """合并新角色表和旧 owner 字段"""
        base = roles_map.get((rtype, ref), {"ownerUserIds": [], "viewerUserIds": [], "creatorUserId": None, "quotaHolderUserIds": []})
        # 兼容：旧数据只有 owner_user_id，将其归入 ownerUserIds（如果新表没有对应记录）
        if legacy_owner and legacy_owner not in base["ownerUserIds"]:
            base["ownerUserIds"] = [legacy_owner] + base["ownerUserIds"]
        platform_managed = bool(base["ownerUserIds"] or base["viewerUserIds"] or base["creatorUserId"] or legacy_owner)
        quota_mode = quota_mode_map.get(rtype, "shared")
        return {**base, "platformManaged": platform_managed, "quotaMode": quota_mode}

    client = _ssh_connect(row)
    try:
        ctr_out, _, _ = _ssh_exec(client, "docker ps -a --format '{{json .}}'")
        img_out, _, _ = _ssh_exec(
            client,
            'docker images --format \'{"id":"{{.ID}}","repo":"{{.Repository}}","tag":"{{.Tag}}","size":"{{.Size}}","created":"{{.CreatedAt}}"}\'',
        )
        vol_out, _, _ = _ssh_exec(client, "docker volume ls --format '{{.Name}}'")
    finally:
        client.close()

    def _parse_jsonlines(text: str) -> list[dict]:
        result = []
        for line in text.strip().splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                result.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return result

    containers = []
    for ctr in _parse_jsonlines(ctr_out):
        name = ctr.get("Names", "").lstrip("/")
        id_short = ctr.get("ID", "")[:12]
        ref = name or id_short
        legacy_owner = ctr_meta.get(name) or ctr_meta.get(id_short)
        roles = _merge_roles("container", ref, legacy_owner)
        containers.append({**ctr, **roles})

    images = []
    for img in _parse_jsonlines(img_out):
        ref_full = f"{img.get('repo', '')}:{img.get('tag', '')}"
        legacy_owner = img_meta.get(ref_full) or img_meta.get(img.get("id", ""))
        roles = _merge_roles("image", ref_full, legacy_owner)
        images.append({**img, **roles})

    volumes = []
    for line in vol_out.strip().splitlines():
        name = line.strip()
        if not name:
            continue
        legacy_owner = vol_meta.get(name)
        roles = _merge_roles("volume", name, legacy_owner)
        volumes.append({"name": name, **roles})

    return {"serverId": server_id, "containers": containers, "images": images, "volumes": volumes}


def assign_resource_roles(
    server_id: str,
    resource_type: str,
    resource_ref: str,
    owner_user_ids: list[str],
    viewer_user_ids: list[str],
    creator_user_id: str,
    user: User,
    quota_holder_user_ids: list[str] | None = None,
    quota_mode: str | None = None,
) -> dict[str, Any]:
    """
    设置服务器资源的多角色绑定（管理员专用）。
    - owner_user_ids: 所有者列表（可多人）
    - viewer_user_ids: 查看者列表（可多人）
    - creator_user_id: 创建者（唯一，传 "" 表示不设置）
    - quota_holder_user_ids: 配额占用者（必须是 owner 的子集，exclusive 模式下独占配额）
    - quota_mode: 配额模式，'shared'（所有者均分）或 'exclusive'（配额占用者独占）

    逻辑：
    - 创建者默认拥有所有者权限（即同时出现在 owner 角色中）
    - 如果管理员从 owner_user_ids 中移除创建者，创建者失去所有者权限（但 creator 角色保留）
    - viewer 不需要额外 owner 权限
    - quota_holder 必须在 owner_user_ids 中
    """
    init_docker_database()
    if user.role != "admin":
        raise ToolboxError("ADMIN_REQUIRED", "仅管理员可以分配资源角色", status_code=403, tool_id=TOOL_ID)
    if resource_type not in {"container", "image", "volume"}:
        raise ToolboxError("INVALID_TYPE", "资源类型无效，应为 container/image/volume", status_code=400, tool_id=TOOL_ID)

    if quota_holder_user_ids is None:
        quota_holder_user_ids = []
    # 校验：quota_holder 必须是 owner 的子集
    owner_set = set(owner_user_ids)
    for qh in quota_holder_user_ids:
        if qh and qh not in owner_set:
            raise ToolboxError(
                "INVALID_QUOTA_HOLDER",
                f"配额占用者 {qh} 必须同时是所有者",
                status_code=400,
                tool_id=TOOL_ID,
            )
    if quota_mode and quota_mode not in {"shared", "exclusive"}:
        raise ToolboxError("INVALID_QUOTA_MODE", "配额模式必须为 shared 或 exclusive", status_code=400, tool_id=TOOL_ID)

    now = _now()

    with get_connection() as conn:
        _get_server_row(conn, server_id)

        # 校验所有用户 ID 的存在性
        all_user_ids = set(owner_user_ids) | set(viewer_user_ids) | set(quota_holder_user_ids)
        if creator_user_id:
            all_user_ids.add(creator_user_id)
        for uid in all_user_ids:
            if uid:
                target = conn.execute("SELECT id FROM users WHERE id = ?", (uid,)).fetchone()
                if not target:
                    raise ToolboxError("USER_NOT_FOUND", f"用户 {uid} 不存在", status_code=404, tool_id=TOOL_ID)

        # 保留已有的 creator 记录（创建者由系统自动写入，管理员无法通过此接口修改）
        existing_creator = conn.execute(
            "SELECT user_id FROM docker_resource_roles WHERE server_id=? AND resource_type=? AND resource_ref=? AND role='creator'",
            (server_id, resource_type, resource_ref),
        ).fetchone()

        # 清除 owner / viewer / quota_holder 记录（保留 creator）
        conn.execute(
            "DELETE FROM docker_resource_roles WHERE server_id=? AND resource_type=? AND resource_ref=? AND role IN ('owner','viewer','quota_holder')",
            (server_id, resource_type, resource_ref),
        )

        # 写入新的 owner / viewer / quota_holder 记录
        for uid in owner_user_ids:
            if uid:
                conn.execute(
                    "INSERT OR IGNORE INTO docker_resource_roles (id, server_id, resource_type, resource_ref, user_id, role, assigned_at) VALUES (?,?,?,?,?,?,?)",
                    (_new_id(), server_id, resource_type, resource_ref, uid, "owner", now),
                )
        for uid in viewer_user_ids:
            if uid:
                conn.execute(
                    "INSERT OR IGNORE INTO docker_resource_roles (id, server_id, resource_type, resource_ref, user_id, role, assigned_at) VALUES (?,?,?,?,?,?,?)",
                    (_new_id(), server_id, resource_type, resource_ref, uid, "viewer", now),
                )
        for uid in quota_holder_user_ids:
            if uid:
                conn.execute(
                    "INSERT OR IGNORE INTO docker_resource_roles (id, server_id, resource_type, resource_ref, user_id, role, assigned_at) VALUES (?,?,?,?,?,?,?)",
                    (_new_id(), server_id, resource_type, resource_ref, uid, "quota_holder", now),
                )
        # 只有当显式传入 creatorUserId 时才写入（通常仅由创建资源的接口设置）
        if creator_user_id:
            conn.execute(
                "INSERT OR IGNORE INTO docker_resource_roles (id, server_id, resource_type, resource_ref, user_id, role, assigned_at) VALUES (?,?,?,?,?,?,?)",
                (_new_id(), server_id, resource_type, resource_ref, creator_user_id, "creator", now),
            )
        # 合并：使用已有 creator（如果本次未传）
        effective_creator = creator_user_id or (existing_creator["user_id"] if existing_creator else "")

        # 更新配额模式（若传入）
        if quota_mode:
            conn.execute(
                """INSERT INTO docker_server_quota_mode (server_id, resource_type, quota_mode, updated_at)
                   VALUES (?, ?, ?, ?)
                   ON CONFLICT(server_id, resource_type) DO UPDATE SET
                       quota_mode = excluded.quota_mode,
                       updated_at = excluded.updated_at""",
                (server_id, resource_type, quota_mode, now),
            )

        # 同步旧 _meta 表（使用第一个 owner 兼容旧查询逻辑）
        first_owner = owner_user_ids[0] if owner_user_ids else ""
        _sync_legacy_meta(conn, server_id, resource_type, resource_ref, first_owner, now)

        # 读取当前配额模式
        qm_row = conn.execute(
            "SELECT quota_mode FROM docker_server_quota_mode WHERE server_id=? AND resource_type=?",
            (server_id, resource_type),
        ).fetchone()
        effective_quota_mode = qm_row["quota_mode"] if qm_row else "shared"

    return {
        "success": True,
        "serverId": server_id,
        "resourceType": resource_type,
        "resourceRef": resource_ref,
        "ownerUserIds": owner_user_ids,
        "viewerUserIds": viewer_user_ids,
        "quotaHolderUserIds": quota_holder_user_ids,
        "quotaMode": effective_quota_mode,
        "creatorUserId": effective_creator or None,
        "assignedAt": now,
    }


def _sync_legacy_meta(conn: sqlite3.Connection, server_id: str, resource_type: str, resource_ref: str, owner_user_id: str, now: str) -> None:
    """同步到旧版 _meta 表，以兼容旧版权限过滤逻辑。"""
    if resource_type == "image":
        if owner_user_id:
            conn.execute(
                """
                INSERT INTO docker_images_meta (id, image_ref, server_id, owner_user_id, assigned_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(image_ref, server_id) DO UPDATE SET
                    owner_user_id = excluded.owner_user_id,
                    assigned_at = excluded.assigned_at
                """,
                (_new_id(), resource_ref, server_id, owner_user_id, now),
            )
        else:
            conn.execute(
                "DELETE FROM docker_images_meta WHERE server_id=? AND image_ref=?",
                (server_id, resource_ref),
            )
    elif resource_type == "container":
        if owner_user_id:
            conn.execute(
                """
                INSERT INTO docker_containers_meta (id, container_ref, server_id, owner_user_id, assigned_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(container_ref, server_id) DO UPDATE SET
                    owner_user_id = excluded.owner_user_id,
                    assigned_at = excluded.assigned_at
                """,
                (_new_id(), resource_ref, server_id, owner_user_id, now),
            )
        else:
            conn.execute(
                "DELETE FROM docker_containers_meta WHERE server_id=? AND container_ref=?",
                (server_id, resource_ref),
            )
    elif resource_type == "volume":
        if owner_user_id:
            conn.execute(
                """
                INSERT INTO docker_volumes_meta (id, volume_name, server_id, owner_user_id, size_gb, created_at)
                VALUES (?, ?, ?, ?, 0, ?)
                ON CONFLICT(volume_name, server_id) DO UPDATE SET
                    owner_user_id = excluded.owner_user_id
                """,
                (_new_id(), resource_ref, server_id, owner_user_id, now),
            )
        else:
            conn.execute(
                "DELETE FROM docker_volumes_meta WHERE server_id=? AND volume_name=?",
                (server_id, resource_ref),
            )


# 兼容旧调用（保留接口，内部转为新逻辑）
def assign_resource_owner(
    server_id: str,
    resource_type: str,
    resource_ref: str,
    owner_user_id: str,
    user: User,
) -> dict[str, Any]:
    """兼容旧接口：单 owner 分配，内部转为多角色逻辑。"""
    owner_ids = [owner_user_id] if owner_user_id else []
    return assign_resource_roles(server_id, resource_type, resource_ref, owner_ids, [], "", user)


def list_my_owned_resources(user: User) -> list[dict[str, Any]]:
    """
    返回当前用户作为 owner 的所有资源（跨所有服务器），并附带每个资源的 viewer 列表。
    供非管理员用户在「我的资源」页面查看和管理查看者权限。
    """
    init_docker_database()
    with get_connection() as conn:
        # 查询当前用户作为 owner 的所有资源
        owned_rows = conn.execute(
            """SELECT server_id, resource_type, resource_ref
               FROM docker_resource_roles
               WHERE user_id=? AND role='owner'
               ORDER BY server_id, resource_type, resource_ref""",
            (user.id,),
        ).fetchall()

        if not owned_rows:
            return []

        # 收集涉及的 server_id 列表，批量查询服务器名称
        server_ids = list({r["server_id"] for r in owned_rows})
        server_map: dict[str, str] = {}
        for sid in server_ids:
            row = conn.execute("SELECT id, name FROM docker_servers WHERE id=?", (sid,)).fetchone()
            if row:
                server_map[row["id"]] = row["name"]

        # 对每个资源查询其 viewer 和 creator 列表
        results = []
        for r in owned_rows:
            roles = _get_resource_roles(conn, r["server_id"], r["resource_type"], r["resource_ref"])
            # 把 viewerUserIds 转换为含用户名的列表
            viewer_details = []
            for vid in roles.get("viewerUserIds", []):
                u = conn.execute("SELECT id, username, display_name FROM users WHERE id=?", (vid,)).fetchone()
                if u:
                    viewer_details.append({
                        "userId": u["id"],
                        "username": u["username"],
                        "displayName": u["display_name"],
                    })
            results.append({
                "serverId": r["server_id"],
                "serverName": server_map.get(r["server_id"], r["server_id"]),
                "resourceType": r["resource_type"],
                "resourceRef": r["resource_ref"],
                "creatorUserId": roles.get("creatorUserId"),
                "viewerUserIds": roles.get("viewerUserIds", []),
                "viewers": viewer_details,
            })
    return results


def set_resource_viewers(
    server_id: str,
    resource_type: str,
    resource_ref: str,
    viewer_user_ids: list[str],
    user: User,
) -> dict[str, Any]:
    """
    允许资源所有者（owner）修改资源的查看者（viewer）列表。
    - 只能设置 viewer，不能修改 owner 或 creator
    - 调用者必须是该资源的 owner（管理员也可以）
    """
    init_docker_database()
    if resource_type not in {"container", "image", "volume"}:
        raise ToolboxError("INVALID_TYPE", "资源类型无效，应为 container/image/volume", status_code=400, tool_id=TOOL_ID)

    now = _now()

    with get_connection() as conn:
        _get_server_row(conn, server_id)

        # 校验调用者是否为该资源的 owner（或管理员）
        if user.role != "admin":
            is_owner = conn.execute(
                "SELECT 1 FROM docker_resource_roles WHERE server_id=? AND resource_type=? AND resource_ref=? AND user_id=? AND role='owner'",
                (server_id, resource_type, resource_ref, user.id),
            ).fetchone()
            if not is_owner:
                raise ToolboxError(
                    "PERMISSION_DENIED",
                    "您不是该资源的所有者，无法修改查看者列表",
                    status_code=403,
                    tool_id=TOOL_ID,
                )

        # 校验 viewer 用户存在性
        for uid in viewer_user_ids:
            if uid:
                target = conn.execute("SELECT id FROM users WHERE id=?", (uid,)).fetchone()
                if not target:
                    raise ToolboxError("USER_NOT_FOUND", f"用户 {uid} 不存在", status_code=404, tool_id=TOOL_ID)

        # 不能把 owner 自己加入 viewer（没有意义）
        owner_ids = {r["user_id"] for r in conn.execute(
            "SELECT user_id FROM docker_resource_roles WHERE server_id=? AND resource_type=? AND resource_ref=? AND role='owner'",
            (server_id, resource_type, resource_ref),
        ).fetchall()}
        viewer_user_ids = [uid for uid in viewer_user_ids if uid not in owner_ids]

        # 只清除 viewer 记录，保留 owner / creator
        conn.execute(
            "DELETE FROM docker_resource_roles WHERE server_id=? AND resource_type=? AND resource_ref=? AND role='viewer'",
            (server_id, resource_type, resource_ref),
        )
        for uid in viewer_user_ids:
            if uid:
                conn.execute(
                    "INSERT OR IGNORE INTO docker_resource_roles (id, server_id, resource_type, resource_ref, user_id, role, assigned_at) VALUES (?,?,?,?,?,?,?)",
                    (_new_id(), server_id, resource_type, resource_ref, uid, "viewer", now),
                )

    return {
        "success": True,
        "serverId": server_id,
        "resourceType": resource_type,
        "resourceRef": resource_ref,
        "viewerUserIds": viewer_user_ids,
        "updatedAt": now,
    }


# ==============================================================
# CUDA 重新扫描
# ==============================================================

def rescan_server_cuda(server_id: str, user: User) -> dict[str, Any]:
    """重新扫描服务器的 CUDA/GPU 情况并更新数据库（管理员专用）"""
    init_docker_database()
    if user.role != "admin":
        raise ToolboxError("ADMIN_REQUIRED", "只有管理员可以重新扫描服务器 CUDA", status_code=403, tool_id=TOOL_ID)
    with get_connection() as conn:
        row = _get_server_row(conn, server_id)
    client = _ssh_connect(row)
    cuda_available = False
    gpu_count = 0
    gpu_info: list[dict[str, Any]] = []
    try:
        cuda_out, _, cuda_rc = _ssh_exec(
            client,
            "nvidia-smi --query-gpu=index,name,memory.total --format=csv,noheader 2>/dev/null",
            timeout=20,
        )
        if cuda_rc == 0 and cuda_out.strip():
            cuda_available = True
            for line in cuda_out.strip().splitlines():
                parts = [p.strip() for p in line.split(",")]
                if len(parts) >= 3:
                    gpu_info.append({
                        "index": int(parts[0]) if parts[0].isdigit() else len(gpu_info),
                        "name": parts[1],
                        "memoryTotal": parts[2],
                    })
            gpu_count = len(gpu_info)
    finally:
        client.close()
    with get_connection() as conn:
        conn.execute(
            "UPDATE docker_servers SET cuda_available=?, gpu_count=?, gpu_info=?, updated_at=? WHERE id=?",
            (1 if cuda_available else 0, gpu_count, json.dumps(gpu_info), _now(), server_id),
        )
        row = _get_server_row(conn, server_id)
    return _public_server(row)


# ==============================================================
# 服务器资源概览（用户侧：卷配额、路径磁盘、CUDA 权限）
# ==============================================================

def get_server_resource_overview(server_id: str, user: User) -> dict[str, Any]:
    """
    获取当前用户在指定服务器上的资源使用概览：
    - 卷配额（总量 / 已用 / 剩余；含配额占用者独占部分 vs 共享部分）
    - 挂载路径的磁盘空间（总量 / 剩余 / 自己已用 / 他人已用）
    - 用户可用的 CUDA GPU 列表
    - 各资源类型配额模式
    """
    init_docker_database()
    with get_connection() as conn:
        _require_permission(conn, server_id, user, "view")
        srv_row = _get_server_row(conn, server_id)
        perms = _get_user_perms(conn, server_id, user)

        # ---------- 卷配额 ----------
        vol_quota_gb = float(perms.get("vol_quota_gb", 0))  # 0 = 不限
        vol_used_self = conn.execute(
            "SELECT COALESCE(SUM(size_gb), 0) as s FROM docker_volumes_meta WHERE server_id=? AND owner_user_id=?",
            (server_id, user.id),
        ).fetchone()["s"]
        vol_used_total = conn.execute(
            "SELECT COALESCE(SUM(size_gb), 0) as s FROM docker_volumes_meta WHERE server_id=?",
            (server_id,),
        ).fetchone()["s"]
        vol_remaining = max(0.0, vol_quota_gb - vol_used_self) if vol_quota_gb > 0 else None

        # ---------- 配额占用者统计 ----------
        # 找出当前用户是哪些资源的 quota_holder
        quota_holder_refs = conn.execute(
            """SELECT resource_ref FROM docker_resource_roles
               WHERE server_id=? AND resource_type='volume' AND user_id=? AND role='quota_holder'""",
            (server_id, user.id),
        ).fetchall()
        quota_holder_ref_set = {r["resource_ref"] for r in quota_holder_refs}

        # 计算以配额占用者身份的卷使用量（仅该用户持有 quota_holder 的卷）
        vol_quota_holder_used_gb: float = 0.0
        if quota_holder_ref_set:
            # 通过 owner 关联统计 quota_holder 持有卷的大小
            placeholders = ",".join("?" * len(quota_holder_ref_set))
            qh_row = conn.execute(
                f"SELECT COALESCE(SUM(size_gb), 0) as s FROM docker_volumes_meta WHERE server_id=? AND volume_name IN ({placeholders})",
                [server_id, *quota_holder_ref_set],
            ).fetchone()
            vol_quota_holder_used_gb = float(qh_row["s"])

        # 所有服务器上被标记为 quota_holder 的卷（被别人独占）
        all_quota_holder_refs = conn.execute(
            """SELECT resource_ref FROM docker_resource_roles
               WHERE server_id=? AND resource_type='volume' AND role='quota_holder'""",
            (server_id,),
        ).fetchall()
        all_quota_holder_ref_set = {r["resource_ref"] for r in all_quota_holder_refs}

        # 被 quota_holder 独占的总卷大小
        vol_exclusive_used_gb: float = 0.0
        if all_quota_holder_ref_set:
            placeholders2 = ",".join("?" * len(all_quota_holder_ref_set))
            exc_row = conn.execute(
                f"SELECT COALESCE(SUM(size_gb), 0) as s FROM docker_volumes_meta WHERE server_id=? AND volume_name IN ({placeholders2})",
                [server_id, *all_quota_holder_ref_set],
            ).fetchone()
            vol_exclusive_used_gb = float(exc_row["s"])

        # 共享配额部分（非 quota_holder 独占的部分）
        vol_shared_used_gb = vol_used_total - vol_exclusive_used_gb

        # ---------- 配额模式 ----------
        quota_mode_rows = conn.execute(
            "SELECT resource_type, quota_mode FROM docker_server_quota_mode WHERE server_id=?",
            (server_id,),
        ).fetchall()
        quota_mode_map: dict[str, str] = {r["resource_type"]: r["quota_mode"] for r in quota_mode_rows}

        # 统计当前服务器各资源类型的所有者数量（用于 shared 模式均分）
        owner_count_rows = conn.execute(
            """SELECT resource_type, COUNT(DISTINCT user_id) as cnt
               FROM docker_resource_roles
               WHERE server_id=? AND role='owner'
               GROUP BY resource_type""",
            (server_id,),
        ).fetchall()
        owner_count_map: dict[str, int] = {r["resource_type"]: r["cnt"] for r in owner_count_rows}

        # ---------- 挂载路径磁盘空间 ----------
        path_whitelist: list[str] = perms.get("ctr_path_whitelist", [])
        paths_info: list[dict[str, Any]] = []
        if path_whitelist:
            client = _ssh_connect(srv_row)
            try:
                for path in path_whitelist:
                    safe_path = shlex.quote(path)
                    # df: total / used / available（1K blocks → GB）
                    out, _, rc = _ssh_exec(
                        client,
                        f"df -k {safe_path} 2>/dev/null | tail -1",
                        timeout=10,
                    )
                    disk_total_gb: float | None = None
                    disk_avail_gb: float | None = None
                    disk_used_gb: float | None = None
                    if rc == 0 and out.strip():
                        cols = out.strip().split()
                        if len(cols) >= 5:
                            try:
                                disk_total_gb = int(cols[1]) / 1024 / 1024
                                disk_used_gb = int(cols[2]) / 1024 / 1024
                                disk_avail_gb = int(cols[3]) / 1024 / 1024
                            except (ValueError, IndexError):
                                pass
                    # 计算当前用户在该路径下的磁盘占用（du -sk）
                    user_used_gb: float | None = None
                    du_out, _, du_rc = _ssh_exec(
                        client,
                        f"du -sk {safe_path} 2>/dev/null | awk '{{print $1}}'",
                        timeout=15,
                    )
                    if du_rc == 0 and du_out.strip().isdigit():
                        user_used_gb = int(du_out.strip()) / 1024 / 1024
                    paths_info.append({
                        "path": path,
                        "totalGb": disk_total_gb,
                        "usedGb": disk_used_gb,
                        "availGb": disk_avail_gb,
                        "pathUsedGb": user_used_gb,
                    })
            finally:
                client.close()

        # ---------- CUDA 权限 ----------
        server_gpu_info: list[dict[str, Any]] = json.loads(srv_row["gpu_info"]) if srv_row["gpu_info"] else []
        cuda_available = bool(srv_row["cuda_available"])
        if user.role == "admin":
            # 管理员拥有所有 GPU
            allowed_gpu_indices = [g["index"] for g in server_gpu_info]
        else:
            allowed_gpu_indices: list[int] = perms.get("cuda_gpu_indices", [])

        # 拼装用户可用的 GPU 详情
        gpu_index_set = set(allowed_gpu_indices)
        available_gpus = [g for g in server_gpu_info if g["index"] in gpu_index_set]

        # 计算被容器占用的 GPU 数量（当前用户有权看到的容器）
        gpu_used_count = 0
        gpu_total_count = len(server_gpu_info)

    return {
        "serverId": server_id,
        "volume": {
            "quotaGb": vol_quota_gb,
            "usedSelfGb": vol_used_self,
            "usedTotalGb": vol_used_total,
            "remainingGb": vol_remaining,
            # 配额占用者独占使用量（本用户作为 quota_holder）
            "quotaHolderUsedGb": vol_quota_holder_used_gb,
            # 全服务器 quota_holder 独占总量
            "exclusiveUsedGb": vol_exclusive_used_gb,
            # 共享区使用量
            "sharedUsedGb": max(0.0, vol_shared_used_gb),
        },
        "paths": paths_info,
        "cuda": {
            "serverHasCuda": cuda_available,
            "allowedGpuIndices": allowed_gpu_indices,
            "availableGpus": available_gpus,
            "totalGpuCount": gpu_total_count,
            "allGpuInfo": server_gpu_info,
        },
        "quotaModes": quota_mode_map,
        "ownerCounts": owner_count_map,
    }
