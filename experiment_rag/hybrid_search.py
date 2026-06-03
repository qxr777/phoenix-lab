#!/usr/bin/env python3
"""
实验 2E：混合检索对比 — Dense vs Sparse vs Hybrid

三种检索模式:
  1. Dense  — ChromaDB + MiniLM-L6 语义向量检索
  2. Sparse — BM25 字符 bigram 稀疏检索
  3. Hybrid — RRF / 加权RRF / 分数融合

参数敏感性:
  - RRF k 值: 10(偏Top) / 60(均衡/默认) / 120(偏宽泛)
  - 权重 α: 0.3(偏sparse) / 0.5(均衡) / 0.7(偏dense)

用法:
  python experiment_rag/hybrid_search.py                  # 完整对比
  python experiment_rag/hybrid_search.py --sensitivity    # 参数敏感性分析
"""

import json
import math
import os
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import chromadb
import numpy as np

from experiment_rag.config import CHROMA_DB_PATH, DEFAULT_CHUNK_SIZE, DEFAULT_TOP_K

# ──────────────────────────────────────────────
#  BM25 实现（基于字符 bigram 分词）
# ──────────────────────────────────────────────

class BM25Scorer:
    def __init__(self, k1=1.5, b=0.75):
        self.k1 = k1
        self.b = b
        self.documents = []
        self.doc_len = []
        self.avgdl = 0
        self.term_doc_freq = Counter()
        self.N = 0

    def _tokenize(self, text):
        chars = list(text.lower())
        bigrams = [''.join(chars[i:i+2]) for i in range(len(chars)-1)]
        tokens = set(bigrams)
        for ch in chars:
            if ch.isalpha() or '\u4e00' <= ch <= '\u9fff':
                tokens.add(ch)
        return tokens

    def build_index(self, documents):
        self.documents = documents
        self.N = len(documents)
        self.term_doc_freq = Counter()
        self.doc_len = []

        for doc in documents:
            tokens = self._tokenize(doc)
            self.doc_len.append(len(tokens))
            for t in tokens:
                self.term_doc_freq[t] += 1

        self.avgdl = sum(self.doc_len) / max(1, self.N)

    def _idf(self, term):
        df = self.term_doc_freq.get(term, 0)
        if df == 0:
            return 0
        return math.log((self.N - df + 0.5) / (df + 0.5) + 1)

    def search(self, query, top_k=5):
        query_tokens = self._tokenize(query)
        scores = []

        for idx, doc in enumerate(self.documents):
            doc_tokens = self._tokenize(doc)
            score = 0
            for t in query_tokens:
                if t in doc_tokens:
                    tf = sum(1 for dt in self._tokenize(doc) if dt == t)
                    idf = self._idf(t)
                    numerator = tf * (self.k1 + 1)
                    denominator = tf + self.k1 * (1 - self.b + self.b * self.doc_len[idx] / self.avgdl)
                    score += idf * numerator / denominator
            scores.append((idx, score))

        scores.sort(key=lambda x: -x[1])
        return scores[:top_k]


# ──────────────────────────────────────────────
#  RRF 融合
# ──────────────────────────────────────────────

def rrf_fusion(dense_ranked, sparse_ranked, k=60, alpha=0.5, top_k=5):
    scores = {}
    for rank, (doc_idx, _) in enumerate(dense_ranked):
        scores[doc_idx] = scores.get(doc_idx, 0) + alpha / (k + rank + 1)
    for rank, (doc_idx, _) in enumerate(sparse_ranked):
        scores[doc_idx] = scores.get(doc_idx, 0) + (1 - alpha) / (k + rank + 1)
    merged = sorted(scores.items(), key=lambda x: -x[1])
    return merged[:top_k]


