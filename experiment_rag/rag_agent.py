#!/usr/bin/env python3
"""
RAG 智能体 — 可配置的检索增强生成引擎（带 Phoenix Trace 集成）

关键特性:
  1. OTLP 自动追踪 OpenAI API 调用（已有）
  2. 自定义 Span 追踪 ChromaDB 检索过程（新增）
  3. --retrieval-only 模式：不调用 LLM，仅检索 → 仍产生 Phoenix trace

用法:
  # 完整 RAG 查询（检索 + LLM 生成）
  ENABLE_PHOENIX_TRACING=true python experiment_rag/rag_agent.py "MCP v1 和 v2 的握手区别？"

  # 仅检索模式（无需 LLM API Key，也会产生 Phoenix trace）
  ENABLE_PHOENIX_TRACING=true python experiment_rag/rag_agent.py "MCP v1 握手" --retrieval-only

  # 交互模式
  ENABLE_PHOENIX_TRACING=true python experiment_rag/rag_agent.py --interactive
"""

import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import chromadb
from dotenv import load_dotenv

load_dotenv()

from experiment_rag.config import (
    CHROMA_DB_PATH,
    DEFAULT_CHUNK_SIZE,
    DEFAULT_TOP_K,
    LLM_MODEL,
    PHOENIX_HOST,
    RERANKER_MODEL,
    SIMILARITY_THRESHOLD,
    USE_RERANKER,
)

# ──────────────────────────────────────────────
#  Phoenix OTLP Tracing
# ──────────────────────────────────────────────

_tracer = None
_phoenix_enabled = False

if os.getenv("ENABLE_PHOENIX_TRACING", "").lower() in ("true", "1", "yes"):
    from opentelemetry import trace
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor

    trace.set_tracer_provider(TracerProvider())
    trace.get_tracer_provider().add_span_processor(
        SimpleSpanProcessor(OTLPSpanExporter(endpoint=PHOENIX_HOST + "/v1/traces"))
    )
    _tracer = trace.get_tracer("rag.agent")
    _phoenix_enabled = True

    from openinference.instrumentation.openai import OpenAIInstrumentor
    OpenAIInstrumentor().instrument()
    print(f"[Phoenix] OTLP Tracing 已启用 → {PHOENIX_HOST}")


# ──────────────────────────────────────────────
#  Reranker (Cross-Encoder)
# ──────────────────────────────────────────────

_reranker = None

def _get_reranker():
    global _reranker
    if _reranker is None:
        try:
            from sentence_transformers import CrossEncoder
            _reranker = CrossEncoder(RERANKER_MODEL)
        except ImportError:
            return None
        except Exception as e:
            print(f"[RAG] 重排模型加载失败: {e}")
            return None
    return _reranker


# ──────────────────────────────────────────────
#  RAG 查询（带 Phoenix Span 追踪）
# ──────────────────────────────────────────────

