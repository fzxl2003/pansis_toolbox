# pansis_toolbox

`pansis_toolbox` 是一个插件式个人/实验室工具箱框架。当前项目由 FastAPI 后端、Vite React 前端和 `tools/` 子工具目录组成：主框架负责登录、工具发现、路由挂载、用户数据目录、小组件注册和异常隔离；每个工具通过自己的 `manifest.json`、后端 router 和前端入口接入。

## 当前结构

```text
backend/          FastAPI 主后端、平台 API、登录、数据库和工具加载器
frontend/         Vite React 主前端
tools/            可按需增删的子工具目录
scripts/          开发启动、工具检查、工具视图生成脚本
docker/           镜像构建和容器部署配置
storage/          本地数据库、上传文件和用户工具数据
```

平台默认读取 `tools/*/manifest.json` 发现工具。`enabled=false` 的工具不会被前端注册，也不会被 Docker 镜像的按需依赖扫描纳入运行环境构建。

## 当前工具

| 工具 ID | 名称 | 分类 | API 前缀 | 额外运行依赖 |
| --- | --- | --- | --- | --- |
| `docker_manager` | Docker 多租户管理 | `ops` | `/api/tools/docker-manager` | `paramiko`, `cryptography` |
| `experiment_monitor` | 实验监控报警与触发 | `ops` | `/api/tools/experiment-monitor` | `paramiko` |
| `memo_demo` | 备忘录 Demo | `text` | `/api/tools/memo-demo` | 无 |
| `server_monitor` | 服务器监控看板 | `ops` | `/api/tools/server-monitor` | 无 |
| `ssh_workspace` | SSH 工作台 | `ops` | `/api/tools/ssh-workspace` | `paramiko`, `cryptography` |
| `text_cleaner` | 文本清洗 | `text` | `/api/tools/text-cleaner` | 无 |
| `url_navigator` | 网址导航 | `network` | `/api/tools/url-navigator` | 无 |
| `web_proxy` | 网页代理 | `network` | `/web-proxy` | 内置 Rammerhead Node sidecar |

## 本地环境配置

推荐使用 Conda 环境 `pansis_toolbox`。当前仓库在该环境下验证过 Python 测试和前端构建。

```bash
conda create -n pansis_toolbox python=3.11 nodejs -c conda-forge
conda activate pansis_toolbox
python -m pip install -e ".[dev]"
npm --prefix frontend install
```

如果环境里没有通过 Conda 安装 Node.js，也可以使用系统 Node.js，但需要保证 `node` 和 `npm` 在当前 shell 中可用。

复制环境变量样例：

```bash
cp .env.example .env
```

常用配置项：

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `APP_ENV` | `development` | 应用运行环境 |
| `API_PREFIX` | `/api` | 主平台 API 前缀 |
| `TOOLS_DIR` | `tools` | 工具发现目录 |
| `FRONTEND_DIST_DIR` | `frontend/dist` | 生产模式静态前端目录 |
| `STORAGE_DIR` | `storage` | 本地持久化数据目录 |
| `PLATFORM_DB_PATH` | `storage/data/platform.db` | 平台 SQLite 数据库 |
| `WIDGET_LAYOUT_PATH` | `storage/data/widget_layout.json` | 首页小组件布局文件 |
| `FRONTEND_ORIGIN` | `http://localhost:5173` | 开发前端允许跨域来源 |
| `SESSION_SECRET` | `change-me-in-production` | 会话签名密钥，生产环境必须修改 |
| `DEFAULT_ADMIN_USERNAME` | `admin` | 默认管理员用户名 |
| `DEFAULT_ADMIN_PASSWORD` | `admin123` | 默认管理员密码，生产环境必须修改 |
| `DEFAULT_ADMIN_DISPLAY_NAME` | `本地管理员` | 默认管理员显示名 |

默认登录账号为 `admin / admin123`。首次部署或对外访问前请修改 `SESSION_SECRET` 和默认管理员密码。

## 本地开发

生成工具前端注册表并同时启动后端和前端：

```bash
conda activate pansis_toolbox
npm run generate:tools
npm run dev
```

默认访问地址：

- 前端开发服务：`http://127.0.0.1:5173/`
- 后端 API：`http://127.0.0.1:8000`
- 健康检查：`http://127.0.0.1:8000/api/health`

也可以分别启动：

```bash
npm run backend
npm run frontend
```

根目录常用脚本：