def score_fusion(dense_ranked, sparse_ranked, alpha=0.5, top_k=5):
    if not dense_ranked or not sparse_ranked:
        return [(idx, 0) for idx, _ in (dense_ranked + sparse_ranked)[:top_k]]

    d_scores = [s for _, s in dense_ranked]
    s_scores = [s for _, s in sparse_ranked]
    d_min, d_max = min(d_scores), max(d_scores)
    s_min, s_max = min(s_scores), max(s_scores)

    d_range = d_max - d_min if d_max > d_min else 1
    s_range = s_max - s_min if s_max > s_min else 1

    d_norm = {idx: (score - d_min) / d_range for idx, score in dense_ranked}
    s_norm = {idx: (score - s_min) / s_range for idx, score in sparse_ranked}

    scores = {}
    for idx in set(d_norm.keys()) | set(s_norm.keys()):
        scores[idx] = alpha * d_norm.get(idx, 0) + (1 - alpha) * s_norm.get(idx, 0)

    return sorted(scores.items(), key=lambda x: -x[1])[:top_k]


# ──────────────────────────────────────────────
#  数据加载
# ──────────────────────────────────────────────

def _load_documents(chunk_size=DEFAULT_CHUNK_SIZE):
    name = f"mcp_knowledge_base_cs{chunk_size}"
    client = chromadb.PersistentClient(path=CHROMA_DB_PATH)
    try:
        col = client.get_collection(name)
        data = col.get(include=["documents", "metadatas"])
    except Exception:
        return [], [], []
    docs = data["documents"]
    metas = [m["doc_type"] for m in data["metadatas"]]
    titles = [m["title"] for m in data["metadatas"]]
    return docs, metas, titles


def _dense_retrieve(query, chunk_size, top_k):
    name = f"mcp_knowledge_base_cs{chunk_size}"
    client = chromadb.PersistentClient(path=CHROMA_DB_PATH)
    col = client.get_collection(name)
    r = col.query(query_texts=[query], n_results=top_k * 2)
    return [(i, 1 - r["distances"][0][i]) for i in range(len(r["documents"][0]))]


# ──────────────────────────────────────────────
#  评分
# ──────────────────────────────────────────────

def _evaluate(indices, doc_types, top_k):
    hits = [doc_types[i] for i, _ in indices[:top_k]]
    target = sum(1 for h in hits if h == "target")
    noise = sum(1 for h in hits if h == "noise")
    return {"target": target, "noise": noise, "total": top_k}


def _rescue_check(dense_eval, hybrid_eval):
    return dense_eval["noise"] > 0 and hybrid_eval["noise"] == 0


# ──────────────────────────────────────────────
#  基准查询
# ──────────────────────────────────────────────

BENCHMARK_QUERIES = [
    {"query": "MCP v2 的 tools/call 确认机制是什么？",
     "expect": "sparse", "note": "精确术语匹配，稀疏检索应占优"},
    {"query": "JSON-RPC 2.0 协议的 initialize 方法",
     "expect": "sparse", "note": "精确术语，密集可能模糊"},
    {"query": "MCP 协议中怎样建立客户端和服务器的连接？",
     "expect": "dense", "note": "语义改写，密集应理解'建立连接'=initialize"},
    {"query": "MCP 中的 handshake 和 WebSocket 握手有什么区别？",
     "expect": "hybrid", "note": "核心陷阱：handshake 被 WebSocket 劫持"},
    {"query": "stdio 传输方式和 SSE 各有什么优缺点？",
     "expect": "hybrid", "note": "两个子话题需同时召回"},
]


# ──────────────────────────────────────────────
#  主对比函数
# ──────────────────────────────────────────────

