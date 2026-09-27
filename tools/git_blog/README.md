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

文章 front matter 可覆盖 `published`、`title`、`date`、`updated`、`slug`、`summary`、`author`、`tags` 与 `cover`。

私有仓库使用设置页生成的 GitHub Deploy Key。复制公开密钥到 GitHub 仓库的 Deploy keys，并仅授予读取权限。
