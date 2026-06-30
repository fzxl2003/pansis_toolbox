"""
Docker 多租户管理工具 - 服务层兼容导出。

实际实现已按功能拆分到 backend/*.py：
base / servers / images / containers / templates / volumes / resources / system。
保留本模块是为了兼容既有 router 和测试中的 tools.docker_manager.backend.service 导入。
"""
from __future__ import annotations

from . import base as _base
from . import servers as _servers
from . import images as _images
from . import containers as _containers
from . import templates as _templates
from . import volumes as _volumes
from . import resources as _resources
from . import system as _system

for _module in (_base, _servers, _images, _containers, _templates, _volumes, _resources, _system):
    globals().update({k: v for k, v in vars(_module).items() if not k.startswith("__")})

_PUBLIC_MODULES = (_base, _servers, _images, _containers, _templates, _volumes, _resources, _system)
__all__ = sorted({k for m in _PUBLIC_MODULES for k in vars(m) if not k.startswith("__")})
