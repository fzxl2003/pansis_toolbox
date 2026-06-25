# pansis_toolbox

`pansis_toolbox` 是一个插件式工具箱网站骨架，采用「FastAPI 后端 + Vite React 前端 + tools 子工具目录」的结构。主平台负责工具发现、路由挂载、工具列表、小组件协议和异常隔离；每个子工具独立维护自己的 manifest、前端、后端和说明文档。

## 技术栈

- 后端：FastAPI、Pydantic、Uvicorn
- 前端：Vite、React、TypeScript、Tailwind CSS
- 工具接入：`tools/{tool_id}/manifest.json` + 独立前端入口 + 独立后端 router
- 推荐环境：Python 3.11+，当前项目使用 Conda 环境 `pansis_toolbox`

## 快速启动

如果环境已经创建好：

```bash
conda activate pansis_toolbox
cd /Users/pan1pansis/Coding/pansis_toolbox
npm run generate:tools
npm run dev
```

如果需要从零创建 Conda 环境：

```bash
conda create -n pansis_toolbox python=3.11
conda activate pansis_toolbox
conda install fastapi uvicorn pydantic-settings pytest httpx python-multipart nodejs
```

默认开发账号：

```text
用户名：admin
密码：admin123
```

可通过 `.env` 中的 `DEFAULT_ADMIN_USERNAME`、`DEFAULT_ADMIN_PASSWORD` 和 `DEFAULT_ADMIN_DISPLAY_NAME` 覆盖。

安装前端依赖：

```bash
cd frontend
npm install
cd ..
```

## 单独启动

只启动后端：

```bash
conda activate pansis_toolbox
uvicorn backend.app.main:app --reload --host 127.0.0.1 --port 8000
```

只启动前端：

```bash
conda activate pansis_toolbox
cd frontend
npm run dev
```

默认访问地址：

- 前端：http://127.0.0.1:5173/
- 后端：http://127.0.0.1:8000
- 健康检查：http://127.0.0.1:8000/api/health

## 常用命令

```bash
python scripts/check_tools.py
python scripts/generate_tool_views.py
python -m pytest
cd frontend && npm run build
```

根目录脚本：

- `npm run dev`：同时启动前端和后端开发服务。
- `npm run backend`：启动 FastAPI 后端。
- `npm run frontend`：启动 Vite 前端。
- `npm run build`：构建前端。
- `npm run generate:tools`：根据 `tools/*/manifest.json` 生成前端工具注册表。
- `npm run check:tools`：检查工具 manifest 和入口文件。
- `npm run test`：运行后端测试。

## 目录结构

```text
backend/              FastAPI 主后端
frontend/             Vite React 主前端
tools/                子工具目录
scripts/              工具创建、检查、注册表生成和开发启动脚本
storage/              上传、输出、临时文件和本地数据目录
```

平台数据库默认位于：

```text
storage/data/platform.db
```

用户工具数据默认位于：

```text
storage/user_data/{user_id}/tools/{tool_id}/
```

示例工具：

```text
tools/text_cleaner/
  manifest.json
  README.md
  backend/router.py
  backend/widget.py
  frontend/index.tsx
```

## 新增工具流程

```bash
python scripts/create_tool.py my_tool
python scripts/check_tools.py
python scripts/generate_tool_views.py
```

每个工具至少包含：

- `manifest.json`：工具 ID、名称、说明、分类、入口、API 前缀、小组件和权限声明。
- `backend/router.py`：导出 FastAPI `APIRouter`，由主服务动态挂载。
- `frontend/index.tsx`：导出 React 默认组件，由工具页懒加载。
- `README.md`：说明工具用途和接口。

工具可在 `manifest.json` 中声明 `displayMode`：

- `standard`：默认模式，显示主框架侧边栏和工具页返回链接。
- `fullscreen`：全屏模式，隐藏侧边栏、主内容 padding 等框架内容。
- `flexible`：两者均可，工具页提供“全屏显示/退出全屏”切换。

工具图标支持内置 Lucide 图标和工具自带图片：

```json
{ "icon": { "type": "lucide", "name": "compass" } }
```

```json
{ "icon": { "type": "image", "src": "assets/icon.png", "alt": "工具图标" } }
```

图片路径为相对路径时，会从工具目录的 `assets/` 静态资源目录读取；也可以使用 `/` 开头的站内路径或完整 `https://` URL。

## 当前示例

`text_cleaner` 是匿名可用的 MVP 示例工具，提供文本清洗能力：

- 清理首尾空白。
- 合并连续空白。
- 移除空行。
- 支持大小写转换。
- 提供一个首页 summary 小组件。

接口示例：

```text
POST /api/tools/text-cleaner/clean
GET  /api/widgets/text_cleaner.summary/data
```

`memo_demo` 是需要登录的用户数据示例工具：

- 上传 `.txt` 文件。
- 保存到当前登录用户自己的工具数据目录。
- 查看、预览、删除自己的备忘录。
- 匿名访问用户数据接口时返回 `LOGIN_REQUIRED`，前端显示登录面板。

接口示例：

```text
POST   /api/tools/memo-demo/upload
GET    /api/tools/memo-demo/memos
GET    /api/tools/memo-demo/memos/{memo_id}
DELETE /api/tools/memo-demo/memos/{memo_id}
```

## 登录与用户数据

后端提供轻量登录接口：

```text
POST /api/auth/login
POST /api/auth/logout
GET  /api/auth/me
GET  /api/auth/sso/login
GET  /api/auth/sso/callback
```

工具后端如果需要用户专属数据，应调用：

```python
from backend.app.core.security import require_user_tool_data_dir

data_dir = require_user_tool_data_dir(request, "tool_id")
```

未登录时该函数会返回统一的 `LOGIN_REQUIRED` 错误；已登录时会创建并返回当前用户的工具专属目录。

## 验证状态

当前已验证：

- `python scripts/check_tools.py`
- `python scripts/generate_tool_views.py`
- `python -m pytest`
- `cd frontend && npm run build`

如果通过 Codex 或受限 shell 启动本地服务时遇到 `listen EPERM`，需要允许本地进程监听 `127.0.0.1:8000` 和 `127.0.0.1:5173`。


权限配置前端界面修改
1、将可见该服务器、使用镜像、使用容器、使用卷这几个选项分别挪到服务器访问、镜像权限等行的最右侧，用一个胶囊开关，而不是勾选框
2、每个权限下面的勾选框第二行现在前面多了空格，进行修复
3、CUDA权限也加一个胶囊开关，其余保持不变


1、docker pull 下来的镜像没有创建者和管理者
2、镜像部分的资源占用概览为空