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

                -- 细粒度权限表
                -- 权限分为两类：
                --   1. 操作能力（用户在该服务器上是否有权执行某操作）
                --   2. 资源配额（用户可使用的资源上限）
                -- 注意：查看/管理「自己」的资源为默认权限，不需要单独配置
                -- img_use/ctr_use/vol_use 控制「是否能使用（看到+访问）该类资源」
                CREATE TABLE IF NOT EXISTS docker_user_perms (
                    server_id TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    -- 服务器可见性（是否能看到/访问此服务器）
                    server_visible INTEGER NOT NULL DEFAULT 0,
                    -- 镜像权限
                    img_use INTEGER NOT NULL DEFAULT 0,      -- 是否有权使用（查看/访问）镜像
                    img_pull INTEGER NOT NULL DEFAULT 0,     -- 拉取新镜像
                    img_view_all INTEGER NOT NULL DEFAULT 0,  -- 查看所有用户的镜像
                    img_manage_all INTEGER NOT NULL DEFAULT 0, -- 管理所有用户的镜像（删除权）
                    img_copy INTEGER NOT NULL DEFAULT 0,     -- 跨服务器复制镜像
                    img_quota_gb REAL NOT NULL DEFAULT 0,    -- 镜像空间配额(GB，0=不限)
                    -- 容器权限
                    ctr_use INTEGER NOT NULL DEFAULT 0,      -- 是否有权使用（查看/访问）容器
                    ctr_view_all INTEGER NOT NULL DEFAULT 0, -- 查看所有用户的容器
                    ctr_create INTEGER NOT NULL DEFAULT 0,         -- 创建容器（run/compose 模式均包含）
                    ctr_create_template INTEGER NOT NULL DEFAULT 0,
                    ctr_manage_all INTEGER NOT NULL DEFAULT 0,   -- 管理所有用户的容器
                    ctr_path_whitelist TEXT NOT NULL DEFAULT '[]',
                    ctr_quota_num INTEGER NOT NULL DEFAULT 0,    -- 容器数量配额(0=不限)
                    -- 卷权限
                    vol_use INTEGER NOT NULL DEFAULT 0,      -- 是否有权使用（查看/访问）卷
                    vol_view_all INTEGER NOT NULL DEFAULT 0,  -- 查看所有用户的卷
                    vol_create INTEGER NOT NULL DEFAULT 0,
                    vol_manage_all INTEGER NOT NULL DEFAULT 0,   -- 管理所有用户的卷（删除权，自动包含查看权）
                    vol_copy INTEGER NOT NULL DEFAULT 0,
                    vol_quota_gb REAL NOT NULL DEFAULT 0,    -- 卷空间配额(GB，0=不限)
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
                -- quota_holder: 配额占用者，资源大小在所有 quota_holder 间均分
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

                CREATE INDEX IF NOT EXISTS idx_resource_roles_server ON docker_resource_roles(server_id);
                CREATE INDEX IF NOT EXISTS idx_resource_roles_ref ON docker_resource_roles(resource_type, resource_ref);
                CREATE INDEX IF NOT EXISTS idx_user_perms_server ON docker_user_perms(server_id);

                -- 容器资源关联缓存表（缓存容器使用的镜像和挂载的卷，用于角色继承）
                -- 当容器查看者继承挂载卷/镜像的查看权时，从此表查询关联关系
                CREATE TABLE IF NOT EXISTS docker_container_resource_cache (
                    server_id TEXT NOT NULL,
                    container_ref TEXT NOT NULL,   -- 容器名称或短 ID
                    resource_type TEXT NOT NULL,   -- image | volume
                    resource_ref TEXT NOT NULL,    -- 镜像 ref 或 卷名称
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY(server_id, container_ref, resource_type, resource_ref),
                    FOREIGN KEY(server_id) REFERENCES docker_servers(id)
                );
                """
            )
            # 迁移：为已有数据库添加新列
            existing_cols = {r[1] for r in conn.execute("PRAGMA table_info(docker_user_perms)").fetchall()}
            if "vol_view_all" not in existing_cols:
                conn.execute("ALTER TABLE docker_user_perms ADD COLUMN vol_view_all INTEGER NOT NULL DEFAULT 0")
            # vol_manage_all 迁移：新列取代旧的 vol_delete_all
            if "vol_manage_all" not in existing_cols:
                conn.execute("ALTER TABLE docker_user_perms ADD COLUMN vol_manage_all INTEGER NOT NULL DEFAULT 0")
                # 如果旧列 vol_delete_all 存在，将其数据迁移到 vol_manage_all
                if "vol_delete_all" in existing_cols:
                    conn.execute("UPDATE docker_user_perms SET vol_manage_all = vol_delete_all")
            if "ctr_view_all" not in existing_cols:
                conn.execute("ALTER TABLE docker_user_perms ADD COLUMN ctr_view_all INTEGER NOT NULL DEFAULT 0")
            if "ctr_create_template" not in existing_cols:
                conn.execute("ALTER TABLE docker_user_perms ADD COLUMN ctr_create_template INTEGER NOT NULL DEFAULT 0")
            if "ctr_path_whitelist" not in existing_cols:
                conn.execute("ALTER TABLE docker_user_perms ADD COLUMN ctr_path_whitelist TEXT NOT NULL DEFAULT '[]'")
            if "ctr_quota_num" not in existing_cols:
                conn.execute("ALTER TABLE docker_user_perms ADD COLUMN ctr_quota_num INTEGER NOT NULL DEFAULT 0")
            if "cuda_gpu_indices" not in existing_cols:
                conn.execute("ALTER TABLE docker_user_perms ADD COLUMN cuda_gpu_indices TEXT NOT NULL DEFAULT '[]'")
            # docker_containers_meta 新列迁移
            ctr_meta_cols = {r[1] for r in conn.execute("PRAGMA table_info(docker_containers_meta)").fetchall()}
            if "display_ports" not in ctr_meta_cols:
                conn.execute("ALTER TABLE docker_containers_meta ADD COLUMN display_ports TEXT")
        _DB_INITIALIZED = True


# ==============================================================
# 工具函数
# ==============================================================

def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_id() -> str:
    return secrets.token_hex(12)


def _parse_size_to_gb(size_str: str) -> float:
    """将 Docker 输出的人类可读大小（如 '1.23GB', '456MB', '100kB'）转为 GB 浮点数。"""
    s = size_str.strip().upper()
    if not s:
        return 0.0
    # 处理 "1.23GB" / "456MB" / "100KB" / "1.5B" 等格式
    # 注意：必须按单位长度从长到短匹配，否则 "B" 会先匹配到 "1.23GB"
    units = {
        "TB": 1024.0,
        "GB": 1.0,
        "MB": 1.0 / 1024,
        "KB": 1.0 / (1024 ** 2),
        "B": 1.0 / (1024 ** 3),
    }
    for unit, factor in units.items():
        if s.endswith(unit):
            try:
                return float(s[:-len(unit)].strip()) * factor
            except ValueError:
                continue  # 解析失败则继续尝试其他单位
    # 无单位时尝试直接解析为字节
    try:
        return float(s) / (1024 ** 3)
    except ValueError:
        return 0.0


def _record_resource_creator(
    conn: sqlite3.Connection,
    server_id: str,
    resource_type: str,
    resource_ref: str,
    user_id: str,
) -> None:
    """记录资源的创建者+所有者角色（创建资源时自动调用）。

    - 写入 docker_resource_roles: creator 和 owner 角色
    - 同步 _meta 表（用于所有权过滤查询）
    - 若已有 creator 记录则保留旧的（不覆盖）
    """
    now = _now()
    # 写入 creator 角色（若已存在则跳过）
    existing_creator = conn.execute(
        "SELECT 1 FROM docker_resource_roles WHERE server_id=? AND resource_type=? AND resource_ref=? AND role='creator'",
        (server_id, resource_type, resource_ref),
    ).fetchone()
    if not existing_creator:
        conn.execute(
            "INSERT OR IGNORE INTO docker_resource_roles (id, server_id, resource_type, resource_ref, user_id, role, assigned_at) VALUES (?,?,?,?,?,?,?)",
            (_new_id(), server_id, resource_type, resource_ref, user_id, "creator", now),
        )
    # 写入 owner 角色（若已存在则跳过）
    existing_owner = conn.execute(
        "SELECT 1 FROM docker_resource_roles WHERE server_id=? AND resource_type=? AND resource_ref=? AND user_id=? AND role='owner'",
        (server_id, resource_type, resource_ref, user_id),
    ).fetchone()
    if not existing_owner:
        conn.execute(
            "INSERT OR IGNORE INTO docker_resource_roles (id, server_id, resource_type, resource_ref, user_id, role, assigned_at) VALUES (?,?,?,?,?,?,?)",
            (_new_id(), server_id, resource_type, resource_ref, user_id, "owner", now),
        )
    # 写入 quota_holder 角色（创建时默认创建者为配额占用者，若已存在则跳过）
    existing_qh = conn.execute(
        "SELECT 1 FROM docker_resource_roles WHERE server_id=? AND resource_type=? AND resource_ref=? AND role='quota_holder'",
        (server_id, resource_type, resource_ref),
    ).fetchone()
    if not existing_qh:
        conn.execute(
            "INSERT OR IGNORE INTO docker_resource_roles (id, server_id, resource_type, resource_ref, user_id, role, assigned_at) VALUES (?,?,?,?,?,?,?)",
            (_new_id(), server_id, resource_type, resource_ref, user_id, "quota_holder", now),
        )
    # 同步旧 _meta 表
    _sync_legacy_meta(conn, server_id, resource_type, resource_ref, user_id, now)


def _require_server_visible(conn: sqlite3.Connection, server_id: str, user: User) -> None:
    """校验用户对服务器是否有可见权限（server_visible 或管理员）"""
    if user.role == "admin":
        return
    perms = _get_user_perms(conn, server_id, user)
    if not perms.get("server_visible"):
        raise ToolboxError(
            "PERMISSION_DENIED",
            "您没有访问该服务器的权限",
            status_code=403,
            tool_id=TOOL_ID,
        )


def _require_admin(user: User) -> None:
    """校验用户是否为管理员"""
    if user.role != "admin":
        raise ToolboxError(
            "PERMISSION_DENIED",
            "只有管理员可以执行此操作",
            status_code=403,
            tool_id=TOOL_ID,
        )


# ── 新细粒度权限 ──────────────────────────────────────────────

_PERMS_DEFAULTS: dict[str, Any] = {
    "server_visible": False,
    # 镜像权限
    "img_use": False,           # 是否有权使用（查看/访问）镜像
    "img_pull": False,          # 拉取新镜像
    "img_view_all": False,      # 查看所有用户的镜像
    "img_manage_all": False,    # 管理所有用户的镜像（删除权）
    "img_copy": False,          # 跨服务器复制镜像
    "img_quota_gb": 0.0,        # 镜像空间配额(GB，0=不限)
    # 容器权限
    "ctr_use": False,           # 是否有权使用（查看/访问）容器
    "ctr_view_all": False,      # 查看所有用户的容器
    "ctr_create": False,         # 创建容器（run/compose 模式均包含）
    "ctr_create_template": False,
    "ctr_manage_all": False,    # 管理所有用户的容器
    "ctr_path_whitelist": [],
    "ctr_quota_num": 0,         # 容器数量配额（0=不限）
    # 卷权限
    "vol_use": False,           # 是否有权使用（查看/访问）卷
    "vol_view_all": False,      # 查看所有用户的卷
    "vol_create": False,
    "vol_manage_all": False,    # 管理所有用户的卷（删除权，自动包含查看权）
    "vol_copy": False,
    "vol_quota_gb": 0.0,        # 卷空间配额(GB，0=不限)
    # 模板权限
    "tpl_use": False,
    "tpl_create": False,
    "tpl_edit": False,
    # CUDA 权限
    "cuda_gpu_indices": [],
}

# 管理员拥有所有权限的完整集合
_ADMIN_PERMS: dict[str, Any] = {k: (True if isinstance(v, bool) else ([] if isinstance(v, list) else 999.0)) for k, v in _PERMS_DEFAULTS.items()}
_ADMIN_PERMS["ctr_path_whitelist"] = []   # 管理员路径不受限
_ADMIN_PERMS["vol_quota_gb"] = 0.0        # 管理员配额无限制（0=不限）
_ADMIN_PERMS["img_quota_gb"] = 0.0        # 管理员配额无限制（0=不限）
_ADMIN_PERMS["ctr_quota_num"] = 0         # 管理员容器数无限制（0=不限）


def _row_to_perms(row: sqlite3.Row) -> dict[str, Any]:
    """将数据库行转换为权限字典"""
    keys = set(row.keys())
    def _b(k: str, fallback: bool = False) -> bool:
        return bool(row[k]) if k in keys else fallback
    def _f(k: str, fallback: float = 0.0) -> float:
        return float(row[k]) if k in keys else fallback
    def _j(k: str, fallback: list) -> list:
        return json.loads(row[k]) if k in keys else fallback
    def _i(k: str, fallback: int = 0) -> int:
        return int(row[k]) if k in keys else fallback

    return {
        "server_visible": _b("server_visible"),
        # 镜像
        "img_use": _b("img_use"),
        "img_pull": _b("img_pull"),
        "img_view_all": _b("img_view_all"),
        "img_manage_all": _b("img_manage_all"),
        "img_copy": _b("img_copy"),
        "img_quota_gb": _f("img_quota_gb"),
        # 容器
        "ctr_use": _b("ctr_use"),
        "ctr_view_all": _b("ctr_view_all"),
        "ctr_create": _b("ctr_create"),
        "ctr_create_template": _b("ctr_create_template"),
        "ctr_manage_all": _b("ctr_manage_all"),
        "ctr_path_whitelist": _j("ctr_path_whitelist", []),
        "ctr_quota_num": _i("ctr_quota_num"),
        # 卷
        "vol_use": _b("vol_use"),
        "vol_view_all": _b("vol_view_all"),
        "vol_create": _b("vol_create"),
        "vol_manage_all": _b("vol_manage_all"),
        "vol_copy": _b("vol_copy"),
        "vol_quota_gb": _f("vol_quota_gb"),
        # 模板
        "tpl_use": _b("tpl_use"),
        "tpl_create": _b("tpl_create"),
        "tpl_edit": _b("tpl_edit"),
        # CUDA
        "cuda_gpu_indices": _j("cuda_gpu_indices", []),
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


def _normalize_image_ref(image_ref: str) -> str:
    """Normalize Docker image refs so ``nginx`` and ``nginx:latest`` match platform metadata."""
    ref = image_ref.strip()
    if not ref:
        return ref
    # sha256/image IDs are already concrete identifiers.
    if ref.startswith("sha256:"):
        return ref
    last_part = ref.split("/")[-1]
    if ":" not in last_part:
        return f"{ref}:latest"
    return ref


def _resource_ref_candidates(resource_type: str, resource_ref: str) -> list[str]:
    """Return likely platform refs for a Docker resource without broadening across resources."""
    ref = resource_ref.strip().lstrip("/") if resource_ref else ""
    refs: list[str] = []
    if ref:
        refs.append(_normalize_image_ref(ref) if resource_type == "image" else ref)
        if resource_type in {"container", "image"} and len(ref) > 12:
            refs.append(ref[:12])
    return list(dict.fromkeys(refs))


def _sql_in_clause(values: list[str]) -> tuple[str, list[str]]:
    placeholders = ",".join("?" * len(values))
    return placeholders, values


def _has_resource_role(
    conn: sqlite3.Connection,
    server_id: str,
    resource_type: str,
    resource_refs: list[str],
    user_id: str,
    roles: tuple[str, ...],
) -> bool:
    if not resource_refs:
        return False
    ref_placeholders, ref_params = _sql_in_clause(resource_refs)
    role_placeholders, role_params = _sql_in_clause(list(roles))
    row = conn.execute(
        f"""SELECT 1 FROM docker_resource_roles
            WHERE server_id=? AND resource_type=? AND user_id=?
              AND resource_ref IN ({ref_placeholders})
              AND role IN ({role_placeholders})
            LIMIT 1""",
        [server_id, resource_type, user_id, *ref_params, *role_params],
    ).fetchone()
    return row is not None


def _legacy_owner_matches(
    conn: sqlite3.Connection,
    server_id: str,
    resource_type: str,
    resource_refs: list[str],
    user_id: str,
) -> bool:
    if not resource_refs:
        return False
    placeholders, params = _sql_in_clause(resource_refs)
    if resource_type == "image":
        row = conn.execute(
            f"SELECT 1 FROM docker_images_meta WHERE server_id=? AND owner_user_id=? AND image_ref IN ({placeholders}) LIMIT 1",
            [server_id, user_id, *params],
        ).fetchone()
    elif resource_type == "container":
        row = conn.execute(
            f"SELECT 1 FROM docker_containers_meta WHERE server_id=? AND owner_user_id=? AND container_ref IN ({placeholders}) LIMIT 1",
            [server_id, user_id, *params],
        ).fetchone()
    elif resource_type == "volume":
        row = conn.execute(
            f"SELECT 1 FROM docker_volumes_meta WHERE server_id=? AND owner_user_id=? AND volume_name IN ({placeholders}) LIMIT 1",
            [server_id, user_id, *params],
        ).fetchone()
    else:
        return False
    return row is not None


def _user_can_access_resource(
    conn: sqlite3.Connection,
    server_id: str,
    resource_type: str,
    resource_ref: str,
    user: User,
) -> bool:
    if user.role == "admin":
        return True
    refs = _resource_ref_candidates(resource_type, resource_ref)
    return (
        _has_resource_role(conn, server_id, resource_type, refs, user.id, ("owner", "viewer"))
        or _legacy_owner_matches(conn, server_id, resource_type, refs, user.id)
    )


def _user_can_manage_resource(
    conn: sqlite3.Connection,
    server_id: str,
    resource_type: str,
    resource_ref: str,
    user: User,
) -> bool:
    if user.role == "admin":
        return True
    refs = _resource_ref_candidates(resource_type, resource_ref)
    return (
        _has_resource_role(conn, server_id, resource_type, refs, user.id, ("owner",))
        or _legacy_owner_matches(conn, server_id, resource_type, refs, user.id)
    )


def _delete_resource_metadata(
    conn: sqlite3.Connection,
    server_id: str,
    resource_type: str,
    resource_refs: list[str],
) -> None:
    refs = [r for r in dict.fromkeys(resource_refs) if r]
    if not refs:
        return
    placeholders, params = _sql_in_clause(refs)
    conn.execute(
        f"DELETE FROM docker_resource_roles WHERE server_id=? AND resource_type=? AND resource_ref IN ({placeholders})",
        [server_id, resource_type, *params],
    )
    if resource_type == "image":
        conn.execute(
            f"DELETE FROM docker_images_meta WHERE server_id=? AND image_ref IN ({placeholders})",
            [server_id, *params],
        )
    elif resource_type == "container":
        conn.execute(
            f"DELETE FROM docker_containers_meta WHERE server_id=? AND container_ref IN ({placeholders})",
            [server_id, *params],
        )
        conn.execute(
            f"DELETE FROM docker_container_resource_cache WHERE server_id=? AND container_ref IN ({placeholders})",
            [server_id, *params],
        )
    elif resource_type == "volume":
        conn.execute(
            f"DELETE FROM docker_volumes_meta WHERE server_id=? AND volume_name IN ({placeholders})",
            [server_id, *params],
        )


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
            perms = _get_user_perms(conn, row["id"], user)
            if user.role != "admin" and not perms.get("server_visible"):
                continue
            srv = _public_server(row)
            srv["serverVisible"] = True
            srv["perms"] = perms
            result.append(srv)
    return result


def check_servers_status(user: User) -> dict[str, str]:
    """检测所有可见服务器的 SSH 连接状态（在线/离线）。

    使用短超时（5 秒）做快速连接测试，返回 {server_id: "online"|"offline"} 映射。
    """
    init_docker_database()
    try:
        import paramiko
    except ImportError:
        return {}

    # 获取用户可见的服务器列表
    with get_connection() as conn:
        all_rows = conn.execute("SELECT * FROM docker_servers ORDER BY name").fetchall()
        visible_rows = []
        for row in all_rows:
            perms = _get_user_perms(conn, row["id"], user)
            if user.role == "admin" or perms.get("server_visible"):
                visible_rows.append(row)

    statuses: dict[str, str] = {}
    for row in visible_rows:
        srv_id = row["id"]
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        password = _decrypt(row["ssh_password_encrypted"])
        try:
            client.connect(
                hostname=row["host"],
                port=row["port"],
                username=row["ssh_username"],
                password=password,
                timeout=5,
                banner_timeout=5,
                auth_timeout=5,
            )
            statuses[srv_id] = "online"
            client.close()
        except Exception:
            statuses[srv_id] = "offline"
            try:
                client.close()
            except Exception:
                pass
    return statuses


def get_server(server_id: str, user: User) -> dict[str, Any]:
    """获取单个服务器信息（含权限校验）"""
    init_docker_database()
    with get_connection() as conn:
        row = _get_server_row(conn, server_id)
        _require_server_visible(conn, server_id, user)
        srv = _public_server(row)
        srv["perms"] = _get_user_perms(conn, server_id, user)
    return srv


def delete_server(server_id: str, user: User) -> None:
    """删除服务器（管理员）"""
    init_docker_database()
    if user.role != "admin":
        raise ToolboxError("ADMIN_REQUIRED", "只有管理员可以删除服务器", status_code=403, tool_id=TOOL_ID)
    with get_connection() as conn:
        _get_server_row(conn, server_id)
        conn.execute("DELETE FROM docker_user_perms WHERE server_id = ?", (server_id,))
        conn.execute("DELETE FROM docker_volumes_meta WHERE server_id = ?", (server_id,))
        conn.execute("DELETE FROM docker_images_meta WHERE server_id = ?", (server_id,))
        conn.execute("DELETE FROM docker_containers_meta WHERE server_id = ?", (server_id,))
        conn.execute("DELETE FROM docker_resource_roles WHERE server_id = ?", (server_id,))
        conn.execute("DELETE FROM docker_container_resource_cache WHERE server_id = ?", (server_id,))
        conn.execute("DELETE FROM docker_servers WHERE id = ?", (server_id,))


def list_server_permissions(server_id: str, user: User) -> list[dict[str, Any]]:
    """列出服务器的用户权限概览（管理员专用）"""
    init_docker_database()
    _require_admin(user)
    with get_connection() as conn:
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
            result.append({
                "userId": row["id"],
                "username": row["username"],
                "displayName": row["display_name"],
                "role": row["role"],
                "perms": perms,
            })
    return result


def get_user_perms_for_user(server_id: str, target_user_id: str, user: User) -> dict[str, Any]:
    """获取指定用户在服务器上的细粒度权限（管理员专用）"""
    init_docker_database()
    _require_admin(user)
    with get_connection() as conn:
        _get_server_row(conn, server_id)
        target = conn.execute("SELECT * FROM users WHERE id = ?", (target_user_id,)).fetchone()
        if not target:
            raise ToolboxError("USER_NOT_FOUND", "目标用户不存在", status_code=404, tool_id=TOOL_ID)
        perms = _get_user_perms(conn, server_id, type('_U', (), {'id': target_user_id, 'role': target['role']})())
    return {"serverId": server_id, "userId": target_user_id, "perms": perms}


def set_user_perms(server_id: str, target_user_id: str, perms: dict[str, Any], user: User) -> dict[str, Any]:
    """设置用户在服务器上的细粒度权限（管理员专用）"""
    init_docker_database()
    _require_admin(user)
    with get_connection() as conn:
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
        img_quota_gb = float(perms.get("img_quota_gb", 0))
        ctr_quota_num = int(perms.get("ctr_quota_num", 0))
        cuda_gpu_indices_json = json.dumps(perms.get("cuda_gpu_indices", []))
        conn.execute(
            """
            INSERT INTO docker_user_perms (
                server_id, user_id, server_visible,
                img_use, img_pull, img_view_all, img_manage_all, img_copy, img_quota_gb,
                ctr_use, ctr_view_all,
                ctr_create, ctr_create_template,
                ctr_manage_all, ctr_path_whitelist, ctr_quota_num,
                vol_use, vol_view_all, vol_create, vol_manage_all, vol_copy, vol_quota_gb,
                tpl_use, tpl_create, tpl_edit,
                cuda_gpu_indices,
                updated_at
            ) VALUES (
                ?, ?, ?,
                ?, ?, ?, ?, ?, ?,
                ?, ?,
                ?, ?,
                ?, ?, ?,
                ?, ?, ?, ?, ?, ?,
                ?, ?, ?,
                ?,
                ?
            )
            ON CONFLICT(server_id, user_id) DO UPDATE SET
                server_visible = excluded.server_visible,
                img_use = excluded.img_use,
                img_pull = excluded.img_pull,
                img_view_all = excluded.img_view_all,
                img_manage_all = excluded.img_manage_all,
                img_copy = excluded.img_copy,
                img_quota_gb = excluded.img_quota_gb,
                ctr_use = excluded.ctr_use,
                ctr_view_all = excluded.ctr_view_all,
                ctr_create = excluded.ctr_create,
                ctr_create_template = excluded.ctr_create_template,
                ctr_manage_all = excluded.ctr_manage_all,
                ctr_path_whitelist = excluded.ctr_path_whitelist,
                ctr_quota_num = excluded.ctr_quota_num,
                vol_use = excluded.vol_use,
                vol_view_all = excluded.vol_view_all,
                vol_create = excluded.vol_create,
                vol_manage_all = excluded.vol_manage_all,
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
                b("img_use"), b("img_pull"), b("img_view_all"), b("img_manage_all"), b("img_copy"), img_quota_gb,
                b("ctr_use"), b("ctr_view_all"),
                b("ctr_create"), b("ctr_create_template"),
                b("ctr_manage_all"), path_whitelist_json, ctr_quota_num,
                b("vol_use"), b("vol_view_all"), b("vol_create"), b("vol_manage_all"), b("vol_copy"), vol_quota_gb,
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


def get_my_quota(server_id: str, user: User) -> dict[str, Any]:
    """获取当前用户在服务器上的细粒度权限与配额信息"""
    init_docker_database()
    with get_connection() as conn:
        _require_server_visible(conn, server_id, user)
        perms = _get_user_perms(conn, server_id, user)
        # 计算已用卷空间（使用 quota_holder 均分逻辑，与 list_volumes / get_server_resource_overview 对齐）
        row = _get_server_row(conn, server_id)
        vol_usage = _calc_user_volume_usage(conn, row, user)
        perms["volumeUsedGb"] = vol_usage["usedSelfGb"]
        perms["volumeTotalGb"] = vol_usage["quotaGb"]
        perms["volumeRemainingGb"] = vol_usage["remainingGb"]
    return perms


# ==============================================================
# 镜像管理
# ==============================================================

def _calc_user_image_usage(
    conn: sqlite3.Connection, server_row: sqlite3.Row, user: User
) -> dict[str, Any]:
    """计算用户在指定服务器上的镜像使用情况与配额。

    配额占用者均分逻辑：用户作为 quota_holder 的镜像，按该镜像的 quota_holder 数量均分大小。
    SSH 连接失败时各项用量按 0 处理（不阻塞调用方）。

    返回:
        quotaGb      配额上限 GB（0=不限）
        usedSelfGb   当前用户配额占用 GB（按配额占用者均分）
        usedTotalGb  服务器全部镜像 GB
        countSelf    当前用户作为配额占用者的镜像数
        countTotal   服务器全部镜像数
        remainingGb  剩余配额 GB（None=不限）
    """
    perms = _get_user_perms(conn, server_row["id"], user)
    img_quota_gb = float(perms.get("img_quota_gb", 0))  # 0 = 不限
    # 获取用户作为 quota_holder 的镜像 ref 集合
    user_qh_img_refs: set[str] = set()
    for r in conn.execute(
        "SELECT resource_ref FROM docker_resource_roles WHERE server_id=? AND resource_type='image' AND user_id=? AND role='quota_holder'",
        (server_row["id"], user.id),
    ).fetchall():
        user_qh_img_refs.add(r["resource_ref"])
    # 构建镜像 ref → quota_holder 数量的映射（仅用户是 quota_holder 的镜像）
    img_qh_counts: dict[str, int] = {}
    for ref in user_qh_img_refs:
        cnt_row = conn.execute(
            """SELECT COUNT(DISTINCT user_id) as cnt FROM docker_resource_roles
               WHERE server_id=? AND resource_type='image' AND resource_ref=? AND role='quota_holder'""",
            (server_row["id"], ref),
        ).fetchone()
        img_qh_counts[ref] = max(1, cnt_row["cnt"])

    # SSH 获取镜像列表及大小
    img_used_self_gb = 0.0
    img_used_total_gb = 0.0
    img_count_self = 0
    img_count_total = 0
    try:
        img_client = _ssh_connect(server_row)
        try:
            img_out, _, img_rc = _ssh_exec(
                img_client,
                'docker images --format \'{{.Repository}}:{{.Tag}}\t{{.Size}}\'',
                timeout=15,
            )
        finally:
            img_client.close()
        if img_rc == 0:
            for line in img_out.strip().splitlines():
                line = line.strip()
                if not line:
                    continue
                parts = line.split("\t")
                if len(parts) < 2:
                    continue
                ref, size_str = parts[0], parts[1]
                size_gb = _parse_size_to_gb(size_str)
                img_used_total_gb += size_gb
                img_count_total += 1
                # 新均分逻辑：用户是 quota_holder 时按配额占用者数量均分
                if ref in user_qh_img_refs:
                    img_used_self_gb += size_gb / img_qh_counts[ref]
                    img_count_self += 1
    except ToolboxError:
        pass  # SSH 连接失败时静默，不影响其他概览数据

    img_remaining_gb = max(0.0, img_quota_gb - img_used_self_gb) if img_quota_gb > 0 else None
    return {
        "quotaGb": img_quota_gb,
        "usedSelfGb": img_used_self_gb,
        "usedTotalGb": img_used_total_gb,
        "countSelf": img_count_self,
        "countTotal": img_count_total,
        "remainingGb": img_remaining_gb,
    }


def _enforce_image_quota(
    conn: sqlite3.Connection, server_row: sqlite3.Row, user: User
) -> None:
    """若用户在指定服务器上的镜像配额已超出上限，抛出 QUOTA_EXCEEDED 异常。

    用于拉取镜像 / 跨服务器复制镜像（目标服务器）前的配额校验。
    管理员不受限（quota_gb=0 表示不限）。
    """
    usage = _calc_user_image_usage(conn, server_row, user)
    quota_gb = float(usage["quotaGb"])
    used_self_gb = float(usage["usedSelfGb"])
    if quota_gb > 0 and used_self_gb >= quota_gb:
        raise ToolboxError(
            "QUOTA_EXCEEDED",
            f"镜像空间配额不足，已用 {used_self_gb:.2f} GB，配额 {quota_gb:.2f} GB",
            status_code=403,
            tool_id=TOOL_ID,
        )


def list_images(server_id: str, user: User) -> list[dict[str, Any]]:
    """列出服务器上的 Docker 镜像，按所有权/角色过滤"""
    init_docker_database()
    with get_connection() as conn:
        _require_server_visible(conn, server_id, user)
        perms = _get_user_perms(conn, server_id, user)
        # 需要 img_use 或 img_view_all 或管理员
        if user.role != "admin" and not perms.get("img_use") and not perms.get("img_view_all"):
            raise ToolboxError("PERMISSION_DENIED", "您没有查看镜像的权限", status_code=403, tool_id=TOOL_ID)
        row = _get_server_row(conn, server_id)
        # 获取镜像所有权元数据
        meta_rows = conn.execute(
            "SELECT image_ref, owner_user_id FROM docker_images_meta WHERE server_id = ?", (server_id,)
        ).fetchall()
        meta_map = {r["image_ref"]: r["owner_user_id"] for r in meta_rows}
        # 新角色表：查询当前用户拥有查看权限（owner/viewer）的所有镜像 ref。creator/quota_holder 不具备查看权
        user_accessible_refs: set[str] = set()
        for r in conn.execute(
            "SELECT resource_ref FROM docker_resource_roles WHERE server_id=? AND resource_type='image' AND user_id=? AND role IN ('owner','viewer')",
            (server_id, user.id),
        ).fetchall():
            user_accessible_refs.add(r["resource_ref"])
        # 查询当前用户拥有 owner 角色的镜像 ref（可管理=可删除/复制）。creator 不拥有管理权，仅 owner 可管理
        user_managed_refs: set[str] = set()
        for r in conn.execute(
            "SELECT resource_ref FROM docker_resource_roles WHERE server_id=? AND resource_type='image' AND user_id=? AND role='owner'",
            (server_id, user.id),
        ).fetchall():
            user_managed_refs.add(r["resource_ref"])
        # 是否有全局镜像管理权限
        has_img_manage_all = user.role == "admin" or bool(perms.get("img_manage_all"))
        # 角色继承：查询用户有访问权的容器，通过缓存表找到这些容器使用的镜像
        # 规则：容器的 owner/viewer 自动继承该容器使用的镜像的查看权（creator/quota_holder 不继承）
        if user.role != "admin":
            accessible_ctrs: set[str] = set()
            for r in conn.execute(
                "SELECT resource_ref FROM docker_resource_roles WHERE server_id=? AND resource_type='container' AND user_id=? AND role IN ('owner','viewer')",
                (server_id, user.id),
            ).fetchall():
                accessible_ctrs.add(r["resource_ref"])
            # _meta 表：作为容器 owner 的也算
            for r in conn.execute(
                "SELECT container_ref FROM docker_containers_meta WHERE server_id=? AND owner_user_id=?",
                (server_id, user.id),
            ).fetchall():
                accessible_ctrs.add(r["container_ref"])
            if accessible_ctrs:
                # 从缓存表查找这些容器关联的镜像
                placeholders = ",".join("?" * len(accessible_ctrs))
                for r in conn.execute(
                    f"SELECT resource_ref FROM docker_container_resource_cache WHERE server_id=? AND resource_type='image' AND container_ref IN ({placeholders})",
                    [server_id, *accessible_ctrs],
                ).fetchall():
                    user_accessible_refs.add(r["resource_ref"])
        view_all = user.role == "admin" or perms.get("img_view_all", False) or perms.get("ctr_view_all", False)

    client = _ssh_connect(row)
    try:
        stdout, stderr, code = _ssh_exec(
            client,
            'docker images --format \'{"id":"{{.ID}}","repo":"{{.Repository}}","tag":"{{.Tag}}","size":"{{.Size}}","created":"{{.CreatedAt}}"}\' ',
        )
        # 获取服务器上所有容器引用的镜像（不受权限过滤），用于判断镜像是否被使用
        ctr_stdout, _, _ = _ssh_exec(
            client,
            "docker ps -a --format '{{.Image}}'",
        )
    finally:
        client.close()

    # 构建被容器使用的镜像引用集合（所有容器，不受权限过滤）
    used_image_refs: set[str] = set()
    for line in ctr_stdout.strip().splitlines():
        ref = line.strip()
        if ref:
            used_image_refs.add(ref)

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
        ref_candidates = _resource_ref_candidates("image", ref_full) + _resource_ref_candidates("image", img.get("id", ""))
        owner = next((meta_map.get(ref) for ref in ref_candidates if meta_map.get(ref)), None)
        img["ownerUserId"] = owner  # 前端字段（第一个所有者）
        img["platformManaged"] = owner is not None or any(ref in user_accessible_refs for ref in ref_candidates)
        # 当前用户是否可管理该镜像（删除/复制）：全局管理权 或 owner 角色。creator 不拥有管理权
        img["canManage"] = has_img_manage_all or any(ref in user_managed_refs for ref in ref_candidates)
        # 该镜像是否被服务器上任意容器使用（不受权限过滤，用于禁用删除按钮）
        img_id_short = (img.get("id", "") or "")[:12]
        img["inUse"] = (
            ref_full in used_image_refs
            or (img.get("tag", "") == "latest" and img.get("repo", "") in used_image_refs)
            or (img_id_short and img_id_short in used_image_refs)
        )

        # 访问过滤：view_all 看全部；否则看有查看角色关联的（owner/viewer）
        if view_all:
            images.append(img)
        elif any(ref in user_accessible_refs for ref in ref_candidates) or owner == user.id:
            images.append(img)

    return images


def pull_image(server_id: str, image_ref: str, user: User) -> dict[str, Any]:
    """在服务器上拉取 Docker 镜像"""
    init_docker_database()
    with get_connection() as conn:
        _require_server_visible(conn, server_id, user)
        if user.role != "admin":
            perms = _get_user_perms(conn, server_id, user)
            if not perms.get("img_pull"):
                raise ToolboxError("PERMISSION_DENIED", "您没有拉取镜像的权限", status_code=403, tool_id=TOOL_ID)
        row = _get_server_row(conn, server_id)
        # 镜像配额校验：拉取会在本服务器新增镜像并占用配额，配额已满则禁止拉取
        if user.role != "admin":
            _enforce_image_quota(conn, row, user)

    client = _ssh_connect(row)
    try:
        stdout, stderr, code = _ssh_exec(client, f"docker pull {shlex.quote(image_ref)}", timeout=300)
    finally:
        client.close()

    if code != 0:
        raise ToolboxError("PULL_FAILED", f"镜像拉取失败: {stderr.strip()}", status_code=502, tool_id=TOOL_ID)

    # 记录创建者+所有者（image_ref 使用 repo:tag 格式）
    image_ref = _normalize_image_ref(image_ref)
    with get_connection() as conn:
        _record_resource_creator(conn, server_id, "image", image_ref, user.id)

    return {"success": True, "output": stdout + stderr}


def delete_image(server_id: str, image_ref: str, user: User, force: bool = False) -> dict[str, Any]:
    """删除服务器上的 Docker 镜像（需 img_use 权限，且须是所有者或 img_manage_all）"""
    init_docker_database()
    normalized_ref = _normalize_image_ref(image_ref)
    with get_connection() as conn:
        _require_server_visible(conn, server_id, user)
        if user.role != "admin":
            perms = _get_user_perms(conn, server_id, user)
            # 有 img_manage_all 权限的可删除任意镜像
            if not perms.get("img_manage_all"):
                # 没有 img_manage_all 的需要 img_use 权限
                if not perms.get("img_use"):
                    raise ToolboxError(
                        "PERMISSION_DENIED",
                        "您没有使用镜像的权限，无法删除镜像",
                        status_code=403, tool_id=TOOL_ID,
                    )
                # 且只能删除自己拥有 owner 角色的镜像（creator 不拥有管理权）
                if not _user_can_manage_resource(conn, server_id, "image", normalized_ref, user):
                    raise ToolboxError("PERMISSION_DENIED", "您只能删除自己拥有的镜像，如需删除他人镜像请申请「管理所有用户的镜像」权限", status_code=403, tool_id=TOOL_ID)
        row = _get_server_row(conn, server_id)

    client = _ssh_connect(row)
    try:
        flag = "-f " if force else ""
        stdout, stderr, code = _ssh_exec(client, f"docker rmi {flag}{shlex.quote(normalized_ref)}")
    finally:
        client.close()

    if code != 0:
        raise ToolboxError("DELETE_IMAGE_FAILED", f"删除镜像失败: {stderr.strip()}", status_code=502, tool_id=TOOL_ID)

    # 清除平台元数据
    with get_connection() as conn:
        _delete_resource_metadata(conn, server_id, "image", _resource_ref_candidates("image", normalized_ref))
    return {"success": True, "output": stdout + stderr}


def copy_image(src_server_id: str, dst_server_id: str, image_ref: str, user: User) -> dict[str, Any]:
    """
    跨服务器复制镜像：docker save | gzip -> 平台中转 -> gunzip | docker load
    整个过程在平台服务器内存中流式处理，避免落盘大文件
    """
    init_docker_database()
    image_ref_norm = _normalize_image_ref(image_ref)
    with get_connection() as conn:
        _require_server_visible(conn, src_server_id, user)
        _require_server_visible(conn, dst_server_id, user)
        if user.role != "admin":
            # 源服务器需要 img_copy 权限（跨服务器复制镜像）
            # 注意：img_copy 是跨服务器复制的专用权限，img_manage_all / 镜像所有者均不能绕过
            src_perms = _get_user_perms(conn, src_server_id, user)
            if not src_perms.get("img_copy"):
                raise ToolboxError(
                    "PERMISSION_DENIED",
                    "您在源服务器没有「跨服务器复制镜像」权限",
                    status_code=403, tool_id=TOOL_ID,
                )
            # 目标服务器需要 img_copy 权限（跨服务器复制镜像）
            dst_perms = _get_user_perms(conn, dst_server_id, user)
            if not dst_perms.get("img_copy"):
                raise ToolboxError(
                    "PERMISSION_DENIED",
                    "您在目标服务器没有「跨服务器复制镜像」权限",
                    status_code=403, tool_id=TOOL_ID,
                )
            can_view_source = (
                src_perms.get("img_view_all")
                or src_perms.get("img_manage_all")
                or _user_can_access_resource(conn, src_server_id, "image", image_ref_norm, user)
            )
            if not can_view_source:
                raise ToolboxError(
                    "PERMISSION_DENIED",
                    "您没有访问源镜像的权限，无法复制该镜像",
                    status_code=403,
                    tool_id=TOOL_ID,
                )
        src_row = _get_server_row(conn, src_server_id)
        dst_row = _get_server_row(conn, dst_server_id)
        # 目标服务器镜像配额校验：复制会在目标服务器新增镜像并占用配额，配额已满则禁止作为复制目标
        if user.role != "admin":
            _enforce_image_quota(conn, dst_row, user)

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

        src_chan.exec_command(f"docker save {shlex.quote(image_ref_norm)} | gzip")
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

        # 在目标服务器记录创建者+所有者（默认为复制者）
        with get_connection() as conn:
            _record_resource_creator(conn, dst_server_id, "image", image_ref_norm, user.id)

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

def _calc_user_container_usage(
    conn: sqlite3.Connection, server_row: sqlite3.Row, user: User
) -> dict[str, Any]:
    """计算用户在指定服务器上的容器使用情况与配额。

    配额占用者均分逻辑：用户作为 quota_holder 的容器计入自身用量。
    SSH 连接失败时各项用量按 0 处理（不阻塞调用方）。

    返回:
        quotaNum     配额上限（0=不限）
        usedSelf     当前用户作为 quota_holder 的容器数
        usedTotal    服务器全部容器数
        remaining    剩余配额（None=不限）
    """
    perms = _get_user_perms(conn, server_row["id"], user)
    ctr_quota_num = int(perms.get("ctr_quota_num", 0))  # 0 = 不限
    # 获取用户作为 quota_holder 的容器 ref 集合
    user_qh_ctr_refs: set[str] = set()
    for r in conn.execute(
        "SELECT resource_ref FROM docker_resource_roles WHERE server_id=? AND resource_type='container' AND user_id=? AND role='quota_holder'",
        (server_row["id"], user.id),
    ).fetchall():
        user_qh_ctr_refs.add(r["resource_ref"])

    # SSH 获取服务器上全部容器数量
    ctr_used_total = 0
    ctr_used_self = len(user_qh_ctr_refs)
    try:
        ctr_client = _ssh_connect(server_row)
        try:
            ctr_out, _, ctr_rc = _ssh_exec(
                ctr_client,
                "docker ps -a --format '{{.ID}}'",
                timeout=15,
            )
        finally:
            ctr_client.close()
        if ctr_rc == 0:
            ctr_used_total = len([l for l in ctr_out.strip().splitlines() if l.strip()])
    except ToolboxError:
        pass  # SSH 连接失败时静默，不影响其他概览数据

    ctr_remaining = max(0, ctr_quota_num - ctr_used_self) if ctr_quota_num > 0 else None
    return {
        "quotaNum": ctr_quota_num,
        "usedSelf": ctr_used_self,
        "usedTotal": ctr_used_total,
        "remaining": ctr_remaining,
    }


def _enforce_container_quota(
    conn: sqlite3.Connection, server_row: sqlite3.Row, user: User
) -> None:
    """若用户在指定服务器上的容器数量配额已超出上限，抛出 QUOTA_EXCEEDED 异常。

    用于创建容器前的配额校验。管理员不受限（quota_num=0 表示不限）。
    """
    usage = _calc_user_container_usage(conn, server_row, user)
    quota_num = int(usage["quotaNum"])
    used_self = int(usage["usedSelf"])
    if quota_num > 0 and used_self >= quota_num:
        raise ToolboxError(
            "QUOTA_EXCEEDED",
            f"容器数量配额不足，已有 {used_self} 个容器，配额 {quota_num} 个",
            status_code=403,
            tool_id=TOOL_ID,
        )


def _validate_gpus_permission(gpus_arg: str, allowed_gpu_indices: list[int]) -> None:
    """校验 --gpus 参数是否在用户允许的 GPU 序号范围内。

    支持格式：'all' / 'device=0,1' / 'device=0' / '"device=0,1"'
    """
    if not gpus_arg:
        return
    gpus_arg = gpus_arg.strip().strip('"').strip("'")
    if gpus_arg.lower() == "all":
        # all 表示使用全部 GPU，需要用户拥有全部 GPU 权限
        # 如果 allowed_gpu_indices 为空列表则拒绝（无 GPU 权限）
        if not allowed_gpu_indices:
            raise ToolboxError(
                "GPU_PERMISSION_DENIED",
                "您没有 GPU 使用权限",
                status_code=403,
                tool_id=TOOL_ID,
            )
        return
    # 解析 device=0,1 格式
    if gpus_arg.startswith("device="):
        indices_str = gpus_arg[len("device="):]
        try:
            indices = [int(x.strip()) for x in indices_str.split(",") if x.strip()]
        except ValueError:
            raise ToolboxError(
                "INVALID_GPU_ARG",
                f"无效的 GPU 参数: {gpus_arg}",
                status_code=400,
                tool_id=TOOL_ID,
            ) from None
        allowed_set = set(allowed_gpu_indices)
        for idx in indices:
            if idx not in allowed_set:
                raise ToolboxError(
                    "GPU_PERMISSION_DENIED",
                    f"您没有使用 GPU {idx} 的权限，可用 GPU: {allowed_gpu_indices}",
                    status_code=403,
                    tool_id=TOOL_ID,
                )
        return
    # 其他格式不做校验（保守策略：拒绝未知格式）
    raise ToolboxError(
        "INVALID_GPU_ARG",
        f"不支持的 --gpus 参数格式: {gpus_arg}，请使用 'all' 或 'device=0,1'",
        status_code=400,
        tool_id=TOOL_ID,
    )


def _extract_volumes_from_raw_cmd(cmd: str) -> list[str]:
    """从 docker run 原始命令中提取 -v / --volume 挂载路径。

    返回宿主机侧路径列表（如 ['/host/path', '/data']）。
    """
    try:
        tokens = shlex.split(cmd)
    except ValueError:
        return []
    volumes: list[str] = []
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        if tok in ("-v", "--volume"):
            if i + 1 < len(tokens):
                vol_spec = tokens[i + 1]
                # -v /host:/container 或 -v /host:/container:ro
                host_part = vol_spec.split(":")[0]
                volumes.append(host_part)
                i += 2
                continue
        elif tok.startswith("-v") and len(tok) > 2:
            # -v/host:/container 格式（连写）
            vol_spec = tok[2:]
            host_part = vol_spec.split(":")[0]
            volumes.append(host_part)
        elif tok.startswith("--volume="):
            vol_spec = tok[len("--volume="):]
            host_part = vol_spec.split(":")[0]
            volumes.append(host_part)
        i += 1
    return volumes


def _extract_volume_specs_from_raw_cmd(cmd: str) -> list[str]:
    """Extract raw -v/--volume specs from a docker run command."""
    try:
        tokens = shlex.split(cmd)
    except ValueError:
        return []
    specs: list[str] = []
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        if tok in ("-v", "--volume"):
            if i + 1 < len(tokens):
                specs.append(tokens[i + 1])
                i += 2
                continue
        elif tok.startswith("-v") and len(tok) > 2:
            specs.append(tok[2:])
        elif tok.startswith("--volume="):
            specs.append(tok[len("--volume="):])
        elif tok in ("--mount",):
            if i + 1 < len(tokens):
                spec = _volume_spec_from_mount_option(tokens[i + 1])
                if spec:
                    specs.append(spec)
                i += 2
                continue
        elif tok.startswith("--mount="):
            spec = _volume_spec_from_mount_option(tok[len("--mount="):])
            if spec:
                specs.append(spec)
        i += 1
    return specs


def _volume_spec_from_mount_option(raw: str) -> str:
    """Convert Docker --mount syntax into a simplified source:target style spec."""
    parts: dict[str, str] = {}
    for item in raw.split(","):
        if "=" not in item:
            continue
        key, value = item.split("=", 1)
        parts[key.strip()] = value.strip()
    mount_type = parts.get("type", "")
    source = parts.get("source") or parts.get("src") or ""
    target = parts.get("target") or parts.get("dst") or parts.get("destination") or ""
    if mount_type == "bind":
        return f"{source}:{target}" if target else source
    if mount_type == "volume":
        return f"{source}:{target}" if source and target else source
    return ""


def _extract_gpus_from_raw_cmd(cmd: str) -> str:
    """从 docker run 原始命令中提取 --gpus 参数值。

    返回 gpus 参数值（如 'all' 或 'device=0,1'），未找到则返回空字符串。
    """
    try:
        tokens = shlex.split(cmd)
    except ValueError:
        return ""
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        if tok == "--gpus":
            if i + 1 < len(tokens):
                return tokens[i + 1]
            i += 1
        elif tok.startswith("--gpus="):
            return tok[len("--gpus="):]
        i += 1
    return ""


def _extract_image_from_raw_cmd(cmd: str) -> str:
    """Best-effort extraction of the image token from a docker run command."""
    try:
        tokens = shlex.split(cmd)
    except ValueError:
        return ""
    if len(tokens) < 3:
        return ""
    i = 2  # docker run
    opts_with_value = {
        "-e", "--env", "--env-file", "-h", "--hostname", "--name", "--user", "-u",
        "-w", "--workdir", "-p", "--publish", "--expose", "-v", "--volume",
        "--mount", "--network", "--restart", "--gpus", "--add-host", "--label",
        "--log-driver", "--log-opt", "--entrypoint", "--cpus", "--memory", "-m",
    }
    while i < len(tokens):
        tok = tokens[i]
        if tok == "--":
            return tokens[i + 1] if i + 1 < len(tokens) else ""
        if not tok.startswith("-"):
            return tok
        if tok in opts_with_value and i + 1 < len(tokens):
            i += 2
            continue
        i += 1
    return ""


def _extract_volumes_from_compose(yaml_content: str) -> list[str]:
    """从 docker-compose YAML 中提取 bind 挂载的宿主机路径。

    仅提取 type: bind 或直接路径格式的挂载，忽略 named volume。
    """
    try:
        import yaml
    except ImportError:
        return []
    try:
        data = yaml.safe_load(yaml_content)
    except Exception:
        return []
    if not isinstance(data, dict):
        return []
    paths: list[str] = []
    services = data.get("services", {})
    if not isinstance(services, dict):
        return paths
    for svc_config in services.values():
        if not isinstance(svc_config, dict):
            continue
        vols = svc_config.get("volumes", [])
        if not isinstance(vols, list):
            continue
        for vol in vols:
            if isinstance(vol, str):
                # "/host:/container" 或 "/host:/container:ro"
                parts = vol.split(":")
                if len(parts) >= 2:
                    host_part = parts[0]
                    # 忽略 named volume（不以 / 开头的）
                    if host_part.startswith("/"):
                        paths.append(host_part)
            elif isinstance(vol, dict):
                # {type: bind, source: /host, target: /container}
                if vol.get("type") == "bind":
                    src = vol.get("source", "")
                    if src and src.startswith("/"):
                        paths.append(src)
    return paths


def _extract_container_resources_from_compose(
    yaml_content: str,
    project_name: str = "",
) -> dict[str, list[str]]:
    """Extract images, bind paths, and named volumes from docker compose YAML."""
    try:
        import yaml
    except ImportError:
        return {"images": [], "bindPaths": [], "volumeSpecs": []}
    try:
        data = yaml.safe_load(yaml_content)
    except Exception:
        return {"images": [], "bindPaths": [], "volumeSpecs": []}
    if not isinstance(data, dict):
        return {"images": [], "bindPaths": [], "volumeSpecs": []}

    top_volumes = data.get("volumes", {})
    external_volumes: set[str] = set()
    if isinstance(top_volumes, dict):
        for name, config in top_volumes.items():
            if isinstance(config, dict) and config.get("external"):
                external_volumes.add(str(config.get("name") or name))

    images: list[str] = []
    bind_paths: list[str] = []
    volume_specs: list[str] = []
    services = data.get("services", {})
    if not isinstance(services, dict):
        return {"images": [], "bindPaths": [], "volumeSpecs": []}

    def _compose_volume_ref(source: str) -> str:
        if source in external_volumes:
            return source
        if project_name and source:
            return f"{project_name}_{source}"
        return source

    for svc_config in services.values():
        if not isinstance(svc_config, dict):
            continue
        image_ref = svc_config.get("image")
        if isinstance(image_ref, str) and image_ref.strip():
            images.append(image_ref.strip())
        vols = svc_config.get("volumes", [])
        if not isinstance(vols, list):
            continue
        for vol in vols:
            if isinstance(vol, str):
                parts = vol.split(":")
                if len(parts) >= 2:
                    source = parts[0]
                    if source.startswith("/") or source.startswith(".") or source.startswith("~"):
                        bind_paths.append(source)
                    elif source:
                        volume_specs.append(f"{_compose_volume_ref(source)}:{parts[1]}")
                elif len(parts) == 1 and parts[0].startswith("/"):
                    volume_specs.append(parts[0])
            elif isinstance(vol, dict):
                mount_type = vol.get("type")
                source = str(vol.get("source") or vol.get("src") or "")
                target = str(vol.get("target") or vol.get("dst") or vol.get("destination") or "")
                if mount_type == "bind":
                    if source:
                        bind_paths.append(source)
                elif mount_type == "volume":
                    if source:
                        volume_specs.append(f"{_compose_volume_ref(source)}:{target}" if target else _compose_volume_ref(source))
                    elif target:
                        volume_specs.append(target)

    return {
        "images": list(dict.fromkeys(images)),
        "bindPaths": list(dict.fromkeys(bind_paths)),
        "volumeSpecs": list(dict.fromkeys(volume_specs)),
    }


def _compose_has_build(yaml_content: str) -> bool:
    try:
        import yaml
    except ImportError:
        return False
    try:
        data = yaml.safe_load(yaml_content)
    except Exception:
        return False
    if not isinstance(data, dict):
        return False
    services = data.get("services", {})
    if not isinstance(services, dict):
        return False
    return any(isinstance(config, dict) and bool(config.get("build")) for config in services.values())


def list_containers(server_id: str, user: User, all_containers: bool = True) -> list[dict[str, Any]]:
    """列出服务器上的容器，按所有权/角色过滤"""
    init_docker_database()
    with get_connection() as conn:
        _require_server_visible(conn, server_id, user)
        perms = _get_user_perms(conn, server_id, user)
        if user.role != "admin" and not perms.get("ctr_use") and not perms.get("ctr_view_all"):
            raise ToolboxError("PERMISSION_DENIED", "您没有查看容器的权限", status_code=403, tool_id=TOOL_ID)
        row = _get_server_row(conn, server_id)
        # 获取容器所有权元数据
        meta_rows = conn.execute(
            "SELECT container_ref, owner_user_id FROM docker_containers_meta WHERE server_id = ?", (server_id,)
        ).fetchall()
        meta_map: dict[str, str] = {r["container_ref"]: r["owner_user_id"] for r in meta_rows}
        # 新角色表：查询当前用户拥有查看权限（owner/viewer）的所有容器 ref。creator/quota_holder 不具备查看权
        user_accessible_refs: set[str] = set()
        for r in conn.execute(
            "SELECT resource_ref FROM docker_resource_roles WHERE server_id=? AND resource_type='container' AND user_id=? AND role IN ('owner','viewer')",
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
    cache_updates: list[tuple[str, str, str, str]] = []  # (container_ref, resource_type, resource_ref, now)
    now_str = _now()
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
        ctr["ownerUserId"] = owner  # 前端字段
        ctr["platformManaged"] = owner is not None or ref in user_accessible_refs

        # 收集容器-镜像/卷关联，用于角色继承缓存
        img_ref = ctr.get("Image", "")
        if ref and img_ref:
            cache_updates.append((ref, "image", img_ref, now_str))
        # Mounts 字段（docker ps --format 默认不含 Mounts，需要 docker inspect）
        # 此处仅缓存镜像关联；卷关联在 get_container_detail 中更新

        if view_all:
            containers.append(ctr)
        elif ref in user_accessible_refs or owner == user.id:
            containers.append(ctr)

    # 更新容器资源关联缓存（镜像关联）
    if cache_updates:
        with get_connection() as conn:
            for container_ref, rtype, rref, ts in cache_updates:
                conn.execute(
                    """INSERT OR REPLACE INTO docker_container_resource_cache
                       (server_id, container_ref, resource_type, resource_ref, updated_at)
                       VALUES (?, ?, ?, ?, ?)""",
                    (server_id, container_ref, rtype, rref, ts),
                )

    return containers


def _validate_path_whitelist(mount_paths: list[str], whitelist: list[str]) -> None:
    """校验挂载路径是否在白名单内（前缀匹配）"""
    for path in mount_paths:
        host_path = path.split(":")[0] if ":" in path else path
        # 仅限制宿主机绝对路径 bind mount；named volume 不属于路径白名单范畴。
        if not host_path.startswith("/"):
            continue
        if not whitelist:
            raise ToolboxError(
                "PATH_NOT_ALLOWED",
                f"未配置宿主机路径挂载白名单，禁止挂载 {host_path}",
                status_code=403,
                tool_id=TOOL_ID,
            )
        allowed = any(host_path.startswith(w.rstrip("/")) for w in whitelist)
        if not allowed:
            raise ToolboxError(
                "PATH_NOT_ALLOWED",
                f"路径 {host_path} 不在允许的挂载白名单中",
                status_code=403,
                tool_id=TOOL_ID,
            )


def _is_bind_mount_source(source: str) -> bool:
    return source.startswith("/") or source.startswith(".") or source.startswith("~")


def _volume_source_from_spec(spec: str) -> tuple[str, str]:
    """Return (kind, source) where kind is bind, named, anonymous, or none."""
    raw = spec.strip()
    if not raw:
        return "none", ""
    parts = raw.split(":")
    if len(parts) == 1:
        return ("anonymous", "") if parts[0].startswith("/") else ("named", parts[0])
    source = parts[0].strip()
    if not source:
        return "anonymous", ""
    if _is_bind_mount_source(source):
        return "bind", source
    return "named", source


def _remote_docker_resource_exists(server_row: sqlite3.Row, resource_type: str, resource_ref: str) -> bool:
    cmd = ""
    if resource_type == "image":
        cmd = f"docker image inspect {shlex.quote(resource_ref)} >/dev/null 2>&1"
    elif resource_type == "volume":
        cmd = f"docker volume inspect {shlex.quote(resource_ref)} >/dev/null 2>&1"
    else:
        return False
    client = _ssh_connect(server_row)
    try:
        _, _, rc = _ssh_exec(client, cmd, timeout=20)
    finally:
        client.close()
    return rc == 0


def _plan_container_image_usage(
    conn: sqlite3.Connection,
    server_row: sqlite3.Row,
    image_refs: list[str],
    user: User,
) -> list[str]:
    """Validate images used by a new container and return auto-pulled refs to record after success."""
    if user.role == "admin":
        return []
    perms = _get_user_perms(conn, server_row["id"], user)
    can_use_images = bool(perms.get("img_use") or perms.get("img_view_all") or perms.get("img_manage_all"))
    pulled_refs: list[str] = []
    for image_ref in [ref for ref in image_refs if ref.strip()]:
        normalized_ref = _normalize_image_ref(image_ref)
        exists = _remote_docker_resource_exists(server_row, "image", normalized_ref)
        can_access_existing = (
            perms.get("img_view_all")
            or perms.get("img_manage_all")
            or _user_can_access_resource(conn, server_row["id"], "image", normalized_ref, user)
        )
        if exists:
            if not can_use_images:
                raise ToolboxError("PERMISSION_DENIED", "您没有使用镜像的权限，无法创建容器", status_code=403, tool_id=TOOL_ID)
            if not can_access_existing:
                raise ToolboxError("PERMISSION_DENIED", f"您没有访问镜像 {normalized_ref} 的权限，无法创建容器", status_code=403, tool_id=TOOL_ID)
            continue
        if not (perms.get("img_use") and perms.get("img_pull")):
            raise ToolboxError(
                "PERMISSION_DENIED",
                f"镜像 {normalized_ref} 不存在或不可见，且您没有拉取并使用镜像的权限",
                status_code=403,
                tool_id=TOOL_ID,
            )
        _enforce_image_quota(conn, server_row, user)
        pulled_refs.append(normalized_ref)
    return list(dict.fromkeys(pulled_refs))


def _plan_container_volume_usage(
    conn: sqlite3.Connection,
    server_row: sqlite3.Row,
    volume_specs: list[str],
    user: User,
) -> list[str]:
    """Validate named/anonymous Docker volumes and return newly created named volumes to record."""
    if user.role == "admin":
        return []
    perms = _get_user_perms(conn, server_row["id"], user)
    created_named_volumes: list[str] = []
    for spec in volume_specs:
        kind, source = _volume_source_from_spec(spec)
        if kind in {"none", "bind"}:
            continue
        if kind == "anonymous":
            if not perms.get("vol_create"):
                raise ToolboxError("PERMISSION_DENIED", "匿名卷会创建新 Docker 卷，您没有创建卷的权限", status_code=403, tool_id=TOOL_ID)
            _enforce_volume_quota(conn, server_row, user)
            continue

        exists = _remote_docker_resource_exists(server_row, "volume", source)
        can_access_existing = (
            perms.get("vol_view_all")
            or perms.get("vol_manage_all")
            or _user_can_access_resource(conn, server_row["id"], "volume", source, user)
        )
        if exists:
            if not perms.get("vol_use") and not perms.get("vol_view_all") and not perms.get("vol_manage_all"):
                raise ToolboxError("PERMISSION_DENIED", "您没有使用卷的权限，无法挂载 Docker 卷", status_code=403, tool_id=TOOL_ID)
            if not can_access_existing:
                raise ToolboxError("PERMISSION_DENIED", f"您没有访问卷 {source} 的权限，无法挂载到容器", status_code=403, tool_id=TOOL_ID)
            continue

        if not perms.get("vol_create"):
            raise ToolboxError("PERMISSION_DENIED", f"卷 {source} 不存在或不可见，且您没有创建卷的权限", status_code=403, tool_id=TOOL_ID)
        _enforce_volume_quota(conn, server_row, user)
        created_named_volumes.append(source)
    return list(dict.fromkeys(created_named_volumes))


def _record_container_resource_creations(
    server_id: str,
    user: User,
    image_refs: list[str],
    volume_refs: list[str],
) -> None:
    if user.role == "admin":
        return
    with get_connection() as conn:
        for image_ref in image_refs:
            _record_resource_creator(conn, server_id, "image", _normalize_image_ref(image_ref), user.id)
        for volume_ref in volume_refs:
            _record_resource_creator(conn, server_id, "volume", volume_ref, user.id)


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
    image_ref = (params.get("image") or "").strip()
    if not image_ref:
        raise ToolboxError("INVALID_IMAGE", "镜像不能为空", status_code=400, tool_id=TOOL_ID)
    images_to_record: list[str] = []
    volumes_to_record: list[str] = []
    with get_connection() as conn:
        if user.role != "admin":
            perms = _get_user_perms(conn, server_id, user)
            # 校验 server_visible + ctr_create 权限
            if not perms.get("server_visible") or not perms.get("ctr_create"):
                raise ToolboxError(
                    "NO_CREATE_PERMISSION", "您没有在此服务器创建容器的权限", status_code=403, tool_id=TOOL_ID
                )
            # 校验挂载路径白名单
            volumes = params.get("volumes", [])
            if volumes:
                whitelist = perms.get("ctr_path_whitelist", [])
                _validate_path_whitelist(volumes, whitelist)
            # 校验 GPU 权限
            gpus_arg = params.get("gpus", "") or ""
            if gpus_arg:
                _validate_gpus_permission(gpus_arg, perms.get("cuda_gpu_indices", []))
        row = _get_server_row(conn, server_id)
        # 容器数量配额校验
        if user.role != "admin":
            _enforce_container_quota(conn, row, user)
            images_to_record = _plan_container_image_usage(conn, row, [image_ref], user)
            volumes_to_record = _plan_container_volume_usage(conn, row, params.get("volumes", []), user)

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

    cmd_parts.append(shlex.quote(image_ref))

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

    # 记录容器所有权（创建者=所有者=配额占用者）
    # docker run -d 输出容器长 ID，取前12位作为短 ID；同时用容器名（若有）作为 ref
    container_id_full = stdout.strip()
    container_id_short = container_id_full[:12]
    container_name = params.get("name", "") or ""
    with get_connection() as conn:
        # 用容器名和短 ID 都记录所有权，确保 list_containers 的双重匹配能命中
        if container_name:
            _record_resource_creator(conn, server_id, "container", container_name, user.id)
        _record_resource_creator(conn, server_id, "container", container_id_short, user.id)
    _record_container_resource_creations(server_id, user, images_to_record, volumes_to_record)

    return {"success": True, "containerId": container_id_full, "command": full_cmd}


def create_container_run_raw(server_id: str, command: str, user: User) -> dict[str, Any]:
    """
    直接在服务器上执行用户提供的完整 docker run 命令（命令行模式）。
    安全校验：
    - 命令必须以 `docker run` 开头（大小写不敏感、允许前缀空格）
    - 用户必须拥有创建容器的权限 + 服务器可见性
    - 校验挂载路径白名单（从命令中解析 -v/--volume）
    - 校验 GPU 权限（从命令中解析 --gpus）
    - 容器数量配额校验
    """
    init_docker_database()
    cmd = command.strip()
    # 安全性：只允许 docker run 命令
    if not cmd.lower().startswith("docker run"):
        raise ToolboxError("INVALID_COMMAND", "命令必须以 'docker run' 开头", status_code=400, tool_id=TOOL_ID)

    raw_image = _extract_image_from_raw_cmd(cmd)
    if not raw_image:
        raise ToolboxError("INVALID_IMAGE", "无法从 docker run 命令中解析镜像", status_code=400, tool_id=TOOL_ID)
    raw_volume_specs = _extract_volume_specs_from_raw_cmd(cmd)
    images_to_record: list[str] = []
    volumes_to_record: list[str] = []
    with get_connection() as conn:
        if user.role != "admin":
            perms = _get_user_perms(conn, server_id, user)
            # 校验 server_visible + ctr_create 权限
            if not perms.get("server_visible") or not perms.get("ctr_create"):
                raise ToolboxError(
                    "NO_CREATE_PERMISSION", "您没有在此服务器创建容器的权限", status_code=403, tool_id=TOOL_ID
                )
            # 校验挂载路径白名单（从原始命令中解析）
            whitelist = perms.get("ctr_path_whitelist", [])
            if raw_volume_specs:
                _validate_path_whitelist(raw_volume_specs, whitelist)
            # 校验 GPU 权限
            raw_gpus = _extract_gpus_from_raw_cmd(cmd)
            if raw_gpus:
                _validate_gpus_permission(raw_gpus, perms.get("cuda_gpu_indices", []))
        row = _get_server_row(conn, server_id)
        # 容器数量配额校验
        if user.role != "admin":
            _enforce_container_quota(conn, row, user)
            images_to_record = _plan_container_image_usage(conn, row, [raw_image], user)
            volumes_to_record = _plan_container_volume_usage(conn, row, raw_volume_specs, user)

    client = _ssh_connect(row)
    try:
        stdout, stderr, code = _ssh_exec(client, cmd, timeout=120)
    finally:
        client.close()

    if code != 0:
        raise ToolboxError(
            "CREATE_CONTAINER_FAILED", f"创建容器失败: {stderr.strip()}", status_code=502, tool_id=TOOL_ID
        )

    # 记录容器所有权
    # 尝试从命令中解析容器名；docker run -d 输出容器长 ID
    container_id_full = stdout.strip()
    container_id_short = container_id_full[:12]
    # 从命令中解析 --name 参数
    container_name = ""
    try:
        tokens = shlex.split(cmd)
        for i, tok in enumerate(tokens):
            if tok == "--name" and i + 1 < len(tokens):
                container_name = tokens[i + 1]
                break
            elif tok.startswith("--name="):
                container_name = tok[len("--name="):]
                break
    except ValueError:
        pass
    with get_connection() as conn:
        if container_name:
            _record_resource_creator(conn, server_id, "container", container_name, user.id)
        _record_resource_creator(conn, server_id, "container", container_id_short, user.id)
    _record_container_resource_creations(server_id, user, images_to_record, volumes_to_record)

    return {"success": True, "containerId": container_id_full, "command": cmd}


def create_container_compose(server_id: str, yaml_content: str, user: User, project_name: str = "") -> dict[str, Any]:
    """
    通过 docker compose 创建容器：将 YAML 上传至服务器临时目录执行。
    安全校验：server_visible + ctr_create + 路径白名单 + 容器配额。
    """
    init_docker_database()
    compose_resources = _extract_container_resources_from_compose(yaml_content, project_name)
    images_to_record: list[str] = []
    volumes_to_record: list[str] = []
    with get_connection() as conn:
        if user.role != "admin":
            perms = _get_user_perms(conn, server_id, user)
            # 校验 server_visible + ctr_create 权限
            if not perms.get("server_visible") or not perms.get("ctr_create"):
                raise ToolboxError(
                    "NO_CREATE_PERMISSION", "您没有在此服务器创建容器的权限", status_code=403, tool_id=TOOL_ID
                )
            # 校验挂载路径白名单（从 YAML 中解析 volumes 的 bind 挂载）
            whitelist = perms.get("ctr_path_whitelist", [])
            if compose_resources["bindPaths"]:
                _validate_path_whitelist(compose_resources["bindPaths"], whitelist)
            if _compose_has_build(yaml_content) and not (perms.get("img_use") and perms.get("img_pull")):
                raise ToolboxError("PERMISSION_DENIED", "Compose build 会创建镜像，您没有使用并拉取/新增镜像的权限", status_code=403, tool_id=TOOL_ID)
        row = _get_server_row(conn, server_id)
        # 容器数量配额校验
        if user.role != "admin":
            _enforce_container_quota(conn, row, user)
            images_to_record = _plan_container_image_usage(conn, row, compose_resources["images"], user)
            volumes_to_record = _plan_container_volume_usage(conn, row, compose_resources["volumeSpecs"], user)

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

    # 记录 compose 创建的容器所有权
    # 通过 docker compose ps 获取该 project 下所有容器
    ctr_refs: list[str] = []
    record_client = _ssh_connect(row)
    try:
        # compose_file 已被删除，只能用 project name 查询
        if project_name:
            ps_cmd = f"docker compose -p {shlex.quote(project_name)} ps --format '{{{{json .}}}}'"
            ps_out, _, ps_rc = _ssh_exec(record_client, ps_cmd, timeout=30)
            if ps_rc == 0:
                for line in ps_out.strip().splitlines():
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        ctr_info = json.loads(line)
                        # docker compose ps 的 Name 字段是容器名
                        name = ctr_info.get("Name", "") or ctr_info.get("Names", "")
                        if name:
                            ctr_refs.append(name.lstrip("/"))
                    except json.JSONDecodeError:
                        continue
    finally:
        record_client.close()

    if ctr_refs:
        with get_connection() as conn:
            for ref in ctr_refs:
                _record_resource_creator(conn, server_id, "container", ref, user.id)
    _record_container_resource_creations(server_id, user, images_to_record, volumes_to_record)

    return {"success": True, "output": stdout + stderr, "projectName": project_name}


def container_action(server_id: str, container_id: str, action: str, user: User) -> dict[str, Any]:
    """
    容器生命周期操作：start/stop/restart/remove
    需要是容器的所有者（owner 角色）或拥有 ctr_manage_all 权限
    """
    init_docker_database()
    if action not in {"start", "stop", "restart", "remove"}:
        raise ToolboxError("INVALID_ACTION", "无效的容器操作", status_code=400, tool_id=TOOL_ID)

    with get_connection() as conn:
        _require_server_visible(conn, server_id, user)
        if user.role != "admin":
            perms = _get_user_perms(conn, server_id, user)
            can_manage_all = perms.get("ctr_manage_all", False)
            # 新模型：检查用户是否是该容器的 owner（creator 不拥有管理权）
            is_resource_owner = _user_can_manage_resource(conn, server_id, "container", container_id, user)
            if not can_manage_all and not is_resource_owner:
                raise ToolboxError("PERMISSION_DENIED", "您没有管理容器的权限（需要是容器所有者或拥有全局管理权限）", status_code=403, tool_id=TOOL_ID)
        row = _get_server_row(conn, server_id)

    docker_cmd = "rm -f" if action == "remove" else action
    cmd = f"docker {docker_cmd} {shlex.quote(container_id)}"
    cleanup_refs = _resource_ref_candidates("container", container_id)

    client = _ssh_connect(row)
    try:
        if action == "remove":
            inspect_out, _, inspect_code = _ssh_exec(
                client,
                f"docker inspect --format '{{{{.Name}}}}\t{{{{.Id}}}}' {shlex.quote(container_id)}",
                timeout=15,
            )
            if inspect_code == 0 and inspect_out.strip():
                parts = inspect_out.strip().split("\t")
                if parts:
                    cleanup_refs.extend(_resource_ref_candidates("container", parts[0]))
                if len(parts) > 1:
                    cleanup_refs.extend(_resource_ref_candidates("container", parts[1]))
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

    # 删除容器时清理平台元数据
    if action == "remove":
        with get_connection() as conn:
            _delete_resource_metadata(conn, server_id, "container", cleanup_refs)

    return {"success": True, "action": action, "containerId": container_id}


def update_restart_policy(server_id: str, container_id: str, policy: str, user: User) -> dict[str, Any]:
    """
    更新容器重启策略（docker update --restart）。
    支持：no / always / unless-stopped / on-failure / on-failure:N
    权限：容器所有者（owner 角色）或 ctr_manage_all / admin（任意容器）。
    """
    allowed = {"no", "always", "unless-stopped", "on-failure"}
    base = policy.split(":")[0] if ":" in policy else policy
    if base not in allowed:
        raise ToolboxError("INVALID_POLICY", "无效的重启策略", status_code=400, tool_id=TOOL_ID)

    init_docker_database()
    with get_connection() as conn:
        _require_server_visible(conn, server_id, user)
        if user.role != "admin":
            perms = _get_user_perms(conn, server_id, user)
            can_manage_all = perms.get("ctr_manage_all", False)
            is_resource_owner = _user_can_manage_resource(conn, server_id, "container", container_id, user)
            if not can_manage_all and not is_resource_owner:
                raise ToolboxError("PERMISSION_DENIED", "您没有管理容器的权限", status_code=403, tool_id=TOOL_ID)
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
    权限：容器的 owner/viewer 角色，或 ctr_view_all / admin（所有容器）。
    """
    init_docker_database()
    with get_connection() as conn:
        _require_server_visible(conn, server_id, user)
        if user.role != "admin":
            perms = _get_user_perms(conn, server_id, user)
            view_all = perms.get("ctr_view_all", False)
            ctr_use = perms.get("ctr_use", False)
            if not view_all and not ctr_use:
                raise ToolboxError("PERMISSION_DENIED", "您没有查看容器详情的权限", status_code=403, tool_id=TOOL_ID)
            if not view_all:
                # 检查是否有该容器的查看角色（owner/viewer）
                if not _user_can_access_resource(conn, server_id, "container", container_id, user):
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
    """获取容器日志（需容器 owner/viewer 角色或 ctr_view_all）"""
    init_docker_database()
    with get_connection() as conn:
        _require_server_visible(conn, server_id, user)
        if user.role != "admin":
            perms = _get_user_perms(conn, server_id, user)
            view_all = perms.get("ctr_view_all", False)
            ctr_use = perms.get("ctr_use", False)
            if not view_all and not ctr_use:
                raise ToolboxError("PERMISSION_DENIED", "您没有查看容器日志的权限", status_code=403, tool_id=TOOL_ID)
            if not view_all:
                if not _user_can_access_resource(conn, server_id, "container", container_id, user):
                    raise ToolboxError("PERMISSION_DENIED", "您只能查看自己有权限的容器日志", status_code=403, tool_id=TOOL_ID)
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

def _calc_user_volume_usage(
    conn: sqlite3.Connection, server_row: sqlite3.Row, user: User
) -> dict[str, Any]:
    """计算用户在指定服务器上的卷使用情况与配额。

    配额占用者均分逻辑：用户作为 quota_holder 的卷，按该卷的 quota_holder 数量均分大小。

    返回:
        quotaGb      配额上限 GB（0=不限）
        usedSelfGb   当前用户配额占用 GB（按配额占用者均分）
        usedTotalGb  服务器全部卷 GB
        countSelf    当前用户作为配额占用者的卷数
        countTotal   服务器全部卷数
        remainingGb  剩余配额 GB（None=不限）
    """
    perms = _get_user_perms(conn, server_row["id"], user)
    vol_quota_gb = float(perms.get("vol_quota_gb", 0))  # 0 = 不限
    # 获取用户作为 quota_holder 的卷 ref 集合
    user_qh_vol_refs: set[str] = set()
    for r in conn.execute(
        "SELECT resource_ref FROM docker_resource_roles WHERE server_id=? AND resource_type='volume' AND user_id=? AND role='quota_holder'",
        (server_row["id"], user.id),
    ).fetchall():
        user_qh_vol_refs.add(r["resource_ref"])
    # 构建卷 ref → quota_holder 数量的映射（仅用户是 quota_holder 的卷）
    vol_qh_counts: dict[str, int] = {}
    for ref in user_qh_vol_refs:
        cnt_row = conn.execute(
            """SELECT COUNT(DISTINCT user_id) as cnt FROM docker_resource_roles
               WHERE server_id=? AND resource_type='volume' AND resource_ref=? AND role='quota_holder'""",
            (server_row["id"], ref),
        ).fetchone()
        vol_qh_counts[ref] = max(1, cnt_row["cnt"])

    # 从 _meta 表获取卷大小
    vol_used_self_gb = 0.0
    vol_used_total_gb = 0.0
    vol_count_self = len(user_qh_vol_refs)
    vol_count_total = 0
    all_vols = conn.execute(
        "SELECT volume_name, size_gb FROM docker_volumes_meta WHERE server_id=?",
        (server_row["id"],),
    ).fetchall()
    for r in all_vols:
        size_gb = float(r["size_gb"] or 0)
        vol_used_total_gb += size_gb
        vol_count_total += 1
        if r["volume_name"] in user_qh_vol_refs:
            vol_used_self_gb += size_gb / vol_qh_counts[r["volume_name"]]

    vol_remaining_gb = max(0.0, vol_quota_gb - vol_used_self_gb) if vol_quota_gb > 0 else None
    return {
        "quotaGb": vol_quota_gb,
        "usedSelfGb": vol_used_self_gb,
        "usedTotalGb": vol_used_total_gb,
        "countSelf": vol_count_self,
        "countTotal": vol_count_total,
        "remainingGb": vol_remaining_gb,
    }


def _enforce_volume_quota(
    conn: sqlite3.Connection, server_row: sqlite3.Row, user: User
) -> None:
    """若用户在指定服务器上的卷空间配额已超出上限，抛出 QUOTA_EXCEEDED 异常。

    用于创建卷 / 跨服务器复制卷（目标服务器）前的配额校验。
    管理员不受限（quota_gb=0 表示不限）。
    """
    usage = _calc_user_volume_usage(conn, server_row, user)
    quota_gb = float(usage["quotaGb"])
    used_self_gb = float(usage["usedSelfGb"])
    if quota_gb > 0 and used_self_gb >= quota_gb:
        raise ToolboxError(
            "QUOTA_EXCEEDED",
            f"卷空间配额不足，已用 {used_self_gb:.2f} GB，配额 {quota_gb:.2f} GB",
            status_code=403,
            tool_id=TOOL_ID,
        )


def _measure_volume_sizes(row: sqlite3.Row) -> dict[str, float]:
    """通过 SSH 测量服务器上所有 Docker 卷的实际磁盘占用大小。

    使用 ``docker volume ls`` + ``docker volume inspect`` + ``du -sk`` 组合命令，
    一次性获取所有卷的实际占用空间（KB），转换为 GB 返回。

    Args:
        row: docker_servers 表行

    Returns:
        ``{volume_name: size_gb}`` 字典。SSH 失败时返回空字典。
    """
    client = _ssh_connect(row)
    try:
        # 一次性测量所有卷的磁盘占用（du -sk 输出 KB）
        cmd = (
            "docker volume ls --format '{{.Name}}' | while IFS= read -r name; do "
            'mp=$(docker volume inspect "$name" --format \'{{.Mountpoint}}\' 2>/dev/null); '
            'size=$(du -sk "$mp" 2>/dev/null | awk \'{print $1}\'); '
            'echo "${name}|${size:-0}"; '
            "done"
        )
        stdout, stderr, code = _ssh_exec(client, cmd, timeout=120)
    finally:
        client.close()

    if code != 0:
        return {}

    sizes: dict[str, float] = {}
    for line in stdout.strip().splitlines():
        line = line.strip()
        if not line or "|" not in line:
            continue
        parts = line.split("|", 1)
        if len(parts) != 2:
            continue
        name, size_kb_str = parts[0].strip(), parts[1].strip()
        if not name:
            continue
        try:
            size_kb = float(size_kb_str)
        except ValueError:
            continue
        sizes[name] = size_kb / (1024 * 1024)  # KB → GB
    return sizes


def refresh_volume_sizes(server_id: str, user: User) -> dict[str, Any]:
    """刷新服务器上所有卷的实际大小并写入数据库。

    管理员或拥有卷查看/管理权限的用户可调用。
    返回 ``{serverId, sizes, count}``。
    """
    init_docker_database()
    with get_connection() as conn:
        _require_server_visible(conn, server_id, user)
        if user.role != "admin":
            perms = _get_user_perms(conn, server_id, user)
            if not (perms.get("vol_use") or perms.get("vol_view_all") or perms.get("vol_manage_all")):
                raise ToolboxError("PERMISSION_DENIED", "您没有刷新卷大小的权限", status_code=403, tool_id=TOOL_ID)
        row = _get_server_row(conn, server_id)

    sizes = _measure_volume_sizes(row)

    # 更新数据库中所有平台管理卷的大小
    with get_connection() as conn:
        for vname, size_gb in sizes.items():
            conn.execute(
                "UPDATE docker_volumes_meta SET size_gb = ? WHERE server_id = ? AND volume_name = ?",
                (size_gb, server_id, vname),
            )

    return {"serverId": server_id, "sizes": sizes, "count": len(sizes)}


def list_volumes(server_id: str, user: User) -> dict[str, Any]:
    """列出服务器上的卷，附加平台元数据，按所有权/角色过滤（对齐镜像 list_images 模式）"""
    init_docker_database()
    with get_connection() as conn:
        _require_server_visible(conn, server_id, user)
        perms = _get_user_perms(conn, server_id, user)
        # 需要 vol_use 或 vol_view_all 或管理员
        if user.role != "admin" and not perms.get("vol_use") and not perms.get("vol_view_all"):
            raise ToolboxError("PERMISSION_DENIED", "您没有查看卷的权限", status_code=403, tool_id=TOOL_ID)
        row = _get_server_row(conn, server_id)

        # 获取当前用户配额信息（使用 quota_holder 均分逻辑）
        if user.role == "admin":
            quota = {"volumeTotalGb": None, "volumeUsedGb": None}
        else:
            vol_usage = _calc_user_volume_usage(conn, row, user)
            quota = {"volumeTotalGb": vol_usage["quotaGb"], "volumeUsedGb": vol_usage["usedSelfGb"]}

        # 查询平台记录的卷元数据
        meta_rows = conn.execute(
            "SELECT * FROM docker_volumes_meta WHERE server_id = ?", (server_id,)
        ).fetchall()
        meta_map = {r["volume_name"]: dict(r) for r in meta_rows}
        # 新角色表：查询当前用户拥有查看权限（owner/viewer）的卷 ref。creator/quota_holder 不具备查看权
        user_accessible_vols: set[str] = set()
        if user.role != "admin":
            for r in conn.execute(
                "SELECT resource_ref FROM docker_resource_roles WHERE server_id=? AND resource_type='volume' AND user_id=? AND role IN ('owner','viewer')",
                (server_id, user.id),
            ).fetchall():
                user_accessible_vols.add(r["resource_ref"])
            # 角色继承：查询用户有访问权的容器，通过缓存表找到这些容器挂载的卷
            # 规则：容器的 owner/viewer 自动继承该容器挂载的卷的查看权（creator/quota_holder 不继承）
            accessible_ctrs: set[str] = set()
            for r in conn.execute(
                "SELECT resource_ref FROM docker_resource_roles WHERE server_id=? AND resource_type='container' AND user_id=? AND role IN ('owner','viewer')",
                (server_id, user.id),
            ).fetchall():
                accessible_ctrs.add(r["resource_ref"])
            for r in conn.execute(
                "SELECT container_ref FROM docker_containers_meta WHERE server_id=? AND owner_user_id=?",
                (server_id, user.id),
            ).fetchall():
                accessible_ctrs.add(r["container_ref"])
            if accessible_ctrs:
                placeholders = ",".join("?" * len(accessible_ctrs))
                for r in conn.execute(
                    f"SELECT resource_ref FROM docker_container_resource_cache WHERE server_id=? AND resource_type='volume' AND container_ref IN ({placeholders})",
                    [server_id, *accessible_ctrs],
                ).fetchall():
                    user_accessible_vols.add(r["resource_ref"])
        # 查询当前用户拥有 owner 角色的卷 ref（可管理=可删除/复制）。creator 不拥有管理权，仅 owner 可管理
        user_managed_vols: set[str] = set()
        for r in conn.execute(
            "SELECT resource_ref FROM docker_resource_roles WHERE server_id=? AND resource_type='volume' AND user_id=? AND role='owner'",
            (server_id, user.id),
        ).fetchall():
            user_managed_vols.add(r["resource_ref"])
        # 是否有全局卷管理权限
        has_vol_manage_all = user.role == "admin" or bool(perms.get("vol_manage_all"))
        # view_all：管理员、vol_view_all、vol_manage_all（管理权自动包含查看权）、ctr_view_all
        view_all = user.role == "admin" or perms.get("vol_view_all", False) or perms.get("vol_manage_all", False) or perms.get("ctr_view_all", False)

    # 从服务器获取实际卷列表
    client = _ssh_connect(row)
    try:
        stdout, stderr, code = _ssh_exec(
            client,
            'docker volume ls --format \'{"name":"{{.Name}}","driver":"{{.Driver}}","mountpoint":"{{.Mountpoint}}"}\'',
        )
    finally:
        client.close()

    # 测量所有卷的实际磁盘占用大小（du -sk）
    actual_sizes = _measure_volume_sizes(row)
    # 同步更新数据库中平台管理卷的大小（供配额计算使用）
    if actual_sizes:
        with get_connection() as conn:
            for vname, size_gb in actual_sizes.items():
                conn.execute(
                    "UPDATE docker_volumes_meta SET size_gb = ? WHERE server_id = ? AND volume_name = ?",
                    (size_gb, server_id, vname),
                )

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
        # 优先使用实测大小，回退到数据库记录
        measured_size = actual_sizes.get(vname)
        if vname in meta_map:
            m = meta_map[vname]
            vol["ownerUserId"] = m["owner_user_id"]  # 前端字段
            vol["sizeGb"] = measured_size if measured_size is not None else m["size_gb"]
            vol["createdAt"] = m["created_at"]
            vol["platformManaged"] = True
        else:
            vol["ownerUserId"] = None
            vol["sizeGb"] = measured_size
            vol["platformManaged"] = vname in user_accessible_vols

        # 当前用户是否可管理该卷（删除）：全局管理权 或 owner 角色。creator 不拥有管理权
        vol["canManage"] = has_vol_manage_all or vname in user_managed_vols

        # 访问过滤：view_all 看全部；否则看有查看角色关联的（owner/viewer）
        if view_all:
            volumes.append(vol)
        elif vname in user_accessible_vols or vol.get("ownerUserId") == user.id:
            volumes.append(vol)

    return {"volumes": volumes, "quota": quota}


def get_volume_detail(server_id: str, volume_name: str, user: User) -> dict[str, Any]:
    """
    获取卷详情：包含平台角色信息（creator/owner/viewer）以及挂载该卷的容器列表。
    容器列表按当前用户权限过滤：
      - 有查看权限（owner/viewer/ctr_view_all）的容器：返回完整信息
      - 无查看权限的容器：仅返回数量
    """
    init_docker_database()
    with get_connection() as conn:
        _require_server_visible(conn, server_id, user)
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
        can_view_volume = (
            user.role == "admin"
            or perms.get("vol_view_all")
            or perms.get("vol_manage_all")
            or (perms.get("vol_use") and _user_can_access_resource(conn, server_id, "volume", volume_name, user))
        )
        if not can_view_volume:
            raise ToolboxError("PERMISSION_DENIED", "您没有权限查看此卷详情", status_code=403, tool_id=TOOL_ID)
        view_all_ctrs = user.role == "admin" or perms.get("ctr_view_all", False)

        # 当前用户可访问的容器 ref 集合（仅 owner/viewer 具备查看权）
        user_accessible_ctrs: set[str] = set()
        if not view_all_ctrs:
            for r in conn.execute(
                "SELECT resource_ref FROM docker_resource_roles WHERE server_id=? AND resource_type='container' AND user_id=? AND role IN ('owner','viewer')",
                (server_id, user.id),
            ).fetchall():
                user_accessible_ctrs.add(r["resource_ref"])
            # _meta 表：owner 也算可见
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
        quota_holder_infos = [_get_user_basic(uid) for uid in roles.get("quotaHolderUserIds", []) if uid]

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
            "quotaHolderUserIds": roles.get("quotaHolderUserIds", []),
            "quotaHolders": [q for q in quota_holder_infos if q],
        },
        "mountedContainers": visible_containers,
        "hiddenContainerCount": hidden_count,
    }


def create_volume(server_id: str, name: str, user: User) -> dict[str, Any]:
    """创建 Docker 卷（含权限与配额校验，对齐镜像 pull_image 模式）

    卷大小由 list_volumes / refresh_volume_sizes 通过 ``du`` 实测，创建时不预设。
    """
    init_docker_database()
    with get_connection() as conn:
        if user.role != "admin":
            perms = _get_user_perms(conn, server_id, user)
            if not perms.get("server_visible") or not perms.get("vol_create"):
                raise ToolboxError("PERMISSION_DENIED", "您没有在此服务器创建卷的权限", status_code=403, tool_id=TOOL_ID)
            # 卷配额校验：创建会在本服务器新增卷并占用配额，配额已满则禁止创建
            row = _get_server_row(conn, server_id)
            _enforce_volume_quota(conn, row, user)
        else:
            row = _get_server_row(conn, server_id)

    client = _ssh_connect(row)
    try:
        stdout, stderr, code = _ssh_exec(client, f"docker volume create {shlex.quote(name)}")
    finally:
        client.close()

    if code != 0:
        raise ToolboxError("CREATE_VOLUME_FAILED", f"创建卷失败: {stderr.strip()}", status_code=502, tool_id=TOOL_ID)

    # 记录创建者+所有者（对齐镜像 pull_image 中的 _record_resource_creator）
    # size_gb 由 list_volumes / refresh_volume_sizes 通过 du 实测填充，此处不预设
    with get_connection() as conn:
        _record_resource_creator(conn, server_id, "volume", name, user.id)

    now = _now()
    return {"success": True, "volumeName": name, "serverId": server_id, "createdAt": now}


def delete_volume(server_id: str, volume_name: str, user: User) -> dict[str, Any]:
    """删除 Docker 卷（需 vol_use 权限且是卷的 owner 角色，或拥有 vol_manage_all 权限，对齐镜像 delete_image 模式）"""
    init_docker_database()
    with get_connection() as conn:
        _require_server_visible(conn, server_id, user)
        if user.role != "admin":
            perms = _get_user_perms(conn, server_id, user)
            can_manage_all = perms.get("vol_manage_all", False)
            # 有 vol_manage_all 权限的可删除任意卷
            if not can_manage_all:
                # 没有 vol_manage_all 的需要 vol_use 权限
                if not perms.get("vol_use"):
                    raise ToolboxError(
                        "PERMISSION_DENIED",
                        "您没有使用卷的权限，无法删除卷",
                        status_code=403, tool_id=TOOL_ID,
                    )
                # 且只能删除自己拥有 owner 角色的卷（creator 不拥有管理权）
                if not _user_can_manage_resource(conn, server_id, "volume", volume_name, user):
                    raise ToolboxError("PERMISSION_DENIED", "您没有删除此卷的权限（需要是卷的所有者或拥有全局管理权限）", status_code=403, tool_id=TOOL_ID)
        row = _get_server_row(conn, server_id)

    client = _ssh_connect(row)
    try:
        stdout, stderr, code = _ssh_exec(client, f"docker volume rm {shlex.quote(volume_name)}")
    finally:
        client.close()

    if code != 0:
        raise ToolboxError("DELETE_VOLUME_FAILED", f"删除卷失败: {stderr.strip()}", status_code=502, tool_id=TOOL_ID)

    # 清除平台元数据与角色（对齐容器删除时的清理逻辑）
    with get_connection() as conn:
        _delete_resource_metadata(conn, server_id, "volume", _resource_ref_candidates("volume", volume_name))

    return {"success": True, "volumeName": volume_name}


def copy_volume(
    src_server_id: str,
    src_volume_name: str,
    dst_server_id: str,
    dst_volume_name: str,
    user: User,
) -> dict[str, Any]:
    """
    跨服务器（或同服务器）复制卷数据（对齐镜像 copy_image 模式）：
      源端: docker run --rm -v <src>:/src:ro alpine tar -czC /src .
      目标端: docker volume create <dst> && docker run --rm -i -v <dst>:/dst alpine sh -c 'tar -xzC /dst'
    整个流程在平台内存中流式中转，不在任何服务器落盘。
    """
    init_docker_database()
    with get_connection() as conn:
        _require_server_visible(conn, src_server_id, user)
        _require_server_visible(conn, dst_server_id, user)
        if user.role != "admin":
            # 源服务器需要 vol_copy 权限（跨服务器复制卷）
            src_perms = _get_user_perms(conn, src_server_id, user)
            if not src_perms.get("vol_copy"):
                raise ToolboxError(
                    "PERMISSION_DENIED",
                    "您在源服务器没有「跨服务器复制卷」权限",
                    status_code=403, tool_id=TOOL_ID,
                )
            # 目标服务器也需要 vol_copy 权限（对齐镜像 copy_image：源和目标均需 img_copy）
            dst_perms = _get_user_perms(conn, dst_server_id, user)
            if not dst_perms.get("vol_copy"):
                raise ToolboxError(
                    "PERMISSION_DENIED",
                    "您在目标服务器没有「跨服务器复制卷」权限",
                    status_code=403, tool_id=TOOL_ID,
                )
            can_view_source = (
                src_perms.get("vol_view_all")
                or src_perms.get("vol_manage_all")
                or _user_can_access_resource(conn, src_server_id, "volume", src_volume_name, user)
            )
            if not can_view_source:
                raise ToolboxError(
                    "PERMISSION_DENIED",
                    "您没有访问源卷的权限，无法复制该卷",
                    status_code=403,
                    tool_id=TOOL_ID,
                )

        src_row = _get_server_row(conn, src_server_id)
        dst_row = _get_server_row(conn, dst_server_id)

        # 目标服务器卷配额校验：复制会在目标服务器新增卷并占用配额，配额已满则禁止作为复制目标
        if user.role != "admin":
            _enforce_volume_quota(conn, dst_row, user)

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

    # 记录目标卷创建者+所有者（对齐镜像 copy_image 中的 _record_resource_creator）
    # size_gb 由 list_volumes / refresh_volume_sizes 通过 du 实测填充，此处不预设
    with get_connection() as conn:
        _record_resource_creator(conn, dst_server_id, "volume", dst_volume_name, user.id)

    now = _now()
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
    判断用户是否能查看（访问）某资源：仅 owner/viewer 角色具备查看权。
    creator 和 quota_holder 不是有权限角色，不授予任何权限。
    """
    row = conn.execute(
        """SELECT COUNT(*) as cnt FROM docker_resource_roles
           WHERE server_id=? AND resource_type=? AND resource_ref=? AND user_id=? AND role IN ('owner','viewer')""",
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

        # 从 _meta 表读取 owner（显示用）
        img_meta = {r["image_ref"]: r["owner_user_id"] for r in
                    conn.execute("SELECT image_ref, owner_user_id FROM docker_images_meta WHERE server_id=?", (server_id,)).fetchall()}
        ctr_meta = {r["container_ref"]: r["owner_user_id"] for r in
                    conn.execute("SELECT container_ref, owner_user_id FROM docker_containers_meta WHERE server_id=?", (server_id,)).fetchall()}
        vol_meta = {r["volume_name"]: r["owner_user_id"] for r in
                    conn.execute("SELECT volume_name, owner_user_id FROM docker_volumes_meta WHERE server_id=?", (server_id,)).fetchall()}

    def _merge_roles(rtype: str, ref: str, legacy_owner: str | None) -> dict:
        """合并新角色表和旧 owner 字段"""
        base = roles_map.get((rtype, ref), {"ownerUserIds": [], "viewerUserIds": [], "creatorUserId": None, "quotaHolderUserIds": []})
        # 旧数据只有 owner_user_id，将其归入 ownerUserIds（如果新表没有对应记录）
        if legacy_owner and legacy_owner not in base["ownerUserIds"]:
            base["ownerUserIds"] = [legacy_owner] + base["ownerUserIds"]
        platform_managed = bool(base["ownerUserIds"] or base["viewerUserIds"] or base["creatorUserId"] or legacy_owner)
        return {**base, "platformManaged": platform_managed}

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
) -> dict[str, Any]:
    """
    设置服务器资源的多角色绑定（管理员专用）。
    - owner_user_ids: 所有者列表（可多人）
    - viewer_user_ids: 查看者列表（可多人）
    - creator_user_id: 创建者（唯一，传 "" 表示不设置）
    - quota_holder_user_ids: 配额占用者（可多人，无需是所有者）

    逻辑：
    - 创建者默认拥有所有者权限（即同时出现在 owner 角色中）
    - 如果管理员从 owner_user_ids 中移除创建者，创建者失去所有者权限（但 creator 角色保留）
    - viewer 不需要额外 owner 权限
    - quota_holder 不需要是 owner（配额占用者独立于所有者）
    """
    init_docker_database()
    if user.role != "admin":
        raise ToolboxError("ADMIN_REQUIRED", "仅管理员可以分配资源角色", status_code=403, tool_id=TOOL_ID)
    if resource_type not in {"container", "image", "volume"}:
        raise ToolboxError("INVALID_TYPE", "资源类型无效，应为 container/image/volume", status_code=400, tool_id=TOOL_ID)

    if quota_holder_user_ids is None:
        quota_holder_user_ids = []

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

        # 同步 _meta 表（使用第一个 owner 用于查询）
        first_owner = owner_user_ids[0] if owner_user_ids else ""
        _sync_legacy_meta(conn, server_id, resource_type, resource_ref, first_owner, now)

    return {
        "success": True,
        "serverId": server_id,
        "resourceType": resource_type,
        "resourceRef": resource_ref,
        "ownerUserIds": owner_user_ids,
        "viewerUserIds": viewer_user_ids,
        "quotaHolderUserIds": quota_holder_user_ids,
        "creatorUserId": effective_creator or None,
        "assignedAt": now,
    }


def _sync_legacy_meta(conn: sqlite3.Connection, server_id: str, resource_type: str, resource_ref: str, owner_user_id: str, now: str) -> None:
    """同步到 _meta 表，用于所有权过滤查询。"""
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


# assign_resource_owner（内部转为新逻辑）
def assign_resource_owner(
    server_id: str,
    resource_type: str,
    resource_ref: str,
    owner_user_id: str,
    user: User,
) -> dict[str, Any]:
    """单 owner 分配，内部转为多角色逻辑。"""
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
    - 卷配额（总量 / 已用 / 剩余；按配额占用者均分计算）
    - 挂载路径的磁盘空间（总量 / 剩余 / 自己已用 / 他人已用）
    - 用户可用的 CUDA GPU 列表
    - 镜像配额（总量 / 已用 / 剩余；按配额占用者均分计算）
    """
    init_docker_database()
    with get_connection() as conn:
        _require_server_visible(conn, server_id, user)
        srv_row = _get_server_row(conn, server_id)
        perms = _get_user_perms(conn, server_id, user)

        # ---------- 卷配额（配额占用者均分） ----------
        vol_usage = _calc_user_volume_usage(conn, srv_row, user)

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

        # ---------- 镜像配额（配额占用者均分） ----------
        img_usage = _calc_user_image_usage(conn, srv_row, user)

        # ---------- 容器数量配额 ----------
        ctr_usage = _calc_user_container_usage(conn, srv_row, user)

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
            "quotaGb": vol_usage["quotaGb"],
            "usedSelfGb": vol_usage["usedSelfGb"],
            "usedTotalGb": vol_usage["usedTotalGb"],
            "remainingGb": vol_usage["remainingGb"],
            "countSelf": vol_usage["countSelf"],
            "countTotal": vol_usage["countTotal"],
        },
        "image": {
            "quotaGb": img_usage["quotaGb"],
            "usedSelfGb": img_usage["usedSelfGb"],
            "usedTotalGb": img_usage["usedTotalGb"],
            "remainingGb": img_usage["remainingGb"],
            "countSelf": img_usage["countSelf"],
            "countTotal": img_usage["countTotal"],
        },
        "container": {
            "quotaNum": ctr_usage["quotaNum"],
            "usedSelf": ctr_usage["usedSelf"],
            "usedTotal": ctr_usage["usedTotal"],
            "remaining": ctr_usage["remaining"],
        },
        "paths": paths_info,
        "cuda": {
            "serverHasCuda": cuda_available,
            "allowedGpuIndices": allowed_gpu_indices,
            "availableGpus": available_gpus,
            "totalGpuCount": gpu_total_count,
            "allGpuInfo": server_gpu_info,
        },
    }
