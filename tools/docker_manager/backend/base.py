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
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Generator

from cryptography.fernet import Fernet, InvalidToken

from backend.app.core.config import get_settings
from backend.app.core.errors import ToolboxError
from backend.app.db.database import get_connection
from backend.app.services.auth_service import User
from backend.app.services import ssh_connection_service
from backend.app.services.ssh_connection_service import SSHConnectionSpec

TOOL_ID = "docker_manager"
DF_CACHE_REFRESH_INTERVAL_SECONDS = 10

# 模板 MD 文件存储目录（放在 storage 数据目录下，不混入代码文件夹）
TEMPLATES_DIR = get_settings().storage_dir / "data" / "tools" / "docker_manager" / "templates"
TEMPLATES_DIR.mkdir(parents=True, exist_ok=True)


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
_DF_CACHE_THREAD_STARTED = False
_DF_CACHE_THREAD_LOCK = threading.Lock()


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
                    updated_at TEXT NOT NULL,
                    deploy_type TEXT NOT NULL DEFAULT 'run',
                    raw_content TEXT NOT NULL DEFAULT '',
                    variables_json TEXT NOT NULL DEFAULT '[]'
                );

                -- 模板多角色关系表（参照容器资源角色模型）
                -- 模板不绑定具体服务器，owner 可编辑/删除模板并管理查看者，viewer 可查看并使用模板
                CREATE TABLE IF NOT EXISTS docker_template_roles (
                    id TEXT PRIMARY KEY,
                    template_id TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    role TEXT NOT NULL,          -- owner | viewer
                    assigned_at TEXT NOT NULL,
                    UNIQUE(template_id, user_id, role),
                    FOREIGN KEY(template_id) REFERENCES docker_templates(id) ON DELETE CASCADE
                );

                CREATE INDEX IF NOT EXISTS idx_template_roles_template ON docker_template_roles(template_id);
                CREATE INDEX IF NOT EXISTS idx_template_roles_user ON docker_template_roles(user_id);

                CREATE TABLE IF NOT EXISTS docker_volumes_meta (
                    id TEXT PRIMARY KEY,
                    volume_name TEXT NOT NULL,
                    server_id TEXT NOT NULL,
                    owner_user_id TEXT NOT NULL,
                    size_gb REAL NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    is_public INTEGER NOT NULL DEFAULT 0,
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
                    is_public INTEGER NOT NULL DEFAULT 0,
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
                    is_public INTEGER NOT NULL DEFAULT 0,
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

                CREATE TABLE IF NOT EXISTS docker_df_cache (
                    server_id TEXT PRIMARY KEY,
                    refreshed_at TEXT NOT NULL,
                    raw_text TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL DEFAULT 'ok',
                    error TEXT NOT NULL DEFAULT '',
                    FOREIGN KEY(server_id) REFERENCES docker_servers(id)
                );

                CREATE TABLE IF NOT EXISTS docker_df_images (
                    server_id TEXT NOT NULL,
                    image_ref TEXT NOT NULL,
                    repository TEXT NOT NULL DEFAULT '',
                    tag TEXT NOT NULL DEFAULT '',
                    image_id TEXT NOT NULL DEFAULT '',
                    created TEXT NOT NULL DEFAULT '',
                    size TEXT NOT NULL DEFAULT '',
                    shared_size TEXT NOT NULL DEFAULT '',
                    unique_size TEXT NOT NULL DEFAULT '',
                    containers INTEGER NOT NULL DEFAULT 0,
                    size_gb REAL NOT NULL DEFAULT 0,
                    refreshed_at TEXT NOT NULL,
                    PRIMARY KEY(server_id, image_ref),
                    FOREIGN KEY(server_id) REFERENCES docker_servers(id)
                );

                CREATE TABLE IF NOT EXISTS docker_df_containers (
                    server_id TEXT NOT NULL,
                    container_id TEXT NOT NULL,
                    image TEXT NOT NULL DEFAULT '',
                    command TEXT NOT NULL DEFAULT '',
                    local_volumes INTEGER NOT NULL DEFAULT 0,
                    size TEXT NOT NULL DEFAULT '',
                    ports TEXT NOT NULL DEFAULT '',
                    created TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL DEFAULT '',
                    names TEXT NOT NULL DEFAULT '',
                    size_gb REAL NOT NULL DEFAULT 0,
                    refreshed_at TEXT NOT NULL,
                    PRIMARY KEY(server_id, container_id),
                    FOREIGN KEY(server_id) REFERENCES docker_servers(id)
                );

                CREATE TABLE IF NOT EXISTS docker_df_volumes (
                    server_id TEXT NOT NULL,
                    volume_name TEXT NOT NULL,
                    links INTEGER NOT NULL DEFAULT 0,
                    size TEXT NOT NULL DEFAULT '',
                    size_gb REAL NOT NULL DEFAULT 0,
                    refreshed_at TEXT NOT NULL,
                    PRIMARY KEY(server_id, volume_name),
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
            df_ctr_cols = {r[1] for r in conn.execute("PRAGMA table_info(docker_df_containers)").fetchall()}
            if "ports" not in df_ctr_cols:
                conn.execute("ALTER TABLE docker_df_containers ADD COLUMN ports TEXT NOT NULL DEFAULT ''")
            # docker_templates 新列迁移
            tpl_cols = {r[1] for r in conn.execute("PRAGMA table_info(docker_templates)").fetchall()}
            if "deploy_type" not in tpl_cols:
                conn.execute("ALTER TABLE docker_templates ADD COLUMN deploy_type TEXT NOT NULL DEFAULT 'run'")
            if "raw_content" not in tpl_cols:
                conn.execute("ALTER TABLE docker_templates ADD COLUMN raw_content TEXT NOT NULL DEFAULT ''")
            if "variables_json" not in tpl_cols:
                conn.execute("ALTER TABLE docker_templates ADD COLUMN variables_json TEXT NOT NULL DEFAULT '[]'")
            # is_public 列迁移（卷、镜像、容器元数据表）
            vol_meta_cols = {r[1] for r in conn.execute("PRAGMA table_info(docker_volumes_meta)").fetchall()}
            if "is_public" not in vol_meta_cols:
                conn.execute("ALTER TABLE docker_volumes_meta ADD COLUMN is_public INTEGER NOT NULL DEFAULT 0")
            img_meta_cols = {r[1] for r in conn.execute("PRAGMA table_info(docker_images_meta)").fetchall()}
            if "is_public" not in img_meta_cols:
                conn.execute("ALTER TABLE docker_images_meta ADD COLUMN is_public INTEGER NOT NULL DEFAULT 0")
            ctr_meta_cols_public = {r[1] for r in conn.execute("PRAGMA table_info(docker_containers_meta)").fetchall()}
            if "is_public" not in ctr_meta_cols_public:
                conn.execute("ALTER TABLE docker_containers_meta ADD COLUMN is_public INTEGER NOT NULL DEFAULT 0")
        _DB_INITIALIZED = True
    _start_df_cache_refresher()


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
    return ssh_connection_service.borrow_client(_ssh_spec(server_row, timeout=15))


def _ssh_exec(client, cmd: str, timeout: int = 60) -> tuple[str, str, int]:
    """执行命令，返回 (stdout, stderr, exit_code)"""
    try:
        stdin, stdout, stderr = client.exec_command(cmd, timeout=timeout)
        exit_code = stdout.channel.recv_exit_status()
        return stdout.read().decode("utf-8", errors="replace"), stderr.read().decode("utf-8", errors="replace"), exit_code
    except Exception:
        if hasattr(client, "invalidate"):
            client.invalidate()
        raise


def _ssh_spec(server_row: sqlite3.Row, timeout: int = 15) -> SSHConnectionSpec:
    return SSHConnectionSpec(
        tool_id=TOOL_ID,
        server_id=server_row["id"],
        host=server_row["host"],
        port=int(server_row["port"]),
        username=server_row["ssh_username"],
        auth_fingerprint=ssh_connection_service.auth_fingerprint(server_row["ssh_password_encrypted"]),
        password=_decrypt(server_row["ssh_password_encrypted"]),
        connect_timeout=timeout,
        connect_error_code="SSH_CONNECT_FAILED",
        missing_dependency_code="MISSING_DEP",
        missing_dependency_message="缺少 paramiko 依赖",
    )


def _get_server_row(conn: sqlite3.Connection, server_id: str) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM docker_servers WHERE id = ?", (server_id,)).fetchone()
    if row is None:
        raise ToolboxError("SERVER_NOT_FOUND", "服务器不存在", status_code=404, tool_id=TOOL_ID)
    return row


# ==============================================================
# docker system df -v 缓存
# ==============================================================

def _start_df_cache_refresher() -> None:
    global _DF_CACHE_THREAD_STARTED
    with _DF_CACHE_THREAD_LOCK:
        if _DF_CACHE_THREAD_STARTED:
            return
        _DF_CACHE_THREAD_STARTED = True
        thread = threading.Thread(target=_df_cache_refresher_loop, name="docker-df-cache-refresher", daemon=True)
        thread.start()


def _df_cache_refresher_loop() -> None:
    while True:
        try:
            refresh_all_docker_df_caches()
        except Exception:
            pass
        time.sleep(DF_CACHE_REFRESH_INTERVAL_SECONDS)


def refresh_all_docker_df_caches() -> None:
    init_docker_database()
    with get_connection() as conn:
        rows = conn.execute("SELECT * FROM docker_servers ORDER BY name").fetchall()
    for row in rows:
        try:
            refresh_docker_df_cache(row["id"])
        except Exception:
            continue


def refresh_docker_df_cache(server_id: str, user: User | None = None) -> dict[str, Any]:
    init_docker_database()
    with get_connection() as conn:
        row = _get_server_row(conn, server_id)
        if user is not None:
            _require_server_visible(conn, server_id, user)

    try:
        collector_results = _run_docker_inventory_collectors(row)
        df_result = collector_results["system_df"]
        if df_result["code"] != 0:
            error = df_result["stderr"].strip() or df_result["stdout"].strip()
            _store_docker_df_error(server_id, error)
            raise ToolboxError("DF_REFRESH_FAILED", f"docker system df -v 失败: {error}", status_code=502, tool_id=TOOL_ID)
        parsed = _parse_docker_system_df_v(df_result["stdout"])
        ports_result = collector_results.get("container_ports")
        if ports_result and ports_result["code"] == 0:
            _merge_container_ports(parsed["containers"], _parse_container_ports_output(ports_result["stdout"]))
        else:
            _merge_container_ports(parsed["containers"], _load_cached_container_ports(server_id))
        _store_docker_df_cache(server_id, df_result["stdout"], parsed)
        return {
            "serverId": server_id,
            "refreshedAt": parsed["refreshedAt"],
            "images": len(parsed["images"]),
            "containers": len(parsed["containers"]),
            "volumes": len(parsed["volumes"]),
            "collectors": {
                name: {"ok": result["code"] == 0, "error": result["stderr"].strip()}
                for name, result in collector_results.items()
            },
        }
    except ToolboxError:
        raise
    except Exception as exc:
        _store_docker_df_error(server_id, str(exc))
        raise ToolboxError("DF_REFRESH_FAILED", f"刷新 Docker 缓存失败: {exc}", status_code=502, tool_id=TOOL_ID) from exc


def _docker_inventory_collectors() -> list[dict[str, Any]]:
    return [
        {
            "name": "system_df",
            "cmd": "docker system df -v",
            "timeout": 120,
        },
        {
            "name": "container_ports",
            "cmd": "docker ps -a --format '{{.ID}}\t{{.Names}}\t{{.Ports}}'",
            "timeout": 60,
        },
    ]


def _run_docker_inventory_collectors(server_row: sqlite3.Row) -> dict[str, dict[str, Any]]:
    collectors = _docker_inventory_collectors()
    results: dict[str, dict[str, Any]] = {}

    def run_one(spec: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        client = _ssh_connect(server_row)
        try:
            stdout, stderr, code = _ssh_exec(client, spec["cmd"], timeout=int(spec.get("timeout", 60)))
            return spec["name"], {"stdout": stdout, "stderr": stderr, "code": code}
        finally:
            client.close()

    with ThreadPoolExecutor(max_workers=max(1, len(collectors))) as executor:
        future_map = {executor.submit(run_one, spec): spec["name"] for spec in collectors}
        for future in as_completed(future_map):
            name = future_map[future]
            try:
                result_name, result = future.result()
                results[result_name] = result
            except Exception as exc:
                results[name] = {"stdout": "", "stderr": str(exc), "code": -1}
    return results


def _store_docker_df_error(server_id: str, error: str) -> None:
    now = _now()
    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO docker_df_cache (server_id, refreshed_at, raw_text, status, error)
            VALUES (?, ?, '', 'error', ?)
            ON CONFLICT(server_id) DO UPDATE SET
                refreshed_at=excluded.refreshed_at,
                status=excluded.status,
                error=excluded.error
            """,
            (server_id, now, error),
        )


def _store_docker_df_cache(server_id: str, raw_text: str, parsed: dict[str, Any]) -> None:
    refreshed_at = parsed["refreshedAt"]
    with get_connection() as conn:
        conn.execute("DELETE FROM docker_df_images WHERE server_id=?", (server_id,))
        conn.execute("DELETE FROM docker_df_containers WHERE server_id=?", (server_id,))
        conn.execute("DELETE FROM docker_df_volumes WHERE server_id=?", (server_id,))
        conn.execute(
            """
            INSERT INTO docker_df_cache (server_id, refreshed_at, raw_text, status, error)
            VALUES (?, ?, ?, 'ok', '')
            ON CONFLICT(server_id) DO UPDATE SET
                refreshed_at=excluded.refreshed_at,
                raw_text=excluded.raw_text,
                status=excluded.status,
                error=excluded.error
            """,
            (server_id, refreshed_at, raw_text),
        )
        for img in parsed["images"]:
            conn.execute(
                """
                INSERT INTO docker_df_images
                    (server_id, image_ref, repository, tag, image_id, created, size, shared_size, unique_size, containers, size_gb, refreshed_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    server_id,
                    img["imageRef"],
                    img["repository"],
                    img["tag"],
                    img["imageId"],
                    img["created"],
                    img["size"],
                    img["sharedSize"],
                    img["uniqueSize"],
                    img["containers"],
                    img["sizeGb"],
                    refreshed_at,
                ),
            )
        for ctr in parsed["containers"]:
            conn.execute(
                """
                INSERT INTO docker_df_containers
                    (server_id, container_id, image, command, local_volumes, size, ports, created, status, names, size_gb, refreshed_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    server_id,
                    ctr["containerId"],
                    ctr["image"],
                    ctr["command"],
                    ctr["localVolumes"],
                    ctr["size"],
                    ctr.get("ports", ""),
                    ctr["created"],
                    ctr["status"],
                    ctr["names"],
                    ctr["sizeGb"],
                    refreshed_at,
                ),
            )
        for vol in parsed["volumes"]:
            conn.execute(
                """
                INSERT INTO docker_df_volumes
                    (server_id, volume_name, links, size, size_gb, refreshed_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (server_id, vol["name"], vol["links"], vol["size"], vol["sizeGb"], refreshed_at),
            )
            conn.execute(
                "UPDATE docker_volumes_meta SET size_gb=? WHERE server_id=? AND volume_name=?",
                (vol["sizeGb"], server_id, vol["name"]),
            )


def _refresh_docker_df_cache_best_effort(server_id: str) -> None:
    try:
        refresh_docker_df_cache(server_id)
    except Exception:
        pass


def _parse_docker_system_df_v(text: str) -> dict[str, Any]:
    sections: dict[str, list[str]] = {"images": [], "containers": [], "volumes": []}
    current: str | None = None
    for raw_line in text.splitlines():
        line = raw_line.rstrip()
        if not line.strip():
            continue
        lowered = line.lower()
        if lowered.startswith("images space usage"):
            current = "images"
            continue
        if lowered.startswith("containers space usage"):
            current = "containers"
            continue
        if lowered.startswith("local volumes space usage"):
            current = "volumes"
            continue
        if lowered.startswith("build cache usage"):
            current = None
            continue
        if current is None:
            continue
        if _looks_like_df_header(line):
            continue
        sections[current].append(line)

    images = []
    for line in sections["images"]:
        parsed = _parse_df_image_line(line)
        if parsed:
            images.append(parsed)
    containers = []
    for line in sections["containers"]:
        parsed = _parse_df_container_line(line)
        if parsed:
            containers.append(parsed)
    volumes = []
    for line in sections["volumes"]:
        parsed = _parse_df_volume_line(line)
        if parsed:
            volumes.append(parsed)

    return {"refreshedAt": _now(), "images": images, "containers": containers, "volumes": volumes}


def _parse_container_ports_output(text: str) -> dict[str, str]:
    ports_by_ref: dict[str, str] = {}
    for line in text.splitlines():
        if not line.strip():
            continue
        parts = line.split("\t", 2)
        if len(parts) < 3:
            continue
        container_id = parts[0].strip()
        name = parts[1].strip().lstrip("/")
        ports = parts[2].strip()
        for key in (container_id, container_id[:12], name):
            if key:
                ports_by_ref[key] = ports
    return ports_by_ref


def _load_cached_container_ports(server_id: str) -> dict[str, str]:
    ports_by_ref: dict[str, str] = {}
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT container_id, names, ports FROM docker_df_containers WHERE server_id=?",
            (server_id,),
        ).fetchall()
    for row in rows:
        ports = row["ports"] or ""
        if not ports:
            continue
        for key in (row["container_id"], row["container_id"][:12], row["names"]):
            if key:
                ports_by_ref[key] = ports
    return ports_by_ref


def _merge_container_ports(containers: list[dict[str, Any]], ports_by_ref: dict[str, str]) -> None:
    for ctr in containers:
        container_id = ctr.get("containerId", "")
        name = ctr.get("names", "")
        ctr["ports"] = ports_by_ref.get(container_id) or ports_by_ref.get(container_id[:12]) or ports_by_ref.get(name) or ""


def _looks_like_df_header(line: str) -> bool:
    compact = " ".join(line.upper().split())
    return (
        compact.startswith("REPOSITORY TAG IMAGE ID")
        or compact.startswith("CONTAINER ID IMAGE COMMAND")
        or compact.startswith("VOLUME NAME LINKS SIZE")
    )


def _split_df_columns(line: str) -> list[str]:
    import re
    return [part.strip() for part in re.split(r"\s{2,}", line.strip()) if part.strip()]


def _parse_df_image_line(line: str) -> dict[str, Any] | None:
    cols = _split_df_columns(line)
    if len(cols) < 8:
        return None
    repository, tag, image_id = cols[0], cols[1], cols[2]
    containers_raw = cols[7] if len(cols) > 7 else "0"
    ref = f"{repository}:{tag}" if tag and tag != "<none>" else image_id
    return {
        "repository": repository,
        "tag": tag,
        "imageId": image_id,
        "created": cols[3],
        "size": cols[4],
        "sharedSize": cols[5],
        "uniqueSize": cols[6],
        "containers": _safe_int(containers_raw),
        "imageRef": _normalize_image_ref(ref) if tag and tag != "<none>" else image_id,
        "sizeGb": _parse_size_to_gb(cols[4]),
    }


def _parse_df_container_line(line: str) -> dict[str, Any] | None:
    cols = _split_df_columns(line)
    if len(cols) < 8:
        return None
    names = cols[-1]
    status = cols[-2]
    created = cols[-3]
    size = cols[-4]
    local_volumes = _safe_int(cols[-5])
    command_cols = cols[2:-5]
    return {
        "containerId": cols[0],
        "image": cols[1],
        "command": " ".join(command_cols),
        "localVolumes": local_volumes,
        "size": size,
        "created": created,
        "status": status,
        "names": names,
        "ports": "",
        "sizeGb": _parse_size_to_gb(size.split(" ", 1)[0]),
    }


def _parse_df_volume_line(line: str) -> dict[str, Any] | None:
    cols = _split_df_columns(line)
    if len(cols) < 3:
        return None
    return {
        "name": cols[0],
        "links": _safe_int(cols[1]),
        "size": cols[2],
        "sizeGb": _parse_size_to_gb(cols[2]),
    }


def _safe_int(value: str) -> int:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return 0


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


def _is_resource_public(
    conn: sqlite3.Connection,
    server_id: str,
    resource_type: str,
    resource_ref: str,
) -> bool:
    """判断资源是否被标记为公开（is_public=1）。

    公开资源自动授予所有有权访问服务器的用户查看权限。
    """
    refs = _resource_ref_candidates(resource_type, resource_ref)
    if not refs:
        return False
    placeholders, params = _sql_in_clause(refs)
    if resource_type == "image":
        row = conn.execute(
            f"SELECT is_public FROM docker_images_meta WHERE server_id=? AND image_ref IN ({placeholders}) LIMIT 1",
            [server_id, *params],
        ).fetchone()
    elif resource_type == "container":
        row = conn.execute(
            f"SELECT is_public FROM docker_containers_meta WHERE server_id=? AND container_ref IN ({placeholders}) LIMIT 1",
            [server_id, *params],
        ).fetchone()
    elif resource_type == "volume":
        row = conn.execute(
            f"SELECT is_public FROM docker_volumes_meta WHERE server_id=? AND volume_name IN ({placeholders}) LIMIT 1",
            [server_id, *params],
        ).fetchone()
    else:
        return False
    return bool(row and row["is_public"])


def _get_public_resource_refs(
    conn: sqlite3.Connection,
    server_id: str,
    resource_type: str,
) -> set[str]:
    """批量查询某服务器上某类型所有公开资源的 ref 集合。"""
    public_refs: set[str] = set()
    if resource_type == "image":
        rows = conn.execute(
            "SELECT image_ref FROM docker_images_meta WHERE server_id=? AND is_public=1",
            (server_id,),
        ).fetchall()
        for r in rows:
            public_refs.add(r["image_ref"])
    elif resource_type == "container":
        rows = conn.execute(
            "SELECT container_ref FROM docker_containers_meta WHERE server_id=? AND is_public=1",
            (server_id,),
        ).fetchall()
        for r in rows:
            public_refs.add(r["container_ref"])
    elif resource_type == "volume":
        rows = conn.execute(
            "SELECT volume_name FROM docker_volumes_meta WHERE server_id=? AND is_public=1",
            (server_id,),
        ).fetchall()
        for r in rows:
            public_refs.add(r["volume_name"])
    return public_refs


def _user_can_access_resource(
    conn: sqlite3.Connection,
    server_id: str,
    resource_type: str,
    resource_ref: str,
    user: User,
) -> bool:
    if user.role == "admin":
        return True
    # 公开资源：所有有权访问服务器的用户自动拥有查看权限
    if _is_resource_public(conn, server_id, resource_type, resource_ref):
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
# 资源角色基础工具
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
