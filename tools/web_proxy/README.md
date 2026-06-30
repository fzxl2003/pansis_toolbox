# 网页代理

基于 vendored Rammerhead 的完整 Web Proxy 工具。工具入口保留在 toolbox 内，实际浏览会跳转到本机 Rammerhead sidecar 页面。

## 使用方式

- 工具入口：`/tools/web_proxy`
- 直达入口：`/web-proxy?url=https%3A%2F%2Fexample.com`

访问代理前必须登录 toolbox。每个 toolbox 用户对应一个独立 Rammerhead session，目标网站的 cookie 和 localStorage 会随该 session 保存。

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
