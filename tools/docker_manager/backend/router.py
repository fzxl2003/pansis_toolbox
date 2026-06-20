"""
Docker 多租户管理工具 - FastAPI 路由层
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from backend.app.core.security import require_user

from tools.docker_manager.backend.service import (
    add_server,
    assign_resource_owner,
    assign_resource_roles,
    container_action,
    copy_image,
    copy_volume,
    create_container_compose,
    create_container_run,
    create_container_run_raw,
    create_from_template,
    create_template,
    create_volume,
    delete_image,
    delete_server,
    delete_template,
    delete_volume,
    get_container_detail,
    get_container_logs,
    update_restart_policy,
    get_my_quota,
    get_template,
    get_user_perms_for_user,
    get_volume_detail,
    list_containers,
    list_images,
    list_my_owned_resources,
    list_server_permissions,
    list_server_resources,
    list_servers,
    list_templates,
    list_volumes,
    pull_image,
    rescan_server_cuda,
    get_server_resource_overview,
    set_resource_viewers,
    set_user_permission,
    set_user_perms,
    set_user_quota,
    update_template,
)

router = APIRouter()


# ==============================================================
# 请求体模型
# ==============================================================

class AddServerPayload(BaseModel):
    name: str
    host: str
    port: int = 22
    sshUsername: str
    sshPassword: str


class SetPermissionPayload(BaseModel):
    userId: str
    level: str  # manage | use | view | none


class SetQuotaPayload(BaseModel):
    userId: str
    volumeTotalGb: float = 0.0
    pathWhitelist: list[str] = Field(default_factory=list)
    canCreateContainer: bool = False
    canManageContainer: bool = False


class SetUserPermsPayload(BaseModel):
    userId: str
    # 服务器可见性
    server_visible: bool = False
    # 镜像权限
    img_pull: bool = False
    img_delete: bool = False
    img_copy: bool = False
    # 容器权限
    ctr_view_own: bool = False
    ctr_view_all: bool = False
    ctr_create_run: bool = False
    ctr_create_compose: bool = False
    ctr_create_template: bool = False
    ctr_manage_own: bool = False
    ctr_manage_all: bool = False
    ctr_path_whitelist: list[str] = Field(default_factory=list)
    # 卷权限
    vol_create: bool = False
    vol_delete_own: bool = False
    vol_delete_all: bool = False
    vol_copy: bool = False
    vol_quota_gb: float = 0.0
    # 模板权限
    tpl_use: bool = False
    tpl_create: bool = False
    tpl_edit: bool = False
    # CUDA 权限（允许使用的显卡序号列表）
    cuda_gpu_indices: list[int] = Field(default_factory=list)


class PullImagePayload(BaseModel):
    imageRef: str


class CopyImagePayload(BaseModel):
    srcServerId: str
    dstServerId: str
    imageRef: str


class CreateContainerRunPayload(BaseModel):
    name: str = ""
    image: str
    command: str = ""
    ports: list[str] = Field(default_factory=list)
    volumes: list[str] = Field(default_factory=list)
    envs: list[str] = Field(default_factory=list)
    network: str = ""
    restart: str = ""
    gpus: str = ""
    extra_args: str = ""


class RunRawPayload(BaseModel):
    command: str  # 完整的 docker run ... 命令


class CreateContainerComposePayload(BaseModel):
    yamlContent: str
    projectName: str = ""


class ContainerActionPayload(BaseModel):
    action: str  # start | stop | restart | remove


class CreateVolumePayload(BaseModel):
    name: str
    sizeGb: float = 0.0


class CopyVolumePayload(BaseModel):
    srcServerId: str
    srcVolumeName: str
    dstServerId: str
    dstVolumeName: str


class CreateTemplatePayload(BaseModel):
    name: str
    description: str = ""
    category: str = "general"
    docContent: str = ""
    config: dict[str, Any] = Field(default_factory=dict)
    isPublic: bool = True


class UpdateTemplatePayload(BaseModel):
    name: str | None = None
    description: str | None = None
    category: str | None = None
    docContent: str | None = None
    config: dict[str, Any] | None = None
    isPublic: bool | None = None


class CreateFromTemplatePayload(BaseModel):
    templateId: str
    overrides: dict[str, Any] = Field(default_factory=dict)
    gpus: str = ""  # GPU 挂载参数，e.g. "all" or "\"device=0,1\""


# ==============================================================
# 服务器管理路由
# ==============================================================

@router.get("/servers")
def list_servers_route(request: Request) -> dict:
    user = require_user(request)
    return {"servers": list_servers(user)}


@router.post("/servers")
def add_server_route(request: Request, payload: AddServerPayload) -> dict:
    user = require_user(request)
    return {"server": add_server(payload.model_dump(), user)}


@router.delete("/servers/{server_id}")
def delete_server_route(request: Request, server_id: str) -> dict:
    user = require_user(request)
    delete_server(server_id, user)
    return {"deleted": True}


@router.get("/servers/{server_id}/permissions")
def list_permissions_route(request: Request, server_id: str) -> dict:
    user = require_user(request)
    return {"permissions": list_server_permissions(server_id, user)}


@router.put("/servers/{server_id}/permissions")
def set_permission_route(request: Request, server_id: str, payload: SetPermissionPayload) -> dict:
    user = require_user(request)
    result = set_user_permission(server_id, payload.userId, payload.level, user)
    return {"permission": result}


@router.put("/servers/{server_id}/quotas")
def set_quota_route(request: Request, server_id: str, payload: SetQuotaPayload) -> dict:
    user = require_user(request)
    result = set_user_quota(
        server_id,
        payload.userId,
        {
            "volumeTotalGb": payload.volumeTotalGb,
            "pathWhitelist": payload.pathWhitelist,
            "canCreateContainer": payload.canCreateContainer,
            "canManageContainer": payload.canManageContainer,
        },
        user,
    )
    return {"quota": result}


@router.get("/servers/{server_id}/user-perms")
def get_user_perms_route(request: Request, server_id: str, userId: str) -> dict:
    """获取指定用户对该服务器的细粒度权限"""
    user = require_user(request)
    return get_user_perms_for_user(server_id, userId, user)


@router.put("/servers/{server_id}/user-perms")
def set_user_perms_route(request: Request, server_id: str, payload: SetUserPermsPayload) -> dict:
    """设置指定用户对该服务器的细粒度权限"""
    user = require_user(request)
    perms_dict = payload.model_dump(exclude={"userId"})
    return set_user_perms(server_id, payload.userId, perms_dict, user)


@router.get("/servers/{server_id}/my-quota")
def my_quota_route(request: Request, server_id: str) -> dict:
    user = require_user(request)
    return {"quota": get_my_quota(server_id, user)}


# ==============================================================
# 镜像管理路由
# ==============================================================

@router.get("/servers/{server_id}/images")
def list_images_route(request: Request, server_id: str) -> dict:
    user = require_user(request)
    return {"images": list_images(server_id, user)}


@router.post("/servers/{server_id}/images/pull")
def pull_image_route(request: Request, server_id: str, payload: PullImagePayload) -> dict:
    user = require_user(request)
    return pull_image(server_id, payload.imageRef, user)


@router.delete("/servers/{server_id}/images/{image_ref:path}")
def delete_image_route(request: Request, server_id: str, image_ref: str, force: bool = False) -> dict:
    user = require_user(request)
    return delete_image(server_id, image_ref, user, force=force)


@router.post("/images/copy")
def copy_image_route(request: Request, payload: CopyImagePayload) -> dict:
    user = require_user(request)
    return copy_image(payload.srcServerId, payload.dstServerId, payload.imageRef, user)


# ==============================================================
# 容器管理路由
# ==============================================================

@router.get("/servers/{server_id}/containers")
def list_containers_route(request: Request, server_id: str, all: bool = True) -> dict:
    user = require_user(request)
    return {"containers": list_containers(server_id, user, all_containers=all)}


@router.post("/servers/{server_id}/containers/run")
def create_run_route(request: Request, server_id: str, payload: CreateContainerRunPayload) -> dict:
    user = require_user(request)
    return create_container_run(server_id, payload.model_dump(), user)


@router.post("/servers/{server_id}/containers/run-raw")
def create_run_raw_route(request: Request, server_id: str, payload: RunRawPayload) -> dict:
    """直接执行用户提供的完整 docker run 命令（命令行模式）"""
    user = require_user(request)
    return create_container_run_raw(server_id, payload.command, user)


@router.post("/servers/{server_id}/containers/compose")
def create_compose_route(request: Request, server_id: str, payload: CreateContainerComposePayload) -> dict:
    user = require_user(request)
    return create_container_compose(server_id, payload.yamlContent, user, payload.projectName)


@router.post("/servers/{server_id}/containers/from-template")
def create_from_template_route(request: Request, server_id: str, payload: CreateFromTemplatePayload) -> dict:
    user = require_user(request)
    return create_from_template(server_id, payload.templateId, payload.overrides, user, gpus=payload.gpus)


@router.post("/servers/{server_id}/containers/{container_id}/action")
def container_action_route(request: Request, server_id: str, container_id: str, payload: ContainerActionPayload) -> dict:
    user = require_user(request)
    return container_action(server_id, container_id, payload.action, user)


@router.get("/servers/{server_id}/containers/{container_id}/detail")
def container_detail_route(request: Request, server_id: str, container_id: str) -> dict:
    """获取容器详情（docker inspect 信息）"""
    user = require_user(request)
    return get_container_detail(server_id, container_id, user)


class UpdateRestartPayload(BaseModel):
    policy: str


@router.put("/servers/{server_id}/containers/{container_id}/restart-policy")
def update_restart_policy_route(request: Request, server_id: str, container_id: str, payload: UpdateRestartPayload) -> dict:
    """更新容器重启策略"""
    user = require_user(request)
    return update_restart_policy(server_id, container_id, payload.policy, user)


@router.get("/servers/{server_id}/containers/{container_id}/logs")
def container_logs_route(request: Request, server_id: str, container_id: str, tail: int = 200) -> dict:
    user = require_user(request)
    return get_container_logs(server_id, container_id, user, tail=tail)


# ==============================================================
# 模板管理路由
# ==============================================================

@router.get("/templates")
def list_templates_route(request: Request) -> dict:
    user = require_user(request)
    return {"templates": list_templates(user)}


@router.post("/templates")
def create_template_route(request: Request, payload: CreateTemplatePayload) -> dict:
    user = require_user(request)
    return {"template": create_template(payload.model_dump(), user)}


@router.get("/templates/{template_id}")
def get_template_route(request: Request, template_id: str) -> dict:
    user = require_user(request)
    return {"template": get_template(template_id, user)}


@router.put("/templates/{template_id}")
def update_template_route(request: Request, template_id: str, payload: UpdateTemplatePayload) -> dict:
    user = require_user(request)
    data = {k: v for k, v in payload.model_dump().items() if v is not None}
    return {"template": update_template(template_id, data, user)}


@router.delete("/templates/{template_id}")
def delete_template_route(request: Request, template_id: str) -> dict:
    user = require_user(request)
    delete_template(template_id, user)
    return {"deleted": True}


# ==============================================================
# 卷管理路由
# ==============================================================

@router.get("/servers/{server_id}/volumes")
def list_volumes_route(request: Request, server_id: str) -> dict:
    user = require_user(request)
    return list_volumes(server_id, user)


@router.get("/servers/{server_id}/volumes/{volume_name}/detail")
def get_volume_detail_route(request: Request, server_id: str, volume_name: str) -> dict:
    """获取卷详情（角色信息 + 挂载容器列表）"""
    user = require_user(request)
    return get_volume_detail(server_id, volume_name, user)


@router.post("/servers/{server_id}/volumes")
def create_volume_route(request: Request, server_id: str, payload: CreateVolumePayload) -> dict:
    user = require_user(request)
    return create_volume(server_id, payload.name, payload.sizeGb, user)


@router.delete("/servers/{server_id}/volumes/{volume_name}")
def delete_volume_route(request: Request, server_id: str, volume_name: str) -> dict:
    user = require_user(request)
    return delete_volume(server_id, volume_name, user)


@router.post("/volumes/copy")
def copy_volume_route(request: Request, payload: CopyVolumePayload) -> dict:
    """跨服务器（或同服务器）复制卷数据（tar 流式）"""
    user = require_user(request)
    return copy_volume(
        payload.srcServerId,
        payload.srcVolumeName,
        payload.dstServerId,
        payload.dstVolumeName,
        user,
    )


# ==============================================================
# 资源所有者管理路由（管理员专用）
# ==============================================================

class AssignResourceOwnerPayload(BaseModel):
    """兼容旧接口：单 owner 分配"""
    resourceType: str   # container | image | volume
    resourceRef: str    # 容器名/镜像 repo:tag/卷名
    ownerUserId: str    # 目标用户 ID，传 "" 表示取消分配


class AssignResourceRolesPayload(BaseModel):
    """新接口：多角色分配"""
    resourceType: str                   # container | image | volume
    resourceRef: str                    # 容器名/镜像 repo:tag/卷名
    ownerUserIds: list[str] = Field(default_factory=list)   # 所有者列表
    viewerUserIds: list[str] = Field(default_factory=list)  # 查看者列表
    creatorUserId: str = ""             # 创建者（唯一），传 "" 表示不设置


@router.get("/servers/{server_id}/resources")
def list_server_resources_route(request: Request, server_id: str) -> dict:
    """列出服务器上所有资源及所有者信息（管理员专用）"""
    user = require_user(request)
    return list_server_resources(server_id, user)


@router.put("/servers/{server_id}/resource-owner")
def assign_resource_owner_route(request: Request, server_id: str, payload: AssignResourceOwnerPayload) -> dict:
    """为服务器上的资源分配所有者（兼容旧接口，管理员专用）"""
    user = require_user(request)
    return assign_resource_owner(
        server_id,
        payload.resourceType,
        payload.resourceRef,
        payload.ownerUserId,
        user,
    )


@router.put("/servers/{server_id}/resource-roles")
def assign_resource_roles_route(request: Request, server_id: str, payload: AssignResourceRolesPayload) -> dict:
    """为服务器上的资源分配多角色（所有者/查看者/创建者，管理员专用）"""
    user = require_user(request)
    return assign_resource_roles(
        server_id,
        payload.resourceType,
        payload.resourceRef,
        payload.ownerUserIds,
        payload.viewerUserIds,
        payload.creatorUserId,
        user,
    )


class SetResourceViewersPayload(BaseModel):
    """Owner 修改资源 viewer 列表"""
    resourceType: str                    # container | image | volume
    resourceRef: str                     # 资源标识
    viewerUserIds: list[str] = Field(default_factory=list)


@router.get("/my-owned-resources")
def list_my_owned_resources_route(request: Request) -> dict:
    """获取当前用户作为 owner 的所有资源及其 viewer 信息（普通用户专用）"""
    user = require_user(request)
    return {"resources": list_my_owned_resources(user)}


@router.put("/servers/{server_id}/resource-viewers")
def set_resource_viewers_route(request: Request, server_id: str, payload: SetResourceViewersPayload) -> dict:
    """资源 owner 修改该资源的查看者列表（owner 或管理员可调用）"""
    user = require_user(request)
    return set_resource_viewers(
        server_id,
        payload.resourceType,
        payload.resourceRef,
        payload.viewerUserIds,
        user,
    )


# ==============================================================
# CUDA 扫描路由
# ==============================================================

@router.post("/servers/{server_id}/rescan-cuda")
def rescan_cuda_route(request: Request, server_id: str) -> dict:
    """重新扫描服务器的 CUDA/GPU 状态（管理员专用）"""
    user = require_user(request)
    return {"server": rescan_server_cuda(server_id, user)}


# ==============================================================
# 服务器资源概览路由（用户侧）
# ==============================================================

@router.get("/servers/{server_id}/resource-overview")
def resource_overview_route(request: Request, server_id: str) -> dict:
    """获取当前用户在服务器上的资源概览（卷配额、路径磁盘、CUDA 权限）"""
    user = require_user(request)
    return get_server_resource_overview(server_id, user)