```bash
npm run generate:tools   # 根据 tools/*/manifest.json 生成前端懒加载注册表
npm run check:tools      # 检查工具 manifest 和前后端入口
npm run build            # 构建前端
npm run test             # 运行 pytest
```

## 生产运行方式

当前后端会在 `FRONTEND_DIST_DIR` 存在 `index.html` 时自动挂载静态前端。因此生产环境可以只运行一个 Uvicorn 服务：

```bash
conda activate pansis_toolbox
npm run generate:tools
npm run build
APP_ENV=production \
FRONTEND_ORIGIN=http://localhost:8000 \
uvicorn backend.app.main:app --host 0.0.0.0 --port 8000
```

访问 `http://localhost:8000/` 即可打开前端页面，API 仍在 `/api` 下。

## Docker 部署

Docker 部署文件位于 `docker/`：

```text
docker/Dockerfile       多阶段镜像构建
docker/build-image.sh   镜像构建脚本
docker/compose.yml      容器创建和启动配置
```

构建镜像：

```bash
./docker/build-image.sh
```

可通过环境变量覆盖镜像名、标签和平台：

```bash
IMAGE_NAME=pansis-toolbox IMAGE_TAG=prod PLATFORM=linux/amd64 ./docker/build-image.sh
```

构建脚本会扫描当前仓库中的 `tools/*/manifest.json`，只处理当前存在且启用的工具。镜像内部同样按工具目录扫描：

- 安装每个启用工具 manifest 中声明的 Python `dependencies`。
- 对启用工具目录下发现的 `package.json` 执行 `npm ci` 或 `npm install`。
- 如果该 Node 包声明了 `build` 脚本，则在镜像构建时执行构建。
- 当前 `web_proxy` 的 Rammerhead sidecar 会因此在镜像构建阶段准备好依赖和客户端产物。

启动容器：

```bash
docker compose -f docker/compose.yml up -d
```

默认访问：

```text
http://localhost:8000/
```

常用 compose 覆盖项：

```bash
HOST_PORT=8080 \
SESSION_SECRET='replace-with-a-long-random-secret' \
DEFAULT_ADMIN_USERNAME=admin \
DEFAULT_ADMIN_PASSWORD='replace-this-password' \
docker compose -f docker/compose.yml up -d
```

如果修改了 `HOST_PORT`，建议同时设置 `FRONTEND_ORIGIN`：

```bash
HOST_PORT=8080 FRONTEND_ORIGIN=http://localhost:8080 docker compose -f docker/compose.yml up -d
```

容器中的持久化数据保存在命名卷 `pansis-toolbox-storage`，挂载到 `/app/storage`。删除容器不会删除该卷；如需彻底清理数据，需要显式删除卷。

## 新增工具

创建工具骨架：

```bash
python scripts/create_tool.py my_tool
```

每个工具至少需要：

- `manifest.json`：工具 ID、名称、分类、入口、API 前缀、权限和依赖声明。
- `backend/router.py`：导出 FastAPI `APIRouter`。
- `frontend/index.tsx`：导出 React 默认组件。

新增或删除工具后运行：

```bash
npm run check:tools
npm run generate:tools
```

工具运行依赖应优先写入 `manifest.json` 的 `dependencies` 字段；带独立 Node sidecar 的工具可以在工具目录下放置自己的 `package.json`。Docker 镜像会按启用工具自动处理这些依赖。

工具可选 `displayMode`：

- `standard`：默认工具页，保留主框架导航。
- `fullscreen`：全屏工具页，隐藏主框架侧边栏和页面 padding。
- `flexible`：允许用户在标准/全屏之间切换。

## 用户数据

平台数据库默认位于：

```text
storage/data/platform.db
```

工具用户数据默认位于：

```text
storage/user_data/{user_id}/tools/{tool_id}/
```

工具后端如果需要用户专属目录，使用：

```python
from backend.app.core.security import require_user_tool_data_dir

data_dir = require_user_tool_data_dir(request, "tool_id")
```

未登录时会返回统一的 `LOGIN_REQUIRED` 错误；已登录时会创建并返回当前用户的工具专属目录。

## 验证

推荐变更后至少运行：

```bash
python -m py_compile backend/app/main.py backend/app/core/config.py
npm run build
python -m pytest backend/tests/test_app.py
git diff --check
```

在当前工作区，全量 `pytest` 仍有 `ssh_workspace` 既有测试与实现契约不一致的问题；如只验证主框架启动、工具注册和本次 Docker 文档相关改动，可先使用 `backend/tests/test_app.py` 与前端构建。