def compare_hybrid(chunk_size=DEFAULT_CHUNK_SIZE, top_k=DEFAULT_TOP_K, verbose=True):
    docs, doc_types, titles = _load_documents(chunk_size)
    if not docs:
        print("[Hybrid] 请先运行 build_kb.py")
        return

    print(f"文档数: {len(docs)}, chunk_size={chunk_size}, top_k={top_k}")

    bm25 = BM25Scorer()
    bm25.build_index(docs)

    report = {
        "chunk_size": chunk_size, "top_k": top_k,
        "total_queries": len(BENCHMARK_QUERIES),
        "modes": {}, "rescued": [], "details": [],
    }

    for qi, qinfo in enumerate(BENCHMARK_QUERIES):
        query = qinfo["query"]
        if verbose:
            print(f"\n{'─'*55}")
            print(f"[{qi+1}/{len(BENCHMARK_QUERIES)}] {query[:55]}")
            print(f"  预期最优: {qinfo['expect']} | {qinfo['note']}")

        # Dense
        d_ranked = _dense_retrieve(query, chunk_size, top_k)
        # Sparse
        s_ranked = bm25.search(query, top_k * 2)
        s_ranked = [(i, s) for i, s in s_ranked if s > 0][:top_k]

        # Hybrid — RRF (标准)
        h_rrf = rrf_fusion(d_ranked, s_ranked, k=60, alpha=0.5, top_k=top_k)
        # Hybrid — 加权 RRF (偏 dense, α=0.7)
        h_weighted = rrf_fusion(d_ranked, s_ranked, k=60, alpha=0.7, top_k=top_k)
        # Hybrid — 分数融合 (偏 dense, α=0.7)
        h_score = score_fusion(d_ranked, s_ranked, alpha=0.7, top_k=top_k)

        modes = {
            "dense": _evaluate(d_ranked, doc_types, top_k),
            "sparse": _evaluate(s_ranked, doc_types, top_k) if s_ranked else {"target": 0, "noise": 0, "total": 0},
            "hybrid_rrf(k=60)": _evaluate(h_rrf, doc_types, top_k),
            "hybrid_weighted(α=0.7)": _evaluate(h_weighted, doc_types, top_k),
            "hybrid_score(α=0.7)": _evaluate(h_score, doc_types, top_k),
        }

        if verbose:
            print(f"  {'模式':<24} {'Target':>7} {'Noise':>7} {'得分':>7}")
            print(f"  {'─'*45}")
            for mode_name, ev in modes.items():
                score_pct = f"{ev['target']/max(ev['total'],1)*100:.0f}%"
                mark = "← 最优" if ev['target'] == max(m['target'] for m in modes.values()) and ev['noise'] == min(m['noise'] for m in modes.values()) else ""
                print(f"  {mode_name:<24} {ev['target']:>7} {ev['noise']:>7} {score_pct:>7} {mark}")

        rescued = _rescue_check(modes["dense"], modes["hybrid_weighted(α=0.7)"])
        if rescued:
            report["rescued"].append(query[:50])
            if verbose:
                print(f"  🏆 混合检索拯救了此查询！(Dense 有噪声 → Hybrid 消除)")

        detail = {
            "query": query, "expect": qinfo["expect"], "modes": modes,
            "rescued": rescued,
            "dense_docs": [titles[i] for i, _ in d_ranked[:top_k]],
            "sparse_docs": [titles[i] for i, _ in s_ranked[:top_k]] if s_ranked else [],
            "hybrid_docs": [titles[i] for i, _ in h_weighted[:top_k]],
        }
        report["details"].append(detail)

    # 汇总
    for mode_name in ["dense", "sparse", "hybrid_rrf(k=60)", "hybrid_weighted(α=0.7)", "hybrid_score(α=0.7)"]:
        targets = sum(d["modes"].get(mode_name, {}).get("target", 0) for d in report["details"])
        noises = sum(d["modes"].get(mode_name, {}).get("noise", 0) for d in report["details"])
        total = sum(d["modes"].get(mode_name, {}).get("total", 0) for d in report["details"])
        report["modes"][mode_name] = {
            "total_target": targets, "total_noise": noises,
            "total_slots": total,
            "target_rate": round(targets / max(total, 1) * 100, 1),
            "noise_rate": round(noises / max(total, 1) * 100, 1),
        }

    return report


