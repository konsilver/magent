## 本文档整理memory的配置

# Vector（必选）
"vector_store": {"provider": "milvus", "config": {"url": ..., "collection_name": "jingxin_memories", "embedding_model_dims": ...}}

# Graph（可选，MEM0_GRAPH_ENABLED=true）
"graph_store": {"provider": "neo4j", "config": {"url": ..., "username": ..., "password": ...}}

# LLM（事实提取用）
"llm": {"provider": "openai", "config": {"model": ..., "openai_base_url": ..., "api_key": ...}}

# Embedder
"embedder": {"provider": "openai", "config": {"model": ..., "openai_base_url": ...}}


LTM 模式 vs 手动模式，选择手动模式控制
