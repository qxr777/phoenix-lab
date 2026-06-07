#!/usr/bin/env python3
"""
Embedding 降维可视化引擎

使用 umap-learn 降维 + plotly 生成交互式 HTML 3D 散点图。
"""

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import numpy as np

from experiment_rag.config import (
    CHROMA_DB_PATH, DEFAULT_CHUNK_SIZE,
    EMBEDDING_MODEL, OUTPUT_DIR,
)

DEFAULT_QUERIES = [
    "MCP 协议的初始化握手流程是什么？",
    "MCP v1 和 v2 在工具调用上有什么区别？",
    "stdio 和 SSE 传输各有什么优缺点？",
    "TCP 连接中的握手和 MCP 的 initialize 有什么不同？",
    "MCP 协议支持哪些消息格式和传输方式？",
]


def _get_embedding_model():
    from chromadb.utils import embedding_functions
    return embedding_functions.SentenceTransformerEmbeddingFunction(
        model_name=EMBEDDING_MODEL
    )


def _embed_texts(texts):
    ef = _get_embedding_model()
    vectors = ef(texts)
    return np.array(vectors) if isinstance(vectors, list) else vectors


def visualize_local_umap(doc_texts, doc_vecs, query_texts=None,
                         query_vecs=None, output_path=None):
    try:
        import umap
        import plotly.graph_objects as go
    except ImportError:
        print("[Viz] pip install umap-learn plotly")
        return False

    combined = [doc_vecs]
    labels = []
    for i, t in enumerate(doc_texts):
        tl = t.lower()
        if "mcp" in tl and "v1" in tl:
            labels.append("MCP v1")
        elif "mcp" in tl and "v2" in tl:
            labels.append("MCP v2")
        elif "transport" in tl or "传输" in t:
            labels.append("Transport")
        elif "rest" in tl or "api" in tl:
            labels.append("Noise: REST")
        elif "websocket" in tl:
            labels.append("Noise: WS")
        else:
            labels.append("Unknown")

    num_docs = len(doc_texts)
    if query_texts and query_vecs is not None:
        combined.append(query_vecs)
        labels.extend([f"Q: {q[:25]}..." for q in query_texts])
    combined = np.vstack(combined)

    print(f"[Viz] UMAP 降维 ({combined.shape[0]} 向量 → 3D)...")
    n_neighbors = min(15, combined.shape[0] - 1)
    reducer = umap.UMAP(n_components=3, random_state=42, n_neighbors=n_neighbors)
    emb_3d = reducer.fit_transform(combined)

    CATEGORY_COLORS = [
        ("MCP v1",      "#4285F4"),
        ("MCP v2",      "#34A853"),
        ("Transport",   "#FBBC05"),
        ("Noise: REST", "#EA4335"),
        ("Noise: WS",   "#FF6D01"),
    ]
    query_color = "#F9AB00"

    fig = go.Figure()
    # 按类别分组，每个类别一个 trace（自动生成legend图例）
    for cat_name, cat_color in CATEGORY_COLORS:
        idxs = [i for i, l in enumerate(labels[:num_docs]) if l == cat_name]
        if not idxs:
            continue
        xs = emb_3d[idxs, 0]
        ys = emb_3d[idxs, 1]
        zs = emb_3d[idxs, 2]
        fig.add_trace(go.Scatter3d(
            x=xs, y=ys, z=zs, mode="markers",
            marker=dict(size=5, color=cat_color, symbol="circle", opacity=0.8),
            text=[f"{labels[i]}<br>{doc_texts[i][:80]}..." for i in idxs],
            hoverinfo="text", name=cat_name,
        ))
    # 未分类文档（兜底）
    unknown_idxs = [i for i, l in enumerate(labels[:num_docs]) if l not in dict(CATEGORY_COLORS)]
    if unknown_idxs:
        fig.add_trace(go.Scatter3d(
            x=emb_3d[unknown_idxs, 0], y=emb_3d[unknown_idxs, 1],
            z=emb_3d[unknown_idxs, 2], mode="markers",
            marker=dict(size=5, color="#9AA0A6", symbol="circle", opacity=0.6),
            text=[f"{labels[i]}<br>{doc_texts[i][:80]}..." for i in unknown_idxs],
            hoverinfo="text", name="其他",
        ))
    # 查询点
    if len(labels) > num_docs:
        fig.add_trace(go.Scatter3d(
            x=emb_3d[num_docs:, 0], y=emb_3d[num_docs:, 1],
            z=emb_3d[num_docs:, 2], mode="markers",
            marker=dict(size=10, color=query_color, symbol="diamond"),
            text=[labels[i] for i in range(num_docs, len(labels))],
            hoverinfo="text", name="查询",
        ))

    fig.update_layout(
        title="MCP 知识库 — Embedding UMAP 3D 投影",
        scene=dict(xaxis_title="UMAP 1", yaxis_title="UMAP 2", zaxis_title="UMAP 3"),
        template="plotly_dark", height=700,
    )

    output_path = output_path or str(OUTPUT_DIR / "embedding_umap_3d.html")
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    fig.write_html(output_path)
    print(f"[Viz] ✅ 本地 UMAP 3D: {output_path}")
    return True


def _load_from_chromadb(chunk_size):
    import chromadb
    name = f"mcp_knowledge_base_cs{chunk_size}"
    try:
        c = chromadb.PersistentClient(path=CHROMA_DB_PATH)
        col = c.get_collection(name)
        return col.get(include=["documents"])["documents"] or []
    except Exception:
        return []


def visualize_embeddings(document_texts=None, query_texts=None,
                         chunk_size=DEFAULT_CHUNK_SIZE, output_path=None):
    if document_texts is None:
        document_texts = _load_from_chromadb(chunk_size)
    if not document_texts:
        return {"success": False, "error": "无文档，请先运行 build_kb.py"}

    print(f"[Viz] {len(document_texts)} 个文档块, 编码中...")
    doc_vecs = _embed_texts(document_texts)
    query_vecs = _embed_texts(query_texts) if query_texts else None

    print("[Viz] 本地 UMAP + Plotly 3D")
    ok = visualize_local_umap(document_texts, doc_vecs, query_texts,
                               query_vecs, output_path)
    return {"success": ok, "method": "local_umap",
            "num_documents": len(document_texts),
            "output_path": output_path}


def main():
    import argparse
    p = argparse.ArgumentParser(description="Embedding 降维可视化")
    p.add_argument("--output", "-o", help="输出 HTML 路径")
    args = p.parse_args()

    print("=" * 60)
    print("RAG Embedding 降维可视化")
    print("=" * 60)
    result = visualize_embeddings(
        query_texts=DEFAULT_QUERIES,
        output_path=args.output,
    )
    print(f"\n结果: {json.dumps(result, ensure_ascii=False, indent=2)}")


if __name__ == "__main__":
    main()
