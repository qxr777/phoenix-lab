# CS599 实验 1：RAG 瓶颈诊断与向量空间聚类实验

> [!NOTE]
> **教师寄语**：当智能体表现不佳时，问题往往不出在大模型上，而是出在检索层。向量相似度是一个"盲人摸象"的度量——它告诉你两个向量有多近，却未必告诉你它们是否真的相关。本实验让你用肉眼看清"语义检索"的物理本质。

---

## 目录

- [1. 实验概述](#1-实验概述)
- [2. 环境准备](#2-环境准备)
- [3. 实验 2A：Embedding 降维可视化](#3-实验-2aembedding-降维可视化)
- [4. 实验 2B：检索失效分析](#4-实验-2b检索失效分析)
- [5. 实验 2C：分块策略对比](#5-实验-2c分块策略对比)
- [6. 实验 2D：重排效果对比](#6-实验-2d重排效果对比)
- [7. 实验 2E：混合检索对比](#7-实验-2e混合检索对比)
- [8. 实验总结与综合思考](#8-实验总结与综合思考)
- [附录 A：故障排查](#附录-a故障排查)

---

## 1. 实验概述

### 1.1 实验目标

本实验构建一个 MCP 协议知识库 RAG 智能体，通过五个递进实验揭示检索增强生成的深层问题：

| 阶段 | 核心任务 | 关键工具 | 产出 |
|------|----------|----------|------|
| **2A** | 将文档向量和查询向量降维到 3D 空间，可视化聚集状态 | Phoenix Embeddings / UMAP + Plotly | 3D 散点图 |
| **2B** | 检测"相似≠相关"的检索失效案例，诊断根因 | LLM-as-Judge | 失效诊断报告 |
| **2C** | 对比 4 种 chunk_size 对检索精度和幻觉率的影响 | ChromaDB 多集合 | 对比矩阵 |
| **2D** | 深入理解 Bi-Encoder / Cross-Encoder 两阶段检索原理，验证重排效果 | ms-marco-MiniLM | 重排对比报告 |
| **2E** ★ | 对比 Dense/Sparse/Hybrid 三种检索模式；深入理解 RRF 和分数融合的原理差异；通过网格搜索实验确定最优参数 | BM25 + RRF/分数融合 | 混合检索对比报告 + 参数敏感性矩阵 |

### 1.2 知识库设计

5 个 Markdown 文档，3 个目标域 + 2 个噪声：

| 文档 | 角色 | 关键特征 |
|------|------|----------|
| `mcp_spec_v1.md` | 目标 | MCP v1 规范：JSON-RPC、工具发现、生命周期 |
| `mcp_spec_v2.md` | 目标 | MCP v2 规范：Streaming、Session、增强安全 |
| `mcp_transport.md` | 目标 | 传输层：stdio 帧协议、SSE 重连、保活 |
| `noise_rest_api.md` | 噪声 | REST API 教程：含 "protocol"、"handshake" 等共享词汇 |
| `noise_websocket.md` | 噪声 | WebSocket 协议：含 "frame"、"connection" 等共享词汇 |

**关键设计**：噪声文档与目标文档共享大量术语，但对 MCP 问题的回答毫无价值。

### 1.3 为什么余弦相似度会失效？

```
查询: "MCP 的 handshake 和 TCP 的 handshake 有何不同？"
      ↓ 向量检索
检索结果:
  Rank 1 (sim=0.92): noise_websocket.md — "WebSocket 握手通过 HTTP Upgrade..."
  Rank 2 (sim=0.88): mcp_spec_v1.md — "MCP v1 使用 initialize 建立连接"
  Rank 3 (sim=0.85): noise_rest_api.md — "REST 认证握手: POST /auth/login"
```

虽然第 2 名的文档才真正相关，但向量相似度只看到了"握手"这个关键词在 noise 文档中的高频出现。这就是**关键词匹配陷阱**——它也是实验 2D（Bi-Encoder → Cross-Encoder 两阶段检索）和实验 2E（Dense + Sparse 混合检索）要解决的核心问题。

---

## 2. 环境准备

### 2.1 Python 依赖

```bash
cd phoenix_lab
pip install -r requirements_rag.txt
```

首次运行会下载 `all-MiniLM-L6-v2` (~80MB)。

### 2.2 Phoenix 容器（可选，用于实验 2A 的 UMAP 可视化）

```bash
cd docker && docker compose up -d
```

### 2.3 Phoenix UI（查看检索 Trace）

```bash
# 1. 确保 Phoenix Docker 已启动
cd ../docker && docker compose up -d && cd ..

# 2. 打开 Phoenix UI
open http://localhost:6006

# 3. 运行 RAG 查询（会自动产生 trace）
source ../venv/bin/activate
ENABLE_PHOENIX_TRACING=true python experiment_rag/rag_agent.py \
  "MCP v1 和 v2 的握手有什么区别？" \
  --retrieval-only --chunk-size 512

# 4. 刷新 Phoenix UI → Traces 页面，查看最新 trace
#    展开 "chromadb_retrieval" span → 查看检索到的文档、相似度分数等
```

### 2.4 首次构建知识库

```bash
python rag_experiment/run_rag_experiments.py
# 选择: setup → 构建全部分块尺寸的知识库
```

---

## 3. 实验 2A：Embedding 降维可视化

### 3.1 目标

将高维 embedding 向量降维到 3D，观察：
- MCP 相关文档是否形成紧致簇？
- 噪声文档是否远离目标簇？
- 查询向量落在哪里？是否被噪声文档"吸引"？

### 3.2 操作步骤

```bash
# Step 1: 构建知识库
ENABLE_PHOENIX_TRACING=true python experiment_rag/knowledge_base/build_kb.py --chunk-size 512

# Step 2: 运行 RAG 查询（产生 Phoenix trace + 看到检索结果）
ENABLE_PHOENIX_TRACING=true python experiment_rag/rag_agent.py \
  "MCP v1 和 v2 的握手有什么区别？" \
  --retrieval-only --chunk-size 512

# Step 3: 打开 Phoenix UI 查看 trace
# → http://localhost:6006 → Traces → 找到 "chromadb_retrieval" span
# → 展开查看: 查询文本、检索到的文档块、相似度分数、文档类型(target/noise)

# Step 4: 生成本地 UMAP 3D 可视化 HTML
python experiment_rag/embedding_viz.py \
  --output experiment_rag/output/embedding_umap_3d.html
open experiment_rag/output/embedding_umap_3d.html
```

### 3.3 Phoenix Trace 面板中看什么

在 Phoenix UI（http://localhost:6006）的 **Traces** 页面，展开最新的 trace，可以看到：

1. **Span: `chromadb_retrieval`** — 检索追踪
   - `query`: 用户查询文本
   - `retrieved_count`: 原始检索到的文档数
   - `filtered_count`: 经过阈值过滤后的文档数
   - `retrieved.0.title` / `.score` / `.doc_type`: 每个检索块的详细信息
   - **重点**：`doc_type` 为 `noise` 的块是"被错误检索"的噪声文档

2. **Span: `llm_generation`**（如果有 LLM 调用）
   - `model`、`context_length`、`response_length`

### 3.4 UMAP 3D HTML 图中看什么

打开生成的 HTML 文件（`experiment_rag/output/embedding_umap_3d.html`），观察：

1. **簇的紧密程度**：MCP v1/v2/Transport 是否聚集在一起？
2. **噪声离群度**：REST API（红色）和 WebSocket（橙色）与目标簇的距离
3. **查询点位置**：黄色菱形（查询）落在哪些区域？

### 3.5 练习任务

1. **基础**：生成 UMAP 3D 图，识别至少 3 个簇，用颜色标注
2. **进阶**：找出 2 个"被噪声吸引"的查询，解释为什么会这样
3. **挑战**：对比 `chunk_size=256` 和 `chunk_size=2048` 的 UMAP 图差异

---

## 4. 实验 2B：检索失效分析

### 4.1 目标

使用 LLM-as-Judge 自动检测"余弦相似度高但语义相关性低"的检索结果。

### 4.2 操作步骤

```bash
python rag_experiment/run_rag_experiments.py -e 2B
```

### 4.3 预期结果

运行后会输出类似这样的诊断：

```
📊 检索失效诊断报告
  分块大小: 512  Top-K: 5
  总查询数: 8  检查块数: 40
  检索失效: 12 (30.0%)  噪声污染: 6 (15.0%)

失效模式分析
  噪声文档污染: 6 次 (50%)
  语义不匹配: 4 次 (33%)
  信息碎片化(分块不当): 2 次 (17%)

  💡 核心发现:
  ⚠️ 噪声文档污染严重 —— 建议: 提高相似度阈值
  ⚠️ 检索失效率高 —— 建议: 引入重排
```

### 4.4 练习任务

1. **基础**：运行诊断，记录有多少个检索块被标记为"失效"
2. **进阶**：选一个失效案例，在 Phoenix Trace 中追踪：该块是哪个文档的？为什么 similarity 高？
3. **挑战**：修改 `retrieval_diagnosis.py` 中的 `TRICKY_QUERIES`，添加你自己的陷阱查询

---

## 5. 实验 2C：分块策略对比

### 5.1 目标

量化不同 chunk_size 对 RAG 性能的影响。回答核心问题：**多大的 chunk 最合适？**

### 5.2 操作步骤

```bash
python rag_experiment/run_rag_experiments.py -e 2C
```

### 5.3 预期输出

```
📊 分块策略对比报告: 固定长度分块

Chunk       准确率    完整度    幻觉分    噪声
  256      6.8/10    5.9/10    5.2/10      2
  512      7.4/10    7.1/10    4.1/10      1
 1024      7.9/10    7.8/10    3.5/10      0
 2048      6.2/10    6.8/10    5.8/10      3

  🏆 最佳 chunk_size: 1024 (准确率: 7.9/10)

  💡 观察:
     chunk 太小 → 信息碎片化
     chunk 太大 → 噪声稀释
     最佳值在 256-1024 之间
```

### 5.4 练习任务

1. **基础**：运行分块对比，记录最佳 chunk_size
2. **进阶**：运行 `--strategies all`，对比三种分块策略的差异
3. **挑战**：修改 `strategies.py`，实现你自己的分块策略并测试

---

## 6. 实验 2D：重排效果对比

### 6.1 目标

验证 Cross-Encoder Reranker 能否从"过度检索"中拯救质量。

### 6.2 核心原理：Bi-Encoder vs Cross-Encoder

#### 6.2.1 Bi-Encoder：快速粗筛

Bi-Encoder 将查询和文档**独立编码**为向量，然后用余弦相似度衡量距离。

```
查询 "MCP 握手协议"
       │
       ▼
   ┌─────────────────────┐
   │  Encoder (MiniLM)   │  ← 编码为 384 维向量: [0.12, -0.34, 0.87, ...]
   └─────────┬───────────┘
             │
   ┌─────────▼───────────┐
   │  余弦相似度 × N 文档   │  ← 文档向量已预计算并索引 (ChromaDB)
   └─────────┬───────────┘
             │
      返回 Top-20 候选
```

**为什么快**：文档向量可以**预先计算并持久化索引**。查询时只需编码一次查询向量 + 一次向量检索（毫秒级）。10 万文档的检索耗时约 10ms。

**为什么不够准**：Bi-Encoder 只看到查询和文档各自的"整体语义"，无法捕捉细粒度的词级交互。比如 "MCP 的 handshake" 可能因为 "handshake" 这个词而高匹配 WebSocket 文档——两个模型看到了相同的词，但不理解上下文意味着完全不同的东西。

#### 6.2.2 Cross-Encoder：精准精排

Cross-Encoder 将查询和文档**拼接后联合编码**，Transformer 内部的 Attention 机制在两者之间做逐词交互。

```
Bi-Encoder 返回的 Top-20 候选
       │
       ▼
   ┌──────────────────────────────────────┐
   │  Cross-Encoder (ms-marco-MiniLM)     │
   │                                      │
   │  输入: [查询 + SEP + 文档] 拼接       │
   │  "MCP 握手协议 [SEP] MCP v1 使用      │
   │   initialize 建立连接..."            │
   │                                      │
   │  Attention 能捕捉的细粒度关系:          │
   │  "handshake" ↔ "initialize" (同义)   │
   │  "MCP" ↔ "JSON-RPC" (领域关联)       │
   │  "TCP" ↔ "WebSocket" (负相关，降权)   │
   │                                      │
   │  输出: 相关度分数 0.87                │
   └──────────────────────────────────────┘
       │
       ▼
   返回 Top-5 最终结果
```

**为什么准**：联合编码 + 内部 Attention 让模型能理解查询和文档中每个词之间的关系——"查询里的 A 等于文档里的 B"，"查询提到的 X 和文档的 Y 是互斥的"。

**为什么慢**：每对 (查询, 文档) 都要完整跑一次 Transformer 前向传播。20 个候选 × 50ms = 1 秒。如果对全量文档做 Cross-Encoder，1 万文档 × 50ms = 8 分钟——完全不可接受。

#### 6.2.3 为什么工业界用两阶段而非单一方案

```
                    N 份文档 (如 10,000)
                         │
         ┌───────────────▼───────────────┐
         │  阶段 1: Bi-Encoder 快速召回   │  ← 10ms, 召回 Top-20
         │  (向量索引预计算，全量扫描)      │
         └───────────────┬───────────────┘
                         │ 20 个候选
         ┌───────────────▼───────────────┐
         │  阶段 2: Cross-Encoder 精排    │  ← 1s, 20 × 50ms
         │  (逐对深度比对，仅 Top-20)      │
         └───────────────┬───────────────┘
                         │ Top-5 最终结果
         ┌───────────────▼───────────────┐
         │  LLM 生成回答                  │
         └───────────────────────────────┘

总延迟: ~1s
精度: 接近全量 Cross-Encoder 的 95%+
成本: 仅为全量 Cross-Encoder 的 0.2%
```

**核心思想**：用廉价的模型快速过滤掉 99.8% 的无关文档，用昂贵的模型精细比较剩下的 0.2%。这不是 RAG 特有的——Google 搜索、推荐系统、向量数据库都是这个思路。这项优化**不需要权重参数**——两个阶段是串行关系，第二阶段直接替代第一阶段的排序。

#### 6.2.4 两种模型的对比

| | Bi-Encoder (召回) | Cross-Encoder (精排) |
|---|---|---|
| **编码方式** | 查询和文档独立编码 | 查询+文档拼接后联合编码 |
| **文档向量** | 可预先计算并索引 | 必须实时计算 |
| **相似度计算** | 余弦相似度（向量点积） | Transformer 全连接推理 |
| **处理能力** | 10 万文档 / 10ms | 20 个文档对 / 1s |
| **精度** | 中等（关键词匹配陷阱） | 高（语义理解） |
| **代表模型** | all-MiniLM-L6-v2 | ms-marco-MiniLM, BGE-Reranker |

### 6.3 操作步骤

```bash
python rag_experiment/run_rag_experiments.py -e 2D
```

此次实验会运行 `reranking_test.py`，对每一查询分别测试无重排和有重排两种情况，然后用量化指标对比差异。

### 6.4 预期结果解读

运行后，关注输出中的这些关键指标：

```
平均幻觉分          5.2/10         2.1/10       -3.1
幻觉次数                 4             1         -3
```

- **"被拯救的查询"**：有重排 → 幻觉消失（`is_hallucination: true → false`）。这类查询在 Bi-Encoder 阶段被噪声文档的高相似度误导，Cross-Encoder 介入后正确识别了噪声
- **"无变化的查询"**：有重排和无重排结果相同（两个模型对同一文档的质量判断一致）

### 6.5 练习任务

1. **基础**：记录有/无重排的幻觉率和准确率差异
2. **进阶**：找出至少 1 个被重排"拯救"的查询（无重排时有幻觉，有重排时无幻觉）。在该查询上分析：Bi-Encoder 把哪份噪声文档排到了前面？Cross-Encoder 把它降到了第几名？为什么它能识别出这是噪声？
3. **挑战**：在 `reranking_test.py` 中修改 `TRICKY_QUERIES`，添加一个你设计的查询，使 Bi-Encoder 产生幻觉但 Cross-Encoder 能修正

---

## 7. 实验 2E：混合检索对比

### 7.1 目标

对比三种检索模式（Dense / Sparse / Hybrid），深入理解两种融合方案的原理与参数选择方法。

### 7.2 为什么 Dense 和 Sparse 分数不能直接相加

#### 7.2.1 分数的量纲完全不同

```
查询: "MCP 握手协议"

Dense 检索结果 (余弦相似度):     Sparse 检索结果 (BM25 词频):
  文档A: 0.92                     文档C: 28.3
  文档B: 0.88                     文档A: 15.1
  文档C: 0.73                     文档D: 12.4
  文档D: 0.65                     文档B: 8.7

Dense 分数范围: [0.65, 0.92]     Sparse 分数范围: [8.7, 28.3]
          (差距 0.27)                     (差距 19.6)
```

如果直接做加权平均 `0.7 × 0.92 + 0.3 × 28.3 = 9.13`，结果完全被 Sparse 的 28.3 支配——不管 α 设多少，BM25 的分数都会"碾压"语义相似度。

**结论**：两个检索源的分数必须在**同一量纲**上才能加权。这就是需要 RRF 或归一化的原因。

#### 7.2.2 两种解决思路

| 方案 | 核心思路 | 参数 |
|------|---------|------|
| **RRF (排名融合)** | 不看分数本身，看"排第几名"。排名天然无量纲，可直接融合 | k（平滑因子） |
| **分数融合 (加权融合)** | 用 Min-Max 归一化把分数压到 [0,1]，再加权求和 | α（Dense 权重） |

### 7.3 方案一：RRF（Reciprocal Rank Fusion）

#### 7.3.1 核心公式

```
RRF_score(文档) = α/(k + rank_dense) + (1-α)/(k + rank_sparse)
```

#### 7.3.2 逐步示例

以 4 份文档为例，展示从原始分数到最终排名的完整计算过程。

**第 1 步：原始分数 → 排名**

```
Dense 排名:                  Sparse 排名:
  rank=1: 文档A (0.92)         rank=1: 文档C (28.3)
  rank=2: 文档B (0.88)         rank=2: 文档A (15.1)
  rank=3: 文档C (0.73)         rank=3: 文档D (12.4)
  rank=4: 文档D (0.65)         rank=4: 文档B (8.7)
```

**第 2 步：计算 `1/(k + rank)`（以 k=60, α=0.5 为例）**

```
文档A: 1/(60+1) + 1/(60+2) = 1/61 + 1/62 = 0.0164 + 0.0161 = 0.0325
文档B: 1/(60+2) + 1/(60+4) = 1/62 + 1/64 = 0.0161 + 0.0156 = 0.0317
文档C: 1/(60+3) + 1/(60+1) = 1/63 + 1/61 = 0.0159 + 0.0164 = 0.0323
文档D: 1/(60+4) + 1/(60+3) = 1/64 + 1/63 = 0.0156 + 0.0159 = 0.0315
```

**第 3 步：按 RRF 总分排序**

| 文档 | Dense rank | Sparse rank | 1/(k+rank_d) | 1/(k+rank_s) | RRF 总分 | 最终排名 |
|------|:---:|:---:|:---:|:---:|:---:|:---:|
| A | 1 | 2 | 0.0164 | 0.0161 | 0.0325 | **1** |
| C | 3 | 1 | 0.0159 | 0.0164 | 0.0323 | **2** |
| B | 2 | 4 | 0.0161 | 0.0156 | 0.0317 | **3** |
| D | 4 | 3 | 0.0156 | 0.0159 | 0.0315 | **4** |

文档 A 在两个检索源中都是 Top-2，RRF 自然地给了它最高分——**不需要调任何分数尺度的参数**。

#### 7.3.3 k 参数的作用

k 控制"排名差异有多大影响"：

```
k=0 时（精英决策）：         k=60 时（默认均衡）：        k=120 时（全民公投）：
rank=1 → 1/(0+1)=1.000 ████ rank=1 → 1/61=0.0164 ████  rank=1 → 1/121=0.0083 ██
rank=2 → 1/(0+2)=0.500 ███  rank=2 → 1/62=0.0161 ████  rank=2 → 1/122=0.0082 ██
rank=3 → 1/(0+3)=0.333 ██   rank=3 → 1/63=0.0159 ████  rank=3 → 1/123=0.0081 ██
rank=4 → 1/(0+4)=0.250 ██   rank=4 → 1/64=0.0156 ████  rank=4 → 1/124=0.0081 ██
     ↑ 第一名权重极大              ↑ 前四名几乎同权              ↑ 排名几乎没有影响
```

- **k=0**：「谁排第一谁说了算」——只有 Top-1 有实质话语权
- **k=10**：Top-3 有明显优势，Top-10 仍有参与感
- **k=60**（默认）：「Top-N 都不错，别纠结谁绝对是第一」
- **k=120**：极平坦——排名差异被高度压缩，更像"投票计数"

RRF 的优势在于**不需要理解检索源的分数分布**——只要你能排出一个 Top-K 列表，RRF 就能融合。这是它成为工业界默认方案的核心原因。

### 7.4 方案二：分数融合（Weighted Score Fusion）

#### 7.4.1 核心公式

```
score_norm = (score - min) / (max - min)     # Min-Max 归一化
final_score = α × dense_norm + (1-α) × sparse_norm
```

#### 7.4.2 逐步示例

**第 1 步：Min-Max 归一化**

```
文档A dense: (0.92 - 0.65) / (0.92 - 0.65) = 1.00
文档B dense: (0.88 - 0.65) / (0.92 - 0.65) = 0.85
文档C dense: (0.73 - 0.65) / (0.92 - 0.65) = 0.30
文档D dense: (0.65 - 0.65) / (0.92 - 0.65) = 0.00

文档C sparse: (28.3 - 8.7) / (28.3 - 8.7) = 1.00
文档A sparse: (15.1 - 8.7) / (28.3 - 8.7) = 0.33
文档D sparse: (12.4 - 8.7) / (28.3 - 8.7) = 0.19
文档B sparse: (8.7  - 8.7) / (28.3 - 8.7) = 0.00
```

现在两者都在 [0, 1] 范围内，可以加权了。

**第 2 步：加权求和（α=0.7，偏重语义）**

```
文档A: 0.7 × 1.00 + 0.3 × 0.33 = 0.70 + 0.10 = 0.80
文档C: 0.7 × 0.30 + 0.3 × 1.00 = 0.21 + 0.30 = 0.51
文档B: 0.7 × 0.85 + 0.3 × 0.00 = 0.60 + 0.00 = 0.60
文档D: 0.7 × 0.00 + 0.3 × 0.19 = 0.00 + 0.06 = 0.06
```

最终排名：A > B > C > D。Dense 的权重高（α=0.7），文档 A 在 Dense 中排第一，最终综合分最高。

#### 7.4.3 RRF vs 分数融合对比

| | RRF | 分数融合 |
|---|---|---|
| **融合的是什么** | 排名 | 原始分数（归一化后） |
| **预处理** | 无 | **必须**归一化 |
| **对异常值的敏感度** | 低（排名限定了 [1, N]） | **高**：一个极端分数会让 min-max 归一化失效 |
| **调参灵活性** | 弱（k 只影响排名敏感性） | 强（α 直接控制 Dense/Sparse 贡献比例） |
| **稳定性** | 高 | 中（取决于归一化质量） |

**典型场景选择**：
- 检索源分数分布完全未知或波动大 → **RRF**（不需要归一化，天然鲁棒）
- 分数分布稳定可控、需要精确调节 → **分数融合**（α 可以直接解读为 Dense 占比）

### 7.5 代码中的三种模式

实验 2E 同时实现了 RRF 和分数融合，位置在 `hybrid_search.py`：

```python
# 模式 1: 标准 RRF (k=60, α=0.5) — hybrid_search.py:100
rrf_fusion(dense_ranked, sparse_ranked, k=60, alpha=0.5, top_k=5)

# 模式 2: 加权 RRF (k=60, α=0.7) — hybrid_search.py:100
rrf_fusion(dense_ranked, sparse_ranked, k=60, alpha=0.7, top_k=5)

# 模式 3: 分数融合 (Min-Max归一化 + α=0.7) — hybrid_search.py:110
score_fusion(dense_ranked, sparse_ranked, alpha=0.7, top_k=5)
```

三种模式的对比结果会在 `compare_hybrid()` 函数的输出中并排显示。

### 7.6 参数选择：通过实验确定最优值

#### 7.6.1 为什么不能"网上查默认值"

"k=60 是通用默认值"是出发点，**不是你的数据的答案**。最优参数值取决于：

- 你的文档类型（技术文档 vs 新闻文章 → 术语密度不同 → Sparse 有效性不同）
- 你的查询分布（语义改写占比 vs 精确术语占比 → Dense 的重要性不同）
- 你的噪声文档特征（噪声是否与目标共享词汇 → 对 k 的敏感度不同）

**唯一确定最优值的方法：在你自己的数据上做实验。**

#### 7.6.2 实验流程

```
① 准备验证查询集
   至少 5-10 条查询，每条标注正确文档
   （代码中已有 BENCHMARK_QUERIES，你也可以自行添加）

② 运行网格搜索
   python experiment_rag/hybrid_search.py --sensitivity
   → 遍历所有 (k, α) 组合: {10,60,120} × {0.3,0.5,0.7}

③ 对每个组合计算检索质量指标
   score = target_recall - noise_docs × 0.5
   （优先目标文档召回，惩罚噪声文档误召回）

④ 选择 score 最高的 (k, α) 组合
   标注 ★ 的那一行就是实验确定的最优参数

⑤ 在额外查询上验证
   用未参与网格搜索的新查询检验最优参数的泛化性
```

#### 7.6.3 代码中的实现

`hybrid_search.py` 的 `parameter_sensitivity()` 函数（行 398-418）就是上述流程的直接实现：

```python
for k in k_values:            # [10, 60, 120]
    for alpha in alpha_values: # [0.3, 0.5, 0.7]
        h_ranked = rrf_fusion(d_ranked, s_ranked, k=k, alpha=alpha)
        h_ev = _evaluate(h_ranked, doc_types, top_k)
        score = h_target - h_noise * 0.5
        if score > best_score:
            best_score = score
            best_params = f"k={k},α={alpha}"   # ← 记录最优组合
```

**这是实验方法论的核心**：不猜测、不查默认值、用数据说话。

#### 7.6.4 预期输出示例

运行 `--sensitivity` 后会输出每个查询的网格搜索结果：

```
  k      α      Hybrid   vs Dense   vs Sparse
  10     0.3     2/5     ↓         ↓
  10     0.5     3/5     ↑+1       ↑+1
  10     0.7     4/5     ↑+2       ↑+2     ★
  60     0.3     2/5     ↓         ↓
  60     0.5     3/5     ↑+1       ↑+1
  60     0.7     4/5     ↑+2       ↑+2     ★
  120    0.3     2/5     ↓         ↓
  120    0.5     2/5     ↓         ↓
  120    0.7     3/5     ↑+1       ↑+1

  最佳参数: k=10,α=0.7 (Hybrid=4/5)
```

#### 7.6.5 解读实验结果

拿到最优参数后，**解释为什么是这个值**——这比参数值本身更重要：

> "我的查询集中有 3 条是语义改写（如'怎样建立连接'→ 需要理解同义词），只有 1 条是精确术语。Dense 检索在这些查询上明显优于 Sparse。所以最优 α=0.7（Dense 占 70% 话语权）是合理的。k 在 10 和 60 之间差异不大，说明我的 Dense 检索本身排序质量较高——前几名确实都是相关文档，不需要大 k 来'全民公投'。"

**这才是科学调参**：数据 → 实验 → 最优值 → 解释原因。

### 7.7 操作步骤

```bash
# 混合检索对比（含参数敏感性分析）
python experiment_rag/run_rag_experiments.py -e 2E

# 或直接运行
python experiment_rag/hybrid_search.py             # 完整对比
python experiment_rag/hybrid_search.py --sensitivity  # 参数网格搜索
```

### 7.8 预期发现

#### 7.8.1 检索模式对比

| 查询类型 | Dense | Sparse | 最佳 Hybrid | 原因 |
|---------|:---:|:---:|:---:|------|
| 语义改写（"怎样建立连接"） | 4/5 | 2/5 | RRF(α=0.7) | "建立连接"≠"initialize"，Sparse 匹配不到 |
| 精确术语（"tools/call 确认"） | 2/5 | 4/5 | RRF(α=0.3) | 精确术语匹配比语义理解更重要 |
| 通用描述（"介绍一下 MCP"） | 3/5 | 3/5 | RRF(α=0.5) | 均衡即可，无一方占优 |

#### 7.8.2 参数敏感性

- **k 的敏感度**：当两个检索源的排名质量都较高时（Top-3 确实相关），k 的影响较小（10 和 60 结果相近）；当某个源的 Top-10 方差大时，大 k 能避免被误判带偏
- **α 的敏感度**：当 Dense 和 Sparse 的排名差异大时（同一文档在两个源中排名悬殊），α 的选择至关重要

### 7.9 练习任务

1. **基础**：运行混合检索对比，找出至少 1 个被 Hybrid"拯救"的查询（Dense 或 Sparse 单独表现差，Hybrid 后改善）
2. **进阶**：运行 `--sensitivity`，对你提供的 3 条自定义查询各确定最优 (k, α)，**分析不同查询的最优参数为何不同**——联系查询类型（语义改写 vs 精确术语）进行解释
3. **挑战**：在 `hybrid_search.py` 中实现你自己的评分函数（除 RRF 和 Min-Max 归一化之外的第三种融合方式），测试并与其他两种对比

---

## 8. 实验总结与综合思考

### 8.1 核心洞见

1. **向量相似度是"盲人摸象"**：高相似度不代表语义相关，噪声文档可能通过共享词汇获得高分。这是实验 2D 和 2E 要解决的核心问题。
2. **分块策略是双刃剑**：太小→碎片化，太大→稀释。最佳尺寸取决于文档密度和查询类型。
3. **两阶段检索（粗筛+精排）** 是工业界主流方案：Bi-Encoder 负责快速召回，Cross-Encoder 负责精准排序。详见实验 2D 的 6.2 节。
4. **可观测性是诊断的前提**：没有 UMAP 可视化，你永远不知道检索质量到底如何。
5. **参数不能靠"网上查"**：RRF 的 k 和加权融合的 α 的最优值取决于**你的数据**——文档类型、查询分布、噪声特征。唯一确定最优值的方法是在你自己的验证集上做网格搜索。详见实验 2E 的 7.6 节。

### 8.2 实验报告要求

| 章节 | 内容要求 |
|------|----------|
| **1. Embedding 可视化** | 附上 UMAP 3D 截图，标注各文档簇和查询点，分析分布特征 |
| **2. 检索失效分析** | 列 3 个"相似≠相关"案例，分析每个失效的根因 |
| **3. 分块策略** | 以表格呈现 4 种 chunk_size 的对比结果，分析最佳尺寸 |
| **4. 重排对比** | 解释 Bi-Encoder / Cross-Encoder 两阶段的原理。列出有/无重排的幻觉率差异，标注至少 1 个被"拯救"的查询并分析原因 |
| **5. 混合检索** | 对比 Dense vs Sparse vs Hybrid 的检索结果。通过 `--sensitivity` 网格搜索确定最优 (k, α)，**解释为什么最优参数是这个值**（联系数据特征和查询类型分析） |
| **6. 改进建议** | 基于实验数据，提出至少一条改进 RAG 质量的建议 |

### 8.3 扩展阅读

- "Retrieval-Augmented Generation for Knowledge-Intensive NLP Tasks" (Lewis et al., 2020)
- Sentence-BERT: Sentence Embeddings using Siamese BERT-Networks (Reimers & Gurevych, 2019)
- UMAP: Uniform Manifold Approximation and Projection (McInnes et al., 2018)
- Arize Phoenix Embeddings: https://docs.arize.com/phoenix/datasets-and-schema/embeddings

---

## 附录 A：故障排查

### SentenceTransformer 模型下载慢

```bash
# 使用国内镜像
export HF_ENDPOINT=https://hf-mirror.com
python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('all-MiniLM-L6-v2')"
```

### ChromaDB 集合冲突

```bash
# build_kb.py 会自动删除同名集合重建，无需手动处理
```

### UMAP 降维报错 n_neighbors

如果文档块数量少于 15，`n_neighbors` 必须小于样本数。`embedding_viz.py` 已处理此边界情况。

### Phoenix 不可用

实验 2A 会自动降级到本地 UMAP + Plotly 方案，不影响实验进行。