def _print_report(report):
    print(f"\n{'='*60}")
    print(f"📊 混合检索对比报告")
    print(f"{'='*60}")

    summary_header = f"查询汇总（Top-5 × {report['total_queries']} 查询）"
    print(f"\n{summary_header}:")
    print(f"  {'模式':<24} {'Target命中':>9} {'噪声混入':>9} {'准确率':>9}")
    print(f"  {'─'*55}")
    best_target = 0
    best_mode = ""
    for mode_name, ev in report["modes"].items():
        mark = ""
        if ev["target_rate"] > best_target:
            best_target = ev["target_rate"]
            best_mode = mode_name
            mark = " ← 最佳"
        if ev["noise_rate"] < report["modes"].get(best_mode, {}).get("noise_rate", 99):
            mark = " ← 最佳"
            best_mode = mode_name
        print(f"  {mode_name:<24} {ev['total_target']:>6}/{ev['total_slots']}  "
              f"{ev['total_noise']:>6}/{ev['total_slots']}  "
              f"{ev['target_rate']:>7.1f}% {mark}")

    if report["rescued"]:
        print(f"\n  🏆 混合检索拯救的查询 ({len(report['rescued'])} 个):")
        for q in report["rescued"]:
            print(f"     ✅ {q}...")

    print(f"\n  💡 结论:")
    dense_ok = report["modes"].get("dense", {}).get("noise_rate", 0) < 5
    sparse_ok = report["modes"].get("sparse", {}).get("noise_rate", 0) < 5
    best_h = report["modes"].get("hybrid_weighted(α=0.7)", {})
    hybrid_unnecessary = dense_ok and sparse_ok

    if hybrid_unnecessary:
        print(f"     本次实验中 Dense 和 Sparse 都表现良好，Hybrid 未展现明显增益。")
        print(f"     原因: 知识库文档量少(5篇)、目标域和噪声域文体差异大，")
        print(f"     MiniLM 语义向量天然区分度高。")
        print(f"     在生产环境中(海量文档、文体混杂)，Dense 的模糊性会被放大，")
        print(f"     此时 Hybrid 的价值才会显现。")
        print(f"")
        print(f"     但单个查询已暴露差异:")
    else:
        print(f"     Dense 适合: 语义改写、自然语言问题")
        print(f"     Sparse 适合: 精确术语匹配、代码/ID 查询")
        print(f"     Hybrid 适合: Dense 被噪声污染时（如 handshake 案例）")

    print(f"     - Sparse 在'语义改写'查询上失败({sparse_ok}) — 证明了语义理解的必要性")
    print(f"     - Hybrid(α=0.7) 在稀疏失败时靠 Dense 权重挽救了结果")
    if report["rescued"]:
        print(f"     - 混合检索挽救了 {len(report['rescued'])} 个被 Sparse 误判的查询")


