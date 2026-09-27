# Git 博客

将 GitHub 仓库中的 Markdown 发布为公开博客。管理者在工具页绑定仓库、分支和内容目录；只有 `published: true`（或博客默认值为 true）的文档会公开。

仓库可选根配置 `.pansis-blog.yml`：

```yaml
site:
  description: 从 Git 同步的博客
  theme: auto # auto | light | dark
defaults:
  published: false
```

文章 front matter 可覆盖 `published`、`title`、`date`、`updated`、`slug`、`summary`、`author`、`tags` 与 `cover`。`cover` 可为仓库内相对图片路径或外部 URL；首页会将其显示在文章卡片右侧。

公开导航包含首页（最新文章）、目录（仅已发布 Markdown 的仓库层级）和标签。RSS、Atom 与 sitemap 接口继续保留。

## 视觉模板

博客编辑页可上传 ZIP 视觉模板包。模板包至少包含一个 `.css` 文件，也可包含字体和图片资源；它只覆盖公开页面的视觉样式，不支持自定义 HTML 或 JavaScript。可随时删除模板并回到内置亮色、暗色或自动主题。

私有仓库使用设置页生成的 GitHub Deploy Key。复制公开密钥到 GitHub 仓库的 Deploy keys，并仅授予读取权限。
