#!/usr/bin/env python3
"""
重排效果对比实验 (Reranking Impact Analysis)

实验设计:
  1. 对同一组查询分别运行"无重排"和"有重排"两种检索
  2. 无重排: 检索 Top-5 直接用于生成
  3. 有重排: 检索 Top-20 → Cross-Encoder 重排 → 取 Top-5
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


def _get_llm():
    from openai import OpenAI
    return OpenAI(timeout=120)


def _judge_answer(question: str, answer: str) -> dict:
    prompt = f"""你是一个严格的答案质量评判专家。
用户问题: "{question}"
系统回答: "{answer}"

请从以下维度评判(0-10)，严格对照评分标准打分:

1. 准确性: 回答中的事实陈述是否正确？
   - 10: 所有陈述都可以被知识库验证，无任何事实错误
   - 7-9: 大部分正确，有轻微不精确或遗漏
   - 4-6: 部分正确，但有明显错误或概念混淆
   - 1-3: 大部分错误或无关

2. 完整性: 回答是否覆盖了问题的核心点？
   - 10: 全面覆盖所有关键维度，深度充分
   - 7-9: 覆盖主要维度，缺少一些细节
   - 4-6: 只覆盖部分内容，遗漏重要信息
   - 1-3: 严重不完整，几乎没有实质内容

3. 幻觉: 回答中是否包含编造或与知识库矛盾的信息？
   - 0: 无任何编造或错误信息
   - 1-3: 轻微不精确，不属于编造
   - 4-6: 有可疑或无法验证的表述
   - 7-10: 明显编造，与事实严重矛盾

overall 是综合考虑后的整体评分(0-10):
   - 准确+完整+无幻觉 → 高分
   - 准确但不完整 → 中等
   - 不准确或有幻觉 → 低分

is_hallucination 判定: hallucination >= 2 则为 true（低分也可疑）

以 JSON 回复（严格只有 JSON，不要其他任何内容）:
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
    gen_k = top_k if use_rerank else retrieval_k
    return query_rag(
        question=question, chunk_size=chunk_size,
        top_k=retrieval_k, generation_top_k=gen_k,
        use_reranker=use_rerank,
        verbose=False,
    )


