#!/usr/bin/env python3
"""
检索失效诊断器 (Retrieval Failure Diagnosis)

核心功能:
  1. 对预定义的"陷阱查询"运行 RAG 检索
  2. 使用 LLM-as-Judge 评判每个检索块的"真实相关性"(0-10)
  3. 标记"高相似度但低相关性"的块为"检索失效"
  4. 分析失效根因: 分块边界 / 关键词误导 / 噪声干扰
  5. 生成诊断报告

用法:
  python retrieval_diagnosis.py
  python retrieval_diagnosis.py --chunk-size 512 --output report.json
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
    CHROMA_DB_PATH, DEFAULT_CHUNK_SIZE, DEFAULT_TOP_K,
    LLM_MODEL, SIMILARITY_THRESHOLD,
)

TRICKY_QUERIES = [
    {
        "query": "MCP 协议中的 handshake 和 WebSocket 的握手有什么区别？",
        "expected_domain": "MCP protocol",
        "trap": "共享关键词 'handshake'/'握手'，噪声文档 WebSocket 也会匹配",
    },
    {
        "query": "JSON-RPC 格式的消息在传输层如何被封装？",
        "expected_domain": "MCP transport",
        "trap": "REST API 噪声文档也提到 JSON 格式和传输",
    },
    {
        "query": "MCP v2 新增了哪些与 v1 不同的特性？",
        "expected_domain": "MCP v1 vs v2",
        "trap": "v1 和 v2 共享大量概念词，检索容易混淆具体版本差异",
    },
    {
        "query": "stdio 传输方式的连接建立流程是怎样的？",
        "expected_domain": "MCP transport stdio",
        "trap": "WebSocket 文档也讨论了连接建立流程",
    },
    {
        "query": "API 协议中如何实现消息的帧格式和边界检测？",
        "expected_domain": "MCP transport frame protocol",
        "trap": "WebSocket 帧协议的描述与 MCP 帧协议共享术语",
    },
    {
        "query": "MCP 服务器和客户端之间如何进行能力协商？",
        "expected_domain": "MCP initialization",
        "trap": "REST 认证握手也涉及能力/权限协商",
    },
    {
        "query": "长时间运行的工具调用如何报告进度？",
        "expected_domain": "MCP v2 progress notifications",
        "trap": "REST API 分页和异步模式可能被误匹配",
    },
    {
        "query": "SSE 流式传输在 MCP 协议中如何工作？",
        "expected_domain": "MCP SSE transport",
        "trap": "正确的问题，但分块不当可能导致 context 断裂",
    },
]


def _judge_relevance(query: str, chunk_text: str) -> dict:
    # 优先使用 LLM 评判，失败则回退到启发式
    try:
        from openai import OpenAI
        client = OpenAI(timeout=10)
        prompt = f"""你是一个检索质量评判专家。
