# 文本清洗

MVP 示例工具，用于验证工具 manifest、后端 router 挂载、前端懒加载和小组件协议。

## API

- `POST /api/tools/text-cleaner/clean`

Body:

```json
{
  "text": " hello   world ",
  "trim": true,
  "collapseWhitespace": true,
  "removeBlankLines": true,
  "caseMode": "none"
}
```