def _status_icon(item: dict) -> str:
    if item["is_hallucination"]:
        return "⚠️"
    return "✅"


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
        "without_rerank": {"results": [], "avg_accuracy": 0, "avg_hallucination": 0, "hallucination_count": 0},
        "with_rerank": {"results": [], "avg_accuracy": 0, "avg_hallucination": 0, "hallucination_count": 0},
        "comparison": [],
    }

    # ── 模式 1: 无重排 ──
    if verbose:
        print("[无重排]")
    for qi, q in enumerate(queries):
        if verbose:
            label = f"  {qi+1}/{len(queries)} 处理中: {q[:60]}..."
            print(f"\r\033[K{label}", end="", flush=True)
        result = _query_rag(q, chunk_size, top_k=DEFAULT_TOP_K, use_rerank=False)
        judgement = _judge_answer(q, result.get("llm_answer", ""))
        item = {
            "query": q,
            "answer": result.get("llm_answer", ""),
            "retrieved_count": result.get("retrieved_count", 0),
            "sources": [c.get("title", "") for c in result.get("retrieved_chunks", [])[:5]],
            "chunks_original": result.get("retrieved_chunks", []),
            "accuracy": judgement.get("accuracy", 0),
            "completeness": judgement.get("completeness", 0),
            "hallucination_score": judgement.get("hallucination", 0),
            "is_hallucination": judgement.get("is_hallucination", False),
        }
        report["without_rerank"]["results"].append(item)
        if item["is_hallucination"]:
            report["without_rerank"]["hallucination_count"] += 1
        if verbose:
            icon = _status_icon(item)
            judge = f"acc:{item['accuracy']:.0f} com:{item['completeness']:.0f} hal:{item['hallucination_score']:.0f} ovr:{judgement.get('overall',0):.0f}"
            done = f"  {qi+1}/{len(queries)}: {icon} {{{judge}}}  {q[:45]}..."
            print(f"\r\033[K{done}")
            sys.stdout.flush()
        time.sleep(0.3)

    # ── 模式 2: 有重排 ──
    if verbose:
        print("\n[有重排]")
    for qi, q in enumerate(queries):
        if verbose:
            label = f"  {qi+1}/{len(queries)} 处理中: {q[:60]}..."
            print(f"\r\033[K{label}", end="", flush=True)
        result = _query_rag(q, chunk_size, top_k=DEFAULT_TOP_K, use_rerank=True)
        judgement = _judge_answer(q, result.get("llm_answer", ""))
        item = {
            "query": q,
            "answer": result.get("llm_answer", ""),
            "retrieved_count": result.get("retrieved_count", 0),
            "sources": [c.get("title", "") for c in result.get("retrieved_chunks", [])[:5]],
            "chunks_reranked": result.get("retrieved_chunks", []),
            "accuracy": judgement.get("accuracy", 0),
            "completeness": judgement.get("completeness", 0),
            "hallucination_score": judgement.get("hallucination", 0),
            "is_hallucination": judgement.get("is_hallucination", False),
        }
        report["with_rerank"]["results"].append(item)
        if item["is_hallucination"]:
            report["with_rerank"]["hallucination_count"] += 1

        w_item = report["without_rerank"]["results"][qi]
        rescued = (
            (w_item["is_hallucination"] and not item["is_hallucination"]) or
            (item["accuracy"] - w_item["accuracy"] >= 2) or
            (item["completeness"] - w_item["completeness"] >= 3)
        )
        if verbose:
            icon = "🏆" if rescued else _status_icon(item)
            judge = f"acc:{item['accuracy']:.0f} com:{item['completeness']:.0f} hal:{item['hallucination_score']:.0f} ovr:{judgement.get('overall',0):.0f}"
            done = f"  {qi+1}/{len(queries)}: {icon} {{{judge}}}  {q[:45]}..."
            if rescued:
                parts = []
                if item["accuracy"] - w_item["accuracy"] >= 2:
                    parts.append(f"acc+{item['accuracy']-w_item['accuracy']:.0f}")
                if item["completeness"] - w_item["completeness"] >= 3:
                    parts.append(f"com+{item['completeness']-w_item['completeness']:.0f}")
                if w_item["is_hallucination"] and not item["is_hallucination"]:
                    parts.append("幻觉消除")
                done += f"  ({'; '.join(parts)})"
            print(f"\r\033[K{done}")
            sys.stdout.flush()
        time.sleep(0.3)

    # ── 计算平均值 ──
    for mode in ["without_rerank", "with_rerank"]:
        results = report[mode]["results"]
        if results:
            scores = [r["accuracy"] for r in results]
            report[mode]["avg_accuracy"] = round(sum(scores) / len(scores), 1)
            report[mode]["avg_hallucination"] = round(
                sum(r["hallucination_score"] for r in results) / len(results), 1
            )

    # ── 逐查询对比 ──
    for i in range(len(queries)):
        w = report["without_rerank"]["results"][i]
        r = report["with_rerank"]["results"][i]
        diff = r["accuracy"] - w["accuracy"]
        rescued = (
            (w["is_hallucination"] and not r["is_hallucination"]) or
            (r["accuracy"] - w["accuracy"] >= 2) or
            (r["completeness"] - w["completeness"] >= 3)
        )
        report["comparison"].append({
            "query": queries[i][:60],
            "accuracy_diff": diff,
            "hallucination_diff": r["hallucination_score"] - w["hallucination_score"],
            "rescued": rescued,
        })

    rescued_count = sum(1 for c in report["comparison"] if c["rescued"])
    report["rescued_count"] = rescued_count
    report["rescued_pct"] = round(rescued_count / len(queries) * 100, 1)
    improved = sum(1 for c in report["comparison"] if c["accuracy_diff"] > 0)
    report["improved_count"] = improved
    return report


def _delta_str(d: float) -> str:
    d = round(d, 1)
    if d >= 0.05:
        return f"+{d:.0f}"
    elif d <= -0.05:
        return f"{d:.0f}"
    return "0"


