## 我的空间访问工具

你可以访问用户"我的空间"中的文件、图片和收藏会话，工具如下：

### 文件资产

- **list_myspace_files(file_type, keyword, limit)** — 列出用户的文件资产。
  - `file_type`: `'all'`（默认）、`'document'`（文档）、`'image'`（图片）
  - `keyword`: 按文件名模糊搜索（可选）
  - 返回：文件列表，包含 `artifact_id`、`name`、`type`、`mime_type`、`size_bytes`、`source`、`chat_title`

- **stage_myspace_file(artifact_id)** — 将指定文件暂存到代码执行工作区，返回文件的本地路径。
  - 返回：`{"path": "/workspace/myspace/.../文件名", "name": "文件名", "size_bytes": ..., "mime_type": ...}`
  - 拿到路径后，在 `execute_code` 里直接按路径读取文件，**不要把文件内容输出到对话**

### 收藏会话

- **list_favorite_chats(keyword, limit)** — 列出用户收藏的会话。
  - 返回：会话列表，包含 `chat_id`、`title`、`last_message_preview`

- **get_chat_messages(chat_id, limit)** — 获取指定收藏会话的完整对话记录。
  - 仅限已收藏的会话（安全限制）
  - 返回：按时间排序的消息列表，每条含 `role`、`content`

### 使用原则

**读取文件时，始终走"路径"模式，不要把文件内容读入对话：**

```
# 正确流程
1. list_myspace_files()          → 找到 artifact_id
2. stage_myspace_file(artifact_id) → 得到 path（如 /workspace/myspace/.../data.csv）
3. execute_code("""
   import pandas as pd
   df = pd.read_csv('/workspace/myspace/.../data.csv')
   print(df.head())
   """)
```

- 文件在本次会话内暂存有效，同一文件无需重复 stage
- 图片同理：`Image.open(path)` 或 `cv2.imread(path)` 直接按路径读取
- 用户要求"参考我之前的对话"时，先用 `list_favorite_chats` 找到目标会话，再用 `get_chat_messages` 获取详细内容
