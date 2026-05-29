# 备忘录 Demo

需要登录的个人数据示例工具。用户上传 `.txt` 文件后，内容保存到自己的工具数据目录中。

数据目录：

```text
storage/user_data/{user_id}/tools/memo_demo/
```

接口：

- `POST /api/tools/memo-demo/upload`
- `GET /api/tools/memo-demo/memos`
- `GET /api/tools/memo-demo/memos/{memo_id}`
- `DELETE /api/tools/memo-demo/memos/{memo_id}`
