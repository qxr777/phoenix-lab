#!/usr/bin/env python3
"""
重排效果对比实验 (Reranking Impact Analysis)

实验设计:
  1. 对同一组查询分别运行"无重排"和"有重排"两种检索
  2. 无重排: 检索 Top-5 → 统计噪声文档数
  3. 有重排: 检索 Top-20 → Cross-Encoder 重排 → 取 Top-5 → 统计噪声文档数
  4. 对比重排前后噪声文档被消除的数量
  5. 展示 Cross-Encoder 如何"踢出"噪声文档

用法:
  python reranking_test.py
  python reranking_test.py --chunk-size 512 --output report.json
"""

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from experiment_rag.config import (
    CHROMA_DB_PATH, DEFAULT_CHUNK_SIZE, DEFAULT_TOP_K,
    RERANKER_MODEL,
)

# 复用实验 2B 的陷阱查询 — Bi-Encoder 容易误检噪声文档
TEST_QUERIES = [
    "MCP 协议中的 handshake 和 WebSocket 的握手有什么区别？",
    "JSON-RPC 格式的消息在传输层如何被封装？",
    "MCP v2 新增了哪些与 v1 不同的特性？",
    "stdio 传输方式的连接建立流程是怎样的？",
    "API 协议中如何实现消息的帧格式和边界检测？",
    "MCP 服务器和客户端之间如何进行能力协商？",
    "长时间运行的工具调用如何报告进度？",
    "SSE 流式传输在 MCP 协议中如何工作？",
]


def _get_reranker():
    from sentence_transformers import CrossEncoder
    return CrossEncoder(RERANKER_MODEL)


def _query_rag_noise(question, chunk_size, top_k, use_rerank):
    """检索并统计 noise/target 文档数（不调用 LLM 生成）"""
    from experiment_rag.rag_agent import query_rag
    retrieval_k = top_k * 4 if use_rerank else top_k
    gen_k = top_k if use_rerank else retrieval_k
    result = query_rag(
        question=question, chunk_size=chunk_size,
        top_k=retrieval_k, generation_top_k=gen_k,
        use_reranker=use_rerank,
        retrieval_only=True, verbose=False,
    )
    chunks = result.get("retrieved_chunks", [])
    noise_titles = []
    target_count = 0
    for c in chunks:
        if c.get("doc_type") == "noise":
            noise_titles.append(c.get("title", "?"))
        else:
            target_count += 1
    return {
        "noise_titles": noise_titles,
        "noise_count": len(noise_titles),
        "target_count": target_count,
        "total": len(chunks),
        "chunks": chunks,
    }


def _run_one_mode(queries, chunk_size, top_k, use_rerank, verbose, label):
    results = []
    if verbose:
        print(f"\n[{label}]")
    for qi, q in enumerate(queries):
        if verbose:
            status = f"  {qi+1}/{len(queries)} 处理中: {q[:55]}..."
            print(f"\r\033[K{status}", end="", flush=True)
        info = _query_rag_noise(q, chunk_size, top_k, use_rerank)
        results.append(info)
        if verbose:
            nc = info["noise_count"]
            icon = "🚫" if nc > 0 else "✅"
            noise_detail = ", ".join(info["noise_titles"][:3]) if nc > 0 else ""
            if nc > 3:
                noise_detail += f"... +{nc-3}"
            done = f"  {qi+1}/{len(queries)}: {icon} noise={nc}"
            if noise_detail:
                done += f"  ({noise_detail})"
            print(f"\r\033[K{done}")
            sys.stdout.flush()
    return results


def run_reranking_test(
    queries: list[str] | None = None,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    verbose: bool = True,
) -> dict:
    if queries is None:
        queries = TEST_QUERIES

    without = _run_one_mode(queries, chunk_size, DEFAULT_TOP_K, False, verbose, "无重排")
    with_rerank = _run_one_mode(queries, chunk_size, DEFAULT_TOP_K, True, verbose, "有重排")

    comparison = []
    rescued_count = 0
    total_noise_before = 0
    total_noise_after = 0

    for i in range(len(queries)):
        w = without[i]
        r = with_rerank[i]
        nb = w["noise_count"]
        na = r["noise_count"]
        total_noise_before += nb
        total_noise_after += na
        rescued = nb > 0 and na < nb
        if rescued:
            rescued_count += 1
        comparison.append({
            "query": queries[i][:60],
            "noise_before": nb,
            "noise_after": na,
            "noise_reduced": nb - na,
            "rescued": rescued,
        })

    return {
        "chunk_size": chunk_size,
        "total_queries": len(queries),
        "total_noise_before": total_noise_before,
        "total_noise_after": total_noise_after,
        "noise_eliminated": total_noise_before - total_noise_after,
        "rescued_count": rescued_count,
        "rescued_pct": round(rescued_count / len(queries) * 100, 1),
        "without_rerank": without,
        "with_rerank": with_rerank,
        "comparison": comparison,
    }


