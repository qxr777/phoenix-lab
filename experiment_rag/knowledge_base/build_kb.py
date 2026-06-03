#!/usr/bin/env python3
from pathlib import Path
"""
RAG 知识库构建器

加载 Markdown 文档 → 分块 → 嵌入 → 存入 ChromaDB → 可选发送到 Phoenix

用法:
  python build_kb.py                        # 默认配置构建
  python build_kb.py --chunk-size 1024      # 指定分块大小
  python build_kb.py --strategy semantic    # 使用语义分块策略
  python build_kb.py --send-to-phoenix      # 同时发送 embeddings 到 Phoenix
"""

import argparse
import hashlib
import json
import os
import sys

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import chromadb
from chromadb.utils import embedding_functions
from dotenv import load_dotenv

load_dotenv()

from experiment_rag.config import (
    CHROMA_DB_PATH,
    DEFAULT_CHUNK_OVERLAP,
    DEFAULT_CHUNK_SIZE,
    DOCUMENTS,
    EMBEDDING_MODEL,
    KB_DIR,
)
from experiment_rag.chunking.strategies import chunk_document

# ──────────────────────────────────────────────
#  嵌入函数
# ──────────────────────────────────────────────

def get_embedding_function():
    return embedding_functions.SentenceTransformerEmbeddingFunction(
        model_name=EMBEDDING_MODEL
    )


# ──────────────────────────────────────────────
#  知识库构建
# ──────────────────────────────────────────────

def build_knowledge_base(
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    chunk_overlap: int = DEFAULT_CHUNK_OVERLAP,
    strategy: str = "fixed",
    collection_name: str = "mcp_knowledge_base",
    send_to_phoenix: bool = False,
) -> dict:
    collection_full_name = f"{collection_name}_cs{chunk_size}"
    ef = get_embedding_function()

    client = chromadb.PersistentClient(path=CHROMA_DB_PATH)
    existing = [c.name for c in client.list_collections()]

    if collection_full_name in existing:
        client.delete_collection(collection_full_name)
        print(f"[KB] 已删除旧集合: {collection_full_name}")

    collection = client.create_collection(
        name=collection_full_name,
        embedding_function=ef,
        metadata={
            "hnsw:space": "cosine",
            "chunk_size": str(chunk_size),
            "strategy": strategy,
        },
    )

    all_chunks = []
    doc_stats = {}

    for filename, title, doc_type in DOCUMENTS:
        filepath = KB_DIR / filename
        if not filepath.exists():
            print(f"[KB] 警告: 文档不存在 — {filename}")
            continue

        content = filepath.read_text(encoding="utf-8")
        chunks = chunk_document(content, chunk_size, chunk_overlap, strategy)

        doc_chunks = []
        for i, chunk_text in enumerate(chunks):
            chunk_id = hashlib.md5(f"{filename}:{i}".encode()).hexdigest()[:12]
            doc_chunks.append((chunk_id, chunk_text, filename, title, doc_type, i))

        if doc_chunks:
            ids = [c[0] for c in doc_chunks]
            texts = [c[1] for c in doc_chunks]
            metadatas = [{
                "source": c[2],
                "title": c[3],
                "doc_type": c[4],
                "chunk_index": c[5],
                "chunk_size": chunk_size,
                "strategy": strategy,
            } for c in doc_chunks]

            collection.add(documents=texts, ids=ids, metadatas=metadatas)

            doc_stats[title] = {"chunk_count": len(doc_chunks), "doc_type": doc_type}
            all_chunks.extend(doc_chunks)
            print(f"[KB] {filename}: {len(doc_chunks)} chunks → {collection_full_name}")

    result = {
        "collection_name": collection_full_name,
        "chunk_size": chunk_size,
        "strategy": strategy,
        "total_chunks": len(all_chunks),
        "documents": doc_stats,
    }

    print(f"\n[KB] 知识库构建完成: {collection_full_name}")
    print(f"   总分块数: {len(all_chunks)}")
    for title, stats in doc_stats.items():
        print(f"   {title}: {stats['chunk_count']} chunks ({stats['doc_type']})")

    if send_to_phoenix:
        _send_to_phoenix(all_chunks)

    return result


def _send_to_phoenix(all_chunks: list):
    try:
        import phoenix
        import numpy as np

        model = get_embedding_function()
        texts = [c[1] for c in all_chunks]
        vectors = model(texts)
        if isinstance(vectors, list):
            vectors = np.array(vectors)

        print(f"[Phoenix] arize-phoenix {phoenix.__version__} 已安装")
        print(f"[Phoenix] 嵌入数据通过 OTLP trace 自动发送到 Phoenix。")
        print(f"[Phoenix] 运行带 ENABLE_PHOENIX_TRACING=true 的 RAG 查询后即可在 Phoenix UI 查看。")
    except ImportError:
        print("[Phoenix] 警告: arize-phoenix 未安装，跳过 Phoenix 发送")
    except Exception as e:
        print(f"[Phoenix] 发送失败: {e}")
        print("[Phoenix] 提示: 确保 Phoenix Docker 已启动 (docker compose up -d)")


# ──────────────────────────────────────────────
#  CLI
# ──────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="RAG 知识库构建器")
    parser.add_argument("--chunk-size", type=int, default=DEFAULT_CHUNK_SIZE, help="分块大小")
    parser.add_argument("--chunk-overlap", type=int, default=DEFAULT_CHUNK_OVERLAP, help="分块重叠")
    parser.add_argument("--strategy", choices=["fixed", "semantic", "paragraph"], default="fixed")
    parser.add_argument("--collection-name", default="mcp_knowledge_base")
    parser.add_argument("--send-to-phoenix", action="store_true", help="发送 embeddings 到 Phoenix")
    parser.add_argument("--all-sizes", action="store_true", help="为所有 chunk_size 变体构建知识库")

    args = parser.parse_args()

    if args.all_sizes:
        from experiment_rag.config import CHUNK_SIZE_VARIANTS
        print(f"[KB] 为 {len(CHUNK_SIZE_VARIANTS)} 种分块尺寸构建知识库...")
        for size in CHUNK_SIZE_VARIANTS:
            print(f"\n{'='*50}")
            build_knowledge_base(
                chunk_size=size,
                chunk_overlap=size // 10,
                strategy=args.strategy,
                collection_name=args.collection_name,
                send_to_phoenix=False,
            )
        print(f"\n[KB] 全部知识库构建完成！")
    else:
        build_knowledge_base(
            chunk_size=args.chunk_size,
            chunk_overlap=args.chunk_overlap,
            strategy=args.strategy,
            collection_name=args.collection_name,
            send_to_phoenix=args.send_to_phoenix,
        )


if __name__ == "__main__":
    main()
