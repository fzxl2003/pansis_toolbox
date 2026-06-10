# 网址导航

个人化多入口网址导航工具。每个登录用户拥有独立的 `links.json`，首次使用时会从 `default_links.json` 复制管理员预置模板。

## 接口

- `GET /api/tools/url-navigator/links`
- `POST /api/tools/url-navigator/links`
- `PUT /api/tools/url-navigator/links/{link_id}`
- `DELETE /api/tools/url-navigator/links/{link_id}`
- `POST /api/tools/url-navigator/links/reset`

自动访问由浏览器端完成：并发探测入口的 `probeUrl || url`，再按延迟优先或优先级优先选择目标地址。
