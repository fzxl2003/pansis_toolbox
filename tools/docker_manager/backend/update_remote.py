"""Prepare a digest-pinned update and hand it off to the target host."""
from __future__ import annotations

import csv
import hashlib
import json
import posixpath
import re
import shlex
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .update_spec import (
    CONTAINER_ID, DIGEST, RunRecipe, UpdateRuleInput, canonical_repository,
    image_repository, invalid, parse_run,
)

WORKER = Path(__file__).with_name("update_worker.sh")
SPOOL = ".local/state/pansis-toolbox/docker-updates"
VARIABLE = re.compile(r"\$\$|\$\{|\$[a-zA-Z_]")


class RemoteUpdateError(Exception):
    def __init__(self, stage: str):
        super().__init__(stage)
        self.stage = stage


@dataclass(frozen=True)
class Snapshot:
    containers: list[dict[str, Any]]
    platform: str
    image: str
    self_update: bool
    run: RunRecipe | None
    extra_mounts: list[str]
    engine_id: str

    @property
    def ids(self) -> dict[str, str]:
        return {item["Id"]: item["Name"].lstrip("/") for item in self.containers}


@dataclass(frozen=True)
class Candidate:
    ref: str
    manifest: str
    image: str


def execute(client: Any, args: list[str], stage: str, timeout: int = 30) -> str:
    channel = None
    try:
        _, stdout, _ = client.exec_command(shlex.join(args), timeout=timeout)
        channel = stdout.channel
        deadline = time.monotonic() + timeout
        out = bytearray()
        while True:
            if channel.recv_ready():
                out.extend(channel.recv(65_536))
            if channel.recv_stderr_ready():
                channel.recv_stderr(65_536)
            if len(out) > 8 * 1024 * 1024 or time.monotonic() >= deadline:
                raise RemoteUpdateError(stage)
            if channel.exit_status_ready() and not channel.recv_ready() and not channel.recv_stderr_ready():
                break
            if not channel.recv_ready() and not channel.recv_stderr_ready():
                time.sleep(0.025)
        code = channel.recv_exit_status()
    except Exception as exc:
        raise RemoteUpdateError(stage) from exc
    finally:
        if channel is not None:
            channel.close()
    if code:
        raise RemoteUpdateError(stage)
    return out.decode("utf-8", errors="replace").strip()


def shell(client: Any, script: str, *args: str, stage: str) -> str:
    return execute(client, ["bash", "-c", script, "toolbox-update", *args], stage)


def remote_json(client: Any, args: list[str], stage: str) -> Any:
    try:
        return json.loads(execute(client, args, stage))
    except ValueError as exc:
        raise RemoteUpdateError(stage) from exc


def preflight(client: Any, compose: bool) -> str:
    script = """
command -v screen >/dev/null || { printf screen_missing; exit 0; }
command -v flock >/dev/null && command -v timeout >/dev/null || { printf worker_tools_missing; exit 0; }
engine=$(docker info --format '{{.ID}}') && [ -n "$engine" ] || { printf docker_unavailable; exit 0; }
docker buildx version >/dev/null || { printf buildx_missing; exit 0; }
if [ "$1" = compose ]; then
    docker compose version >/dev/null || { printf compose_missing; exit 0; }
fi
printf 'ready %s' "$engine"
"""
    response = shell(client, script, "compose" if compose else "run", stage="preflight")
    if response.startswith("ready "):
        return response.removeprefix("ready ")
    raise RemoteUpdateError(response)


def start_detach_probe(client: Any, probe_id: str, path: str) -> None:
    probe = """
for ((attempt=0; attempt<150; attempt++)); do
    if [ -f "$1/disconnected" ]; then
        sleep 1
        docker info --format '{{.ID}}' > "$1/engine.tmp" && mv -- "$1/engine.tmp" "$1/engine"
        exit
    fi
    sleep 0.1
done
"""
    create_work_dir(client, path, stage="screen_detach")
    execute(client, [
        "screen", "-c", "/dev/null", "-dmS", f"toolbox-{probe_id}",
        "timeout", "-k", "2s", "20s", "/bin/bash", "-c", probe, "toolbox-screen-probe", path,
    ], "screen_detach")


def verify_detach_probe(client: Any, path: str, engine: str) -> None:
    script = """
set -eu
umask 077
touch -- "$1/disconnected"
for ((attempt=0; attempt<60; attempt++)); do
    if [ -f "$1/engine" ]; then
        cat -- "$1/engine"
        exit
    fi
    sleep 0.1
done
exit 1
"""
    if shell(client, script, path, stage="screen_detach") != engine:
        raise RemoteUpdateError("docker_context")