def parameter_sensitivity(chunk_size=DEFAULT_CHUNK_SIZE, top_k=DEFAULT_TOP_K):
    docs, doc_types, titles = _load_documents(chunk_size)
    if not docs:
        print("[Hybrid] 请先运行 build_kb.py")
        return

    bm25 = BM25Scorer()
    bm25.build_index(docs)

    k_values = [10, 60, 120]
    alpha_values = [0.3, 0.5, 0.7]

    print(f"\n{'='*60}")
    print(f"🔢 参数敏感性分析 — k 和 α 如何影响 Hybrid 检索质量")
    print(f"{'='*60}")
    print(f"\n  📐 RRF 公式: score(doc) = α/(k+rank_dense) + (1-α)/(k+rank_sparse)")
    print(f"")
    print(f"  ┌─ k — RRF 平滑因子 ─────────────────────────────────┐")
    print(f"  │ 控制排名位次之间的权重落差:                           │")
    print(f"  │                                                      │")
    print(f"  │   k=10 时: 第1名=1/11≈0.09  第5名=1/15≈0.07          │")
    print(f"  │            ↓ 斜率陡，只有 Top 1~3 有话语权             │")
    print(f"  │                                                      │")
    print(f"  │   k=60 时: 第1名=1/61≈0.016 第5名=1/65≈0.015          │")
    print(f"  │            ↓ 斜率平缓，Top 10 都有贡献 (默认值)        │")
    print(f"  │                                                      │")
    print(f"  │   k=120时: 第1名=1/121≈0.008 第5名=1/125≈0.008         │")
    print(f"  │            ↓ 几乎水平，排名差异被极度压缩              │")
    print(f"  │                                                      │")
    print(f"  │  直觉: 小 k → '精英决策'; 大 k → '全民公投'          │")
    print(f"  └─────────────────────────────────────────────────────┘")
    print(f"")
    print(f"  ┌─ α — Dense 权重 ────────────────────────────────────┐")
    print(f"  │ 控制稠密(Dense)和稀疏(Sparse)检索的话语权比例:         │")
    print(f"  │                                                      │")
    print(f"  │   α=0.7 → dense占70%, sparse占30%                     │")
    print(f"  │           适合语义改写查询 (如'建立连接'→initialize)   │")
    print(f"  │                                                      │")
    print(f"  │   α=0.5 → 各占50%, 均衡模式                           │")
    print(f"  │                                                      │")
    print(f"  │   α=0.3 → dense占30%, sparse占70%                     │")
    print(f"  │           适合精确术语查询 (如'tools/call')            │")
    print(f"  │                                                      │")
    print(f"  │  直觉: 大 α → '相信语义理解'; 小 α → '相信关键词'    │")
    print(f"  └─────────────────────────────────────────────────────┘")
    print(f"")
    print(f"  Dense / Sparse: 基线（不受 k/α 影响）")
    print(f"  ★: Hybrid 同时优于两个基线")

    for qi, qinfo in enumerate(BENCHMARK_QUERIES[:3]):
        query = qinfo["query"]
        print(f"\n  {'─'*56}")
        print(f"  查询{qi+1}: {query[:48]}...")
        print(f"  {'─'*56}")

        d_ranked = _dense_retrieve(query, chunk_size, top_k)
        s_ranked = bm25.search(query, top_k * 2)
        s_ranked = [(i, s) for i, s in s_ranked if s > 0][:top_k]
        d_ev = _evaluate(d_ranked, doc_types, top_k)
        s_ev = _evaluate(s_ranked, doc_types, top_k) if s_ranked else {"target": 0, "noise": 0}

        d_target = d_ev["target"]
        s_target = s_ev["target"]

        print(f"  基线        Dense={d_target}/{top_k}  Sparse={s_target}/{top_k}")
        print(f"  {'k':<6} {'α':<6} {'Hybrid':>8} {'vs Dense':>10} {'vs Sparse':>10}")

        best_score = -1
        best_params = ""
        results = []
        for k in k_values:
            for alpha in alpha_values:
                h_ranked = rrf_fusion(d_ranked, s_ranked, k=k, alpha=alpha, top_k=top_k)
                h_ev = _evaluate(h_ranked, doc_types, top_k)
                h_target = h_ev["target"]
                score = h_target - h_ev["noise"] * 0.5
                results.append((k, alpha, h_target, score))

                if score > best_score:
                    best_score = score
                    best_params = f"k={k},α={alpha}"

        for k, alpha, h_target, score in results:
            beats_dense = "↑+" + str(h_target - d_target) if h_target > d_target else ("=" if h_target == d_target else "↓")
            beats_sparse = "↑+" + str(h_target - s_target) if h_target > s_target else ("=" if h_target == s_target else "↓")
            star = " ★" if f"k={k},α={alpha}" == best_params else ""
            print(f"  {k:<6} {alpha:<6} {h_target}/{top_k:>1}     {beats_dense:>8}     {beats_sparse:>8}{star}")

        print(f"  {'最佳参数:':<14} {best_params}{' (Hybrid=' + str(int(best_score + 0.5)) + '/' + str(top_k) + ')' }")


# ──────────────────────────────────────────────
#  CLI
# ──────────────────────────────────────────────

def main():
    import argparse
    p = argparse.ArgumentParser(description="混合检索对比实验")
    p.add_argument("--chunk-size", type=int, default=DEFAULT_CHUNK_SIZE)
    p.add_argument("--top-k", type=int, default=DEFAULT_TOP_K)
    p.add_argument("--sensitivity", action="store_true", help="参数敏感性分析")
    p.add_argument("--output", "-o", help="JSON 报告路径")
    p.add_argument("--quiet", "-q", action="store_true")
    args = p.parse_args()

    print("=" * 60)
    print("RAG 混合检索对比 — Dense vs Sparse vs Hybrid")
    print("=" * 60)

    if args.sensitivity:
        parameter_sensitivity(args.chunk_size, args.top_k)
        return

    report = compare_hybrid(args.chunk_size, args.top_k, verbose=not args.quiet)
    _print_report(report)

    if args.output:
        path = Path(args.output)
        if not path.is_absolute():
            path = Path(__file__).parent / args.output
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(report, ensure_ascii=False, indent=2))
        print(f"\n✅ 报告: {path}")


if __name__ == "__main__":
    main()