def query_rag(
    question: str,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    top_k: int = DEFAULT_TOP_K,
    generation_top_k: int | None = None,
    similarity_threshold: float = SIMILARITY_THRESHOLD,
    use_reranker: bool = USE_RERANKER,
    retrieval_only: bool = False,
    verbose: bool = True,
) -> dict:
    collection_name = f"mcp_knowledge_base_cs{chunk_size}"
    chroma_client = chromadb.PersistentClient(path=CHROMA_DB_PATH)

    try:
        collection = chroma_client.get_collection(collection_name)
    except Exception:
        available = [c.name for c in chroma_client.list_collections()]
        return {
            "question": question,
            "error": f"知识库集合 '{collection_name}' 不存在",
            "available_collections": available,
            "retrieved_chunks": [],
        }

    # ── Span 1: ChromaDB 检索 ──
    if _phoenix_enabled and _tracer:
        with _tracer.start_as_current_span("chromadb_retrieval") as span:
            span.set_attribute("query", question)
            span.set_attribute("collection", collection_name)
            span.set_attribute("top_k", top_k)
            span.set_attribute("similarity_threshold", similarity_threshold)

            retrieval_top_k = top_k * 4 if use_reranker else top_k
            results = collection.query(
                query_texts=[question],
                n_results=retrieval_top_k,
                include=["documents", "metadatas", "distances"],
            )

            retrieved_count = len(results["documents"][0])
            span.set_attribute("retrieved_count", retrieved_count)

            chunks = []
            for i in range(retrieved_count):
                distance = results["distances"][0][i]
                similarity = 1 - distance if distance is not None else 0
                if similarity >= similarity_threshold:
                    chunks.append({
                        "text": results["documents"][0][i],
                        "score": round(similarity, 4),
                        "distance": round(distance, 4) if distance else None,
                        "source": results["metadatas"][0][i].get("source", ""),
                        "title": results["metadatas"][0][i].get("title", ""),
                        "doc_type": results["metadatas"][0][i].get("doc_type", ""),
                    })

            span.set_attribute("filtered_count", len(chunks))

            # 将检索结果写入 span 属性供 Phoenix 展示
            for i, c in enumerate(chunks[:5]):
                span.set_attribute(f"retrieved.{i}.title", c["title"])
                span.set_attribute(f"retrieved.{i}.score", c["score"])
                span.set_attribute(f"retrieved.{i}.doc_type", c["doc_type"])
                span.set_attribute(f"retrieved.{i}.preview", c["text"][:120])
    else:
        retrieval_top_k = top_k * 4 if use_reranker else top_k
        results = collection.query(
            query_texts=[question],
            n_results=retrieval_top_k,
            include=["documents", "metadatas", "distances"],
        )
        chunks = []
        for i in range(len(results["documents"][0])):
            distance = results["distances"][0][i]
            similarity = 1 - distance if distance is not None else 0
            if similarity >= similarity_threshold:
                chunks.append({
                    "text": results["documents"][0][i],
                    "score": round(similarity, 4),
                    "distance": round(distance, 4) if distance else None,
                    "source": results["metadatas"][0][i].get("source", ""),
                    "title": results["metadatas"][0][i].get("title", ""),
                    "doc_type": results["metadatas"][0][i].get("doc_type", ""),
                })

    # ── 可选: Cross-Encoder 重排 ──
    gen_k = generation_top_k or top_k
    if use_reranker and len(chunks) > gen_k:
        reranker = _get_reranker()
        if reranker is not None:
            pairs = [(question, c["text"]) for c in chunks]
            scores = reranker.predict(pairs)
            for i, c in enumerate(chunks):
                c["rerank_score"] = round(float(scores[i]), 4)
            chunks.sort(key=lambda x: x["rerank_score"], reverse=True)

    retrieved = chunks[:gen_k]

    # ── Span 2: LLM 生成（或跳过）──
    llm_response = ""
    if retrieval_only:
        llm_response = "[检索仅模式] 未调用 LLM 生成"
    else:
        context_parts = []
        for i, c in enumerate(retrieved):
            context_parts.append(
                f"[文档 {i+1}] (来源: {c['title']}, 类型: {c['doc_type']})\n{c['text']}"
            )
        context = "\n\n---\n\n".join(context_parts)

        system_prompt = f"""你是一个 MCP 协议专家助手。
请根据以下检索到的文档内容回答用户问题。
如果文档内容不足以回答问题，请明确说明，不要编造信息。
回答时请引用文档来源。

检索到的文档内容:
{context}"""

        try:
            from openai import OpenAI
            llm_client = OpenAI()
            if _phoenix_enabled and _tracer:
                with _tracer.start_as_current_span("llm_generation") as span:
                    span.set_attribute("model", LLM_MODEL)
                    span.set_attribute("context_length", len(context))
                    response = llm_client.chat.completions.create(
                        model=LLM_MODEL,
                        messages=[
                            {"role": "system", "content": system_prompt},
                            {"role": "user", "content": question},
                        ],
                        temperature=0,
                    )
                    llm_response = response.choices[0].message.content or ""
                    span.set_attribute("response_length", len(llm_response))
            else:
                response = llm_client.chat.completions.create(
                    model=LLM_MODEL,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": question},
                    ],
                    temperature=0,
                )
                llm_response = response.choices[0].message.content or ""
        except Exception as e:
            llm_response = f"[LLM 调用失败: {e}]"

    if verbose:
        print(f"\n{'='*60}")
        print(f"查询: {question}")
        print(f"知识库: {collection_name} | Top-K: {top_k} | 阈值: {similarity_threshold}")
        if _phoenix_enabled:
            print(f"Phoenix: {Colors.GREEN}✓ Trace 已发送{Colors.RESET}")
        print(f"{'='*60}")
        print(f"检索到 {len(retrieved)} 个相关文档块:")
        for i, c in enumerate(retrieved):
            print(f"\n  [{i+1}] {c['title']} ({c['doc_type']}) — 相似度: {c['score']}")
            if c['doc_type'] == 'noise':
                print(f"       ⚠️ 噪声文档！")
            print(f"      {c['text'][:120]}...")
        if not retrieval_only:
            print(f"\n{'='*60}")
            print(f"LLM 回答:\n{llm_response}")
        print(f"{'='*60}")

    return {
        "question": question,
        "chunk_size": chunk_size,
        "top_k": top_k,
        "use_reranker": use_reranker,
        "retrieved_chunks": retrieved,
        "retrieved_count": len(retrieved),
        "llm_answer": llm_response,
        "phoenix_enabled": _phoenix_enabled,
        "collection_name": collection_name,
    }