def work_dir(client: Any, run_id: str) -> str:
    home = shell(client, 'printf "%s" "$HOME"', stage="remote_directory")
    if not home.startswith("/") or "\n" in home:
        raise RemoteUpdateError("remote_directory")
    return posixpath.join(home, SPOOL, run_id)


def create_work_dir(client: Any, path: str, *, stage: str) -> None:
    shell(client, 'umask 077; mkdir -p -- "$1" && chmod -- 700 "${1%/*}" "$1"', path, stage=stage)


def upload(client: Any, path: str, files: dict[str, str]) -> None:
    create_work_dir(client, path, stage="remote_directory")
    try:
        with client.open_sftp() as sftp:
            for name, content in files.items():
                with sftp.file(f"{path}/{name}", "w") as handle:
                    handle.write(content.encode("utf-8"))
                sftp.chmod(f"{path}/{name}", 0o600)
    except Exception as exc:
        raise RemoteUpdateError("upload_failed") from exc


def compose_config(client: Any, payload: UpdateRuleInput, path: str) -> dict[str, Any]:
    if any(match[0] != "$$" for match in VARIABLE.finditer(payload.recipe)):
        raise invalid("请先解析 Compose 变量，保存可独立运行的 YAML")
    upload(client, path, {"compose.yaml": payload.recipe})
    return remote_json(client, [
        "docker", "compose", "--env-file", "/dev/null", "--project-directory", path,
        "-p", payload.project, "-f", f"{path}/compose.yaml", "config", "--format", "json",
    ], "compose_config")


def compose_instances(client: Any, project: str, service: str) -> list[dict[str, Any]]:
    ids = execute(client, [
        "docker", "ps", "-aq", "--no-trunc",
        "--filter", f"label=com.docker.compose.project={project}",
        "--filter", f"label=com.docker.compose.service={service}",
    ], "target_missing").splitlines()
    if not ids:
        raise RemoteUpdateError("target_missing")
    instances = remote_json(client, ["docker", "container", "inspect", *ids], "target_missing")
    return [item for item in instances if str((item["Config"].get("Labels") or {}).get(
        "com.docker.compose.oneoff", "")).lower() != "true"]


def _environment(entries: list[str]) -> dict[str, str]:
    return dict(item.split("=", 1) for item in entries if "=" in item)


def _port(value: str) -> tuple[str, str, str]:
    binding, _, protocol = value.partition("/")
    parts = binding.rsplit(":", 2)
    if len(parts) == 1:
        return f"{parts[0]}/{protocol or 'tcp'}", "", ""
    if len(parts) == 2:
        return f"{parts[1]}/{protocol or 'tcp'}", "", parts[0]
    return f"{parts[2]}/{protocol or 'tcp'}", parts[0], parts[1]


def _port_ip(value: str) -> str:
    return "" if value in {"0.0.0.0", "::"} else value.strip("[]")


def _verify_self_update(item: dict[str, Any], env: dict[str, Any], ports: list[tuple[str, str, str]]) -> None:
    original = _environment(item["Config"].get("Env") or [])
    actual_ports = {
        (target, _port_ip(binding["HostIp"]), binding["HostPort"])
        for target, bindings in (item["HostConfig"].get("PortBindings") or {}).items()
        for binding in bindings or []
    }
    storage = [mount for mount in item["Mounts"] if mount["Destination"] == "/app/storage"]
    if (not original.get("SESSION_SECRET") or original["SESSION_SECRET"] != env.get("SESSION_SECRET")
            or not storage or not storage[0]["RW"] or any(port in {"", "0"} for _, _, port in actual_ports)
            or actual_ports != {(target, _port_ip(ip), port) for target, ip, port in ports}):
        raise invalid("Toolbox 自更新需要沿用现有 /app/storage 挂载、端口和 SESSION_SECRET")


def _mount(source: str, destination: str, read_only: bool, kind: str = "") -> tuple[str, str, bool, str]:
    if not destination.startswith("/") or (source and not source.startswith("/") and "/" in source):
        raise invalid("请使用绝对路径或命名卷")
    return destination, source, read_only, kind or ("bind" if source.startswith("/") else "volume")


def _run_mounts(recipe: RunRecipe) -> list[tuple[str, str, bool, str]]:
    mounts = []
    for value in recipe.values("--volume"):
        parts = value.split(":")
        source, destination, options = ("", parts[0], "") if len(parts) == 1 else (*parts[:2], next(iter(parts[2:]), ""))
        mounts.append(_mount(source, destination, "ro" in options.split(",")))
    for value in recipe.values("--mount"):
        entries = next(csv.reader([value]))
        opts = dict(entry.partition("=")[::2] for entry in entries)
        kind = opts.get("type", "volume")
        if kind == "tmpfs":
            continue
        mounts.append(_mount(
            opts.get("source", opts.get("src", "")),
            opts.get("target", opts.get("destination", opts.get("dst", ""))),
            any(key in opts and opts[key] != "false" for key in ("readonly", "ro")), kind,
        ))
    return mounts


