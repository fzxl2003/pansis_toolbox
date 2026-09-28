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

工具页的“添加主题”可建立当前用户的 Typora 主题库。上传单个 `.css` 文件后即可预览、删除，并可在创建或编辑博客时选择。CSS 会保持原始内容并直接应用到公开文章页；删除正在使用的主题时，相关博客会自动恢复内置主题。

公开文章容器同时使用 Typora 常见的 `#write` 与 `.typora-export` 选择器，并提供 `--bg-color`、`--text-color`、`--window-border`、`--code-block-bg-color` 等常见 CSS 变量。Typora 编辑器专属界面的规则没有对应元素，其他正文排版规则可直接生效。

原有的博客 ZIP 视觉模板接口仍保留，用于包含字体或图片等多文件资源的高级场景。

私有仓库使用设置页生成的 GitHub Deploy Key。复制公开密钥到 GitHub 仓库的 Deploy keys，并仅授予读取权限。

## 同步与快照

编辑博客时可单独开启或关闭周期自动同步；关闭后仍可使用仓库列表中的“立即同步”。每次成功同步会按提交保存快照，每个博客最多保留 5 个不同提交。在编辑页可快速回滚到任一保留快照，回滚完成后自动关闭周期同步，避免远端新版本立即覆盖回滚内容。

同步会分别比较远端提交和本地渲染指纹。两者都未变化时只记录“当前已是最新版本”；远端提交未变化但内容目录、发布配置、博客地址或渲染器版本发生变化时，会直接复用已有快照重新生成索引，不再克隆仓库。保存影响输出的配置会自动加入本地重建队列，即使周期自动同步已关闭也会执行。
