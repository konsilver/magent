## 本文档整理memory的操作api

memory.add(messages, user_id=user_id) ：写入一段对话，触发事实提取 → 向量化 → 存入 Milvus
messages:[
    {"role": "user",      "content": user_message},
    {"role": "assistant", "content": assistant_message},
]


memory.search(query, user_id=user_id, limit=limit)：向量语义检索，按 user_id 跨会话检索，不按 chat_id 隔离,返回：
{
    "results": [
        {"memory": "...", "score": 0.87, "updated_at": "..."},
        ...
    ],
    "relations": [...]   # Graph 结果，未启用时为空
}
在 search 之后额外做了：相关性阈值过滤（min_score=0.4）+ 时间衰减加权（半衰期 ~70 天）+ top-5 截断。

memory.get_all(user_id=user_id)：  拉取用户全量记忆条目（管理接口用）

memory.delete(memory_id) ：删除单条记忆

memory.delete_all(user_id=user_id)：清空某用户所有记忆

Graph 不需要单独调用接口，与 KV 接口共用，mem0 内部自动处理