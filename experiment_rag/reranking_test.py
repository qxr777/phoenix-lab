#!/usr/bin/env python3
"""
重排效果对比实验 (Reranking Impact Analysis)

实验设计:
  1. 对同一组查询分别运行"无重排"和"有重排"两种检索
  2. 无重排: 检索 Top-3 直接用于生成
  3. 有重排: 检索 Top-20 → Cross-Encoder 重排 → 取 Top-3
  4. 使用 LLM-as-Judge 评判答案准确率和幻觉率
  5. 统计重排"修复"的查询数量

用法:
  python reranking_test.py
  python reranking_test.py --queries 15 --output report.json
"""

import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv
load_dotenv()

from experiment_rag.config import (
    CHROMA_DB_PATH, DEFAULT_CHUNK_SIZE, DEFAULT_TOP_K,
    LLM_MODEL, RERANKER_MODEL,
)

TEST_QUERIES = [
    "MCP v1 和 v2 的初始化握手有什么不同？",
    "MCP 协议的流式响应是如何实现的？",
    "stdio 传输和 SSE 传输各自的适用场景是什么？",
    "WebSocket 的帧协议和 MCP v2 的帧协议有何异同？",
    "MCP 的工具调用确认机制是什么？",
    "MCP 协议如何实现客户端和服务器的能力协商？",
    "MCP v2 新增的会话管理功能包括哪些？",
    "JSON-RPC 请求在 MCP 中如何被传输和处理？",
    "MCP 中怎样实现长时间运行任务的进度报告？",
    "SSL/TLS 在 MCP SSE 传输中的作用是什么？",
]


def _get_reranker():
    from sentence_transformers import CrossEncoder
    return CrossEncoder(RERANKER_MODEL)


def _get_llm():
    from openai import OpenAI
    return OpenAI()


def _judge_answer(question: str, answer: str) -> dict:
    prompt = f"""你是一个答案质量评判专家。
用户问题: "{question}"
系统回答: "{answer}"

请从以下维度评判(0-10):
  1. 准确性: 回答是否事实正确？
  2. 完整性: 回答是否覆盖了问题的核心点？
  3. 幻觉: 回答中是否包含编造或错误信息？(0=无幻觉, 10=全是幻觉)

以 JSON 回复:
{{"accuracy": <0-10>, "completeness": <0-10>,
  "hallucination": <0-10>, "overall": <0-10>,
  "is_hallucination": <true/false>}}"""
    try:
        llm = _get_llm()
        resp = llm.chat.completions.create(
            model=LLM_MODEL, temperature=0,
            messages=[{"role": "user", "content": prompt}],
        )
        content = resp.choices[0].message.content or "{}"
        import re
        match = re.search(r'\{[^{}]*\}', content)
        if match:
            return json.loads(match.group())
        return {"overall": 0, "is_hallucination": False}
    except Exception as e:
        return {"overall": 0, "is_hallucination": False, "error": str(e)}


def _query_rag(question, chunk_size, top_k, use_rerank):
    from experiment_rag.rag_agent import query_rag
    retrieval_k = top_k * 4 if use_rerank else top_k
    return query_rag(
        question=question, chunk_size=chunk_size,
        top_k=retrieval_k, use_reranker=use_rerank,
    )


def run_reranking_test(
    queries: list[str] | None = None,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    verbose: bool = True,
) -> dict:
    if queries is None:
        queries = TEST_QUERIES

    report = {
        "chunk_size": chunk_size,
        "total_queries": len(queries),
        "without_rerank": {"results": [], "avg_accuracy": 0, "hallucination_count": 0},
        "with_rerank": {"results": [], "avg_accuracy": 0, "hallucination_count": 0},
        "comparison": [],
    }

    for mode, use_rerank in [("without_rerank", False), ("with_rerank", True)]:
        label = "有重排" if use_rerank else "无重排"
        if verbose:
            print(f"\n{'='*50}")
            print(f" 模式: {label}")
            print(f"{'='*50}")

        for qi, q in enumerate(queries):
            if verbose:
                print(f"\n[{qi+1}/{len(queries)}] {q[:60]}...")

            result = _query_rag(q, chunk_size, top_k=DEFAULT_TOP_K,
                                use_rerank=use_rerank)
            judgement = _judge_answer(q, result.get("llm_answer", ""))

            item = {
                "query": q,
                "answer": result.get("llm_answer", ""),
                "retrieved_count": result.get("retrieved_count", 0),
                "sources": [c.get("title", "") for c in result.get("retrieved_chunks", [])],
                "accuracy": judgement.get("accuracy", 0),
                "completeness": judgement.get("completeness", 0),
                "hallucination_score": judgement.get("hallucination", 0),
                "is_hallucination": judgement.get("is_hallucination", False),
            }
            report[mode]["results"].append(item)

            if item["is_hallucination"]:
                report[mode]["hallucination_count"] += 1

            if verbose:
                status = "⚠️ 幻觉" if item["is_hallucination"] else "✅"
                print(f"  Accuracy: {item['accuracy']}/10 | "
                      f"Hallucination: {item['hallucination_score']}/10 {status}")
                print(f"  Sources: {item['sources']}")
            time.sleep(1)

    # 计算平均值和对比
    for mode in ["without_rerank", "with_rerank"]:
        results = report[mode]["results"]
        if results:
            scores = [r["accuracy"] for r in results]
            report[mode]["avg_accuracy"] = round(sum(scores) / len(scores), 1)
            report[mode]["avg_hallucination"] = round(
                sum(r["hallucination_score"] for r in results) / len(results), 1
            )

    # 逐查询对比
    for i in range(len(queries)):
        w = report["without_rerank"]["results"][i]
        r = report["with_rerank"]["results"][i]
        diff = r["accuracy"] - w["accuracy"]
        report["comparison"].append({
            "query": queries[i][:60],
            "accuracy_diff": diff,
            "hallucination_diff": r["hallucination_score"] - w["hallucination_score"],
            "rescued": diff > 1.0 and w["is_hallucination"] and not r["is_hallucination"],
        })

    rescued_count = sum(1 for c in report["comparison"] if c["rescued"])
    report["rescued_count"] = rescued_count
    report["rescued_pct"] = round(rescued_count / len(queries) * 100, 1)

    improved = sum(1 for c in report["comparison"] if c["accuracy_diff"] > 0)
    report["improved_count"] = improved

    return report