def print_report(report: dict):
    w_results = report["without_rerank"]
    r_results = report["with_rerank"]
    comp = report["comparison"]

    print(f"\n{'=' * 60}")
    print(f"📊 重排效果对比报告 — 噪声文档消除")
    print(f"{'=' * 60}")

    # ── 整体指标 ──
    print(f"\n  {'指标':<22} {'无重排':>8} {'有重排':>8} {'消除':>8}")
    print(f"  {'─' * 46}")
    print(f"  {'Top-5 噪声文档总数':<22} {report['total_noise_before']:>8} "
          f"{report['total_noise_after']:>8} {report['noise_eliminated']:>8}")
    print(f"  {'噪声查询数':<22} "
          f"{sum(1 for c in comp if c['noise_before'] > 0):>8} "
          f"{sum(1 for c in comp if c['noise_after'] > 0):>8} "
          f"{sum(1 for c in comp if c['noise_before'] > 0 and c['noise_after'] < c['noise_before']):>8}")

    # ── 逐查询对比表 ──
    print(f"\n{'─' * 70}")
    print(f"  {'查询':<38} {'无重排':>6} {'有重排':>6} {'消除':>5}")
    print(f"  {'─' * 70}")
    for i, (wi, ri, ci) in enumerate(zip(w_results, r_results, comp)):
        q_short = TEST_QUERIES[i][:37] if i < len(TEST_QUERIES) else f"query_{i}"
        nb = ci["noise_before"]
        na = ci["noise_after"]
        reduced = nb - na
        icon_before = "🚫" if nb > 0 else "✅"
        icon_after = "✅" if na == 0 else ("🚫" if na > 0 else "✅")
        trophy = " 🏆" if ci["rescued"] else ""
        print(f"  {q_short:<38} {icon_before} {nb:>2}  {icon_after} {na:>2}  {reduced:>5}{trophy}")

    # ── 总结 ──
    print(f"\n{'─' * 60}")
    print(f"  🏆 Cross-Encoder 消除了 {report['noise_eliminated']} 个噪声文档  |  "
          f"拯救了 {report['rescued_count']}/{report['total_queries']} 个查询 ({report['rescued_pct']}%)")

    # ── 教学结论 ──
    if report["noise_eliminated"] > 0:
        print(f"\n  💡 Cross-Encoder 重排有效过滤了噪声文档:")
        print(f"     重排前噪声总数: {report['total_noise_before']} → 重排后: {report['total_noise_after']}")
        print(f"     代价: Top-5 → Top-20 检索 (多 ~15 chunks) + Cross-Encoder 评分 (~1s)")
        print(f"     原理: Bi-Encoder 被共享关键词误导 → Cross-Encoder 逐对理解语义 → 降权噪声")

    # ── 被拯救查询的深度分析 ──
    rescued_indices = [i for i, c in enumerate(comp) if c["rescued"]]
    if rescued_indices:
        print(f"\n{'─' * 60}")
        print(f"  🔍 被拯救查询详情 (Cross-Encoder 踢出了哪些噪声)")
        print(f"{'─' * 60}")
        for idx in rescued_indices[:3]:
            wi = w_results[idx]
            ri = r_results[idx]
            q_text = TEST_QUERIES[idx][:70] if idx < len(TEST_QUERIES) else f"query_{idx}"
            nb = comp[idx]["noise_before"]
            na = comp[idx]["noise_after"]
            print(f"\n  📝 查询{idx+1}: \"{q_text}...\"")
            print(f"    无重排: 🚫 {nb} 个噪声 → {', '.join(wi['noise_titles'][:4])}")
            print(f"    有重排: 🏆 {na} 个噪声" + (f" → {', '.join(ri['noise_titles'][:4])}" if na > 0 else " (全部清除!)"))
        if len(rescued_indices) > 3:
            print(f"\n  ... 还有 {len(rescued_indices) - 3} 个被拯救查询")


def main():
    import argparse
    p = argparse.ArgumentParser(description="重排效果对比实验")
    p.add_argument("--chunk-size", type=int, default=DEFAULT_CHUNK_SIZE)
    p.add_argument("--output", "-o", help="JSON 报告路径")
    p.add_argument("--quiet", "-q", action="store_true")
    args = p.parse_args()

    print("=" * 60)
    print("RAG 重排效果对比实验 — 噪声文档消除")
    print("=" * 60)
    print(f"分块大小: {args.chunk_size}")
    print(f"测试查询: {len(TEST_QUERIES)} 个 (陷阱查询)")
    print(f"重排模型: {RERANKER_MODEL}\n")

    report = run_reranking_test(
        chunk_size=args.chunk_size,
        verbose=not args.quiet,
    )

    print_report(report)

    if args.output:
        path = Path(args.output)
        if not path.is_absolute():
            path = Path(__file__).parent / args.output
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(report, ensure_ascii=False, indent=2))
        print(f"\n✅ 报告: {path}")


if __name__ == "__main__":
    main()