class Colors:
    GREEN = "\033[92m"
    RED = "\033[91m"
    YELLOW = "\033[93m"
    CYAN = "\033[96m"
    BOLD = "\033[1m"
    RESET = "\033[0m"


# ──────────────────────────────────────────────
#  CLI
# ──────────────────────────────────────────────

def main():
    import argparse
    parser = argparse.ArgumentParser(description="RAG 查询工具（带 Phoenix Trace）")
    parser.add_argument("query", nargs="?", help="查询文本")
    parser.add_argument("--chunk-size", type=int, default=DEFAULT_CHUNK_SIZE)
    parser.add_argument("--top-k", type=int, default=DEFAULT_TOP_K)
    parser.add_argument("--rerank", action="store_true", help="启用 Cross-Encoder 重排")
    parser.add_argument("--threshold", type=float, default=SIMILARITY_THRESHOLD)
    parser.add_argument("--retrieval-only", action="store_true",
                        help="仅检索不调用 LLM（无需 API Key，仍产生 Phoenix trace）")
    parser.add_argument("--interactive", "-i", action="store_true", help="交互模式")
    parser.add_argument("--quiet", "-q", action="store_true", help="简洁输出")

    args = parser.parse_args()

    if args.interactive:
        print(f"\n{Colors.CYAN}RAG 查询交互模式{Colors.RESET}")
        print(f"知识库: mcp_knowledge_base_cs{args.chunk_size}")
        if _phoenix_enabled:
            print(f"Phoenix: {Colors.GREEN}已连接 → {PHOENIX_HOST}{Colors.RESET}")
        else:
            print(f"Phoenix: {Colors.YELLOW}未启用（设置 ENABLE_PHOENIX_TRACING=true）{Colors.RESET}")
        print(f"模式: {'仅检索' if args.retrieval_only else '检索 + LLM 生成'}")
        print(f"(输入 'quit' 退出)\n")
        qnum = 0
        while True:
            q = input(f"{Colors.BOLD}查询 [{qnum+1}]: {Colors.RESET}").strip()
            if q.lower() in ("quit", "exit", "q"):
                break
            if not q:
                continue
            query_rag(q, chunk_size=args.chunk_size, top_k=args.top_k,
                      use_reranker=args.rerank, retrieval_only=args.retrieval_only,
                      verbose=not args.quiet)
            qnum += 1
        return

    if not args.query:
        parser.error("请提供查询文本或使用 --interactive 进入交互模式")

    result = query_rag(
        args.query,
        chunk_size=args.chunk_size,
        top_k=args.top_k,
        use_reranker=args.rerank,
        retrieval_only=args.retrieval_only,
        verbose=not args.quiet,
    )

    if args.quiet:
        print(json.dumps({
            "question": result["question"],
            "retrieved_count": result["retrieved_count"],
            "top_titles": [c["title"] for c in result["retrieved_chunks"][:3]],
            "llm_answer": result["llm_answer"][:100] + "..." if len(result["llm_answer"]) > 100 else result["llm_answer"],
            "phoenix_enabled": result["phoenix_enabled"],
        }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