用户查询: "{query}"
检索到的文档块: ---
{chunk_text[:800]}
---
请评判这个文档块对回答用户查询有多少帮助，打分 0-10。
以 JSON 回复: {{"score": <0-10>, "semantic_match": <0-10>,
 "completeness": <0-10>, "noise_level": <0-10>,
 "reason": "<简要原因>", "is_retrieval_failure": <true/false>}}"""
        resp = client.chat.completions.create(
            model="qwen2.5-7b",
            messages=[{"role": "user", "content": prompt}],
            temperature=0,
            max_tokens=150,
            timeout=8,
        )
        content = resp.choices[0].message.content or "{}"
        try:
            result = json.loads(content)
            return result
        except json.JSONDecodeError:
            import re
            match = re.search(r'\{[^}]+\}', content)
            if match:
                return json.loads(match.group())
    except Exception:
        pass

    # ── 启发式 fallback ──
    query_lower = query.lower()
    chunk_lower = chunk_text.lower()

    keyword_hits = 0
    keywords = [w.strip().lower() for w in query.split() if len(w.strip()) > 2]
    for kw in keywords:
        if kw in chunk_lower:
            keyword_hits += 1
    kw_score = min(8, keyword_hits * 3)

    mcp_terms = {"mcp", "json-rpc", "initialize", "tools/list", "tools/call",
                 "streaming", "session", "stdio", "transport", "sse"}
    noise_terms = {"rest", "websocket", "http upgrade", "tcp", "ws://", "wss://",
                   "crud", "api endpoint", "authorization: bearer"}

    is_mcp_query = any(t in query_lower for t in mcp_terms)
    is_mcp_chunk = any(t in chunk_lower for t in mcp_terms)
    is_noise_chunk = any(t in chunk_lower for t in noise_terms)

    if is_mcp_query and is_mcp_chunk:
        score = max(5, kw_score)
        is_failure = False
        reason = "MCP 术语匹配(启发式)"
    elif is_mcp_query and is_noise_chunk:
        score = min(3, kw_score)
        is_failure = True
        reason = "噪声文档(启发式)"
    else:
        score = min(5, kw_score)
        is_failure = score < 3
        reason = "弱匹配(启发式)"

    return {
        "score": score, "semantic_match": score,
        "completeness": min(4, score), "noise_level": 8 if is_noise_chunk else 3,
        "reason": reason, "is_retrieval_failure": is_failure,
    }


_chroma_client = None

def _retrieve(query: str, chunk_size: int, top_k: int) -> list[dict]:
    global _chroma_client
    if _chroma_client is None:
        _chroma_client = chromadb.PersistentClient(path=CHROMA_DB_PATH)
    name = f"mcp_knowledge_base_cs{chunk_size}"
    try:
        col = _chroma_client.get_collection(name)
    except Exception:
        return []
    results = col.query(
        query_texts=[query], n_results=top_k,
        include=["documents", "metadatas", "distances"],
    )
    chunks = []
    for i in range(len(results["documents"][0])):
        d = results["distances"][0][i]
        chunks.append({
            "text": results["documents"][0][i],
            "score": round(1 - d, 4) if d else 0,
            "source": results["metadatas"][0][i].get("source", ""),
            "title": results["metadatas"][0][i].get("title", ""),
            "doc_type": results["metadatas"][0][i].get("doc_type", ""),
        })
    return chunks


def diagnose_retrieval(
    queries: list[dict] | None = None,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    top_k: int = DEFAULT_TOP_K,
    verbose: bool = True,
) -> dict:
    if queries is None:
        queries = TRICKY_QUERIES

    report = {
        "chunk_size": chunk_size,
        "top_k": top_k,
        "total_queries": len(queries),
        "total_chunks_examined": 0,
        "retrieval_failures": 0,
        "noise_contamination": 0,
        "details": [],
    }

    for qi, q_info in enumerate(queries):
        query = q_info["query"]
        if verbose:
            print(f"\n[{qi+1}/{len(queries)}] 查询: {query}")
            print(f"  陷阱: {q_info['trap']}")

        chunks = _retrieve(query, chunk_size, top_k)
        report["total_chunks_examined"] += len(chunks)

        query_detail = {
            "query": query,
            "expected_domain": q_info["expected_domain"],
            "retrieved": [],
            "failure_count": 0,
        }

        for ci, chunk in enumerate(chunks):
            judgement = _judge_relevance(query, chunk["text"])

            is_failure = (
                chunk["doc_type"] == "noise" or
                judgement.get("is_retrieval_failure", False) or
                judgement.get("score", 5) < 5
            )

            if is_failure:
                report["retrieval_failures"] += 1
                query_detail["failure_count"] += 1
                if chunk["doc_type"] == "noise":
                    report["noise_contamination"] += 1

            item = {
                "rank": ci + 1,
                "source": f"{chunk['title']} ({chunk['doc_type']})",
                "similarity_score": chunk["score"],
                "judgement_score": judgement.get("score", 0),
                "is_failure": is_failure,
                "failure_reason": "",
            }

            if is_failure:
                reasons = []
                if chunk["doc_type"] == "noise":
                    reasons.append("噪声文档污染")
                if judgement.get("semantic_match", 0) < 5:
                    reasons.append("语义不匹配")
                if judgement.get("completeness", 0) < 3:
                    reasons.append("信息碎片化(分块不当)")
                item["failure_reason"] = "; ".join(reasons)

            query_detail["retrieved"].append(item)

            if verbose:
                status = "❌ 失效" if is_failure else "✅ 有效"
                print(f"  [{ci+1}] {item['source']} "
                      f"sim={item['similarity_score']:.3f} "
                      f"judge={item['judgement_score']} {status}")
                if is_failure:
                    print(f"       原因: {item['failure_reason']}")

        report["details"].append(query_detail)
        time.sleep(1)

    report["failure_rate"] = round(
        report["retrieval_failures"] / max(report["total_chunks_examined"], 1) * 100, 1
    )
    report["noise_contamination_rate"] = round(
        report["noise_contamination"] / max(report["total_chunks_examined"], 1) * 100, 1
    )

    return report


def print_report(report: dict):
    print(f"\n{'=' * 60}")
    print(f"📊 检索失效诊断报告")
    print(f"{'=' * 60}")
    print(f"  分块大小: {report['chunk_size']}")
    print(f"  Top-K: {report['top_k']}")
    print(f"  总查询数: {report['total_queries']}")
    print(f"  检查块数: {report['total_chunks_examined']}")
    print(f"  检索失效: {report['retrieval_failures']} "
          f"({report['failure_rate']}%)")
    print(f"  噪声污染: {report['noise_contamination']} "
          f"({report['noise_contamination_rate']}%)")

    print(f"\n{'─' * 60}")
    print("失效模式分析")
    print(f"{'─' * 60}")

    by_reason = {}
    for detail in report["details"]:
        for item in detail["retrieved"]:
            if item["is_failure"] and item["failure_reason"]:
                parts = item["failure_reason"].split("; ")
                for p in parts:
                    by_reason[p] = by_reason.get(p, 0) + 1

    for reason, count in sorted(by_reason.items(), key=lambda x: -x[1]):
        pct = round(count / max(report["retrieval_failures"], 1) * 100)
        print(f"  {reason}: {count} 次 ({pct}%)")

    print(f"\n  💡 核心发现:")
    if report["noise_contamination_rate"] > 20:
        print(f"  ⚠️ 噪声文档污染严重 ({report['noise_contamination_rate']}%)")
        print(f"     建议: 提高相似度阈值或增加语义分块策略")
    if report["failure_rate"] > 30:
        print(f"  ⚠️ 检索失效率高 ({report['failure_rate']}%)")
        print(f"     建议: 引入重排(Reranking)或调整 chunk_size")


def main():
    import argparse
    p = argparse.ArgumentParser(description="检索失效诊断")
    p.add_argument("--chunk-size", type=int, default=DEFAULT_CHUNK_SIZE)
    p.add_argument("--top-k", type=int, default=DEFAULT_TOP_K)
    p.add_argument("--output", "-o", help="JSON 报告输出路径")
    p.add_argument("--quiet", "-q", action="store_true")
    args = p.parse_args()

    print("=" * 60)
    print("RAG 检索失效诊断")
    print("=" * 60)
    print(f"分块大小: {args.chunk_size}, Top-K: {args.top_k}")
    print(f"陷阱查询: {len(TRICKY_QUERIES)} 个\n")

    report = diagnose_retrieval(
        chunk_size=args.chunk_size,
        top_k=args.top_k,
        verbose=not args.quiet,
    )

    print_report(report)

    if args.output:
        path = Path(args.output)
        if not path.is_absolute():
            path = Path(__file__).parent / args.output
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(report, ensure_ascii=False, indent=2))
        print(f"\n✅ 报告已保存: {path}")


if __name__ == "__main__":
    main()