def _compose_mounts(config: dict[str, Any], service: dict[str, Any]) -> list[tuple[str, str, bool, str]]:
    mounts = []
    for value in service.get("volumes", []):
        kind = value["type"]
        if kind not in {"bind", "volume"}:
            continue
        source = value.get("source", "")
        if kind == "volume" and source:
            source = config["volumes"][source]["name"]
        mounts.append(_mount(source, value["target"], value.get("read_only", False), kind))
    return mounts


def _validate_mounts(item: dict[str, Any], desired: list[tuple[str, str, bool, str]], *, compose: bool) -> list[str]:
    existing = {mount["Destination"]: mount for mount in item["Mounts"] if mount["Type"] in {"volume", "bind"}}
    extra = []
    supplied = set()
    for destination, source, read_only, kind in desired:
        mount = existing.get(destination)
        if (not mount or mount["Type"] != kind or mount["RW"] == read_only
                or source and source != (mount["Source"] if kind == "bind" else mount.get("Name"))):
            raise invalid("部署配置的持久挂载与现有容器不一致")
        supplied.add(destination)
        if not source and not compose:
            raise invalid("请在 docker run 中显式引用现有卷名称")
    for destination in existing.keys() - supplied:
        mount = existing[destination]
        if mount["Type"] != "volume" or destination not in (item["Config"].get("Volumes") or {}):
            raise invalid("部署配置缺少现有持久挂载")
        if not compose:
            read_only = ",readonly" if not mount["RW"] else ""
            extra.append(f"type=volume,source={mount['Name']},target={destination}{read_only}")
    return extra


def snapshot(client: Any, payload: UpdateRuleInput, image: str, path: str, engine_id: str) -> Snapshot:
    run = parse_run(payload.recipe) if payload.mode == "run" else None
    if run:
        instances = remote_json(client, ["docker", "container", "inspect", run.name], "target_missing")
        if (instances[0]["Config"].get("Labels") or {}).get("com.docker.compose.project"):
            raise invalid("请使用 Compose 模式管理该容器")
        mounts = _run_mounts(run)
        env = _environment(run.values("--env"))
        ports = [_port(value) for value in run.values("--publish")]
    else:
        config = compose_config(client, payload, path)
        service = config["services"][payload.service]
        resolved_image = service.get("image", "")
        if resolved_image not in {image, image.removesuffix(":latest")}:
            raise invalid("解析后的 Compose 镜像与规则不一致")
        instances = compose_instances(client, payload.project, payload.service)
        mounts = _compose_mounts(config, service)
        env = service.get("environment") or {}
        ports = [
            (f"{port['target']}/{port.get('protocol', 'tcp')}", port.get("host_ip", ""),
             str(port.get("published", ""))) for port in service.get("ports", [])
        ]
    if not instances or any(item["State"]["Status"] != "running" for item in instances):
        raise RemoteUpdateError("target_not_running")
    instances.sort(key=lambda item: item["Id"])
    images = {item["Image"] for item in instances}
    if len(images) != 1:
        raise invalid("服务副本须运行同一镜像以便回滚")
    old_image = next(iter(images))
    if not DIGEST.fullmatch(old_image) or not all(CONTAINER_ID.fullmatch(item["Id"]) for item in instances):
        raise RemoteUpdateError("target_metadata")
    self_update = payload.selfUpdate or any(
        _environment(item["Config"].get("Env") or []).get("STORAGE_DIR") == "/app/storage"
        and ("/api/health" in " ".join((item["Config"].get("Healthcheck") or {}).get("Test", []))
             or "backend.app.main:app" in (item["Config"].get("Cmd") or []))
        for item in instances
    )
    extra_mounts = []
    for item in instances:
        if canonical_repository(item["Config"]["Image"]) != canonical_repository(image):
            raise invalid("规则必须跟踪现有容器的镜像仓库")
        extra_mounts = _validate_mounts(item, mounts, compose=run is None)
        if self_update:
            _verify_self_update(item, env, ports)
            if not (item["State"].get("Health") or {}).get("Status"):
                raise invalid("Toolbox 自更新需要原容器已配置 Docker 健康检查")
    metadata = remote_json(client, ["docker", "image", "inspect", old_image], "target_metadata")[0]
    platform = "/".join(part for part in (metadata["Os"], metadata["Architecture"], metadata.get("Variant")) if part)
    return Snapshot(instances, platform, old_image, self_update, run, extra_mounts, engine_id)


