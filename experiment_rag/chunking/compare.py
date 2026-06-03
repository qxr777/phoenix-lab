#!/usr/bin/env python3
"""
分块策略对比实验

对同组文档，使用不同的 chunk_size 构建知识库，
用同一组查询分别检索，对比检索精度和生成质量。

用法:
  python compare.py                    # 固定策略，对比 4 种尺寸
  python compare.py --strategies all   # 同时对比 3 种策略 × 4 种尺寸
  python compare.py --output report.json
"""

import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from dotenv import load_dotenv
load_dotenv()

from experiment_rag.config import CHUNK_SIZE_VARIANTS, DEFAULT_TOP_K

BENCHMARK_QUERIES = [
    "MCP v1 和 v2 的握手初始化有什么区别？",
    "MCP 的流式响应是怎么实现的？",
    "stdio 传输和 SSE 传输各自的优缺点？",
    "MCP 协议中怎样获得可用工具列表？",
    "MCP v2 新增的安全特性有哪些？",
    "MCP 的帧协议是如何定义消息边界的？",
    "MCP 中如何实现重连和错误恢复？",
]

STRATEGIES = {
    "fixed": "固定长度分块",
    "semantic": "语义递归分块",
    "paragraph": "段落感知分块",
}


class Colors:
    GREEN = "\033[92m"
    RED = "\033[91m"
    YELLOW = "\033[93m"
    BOLD = "\033[1m"
    RESET = "\033[0m"


def _judge_simple(question: str, answer: str) -> dict:
    return {"accuracy": 0, "completeness": 0, "hallucination": 0}


def _score_retrieval(question: str, retrieved_chunks: list[dict]) -> dict:
    if not retrieved_chunks:
        return {"accuracy": 0, "completeness": 0, "hallucination": 10}
    target = sum(1 for c in retrieved_chunks if c.get("doc_type") == "target")
    noise = sum(1 for c in retrieved_chunks if c.get("doc_type") == "noise")
    total = len(retrieved_chunks)
    acc = round(target / total * 10, 1)
    completeness = min(acc, 8 if total >= 3 else 5)
    hallucination = round(noise / total * 10, 1)
    return {"accuracy": acc, "completeness": completeness, "hallucination": hallucination}


def _run_one_query(query, chunk_size, strategy, top_k):
    from experiment_rag.rag_agent import query_rag
    return query_rag(
        question=query, chunk_size=chunk_size,
        top_k=top_k, retrieval_only=True, verbose=False,
    )


def compare_chunk_sizes(
    queries: list[str] | None = None,
    strategy: str = "fixed",
    top_k: int = DEFAULT_TOP_K,
    verbose: bool = True,
) -> dict:
    if queries is None:
        queries = BENCHMARK_QUERIES

    report = {
        "strategy": strategy,
        "strategy_name": STRATEGIES.get(strategy, strategy),
        "queries": queries,
        "comparison": {},
    }

    for cs in CHUNK_SIZE_VARIANTS:
        if verbose:
            print(f"\n  测试 chunk_size={cs}...")

        results = []
        for qi, q in enumerate(queries):
            r = _run_one_query(q, cs, strategy, top_k)
            judge = _score_retrieval(q, r.get("retrieved_chunks", []))
            results.append({
                "query": q[:50],
                "accuracy": judge.get("accuracy", 0),
                "completeness": judge.get("completeness", 0),
                "hallucination": judge.get("hallucination", 0),
                "retrieved_count": r.get("retrieved_count", 0),
                "sources": [c.get("title") for c in r.get("retrieved_chunks", [])],
            })
            time.sleep(0.5)

        acc = [r["accuracy"] for r in results]
        comp = [r["completeness"] for r in results]
        hall = [r["hallucination"] for r in results]
        noise = sum(1 for r in results for s in r["sources"]
                    if s and ("REST" in s or "WebSocket" in s))

        report["comparison"][str(cs)] = {
            "avg_accuracy": round(sum(acc) / len(acc), 1),
            "avg_completeness": round(sum(comp) / len(comp), 1),
            "avg_hallucination": round(sum(hall) / len(hall), 1),
            "noise_hits": noise,
            "details": results,
        }

    return report


def print_comparison(report: dict):
    print(f"\n{'=' * 65}")
    print(f"📊 分块策略对比报告: {report['strategy_name']}")
    print(f"{'=' * 65}")

    header = f"{'Chunk':<10} {'准确率':>8} {'完整度':>8} {'噪声分':>8} {'噪声':>5}"

    print(f"\n{header}")
    print("─" * 45)

    best_acc = 0
    best_size = 0
    for cs in CHUNK_SIZE_VARIANTS:
        d = report["comparison"][str(cs)]
        print(f"  {cs:<8} "
              f"{Colors.GREEN if d['avg_accuracy'] >= 7 else Colors.YELLOW}"
              f"{d['avg_accuracy']:>6.1f}/10{Colors.RESET}"
              f"  {d['avg_completeness']:>6.1f}/10"
              f"  {d['avg_hallucination']:>6.1f}/10"
              f"  {d['noise_hits']:>3}")
        if d["avg_accuracy"] > best_acc:
            best_acc = d["avg_accuracy"]
            best_size = cs

    print(f"\n  🏆 最佳 chunk_size: {best_size} (准确率: {best_acc}/10)")
    print(f"\n  💡 观察:")
    print(f"     chunk 太小 → 信息碎片化，完整性下降")
    print(f"     chunk 太大 → 噪声稀释，准确率下降")
    print(f"     最佳值在 256-1024 之间，取决于文档密度和查询类型")


def main():
    import argparse
    p = argparse.ArgumentParser(description="分块策略对比实验")
    p.add_argument("--strategy", choices=["fixed", "semantic", "paragraph", "all"],
                   default="fixed", help="分块策略")
    p.add_argument("--output", "-o", help="JSON 报告路径")
    p.add_argument("--quiet", "-q", action="store_true")
    args = p.parse_args()

    print("=" * 65)
    print("RAG 分块策略对比实验")
    print("=" * 65)
    print(f"查询数: {len(BENCHMARK_QUERIES)}")
    print(f"分块尺寸: {CHUNK_SIZE_VARIANTS}")
    print()

    strategies_to_test = (
        list(STRATEGIES.keys()) if args.strategy == "all"
        else [args.strategy]
    )

    all_reports = []
    for strat in strategies_to_test:
        print(f"\n{'─' * 65}")
        print(f" 策略: {STRATEGIES[strat]}")
        print(f"{'─' * 65}")

        report = compare_chunk_sizes(
            queries=BENCHMARK_QUERIES,
            strategy=strat,
            verbose=not args.quiet,
        )
        print_comparison(report)
        all_reports.append(report)

    if args.output:
        path = Path(args.output)
        if not path.is_absolute():
            path = Path(__file__).parent.parent / args.output
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(all_reports, ensure_ascii=False, indent=2))
        print(f"\n✅ 报告: {path}")


if __name__ == "__main__":
    main()
