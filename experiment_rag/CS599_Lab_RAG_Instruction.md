# CS599 实验 X：RAG 瓶颈诊断与向量空间聚类实验

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
| **2D** | 验证 Cross-Encoder Reranker 能否拯救检索质量 | ms-marco-MiniLM | 重排对比报告 |
| **2E** | 对比 Dense/Sparse/Hybrid 三种检索模式，分析 RRF 参数敏感性 | BM25 + RRF 融合 | 混合检索对比报告 + 参数敏感性矩阵 |

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

虽然第 2 名的文档才真正相关，但向量相似度只看到了"握手"这个关键词在 noise 文档中的高频出现。这就是**关键词匹配陷阱**。

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

**原理**：
- **Bi-Encoder**（检索阶段）：将查询和文档独立编码，计算余弦相似度。速度快但精度有限。
- **Cross-Encoder**（重排阶段）：将查询和文档拼接后联合编码，逐对打分。精度高但速度慢。

### 6.2 操作步骤

```bash
python rag_experiment/run_rag_experiments.py -e 2D
```

### 6.3 练习任务

1. **基础**：记录有/无重排的幻觉率和准确率差异
2. **进阶**：找出至少 1 个被重排"拯救"的查询（无重排时有幻觉，有重排时无幻觉）

---

## 7. 实验 2E：混合检索对比

### 7.1 目标

对比三种检索模式，理解为什么工业界采用"Dense + Sparse 融合"而非单一策略。

| 模式 | 原理 | 优势 | 劣势 |
|------|------|------|------|
| **Dense** | MiniLM 语义向量 | 理解同义词/改写 | 对精确术语不敏感 |
| **Sparse** | BM25 词频统计 | 精确关键词匹配 | 不懂语义 |
| **Hybrid** | RRF 融合两者排名 | 取长补短 | 参数需调优 |

### 7.2 操作步骤

```bash
# 混合检索对比（含参数敏感性分析）
python experiment_rag/run_rag_experiments.py -e 2E

# 或直接运行
python experiment_rag/hybrid_search.py             # 完整对比
python experiment_rag/hybrid_search.py --sensitivity  # 参数分析
```

### 7.3 RRF 公式与参数

```
score(doc) = α/(k + rank_dense) + (1-α)/(k + rank_sparse)
```

| 参数 | 含义 | 小值 | 大值 |
|------|------|------|------|
| **k** | RRF 平滑因子 | k=10：只有 Top-3 有话语权，"精英决策" | k=120：Top-10 权重几乎相同，"全民公投" |
| **α** | Dense 权重 | α=0.3：偏向关键词匹配 | α=0.7：偏向语义理解 |

### 7.4 预期发现

| 查询类型 | Sparse 表现 | 最优 α | 原因 |
|---------|------------|--------|------|
| 语义改写（"怎样建立连接"） | 3/5 目标 | 0.7 | "建立连接"≠"initialize"，Sparse 无法匹配 |
| 精确术语（"tools/call 确认机制"） | 4/5 目标 | 0.5 | Sparse 本身还行，均衡即可 |
| 两者都满分 | 5/5 目标 | 0.3 | 混合无增益，α 不重要 |

### 7.5 练习任务

1. **基础**：运行混合检索对比，找出被 Hybrid"拯救"的查询
2. **进阶**：对比 k=10/60/120 对同一查询的影响，记录最优 k 值
3. **挑战**：在 `hybrid_search.py` 的 `parameter_sensitivity()` 中添加你自己的查询，观察参数敏感性

---

## 8. 实验总结与综合思考

### 8.1 核心洞见

1. **向量相似度是"盲人摸象"**：高相似度不代表语义相关，噪声文档可能通过共享词汇获得高分
2. **分块策略是双刃剑**：太小→碎片化，太大→稀释。最佳尺寸取决于文档密度和查询类型
3. **两阶段检索（粗筛+精排）** 是工业界主流方案：Bi-Encoder 负责快速召回，Cross-Encoder 负责精准排序
4. **可观测性是诊断的前提**：没有 UMAP 可视化，你永远不知道检索质量到底如何
5. **混合检索不是银弹**：Dense/Sparse 各有优势，RRF 参数 (k/α) 需要根据查询类型动态调整——语义改写用大 α，精确术语用小 α，两边都满分时混合无增益

### 8.2 实验报告要求

| 章节 | 内容要求 |
|------|----------|
| **1. Embedding 可视化** | 附上 UMAP 3D 截图，标注各文档簇和查询点，分析分布特征 |
| **2. 检索失效分析** | 列 3 个"相似≠相关"案例，分析每个失效的根因 |
| **3. 分块策略** | 以表格呈现 4 种 chunk_size 的对比结果，分析最佳尺寸 |
| **4. 重排对比** | 列出有/无重排的幻觉率差异，标记被"拯救"的查询 |
| **5. 混合检索** | 分析 Dense vs Sparse vs Hybrid 的对比结果，说明 RRF 参数 (k/α) 的最优选择及其原因 |
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
