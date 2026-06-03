#!/usr/bin/env python3
"""
RAG 实验 — 全局配置

所有可调参数集中管理，学生可通过修改本文件或环境变量来调整实验参数。
"""

import os
from pathlib import Path

BASE_DIR = Path(__file__).parent

# ── 嵌入模型 ──
EMBEDDING_MODEL = os.getenv("RAG_EMBEDDING_MODEL", "all-MiniLM-L6-v2")

# ── 分块策略 ──
DEFAULT_CHUNK_SIZE = int(os.getenv("RAG_CHUNK_SIZE", "512"))
DEFAULT_CHUNK_OVERLAP = int(os.getenv("RAG_CHUNK_OVERLAP", "50"))

# 分块策略对比实验中使用的尺寸列表
CHUNK_SIZE_VARIANTS = [256, 512, 1024, 2048]

# ── 检索参数 ──
DEFAULT_TOP_K = int(os.getenv("RAG_TOP_K", "5"))
SIMILARITY_THRESHOLD = float(os.getenv("RAG_SIMILARITY_THRESHOLD", "0.3"))

# ── 重排 ──
RERANKER_MODEL = "cross-encoder/ms-marco-MiniLM-L-6-v2"
USE_RERANKER = os.getenv("RAG_USE_RERANKER", "").lower() in ("true", "1", "yes")

# ── Phoenix ──
PHOENIX_ENDPOINT = os.getenv("PHOENIX_COLLECTOR_ENDPOINT", "http://127.0.0.1:6006/v1/traces")
PHOENIX_HOST = PHOENIX_ENDPOINT.replace("/v1/traces", "").rstrip("/")

# ── 存储路径 ──
KB_DIR = BASE_DIR / "knowledge_base"
CHROMA_DB_PATH = str(BASE_DIR / "chroma_db")
OUTPUT_DIR = BASE_DIR / "output"

# ── LLM ──
LLM_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")

# ── 文档列表（按优先级排列：前 3 个是目标域，后 2 个是噪声） ──
DOCUMENTS = [
    ("mcp_spec_v1.md", "MCP 协议 v1 规范", "target"),
    ("mcp_spec_v2.md", "MCP 协议 v2 规范", "target"),
    ("mcp_transport.md", "MCP 传输层文档", "target"),
    ("noise_rest_api.md", "REST API 设计最佳实践", "noise"),
    ("noise_websocket.md", "WebSocket 实时通信协议", "noise"),
]