def print_report(report: dict):
    w = report["without_rerank"]
    r = report["with_rerank"]

    print(f"\n{'=' * 60}")
    print(f"📊 重排效果对比报告")
    print(f"{'=' * 60}")

    # ── 整体指标 ──
    print(f"\n  {'指标':<20} {'无重排':>10} {'有重排':>10} {'变化':>10}")
    print(f"  {'─' * 50}")
    print(f"  {'平均准确率':<20} {w['avg_accuracy']:>8.1f}/10 {r['avg_accuracy']:>8.1f}/10 "
          f"{_delta_str(r['avg_accuracy'] - w['avg_accuracy']):>8}")
    print(f"  {'平均幻觉分':<20} {w['avg_hallucination']:>8.1f}/10 "
          f"{r['avg_hallucination']:>8.1f}/10 "
          f"{_delta_str(r['avg_hallucination'] - w['avg_hallucination']):>8}")
    print(f"  {'幻觉查询数':<20} {w['hallucination_count']:>8} {r['hallucination_count']:>8} "
          f"{_delta_str(r['hallucination_count'] - w['hallucination_count']):>8}")

    # ── 逐查询对比表 ──
    print(f"\n{'─' * 70}")
    print(f"  {'查询':<35} {'无重排':>6}  {'有重排':>6}  {'变化':>5}")
    print(f"  {'─' * 70}")
    for i, (wi, ri, ci) in enumerate(zip(w["results"], r["results"], report["comparison"])):
        q_short = wi["query"][:34]
        w_acc = f"{'⚠️' if wi['is_hallucination'] else '✅'} {wi['accuracy']:.0f}/10"
        r_acc = f"{'⚠️' if ri['is_hallucination'] else '✅'} {ri['accuracy']:.0f}/10"
        delta = _delta_str(ri['accuracy'] - wi['accuracy'])
        rescued = " 🏆" if ci["rescued"] else ""
        print(f"  {q_short:<35} {w_acc:>6}  {r_acc:>6}  {delta:>5}{rescued}")

    # ── 总结 ──
    print(f"\n{'─' * 60}")
    print(f"  🏆 拯救了 {report['rescued_count']}/{report['total_queries']} 个查询 "
          f"({report['rescued_pct']}%)  |  "
          f"准确率提升 {report['improved_count']}/{report['total_queries']} 个查询")

    # ── 教学结论 ──
    if r["avg_accuracy"] > w["avg_accuracy"]:
        print(f"\n  💡 Cross-Encoder 重排有效提升了答案质量:")
        print(f"     准确率 +{r['avg_accuracy'] - w['avg_accuracy']:.1f} | "
              f"幻觉减少 {w['hallucination_count'] - r['hallucination_count']} 次")
        print(f"     代价: Top-5 → Top-20 检索 (多 ~15 chunks) + Cross-Encoder 评分 (~1s)")

    # ── 被拯救查询的深度分析 ──
    rescued_queries = [(i, ci) for i, ci in enumerate(report["comparison"]) if ci["rescued"]]
    if rescued_queries:
        print(f"\n{'─' * 60}")
        print(f"  🔍 被拯救查询详情 (Cross-Encoder 做了什么)")
        print(f"{'─' * 60}")
        for idx, _ in rescued_queries[:3]:
            wi = w["results"][idx]
            ri = r["results"][idx]
            print(f"\n  📝 查询{idx+1}: \"{wi['query'][:70]}...\"")
            print(f"    无重排: {_status_icon(wi)} acc={wi['accuracy']}/10 | 来源:")
            for j, src in enumerate(wi["sources"][:5]):
                marker = " ⚡噪声" if "(noise)" in src.lower() or "WebSocket" in src or "REST" in src else ""
                print(f"      {j+1}. {src}{marker}")
            print(f"    有重排: {_status_icon(ri)} acc={ri['accuracy']}/10 | 来源:")
            for j, src in enumerate(ri["sources"][:5]):
                marker = " ⚡噪声" if "(noise)" in src.lower() or "WebSocket" in src or "REST" in src else ""
                print(f"      {j+1}. {src}{marker}")

            # Show Cross-Encoder promotion/demotion
            if "chunks_reranked" in ri:
                reranked = ri["chunks_reranked"]
                moved = []
                for chunk in reranked:
                    if "rerank_score" in chunk:
                        orig_sim = chunk.get("score", 0)
                        rerank_s = chunk["rerank_score"]
                        title = chunk.get("title", "?")
                        if abs(rerank_s - orig_sim) > 0.1:
                            direction = "↑" if rerank_s > orig_sim else "↓"
                            moved.append((title, direction, orig_sim, rerank_s))
                if moved:
                    print(f"    Cross-Encoder 调整:")
                    for title, direction, orig, new in moved[:4]:
                        print(f"      {direction} {title}: {orig:.3f} → {new:.3f}")
        if len(rescued_queries) > 3:
            print(f"\n  ... 还有 {len(rescued_queries) - 3} 个被拯救查询")


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
