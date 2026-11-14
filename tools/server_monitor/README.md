# 服务器监控看板

通过 SSH 查看服务器 CPU、内存、CUDA GPU、磁盘挂载和固定目录用量。看板引用平台 SSH 服务器配置，并遵循其访问权限。

- 实时快照每 5 秒读取一次，服务端在 30 秒内复用最近结果。手动刷新会重新采集。
- CPU、内存、GPU 负载和显存历史每 30 秒最多保留一条，保留 30 天。磁盘挂载和 GPU 进程只出现在最新快照中。
- 24 小时趋势每 5 分钟汇总一次，曲线显示均值，悬浮显示同一时段的均值和峰值。

## 升级现有监控库

升级到 1.4.1 时，用新版本替换写入同一存储卷的旧实例。应用启动后会自动逐库迁移，首次访问尚未处理的旧库也会触发迁移。迁移在 SQLite 事务内完成，保留服务器配置、目录缓存，以及最近 30 天内每个 30 秒区间最早的一条有效趋势采样。

后台在迁移提交后执行 `VACUUM` 回收空间。遇到锁竞争或进程重启时，后续后台任务会继续尝试回收。自动升级在原库完成，不额外留存包含进程命令行的旧库副本。

### 可选的离线备份与迁移

如果需要保留旧版回退副本，可在启动 1.4.1 前停止写入并运行以下命令。备份目录放在在线存储根目录之外。

```sh
python -m tools.server_monitor.backend.migrate_history \
  --storage-dir /path/to/storage --backup-dir /path/to/monitor-backups
```

使用 Docker Compose 时，可通过新镜像运行一次性容器。先停止 `pansis-toolbox` 服务，并将宿主机备份目录挂载到 `/monitor-backups`。

```sh
docker compose stop pansis-toolbox
docker compose run --rm --no-deps -v /path/to/monitor-backups:/monitor-backups \
  pansis-toolbox python -m tools.server_monitor.backend.migrate_history \
  --storage-dir /app/storage --backup-dir /monitor-backups
```

命令在迁移前用 SQLite 备份整库，并输出各库的备份路径和迁移前后统计。已迁移的库可安全重跑。

需要回退旧版时，保持停服，先另存当前数据库及关联文件，再用本次迁移报告指向的备份替换对应用户的数据库，最后启动旧版。备份包含旧快照明细，按原数据库的访问权限管理。
