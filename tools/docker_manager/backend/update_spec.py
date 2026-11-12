"""Validate and normalize deployment recipes for automatic image updates."""
from __future__ import annotations

import re
import shlex
from dataclasses import dataclass
from typing import Literal

import yaml
from pydantic import BaseModel, Field, field_validator

from backend.app.core.errors import ToolboxError

NAME = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9_.-]*")
IMAGE = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9_./:\[\]-]*")
DIGEST = re.compile(r"sha256:[0-9a-f]{64}")
CONTAINER_ID = re.compile(r"[0-9a-f]{64}")

VALUE_FLAGS = set("""
--add-host --annotation --blkio-weight --blkio-weight-device --cap-add --cap-drop
--cgroup-parent --cgroupns --cidfile --cpu-count --cpu-percent --cpu-period --cpu-quota
--cpu-rt-period --cpu-rt-runtime --cpu-shares --cpus --cpuset-cpus --cpuset-mems
--detach-keys --device --device-cgroup-rule --device-read-bps --device-read-iops
--device-write-bps --device-write-iops --dns --dns-option --dns-search --domainname
--entrypoint --env --env-file --expose --gpus --group-add --health-cmd --health-interval
--health-retries --health-start-interval --health-start-period --health-timeout --hostname
--ip --ip6 --ipc --isolation --kernel-memory --label --label-file --link --link-local-ip
--log-driver --log-opt --mac-address --memory --memory-reservation --memory-swap
--memory-swappiness --mount --name --network --network-alias --oom-score-adj --pid
--pids-limit --platform --publish --pull --restart --runtime --security-opt --shm-size
--stop-signal --stop-timeout --storage-opt --sysctl --tmpfs --ulimit --user --userns
--uts --volume --volume-driver --volumes-from --workdir
""".split())
BOOLEAN_FLAGS = set("""
--detach --init --interactive --no-healthcheck --oom-kill-disable --privileged
--publish-all --read-only --rm --sig-proxy --tty --use-api-socket
""".split())
SHORT_FLAGS = {
    "d": "--detach", "e": "--env", "h": "--hostname", "i": "--interactive",
    "l": "--label", "m": "--memory", "p": "--publish", "P": "--publish-all",
    "t": "--tty", "u": "--user", "v": "--volume", "w": "--workdir",
}
UNSUPPORTED_FLAGS = {
    "--cidfile", "--env-file", "--label-file", "--link", "--publish-all", "--volumes-from",
}


def invalid(message: str) -> ToolboxError:
    return ToolboxError("INVALID_AUTO_UPDATE", message, status_code=422, tool_id="docker_manager")


class UpdateRuleInput(BaseModel):
    mode: Literal["run", "compose"]
    recipe: str = Field(min_length=1, max_length=128_000, repr=False)
    project: str = Field(default="", max_length=120)
    service: str = Field(default="", max_length=120)
    intervalMinutes: int = Field(default=15, ge=1, le=1440)
    recipients: list[str] = Field(default_factory=list, max_length=20)
    selfUpdate: bool = False
    enabled: bool = False

    @field_validator("recipients")
    @classmethod
    def validate_recipients(cls, values: list[str]) -> list[str]:
        addresses = [value.strip() for value in values]
        if any(len(value) > 254 or not re.fullmatch(r"[^\s@,;<>]+@[^\s@,;<>]+\.[^\s@,;<>]+", value)
               for value in addresses):
            raise ValueError("请输入有效的邮件地址")
        return list(dict.fromkeys(addresses))


@dataclass(frozen=True)
class RunRecipe:
    name: str
    image: str
    options: list[tuple[str, str | None]]
    args: list[str]

    def values(self, flag: str) -> list[str]:
        return [value for key, value in self.options if key == flag and value is not None]

    def command(self, image: str, extra_mounts: list[str]) -> list[str]:
        args = ["docker", "run", "--detach", "--pull=never"]
        for flag, value in self.options:
            if flag not in {"--detach", "--pull", "--rm", "--sig-proxy"}:
                args.append(flag if value is None else f"{flag}={value}")
        for mount in extra_mounts:
            args.extend(("--mount", mount))
        return [*args, "--", image, *self.args]


def image_repository(ref: str) -> str:
    ref = ref.split("@", 1)[0]
    return ref.rsplit(":", 1)[0] if ":" in ref.rsplit("/", 1)[-1] else ref


def canonical_repository(ref: str) -> str:
    repository = image_repository(ref)
    host, _, path = repository.partition("/")
    if not path or not any(character in host for character in ".:") and host != "localhost":
        path, host = repository, "docker.io"
    if host in {"index.docker.io", "registry-1.docker.io"}:
        host = "docker.io"
    if host == "docker.io" and "/" not in path:
        path = f"library/{path}"
    return f"{host}/{path}"


