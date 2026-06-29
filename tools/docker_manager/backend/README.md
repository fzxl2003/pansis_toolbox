# Docker Manager Backend

Docker Manager 后端按资源和职责拆分。外部路由仍通过 `router.py` 调用 `service.py`，但 `service.py` 只做兼容导出，真实实现分布在下列模块中。

## 代码结构

- `router.py`：FastAPI 路由和请求体模型。这里不放业务逻辑，只做参数解析、鉴权用户获取和调用 service。
- `service.py`：兼容导出层。保留旧的 `tools.docker_manager.backend.service` 导入路径，便于路由和测试稳定。
- `base.py`：公共基础设施。
  - 数据库初始化和迁移：`init_docker_database`
  - SSH 执行：`_ssh_connect` / `_ssh_exec`
  - 权限基础：`_get_user_perms` / `_require_server_visible` / `_require_admin`
  - 资源角色基础：`_record_resource_creator` / `_user_can_access_resource` / `_user_can_manage_resource`
  - Docker inventory 缓存：`refresh_docker_df_cache` / `refresh_all_docker_df_caches`
- `servers.py`：服务器增删查、连接状态、用户权限配置和个人配额。
- `images.py`：镜像列表、拉取、删除、跨服务器复制、镜像配额计算。
- `containers.py`：容器列表、创建、命令解析、权限预检、生命周期操作、详情和日志。
- `volumes.py`：卷列表、详情、创建、删除、复制、卷配额计算。
- `templates.py`：模板 CRUD 和从模板创建容器。
- `resources.py`：管理员资源角色管理、资源 owner/viewer/quota_holder 分配、我的资源。
- `system.py`：CUDA 重新扫描和服务器资源概览。

## 调用约定

1. 新 API 优先加到对应资源模块，不再继续堆到 `service.py`。
2. 路由层仍然从 `service.py` 导入业务函数，保持统一入口。
3. 跨资源逻辑放在最贴近主流程的模块中：
   - 容器创建涉及镜像和卷权限，放在 `containers.py`。
   - 配额计算函数分别放在对应资源模块，由 `system.py` 汇总。
4. 后端权限必须强制校验，前端隐藏按钮只是体验优化，不能替代后端判断。

## Docker Inventory 缓存

列表页不直接临时 SSH 查询 Docker，而是读取缓存表。`base.py` 中的刷新器默认每 10 秒运行一次，也可通过刷新按钮触发。

当前 collector：

- `system_df`：执行 `docker system df -v`，维护镜像、容器、卷的基础列表和大小。
- `container_ports`：执行 `docker ps -a --format '{{.ID}}\t{{.Names}}\t{{.Ports}}'`，补充容器端口缓存。

后续如果需要缓存网络、GPU、inspect 摘要等 SSH 调用，应加入 `base.py` 的 `_docker_inventory_collectors()`，再将结果合并进对应缓存表或新表。

## 数据库结构

核心表：

- `docker_servers`：服务器连接信息、加密后的 SSH 密码、CUDA 扫描结果。
- `docker_user_perms`：用户在某服务器上的细粒度权限和配额。
- `docker_resource_roles`：资源多角色关系。
  - `resource_type`：`container` / `image` / `volume`
  - `role`：`owner` / `viewer` / `creator` / `quota_holder`
- `docker_images_meta` / `docker_containers_meta` / `docker_volumes_meta`：旧版 owner 元数据和快速过滤辅助。
- `docker_container_resource_cache`：容器使用的镜像/卷关系缓存，用于权限继承。
- `docker_templates`：容器模板元数据。

Inventory 缓存表：

- `docker_df_cache`：每台服务器最近一次 inventory 刷新状态、时间、原始 `docker system df -v` 文本和错误信息。
- `docker_df_images`：镜像缓存，包含 repo/tag/id/大小/shared/unique/容器引用数。
- `docker_df_containers`：容器缓存，包含 id/image/command/status/name/size/local_volumes/ports。
- `docker_df_volumes`：卷缓存，包含 name/links/size。

权限要点：

- `server_visible` 是访问任何服务器资源的前置条件。
- `*_use` 表示允许使用/查看该类资源，但只能看到自己有 `owner/viewer` 的资源。
- `*_view_all` 表示查看该服务器该类型全部资源。
- `*_manage_all` 表示管理该类型全部资源，并隐含查看该类型全部资源。
- `ctr_view_all` 不应绕过卷或镜像的查看权限；跨资源继承必须通过 `docker_container_resource_cache` 中明确的资源关系。
