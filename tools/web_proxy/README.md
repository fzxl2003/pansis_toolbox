# 网页代理

基于 vendored Rammerhead 的完整 Web Proxy 工具。工具入口保留在 toolbox 内，实际浏览会跳转到本机 Rammerhead sidecar 页面。

## 使用方式

- 工具入口：`/tools/web_proxy`
- 直达入口：`/web-proxy?url=https%3A%2F%2Fexample.com`
- 出口选择：`/web-proxy?url=https%3A%2F%2Fexample.com&serverId=<server-id>`；使用 `serverId=direct` 强制本机直连。

访问代理前必须登录 toolbox。每个 toolbox 用户对应一个独立 Rammerhead session，目标网站的 cookie 和 localStorage 会随该 session 保存。

## SSH 二次代理

先在全局“设置 → SSH 服务器”中配置服务器，再在网页代理的“出口网络”下拉框中选择它。工具会在本机的 `127.0.0.1` 临时端口提供 HTTP CONNECT 转发，并用 SSH `direct-tcpip` 通道让所有目标连接从该服务器出网；远端不需要安装 HTTP、SOCKS 或其他守护进程。

- SSH 服务器及其加密凭据由全局设置统一管理；网页代理只保存当前会话选择的服务器 ID，不复制凭据，也不提供添加、编辑或删除入口。
- 清空代理会话或重启应用会释放本地监听和 SSH 连接。应用重启后，下次打开页面会自动重建选中的出口；若全局服务器被删除或撤销授权，下一次使用会被拒绝。

## 出口连通性测试

选择 SSH 出口后，工具页可点击“测试出口连通性”。在“测试网站设置”弹窗中添加需要验证的 HTTP/HTTPS 地址后，工具会逐个经该 SSH 隧道请求站点，并显示状态码和耗时。测试站点列表按 toolbox 用户隔离保存；测试本身只读取响应头，不下载完整页面内容。

该功能提升常见站点的代理兼容性并避免泄漏工具的反向代理头，但不规避 CAPTCHA、账户风控、访问频率限制或站点专有反自动化策略。

## 第三方代码

`vendor/rammerhead` 来自 `https://github.com/binary-person/rammerhead`，固定来源 commit：

```text
ee5fbb7837f5fe752c4b82c18184f42449678d5b
```

Rammerhead 使用 MIT License，原始 license/package 元数据保留在 vendor 目录。

## 运行说明

首次使用时后端会在 `vendor/rammerhead` 下执行 `npm install --ignore-scripts` 和 `npm run build` 准备 sidecar 依赖与客户端产物，然后启动本机进程：

```text
127.0.0.1:8787
127.0.0.1:8788
```

Rammerhead session 存在当前用户的工具数据目录；JS cache 存在 `storage/web_proxy/cache-js`。