def validate_tag(ref: str) -> str:
    if not IMAGE.fullmatch(ref) or len(ref) > 512:
        raise invalid("部署配置需要使用可追踪的仓库镜像标签")
    return ref if ":" in ref.rsplit("/", 1)[-1] else f"{ref}:latest"


def parse_run(command: str) -> RunRecipe:
    try:
        tokens = shlex.split(re.sub(r"\\\r?\n", "", command))
    except ValueError as exc:
        raise invalid("docker run 命令的引号不完整") from exc
    if tokens[:2] != ["docker", "run"]:
        raise invalid("请输入一条 docker run 命令")
    options: list[tuple[str, str | None]] = []
    index = 2
    while index < len(tokens) and tokens[index].startswith("-"):
        token = tokens[index]
        index += 1
        if token == "--":
            break
        key, equals, value = token.partition("=")
        if key.startswith("--"):
            flags = [key]
        elif len(key) > 2 and SHORT_FLAGS.get(key[1]) in VALUE_FLAGS:
            flags, equals, value = [SHORT_FLAGS[key[1]]], "=", token[2:]
        else:
            flags = [SHORT_FLAGS.get(char, "") for char in key[1:]]
        for flag in flags:
            if flag in VALUE_FLAGS and flag not in UNSUPPORTED_FLAGS:
                if not equals:
                    if index >= len(tokens):
                        raise invalid("docker run 参数缺少值")
                    value = tokens[index]
                    index += 1
                if flag == "--env" and "=" not in value:
                    raise invalid("请在 docker run 命令中明确指定环境变量值")
                options.append((flag, value))
            elif flag in BOOLEAN_FLAGS and flag not in UNSUPPORTED_FLAGS:
                if equals and value not in {"true", "false"}:
                    raise invalid("docker run 布尔参数需要 true 或 false")
                if flag == "--rm" and value != "false":
                    raise invalid("自动更新需要保留旧容器以便回滚，请移除 --rm")
                options.append((flag, value if equals else None))
            else:
                raise invalid("docker run 包含自动更新无法验证的参数")
    names = [value for key, value in options if key == "--name"]
    if len(names) != 1 or not names[0] or not NAME.fullmatch(names[0]) or index >= len(tokens):
        raise invalid("docker run 需要唯一的 --name 和镜像")
    return RunRecipe(names[0], validate_tag(tokens[index]), options, tokens[index + 1:])


def parse_compose(recipe: str, project: str, service: str) -> str:
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]*", project) or not NAME.fullmatch(service):
        raise invalid("请输入原 Compose 项目名和服务名")
    try:
        config = yaml.safe_load(recipe)
    except (yaml.YAMLError, RecursionError) as exc:
        raise invalid("Compose YAML 无法解析") from exc
    if not isinstance(config, dict) or "include" in config:
        raise invalid("请输入独立的 Compose YAML")
    services = config.get("services")
    if not isinstance(services, dict) or not isinstance(services.get(service), dict):
        raise invalid("Compose YAML 中没有指定服务")
    for definition in services.values():
        if not isinstance(definition, dict) or any(key in definition for key in ("env_file", "extends", "label_file")):
            raise invalid("请将外部配置合并为独立的 Compose YAML")
        mounts = definition.get("volumes") or []
        if not isinstance(mounts, list):
            raise invalid("Compose 卷配置无效")
        for mount in mounts:
            if isinstance(mount, str):
                source = mount.split(":", 1)[0] if ":" in mount else ""
                if source and not (source.startswith("/") or NAME.fullmatch(source)):
                    raise invalid("Compose bind 挂载需要绝对路径")
            elif isinstance(mount, dict) and mount.get("type") == "bind":
                if not str(mount.get("source", "")).startswith("/"):
                    raise invalid("Compose bind 挂载需要绝对路径")
    for section in ("configs", "secrets"):
        resources = config.get(section) or {}
        if not isinstance(resources, dict) or any(
            not isinstance(value, dict) or "file" in value or "environment" in value
            for value in resources.values()
        ):
            raise invalid("独立 Compose YAML 需要内联或外部已存在的配置及密钥")
    image = services[service].get("image")
    if not isinstance(image, str) or "build" in services[service]:
        raise invalid("指定服务需要可追踪的镜像标签")
    return validate_tag(image)


def rule_target(payload: UpdateRuleInput) -> tuple[str, str]:
    if "\0" in payload.recipe:
        raise invalid("部署配置包含无效字符")
    if payload.mode == "run":
        spec = parse_run(payload.recipe)
        return f"run:{spec.name}", spec.image
    image = parse_compose(payload.recipe, payload.project, payload.service)
    return f"compose:{payload.project}:{payload.service}", image
