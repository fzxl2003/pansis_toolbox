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

公开文章容器同时使用 Typora 常见的 `#write` 与 `.typora-export` 选择器，并提供 `--bg-color`、`--text-color`、`--window-border`、`--code-block-bg-color` 等常见 CSS 变量。将 Typora 主题的 CSS 放入模板 ZIP 并按博客实际页面微调后，可复用其中大部分排版规则；涉及 Typora 编辑器专属界面的规则会被忽略。

私有仓库使用设置页生成的 GitHub Deploy Key。复制公开密钥到 GitHub 仓库的 Deploy keys，并仅授予读取权限。

## 同步与快照

编辑博客时可单独开启或关闭周期自动同步；关闭后仍可使用仓库列表中的“立即同步”。每次成功同步会按提交保存快照，每个博客最多保留 5 个不同提交。在编辑页可快速回滚到任一保留快照，回滚完成后自动关闭周期同步，避免远端新版本立即覆盖回滚内容。