def candidate_image(client: Any, tag: str, platform: str) -> Candidate:
    descriptor = remote_json(client, [
        "docker", "buildx", "imagetools", "inspect", "--format", "{{json .Manifest}}", tag,
    ], "registry_query")
    digest = descriptor.get("digest", "")
    if "manifests" in descriptor:
        os_name, arch, *variant = platform.split("/")
        matches = [item for item in descriptor["manifests"] if
                   item.get("platform", {}).get("os") == os_name
                   and item["platform"].get("architecture") == arch]
        if variant:
            matches = [item for item in matches if item["platform"].get("variant", "") == variant[0]
                       or arch == "arm64" and variant[0] == "v8" and not item["platform"].get("variant")]
        elif len(matches) > 1:
            matches = [item for item in matches if not item["platform"].get("variant")
                       or arch == "arm64" and item["platform"]["variant"] == "v8"]
        if len(matches) != 1:
            raise RemoteUpdateError("registry_platform")
        digest = matches[0]["digest"]
    if not DIGEST.fullmatch(digest):
        raise RemoteUpdateError("registry_digest")
    ref = f"{image_repository(tag)}@{digest}"
    manifest = remote_json(client, ["docker", "buildx", "imagetools", "inspect", "--raw", ref], "registry_query")
    image = manifest.get("config", {}).get("digest", "")
    if not DIGEST.fullmatch(image):
        raise RemoteUpdateError("registry_digest")
    return Candidate(ref, digest, image)


def _override(service: str, image: str) -> str:
    return json.dumps({"services": {service: {"image": image}}})


def stage_worker(client: Any, run_id: str, path: str, payload: UpdateRuleInput, target: str,
                 current: Snapshot, candidate: Candidate) -> None:
    backup = f"toolbox-rollback-{run_id}" if current.run else f"{image_repository(candidate.ref)}:toolbox-rollback-{run_id}"
    settings = {
        "mode": payload.mode, "target": current.run.name if current.run else target,
        "project": payload.project, "service": payload.service, "run_id": run_id,
        "self_update": str(int(current.self_update)), "candidate": candidate.ref,
        "new_image": candidate.image, "old_image": current.image, "backup": backup,
        "platform": current.platform, "engine_id": current.engine_id,
        "target_lock": hashlib.sha256(target.encode()).hexdigest(),
    }
    lines = [f"{key}={shlex.quote(value)}" for key, value in settings.items()]
    lines.append(f"old_ids=({shlex.join(list(current.ids))})")
    files = {}
    if current.run:
        command = current.run.command(candidate.ref, current.extra_mounts)
        image_separator = command.index("--")
        command[image_separator:image_separator] = ["--label", f"com.pansis-toolbox.update-run={run_id}"]
        lines.extend(("launch_run() {", f"    {shlex.join(command)}", "}"))
    else:
        files = {"next.json": _override(payload.service, candidate.ref),
                 "previous.json": _override(payload.service, backup)}
    files.update({"worker.sh": WORKER.read_text(), "plan.sh": "\n".join(lines) + "\n"})
    upload(client, path, files)


def launch(client: Any, run_id: str, path: str) -> None:
    execute(client, [
        "screen", "-c", "/dev/null", "-dmS", f"toolbox-update-{run_id}",
        "timeout", "-k", "5s", "35m", "/bin/bash", f"{path}/worker.sh", path,
    ], "handoff")


def outcome(client: Any, run_id: str, path: str) -> tuple[str, str, dict[str, str]] | None:
    script = r"""
if [ -f "$1/result" ]; then
    cat -- "$1/result"
    exit
fi
command -v screen >/dev/null || exit 1
sessions=$(LC_ALL=C screen -ls "$2" 2>&1) || true
while read -r session state; do
    if [[ "$session" =~ ^[0-9]+[.] && "$session" == *."$2" && "$state" =~ \((Attached|Detached)\) ]]; then
        printf running
        exit
    fi
done <<< "$sessions"
if [ -f "$1/result" ]; then cat -- "$1/result"; else printf missing; fi
"""
    result = shell(client, script, path, f"toolbox-update-{run_id}", stage="read_result").split()
    if result == ["running"]:
        return None
    if result == ["missing"]:
        return "needs_review", "worker_missing", {}
    if (len(result) < 2 or result[0] not in {"failed", "succeeded", "rolled_back", "needs_review"}
            or not re.fullmatch(r"[a-z_]+", result[1])
            or any(not re.fullmatch(r"[0-9a-f]{64}=[0-9a-f]{64}", pair) for pair in result[2:])):
        raise RemoteUpdateError("invalid_result")
    return result[0], result[1], dict(pair.split("=", 1) for pair in result[2:])


def clean(client: Any, path: str) -> None:
    shell(client, 'rm -rf -- "$1"', path, stage="cleanup")