def print_report(report: dict):
    print(f"\n{'=' * 60}")
    print(f"📊 重排效果对比报告")
    print(f"{'=' * 60}")

    print(f"\n{'指标':<20} {'无重排':>10} {'有重排':>10} {'变化':>10}")
    print(f"{'─'*50}")
    w = report["without_rerank"]
    r = report["with_rerank"]
    print(f"{'平均准确率':<20} {w['avg_accuracy']:>8.1f}/10 {r['avg_accuracy']:>8.1f}/10 "
          f"{r['avg_accuracy']-w['avg_accuracy']:>+8.1f}")
    print(f"{'平均幻觉分':<20} {w['avg_hallucination']:>8.1f}/10 "
          f"{r['avg_hallucination']:>8.1f}/10 "
          f"{r['avg_hallucination']-w['avg_hallucination']:>+8.1f}")
    print(f"{'幻觉次数':<20} {w['hallucination_count']:>8} {r['hallucination_count']:>8} "
          f"{r['hallucination_count']-w['hallucination_count']:>+8}")

    print(f"\n{'─'*60}")
    print(f"  🏆 重排效果总结:")
    print(f"     准确率提升: {report['improved_count']}/{report['total_queries']} 个查询")
    print(f"     拯救幻觉: {report['rescued_count']} 个 ({report['rescued_pct']}%)")

    if report['rescued_count'] > 0:
        print(f"\n  被重排拯救的查询:")
        for c in report["comparison"]:
            if c["rescued"]:
                print(f"    ✅ {c['query']}...")

    print(f"\n  💡 结论:")
    if r["avg_accuracy"] > w["avg_accuracy"]:
        print(f"     ✅ 重排显著提升了答案准确率 (+{r['avg_accuracy']-w['avg_accuracy']:.1f})")
    if r["hallucination_count"] < w["hallucination_count"]:
        print(f"     ✅ 重排减少了幻觉次数 ({w['hallucination_count']} → {r['hallucination_count']})")


def main():
    import argparse
    p = argparse.ArgumentParser(description="重排效果对比实验")
    p.add_argument("--chunk-size", type=int, default=DEFAULT_CHUNK_SIZE)
    p.add_argument("--output", "-o", help="JSON 报告路径")
    p.add_argument("--quiet", "-q", action="store_true")
    args = p.parse_args()

    print("=" * 60)
    print("RAG 重排效果对比实验")
    print("=" * 60)
    print(f"分块大小: {args.chunk_size}")
    print(f"测试查询: {len(TEST_QUERIES)} 个")
    print(f"重排模型: {RERANKER_MODEL}\n")

    report = run_reranking_test(
        chunk_size=args.chunk_size,
        verbose=not args.quiet,
    )

    print_report(report)

    if args.output:
        filtered = {k: v for k, v in report.items()
                    if k not in ("without_rerank", "with_rerank")}
        for mode in ["without_rerank", "with_rerank"]:
            filtered[mode] = {
                "avg_accuracy": report[mode]["avg_accuracy"],
                "avg_hallucination": report[mode]["avg_hallucination"],
                "hallucination_count": report[mode]["hallucination_count"],
            }
        path = Path(args.output)
        if not path.is_absolute():
            path = Path(__file__).parent / args.output
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(filtered, ensure_ascii=False, indent=2))
        print(f"\n✅ 报告: {path}")


if __name__ == "__main__":
    main()
