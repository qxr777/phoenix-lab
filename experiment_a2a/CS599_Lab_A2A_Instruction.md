# CS599 实验 4：A2A 多智能体分布式追溯与性能瓶颈优化实验

> [!NOTE]
> **教师寄语**：当单个 Agent 变成多 Agent 协作系统，性能瓶颈从"模型推理"扩散到了"Agent 拓扑"。一个子智能体的 3 次重试、一段过长的 System Prompt、一个不断膨胀的上下文窗口——每个点都可能成为性能杀手。本实验将传统的微服务 APM（应用性能监控）思维无缝平移到 AI 时代，让你像诊断分布式系统一样诊断多智能体系统。

---

## 目录

- [1. 实验概述](#1-实验概述)
- [2. 环境准备](#2-环境准备)
- [3. 实验 I：构建多智能体系统](#3-实验-i构建多智能体系统)
- [4. 实验 J：关键路径分析](#4-实验-j关键路径分析-critical-path-analysis)
- [5. 实验 K：Token 成本控制](#5-实验-ktoken-成本控制)
- [6. 实验 L：性能优化回测](#6-实验-l性能优化回测)
- [7. 实验总结与综合思考](#7-实验总结与综合思考)
- [附录 A：代码导航指南](#附录-a代码导航指南)
- [附录 B：性能指标详解](#附录-b性能指标详解)
- [附录 C：三个场景的详细设计](#附录-c三个场景的详细设计)
- [附录 D：故障排查](#附录-d故障排查)

---

## 1. 实验概述

### 1.1 实验目标

本实验构建一个四层多智能体协作系统，通过 Phoenix 的分布式追踪能力进行性能诊断和优化：

| 阶段 | 核心任务 | 关键工具 | 产出 | 预计耗时 |
|------|----------|----------|------|----------|
| **I** | 搭建 Gateway → Planner → Executors × N → Reviewer 四层拓扑，运行 3 个场景 | `a2a_orchestrator.py` | 多智能体 Trace 数据 (JSON) | 5-15 分钟 |
| **J** | 从 Phoenix 拓扑树中逐 span 分析延迟，标注 Critical Path，揪出性能瓶颈 | `critical_path.py` | Critical Path 报告 + Agent 延迟分布 (HTML/JSON) | < 1 分钟 |
| **K** | 绘制 Token 消耗曲线的逐轮变化，计算上下文利用率，给出裁剪建议 | `token_analyzer.py` | Token 膨胀曲线 + 裁剪方案 (HTML/JSON) | < 1 分钟 |
| **L** | 应用三项优化（精简 Prompt、裁剪上下文、消除重试），对比 `--optimize` 模式与基线 | `a2a_orchestrator.py --optimize` | 优化回测对比报告 | 2-5 分钟 |

### 1.2 前置知识

在开始本实验之前，你应该已经掌握：

- **LLM 的 Tool Calling / Function Calling 机制**：理解 Agent 如何通过 `tool_choice="auto"` 决定调用哪个工具，以及工具返回结果如何进入下一轮对话。本实验中每个 Agent 都使用了工具调用模式。
- **OpenTelemetry (OTel) 基础概念**：理解 Span（一次操作的时间记录）和 Trace（一组 Span 的树状集合）的基本含义。Phoenix 的追踪数据基于 OTel 协议。
- **微服务 APM 基本概念**：如果你了解过 Jaeger 或 Zipkin 等分布式追踪工具，你会发现 Phoenix 的拓扑树与它们非常相似——"Critical Path"、"Bottleneck"、"Latency Distribution" 等概念可以直接平移。
- **Token 经济学基础**：理解 LLM 的 `prompt_tokens`（输入）和 `completion_tokens`（输出）的区别，以及它们如何影响成本（按 token 计费）和延迟（更大的 context = 更慢的推理）。

### 1.3 为什么需要分布式 APM 思维？

```
传统微服务 APM:
  Service A (100ms) → Service B (250ms) → Service C (3,000ms) ← 瓶颈！
  Jaeger/Zipkin 可以直接看到每条调用链的耗时分布。

多智能体系统:
  Gateway (1.2s) → Planner (2.8s) → Executor × 3 (1.5s / 10s / 5.6s) → Reviewer (6.8s)
  Phoenix Trace 同样可以呈现这个拓扑树——但你需要学会如何解读它。
```

**APM 概念 × AI Agent = A2A 性能可观测性**：

| 概念 | 传统 APM 含义 | AI Agent 中的含义 |
|------|-------------|-----------------|
| **Span** | 一次 RPC 调用 | 一个 Agent 的 LLM 调用 + 工具调用 |
| **Trace** | 从入口到出口的完整调用链 | 从用户请求到最终回复的完整 Agent 链 |
| **Critical Path** | 耗时最长的调用路径 | 决定端到端延迟的 Agent 序列 |
| **Bottleneck** | 性能瓶颈服务 | 延迟占比最高的 Agent |
| **TTFT** (Time To First Token) | N/A | Agent 收到输入后到输出第一个 token 的延迟 |
| **Context Inflation** | N/A | 上下文窗口随轮次增加的 Token 膨胀 |

### 1.4 系统拓扑（详细版）

本系统包含 4 层、6 个 Agent 角色，每个角色在 Phoenix Trace 中有独立的 Span：

```
┌─────────────────────────────────────────────────────────────────────┐
│                        A2A 多智能体拓扑                                │
│                                                                     │
│  用户请求 [User Request]                                             │
│    │                                                                │
│    ▼                                                                │
│  ┌─────────────────────────────────────────┐                       │
│  │ Layer 1: Gateway Agent                   │  role: 轻量路由        │
│  │   文件: agents/gateway.py                │  sys_prompt: ~100 tok │
│  │   工具: 无（纯 LLM 调用）                  │  target: ~1-3s        │
│  │   span: gateway.route                    │  trap: 无（正常基线）   │
│  │   输出: 意图识别 + 路由指令                 │                       │
│  └────────────────┬────────────────────────┘                       │
│                   ▼                                                 │
│  ┌─────────────────────────────────────────┐                       │
│  │ Layer 2: Planner Agent                   │  role: 任务分解        │
│  │   文件: agents/planner.py                │  sys_prompt: ~800 tok │
│  │   工具: 无（纯 LLM 调用）                  │  target: ~2-5s        │
│  │   span: planner.decompose                │  trap: 高 TTFT        │
│  │   输出: 任务分解为 1-4 个子任务              │  10+ 条规则 → 首字延迟  │
│  └────┬──────────┬──────────┬──────────────┘                       │
│       │          │          │                                       │
│       ▼          ▼          ▼                                       │
│  ┌─────────┐┌──────────┐┌──────────┐                               │
│  │Layer 3: ││Layer 3:  ││Layer 3:  │  role: 执行子任务              │
│  │ Search  ││  Code    ││  Data    │                                │
│  │Executor ││ Executor ││ Executor │                                │
│  │ ~1.5s   ││ ~10s     ││ ~5.6s    │  tools: mcp_search /          │
│  │ baseline││ retry    ││ TTFT+slow│          run_code_check /      │
│  │ 无陷阱  ││ trap     ││  trap    │          query_database        │
│  └────┬────┘└────┬─────┘└────┬─────┘                               │
│       │          │          │                                       │
│       └──────────┼──────────┘                                       │
│                  ▼                                                  │
│  ┌─────────────────────────────────────────┐                       │
│  │ Layer 4: Reviewer Agent                  │  role: 质量审核        │
│  │   文件: agents/reviewer.py               │  sys_prompt: ~200 tok │
│  │   工具: 无（纯 LLM 调用）                  │  target: ~5-10s       │
│  │   span: reviewer.assemble                │  trap: 上下文膨胀      │
│  │   输入: 所有 executor 的完整输出            │  → 最大 prompt tokens │
│  │   输出: 最终用户回复 + 质量审核             │                       │
│  └─────────────────────────────────────────┘                       │
│                  │                                                  │
│                  ▼                                                  │
│            最终回复 [Final Response]                                 │
│                  │                                                  │
│                  ▼                                                  │
│         Phoenix OTLP Traces (:6006)                                 │
└─────────────────────────────────────────────────────────────────────┘
```

### 1.5 三个预定义场景

| 场景 ID | 任务描述 | 触发瓶颈 | 教学重点 |
|---------|---------|---------|---------|
| `code_review_task` | 审查一个 Python 函数并给出优化建议 | **executor_code** 3 次自修正重试 | 重试风暴：单 Agent 的格式自修正导致延迟扩大 3-4 倍 |
| `data_report_task` | 分析 Q1-Q4 销售数据并生成周报 | **executor_data** 高 TTFT + 慢 MCP 工具 | 双延迟来源：首字延迟（TTFT）和工具响应延迟是独立的 |
| `multi_search_task` | 调研三组技术方案对比（gRPC vs REST 等） | **reviewer** 上下文膨胀 + 木桶效应 | 并联瓶颈 + 上下文爆炸：多个 executor 并行，最慢者决定总延迟，reviewer 的 context 达到峰值 |

### 1.6 文件结构总览

```
experiment_a2a/
├── CS599_Lab_A2A_Instruction.md   ← 你正在阅读的文档
├── run_experiments.py              ← 实验启动器（I/J/K/L + 交互菜单）
├── requirements.txt                ← Python 依赖
│
├── a2a_orchestrator.py            ← ★ 多智能体编排器主入口
│   - 按拓扑顺序调用各 Agent
│   - 手动创建 OTEL Span 标记 Agent 边界
│   - 支持 --optimize 模式进行性能优化
│
├── agents/                         ← 6 个 Agent 模块
│   ├── gateway.py                  ← Layer 1: 前端网关 (~100 行)
│   ├── planner.py                  ← Layer 2: 任务规划 (~110 行)
│   ├── executor_search.py          ← Layer 3a: 信息检索 (~110 行)
│   ├── executor_code.py            ← Layer 3b: 代码审查 ★ 重试陷阱 (~160 行)
│   ├── executor_data.py            ← Layer 3c: 数据分析 ★ 双陷阱 (~155 行)
│   └── reviewer.py                 ← Layer 4: 审核拼装 (~90 行)
│
├── scenarios/
│   └── tasks.json                  ← 3 个预定义协作场景
│
├── analysis/                       ← 分析工具（实验 J & K）
│   ├── critical_path.py            ← 关键路径分析：span 树 → bottleneck
│   └── token_analyzer.py           ← Token 分析：曲线 → 利用率 → 裁剪
│
└── output/                         ← 运行时生成
    ├── a2a_latest_trace.json       ← 实验 I 产生的原始 trace 数据
    ├── a2a_optimized_trace.json    ← 实验 L 产生的优化后 trace 数据
    ├── critical_path_report.html   ← 实验 J 的 HTML 报告
    ├── token_report.html           ← 实验 K 的 HTML 报告
    └── optimization_summary.md     ← 实验 L 的对比总结
```

---

## 2. 环境准备

### 2.1 安装 Python 依赖

```bash
cd phoenix_lab
pip install -r experiment_a2a/requirements.txt
```

本实验的核心依赖与 ASI 和 LLM-Judge 实验相同（OpenAI SDK + OTel），不需要额外的库。

### 2.2 配置 LLM 服务

编辑项目根目录 `.env` 文件。以下两种方式任选其一：

**推荐（速度优先）—— 云端 API：**
```bash
OPENAI_API_KEY=sk-your-key-here
OPENAI_BASE_URL=https://api.openai.com/v1
OPENAI_MODEL=gpt-4o-mini
```

**本地部署（可控性好，但较慢）—— llama-server：**
```bash
OPENAI_API_KEY=not-needed
OPENAI_BASE_URL=http://localhost:8080/v1
OPENAI_MODEL=qwen2.5-7b
```

> ⚠️ **性能提示**：本实验包含 6 个 Agent 的 LLM 调用（每个场景约 6-12 次 API 调用）。使用本地模型（如 qwen2.5-7b）时，每个场景的耗时可能达到 2-5 分钟，3 个场景总计 6-15 分钟。使用云端 API（如 gpt-4o-mini）可将每个场景压缩到 30-60 秒。

### 2.3 启动 Phoenix 容器（强烈推荐）

Phoenix 是本实验的核心观测工具。虽然在未启动 Phoenix 时实验仍可运行（进程内 Trace），但拓扑树、Span 详情等关键功能需要 Phoenix UI。

```bash
# 启动 Phoenix
cd docker && docker compose up -d && cd ..

# 验证
curl http://localhost:6006/health

# 打开 Phoenix UI
open http://localhost:6006
```

### 2.4 启用 Phoenix 遥测

在 `.env` 中添加以下配置（仅当 Phoenix 容器运行时才生效）：

```bash
ENABLE_PHOENIX_TRACING=true
PHOENIX_COLLECTOR_ENDPOINT=http://127.0.0.1:6006/v1/traces
```

### 2.5 环境验证

```bash
python experiment_a2a/run_experiments.py check
```

预期输出：
```
环境检查...
  .env 配置: ✓
  LLM 服务: ✓ (http://localhost:8080/v1)
  Phoenix 遥测: 已启用
  Phoenix 连接: ✓ (http://127.0.0.1:6006)
  场景文件: ✓
```

---

## 3. 实验 I：构建多智能体系统

### 3.1 目标

运行 A2A 编排器，观察四层 Agent 协作的完整执行过程，为后续实验 J 和 K 生成 Trace 数据。

### 3.2 操作步骤

```bash
# 方式 1：通过实验启动器（推荐，运行全部 3 个场景）
python experiment_a2a/run_experiments.py --experiment I

# 方式 2：直接运行编排器（逐个场景运行，便于观察）
python experiment_a2a/a2a_orchestrator.py --task code_review_task
python experiment_a2a/a2a_orchestrator.py --task data_report_task
python experiment_a2a/a2a_orchestrator.py --task multi_search_task

# 方式 3：运行单个场景并查看详细 trace
python experiment_a2a/a2a_orchestrator.py --task code_review_task \
  --output-json experiment_a2a/output/my_trace.json
```

### 3.3 运行过程解读

以下是 `code_review_task` 场景的完整执行过程。我们逐行解读每个输出的含义：

```
🔗 A2A 多智能体编排器
============================================================
模型: gpt-4o-mini
Phoenix 遥测: 已启用
可用场景: 3 个

────────────────────────────────────────────────────────────
🚀 A2A Pipeline: 代码审查与优化建议
────────────────────────────────────────────────────────────
查询: 请帮我审查以下 Python 函数的性能并给出优化建议：...

  [1/4] Gateway: 解析请求... ✓ (1234ms)
```

**解读**：Gateway Agent 完成了请求解析。`(1234ms)` = 从发送请求给 LLM 到收到完整回复的总延迟。Gateway 的 System Prompt 仅有 ~100 tokens，所以延迟主要由 LLM 推理决定。

```
  [2/4] Planner: 分解任务... ✓ (2856ms, 2 个子任务)
```

**解读**：Planner 将用户请求分解为 2 个可并行执行的子任务。延迟 2856ms——注意这比 Gateway 的 1234ms 慢了一倍多，因为 Planner 的 System Prompt 故意设计为 ~800 tokens（见附录 C），导致 TTFT（首字延迟）更高。

```
  [3/4] Executors: 并行执行 2 个子任务...
         ├─ subtask_1 (search)... ✓ (1522ms)
         ├─ subtask_2 (code_review)... ✓ (10234ms, 3 retries)  ← 注意这里！
         └─ 总耗时: 10234ms
```

**解读**：
- `subtask_1 (search)` 是正常基线——1522ms，无陷阱。
- `subtask_2 (code_review)` 耗时 10234ms，显示 `3 retries` —— 这是本实验的核心教学点。
  - 原因：`run_code_check()` 工具的前两次调用返回了 `FORMAT_ERROR`，导致 Agent 必须重试修正格式。
  - 如果工具首次就返回正确结果，这个 Agent 可能只需要 ~3000ms。

```
  [4/4] Reviewer: 审核 + 拼装... ✓ (6834ms)

  总结:
    总延迟: 21890ms
    Token in: 1845, out: 623
    Bottleneck: executor_code
    Teaching point: 重试风暴 — 单个 agent 的格式自修正占据了总耗时的 25% 以上
```

**解读**：
- Reviewer 的延迟 6834ms 较高，因为它接收了所有 executor 的完整输出（大上下文 → 更慢推理）。
- `总延迟: 21890ms` = 从 Gateway 开始到 Reviewer 结束的墙上时钟时间。
- `Bottleneck: executor_code` 指出执行代码审查的 Agent 是延迟最大的单点。

### 3.4 三个场景的差异对比

| 观察维度 | code_review_task | data_report_task | multi_search_task |
|---------|------------------|------------------|--------------------|
| Planner 子任务数 | 2 | 2-3 | 3 |
| executor_code 重试 | **3 次** ← 关键 | 0-1 次（非代码任务） | 0-1 次 |
| executor_data 延迟 | N/A | **高** (TTFT + 慢工具) | 高 |
| Reviewer context 大小 | 中 | 中 | **最大**（3 个 executor 输出） |
| Bottleneck | executor_code | executor_data | reviewer（木桶效应） |

### 3.5 在 Phoenix UI 中查看 Trace

运行实验 I 后，打开 Phoenix UI 查看拓扑树：

1. 在浏览器打开 `http://localhost:6006`
2. 左侧导航 → **Tracing** → **Traces**
3. 在列表中按时间排序，找到最新的 trace
4. 点击展开，你应该能看到如图 1 所示的拓扑树

**关键 Span 识别**：
- `gateway.route` — Gateway Agent 的 LLM 调用
- `planner.decompose` — Planner Agent 的 LLM 调用
- `executor.search` / `executor.code_review` / `executor.data_analysis` — 各 Executor
- `reviewer.assemble` — Reviewer Agent 的 LLM 调用

> 💡 **提示**：如果你在 Phoenix 中看不到上述 span 名称，可能是因为 Phoenix 的 OpenInference 自动命名覆盖了手动创建的 span。此时手动 span 和 LLM span 会分别显示。

### 3.6 输出文件

实验 I 完成后，以下文件将被生成：

| 文件 | 内容 | 用途 |
|------|------|------|
| `output/a2a_latest_trace.json` | 所有场景的完整 Agent 调用链数据 | 实验 J 和 K 的输入 |
| `output/_temp_interviewer_result.json` | 单次运行的临时结果 | 调试（可忽略） |

### 3.7 练习任务

1. **基础**（必做）：运行 3 个场景，填写下表：

   | 场景 | 总延迟 (ms) | Bottleneck Agent | Bottleneck 占比 | Token in | Token out |
   |------|------------|-----------------|----------------|----------|-----------|
   | code_review_task | | | | | |
   | data_report_task | | | | | |
   | multi_search_task | | | | | |

2. **进阶**：打开 Phoenix UI，在 code_review_task 的 trace 中找到 executor_code 相关的 Span，观察 3 次重试的时间线。在同一 trace 中，你能看到多少个 Span？

3. **挑战**：修改 `scenarios/tasks.json`，添加你自己的第 4 个协作场景。要求该场景能够触发至少一个性能陷阱。运行它并记录结果。

---

## 4. 实验 J：关键路径分析 (Critical Path Analysis)

### 4.1 核心概念

#### 什么是 Critical Path？

在分布式系统中，**Critical Path** 是从 trace 的根 span 到叶 span 的所有路径中，总耗时最长的那条路径。它决定了整个系统的端到端延迟。

**类比**：假设你要做一顿饭，有三道菜需要同时准备。你能开始吃的时间取决于**最后完成**的那道菜。这道菜就是你的 Critical Path。

#### 为什么 Critical Path 是关键指标？

- 优化非 Critical Path 上的组件**不会**减少端到端延迟
- 只有缩短 Critical Path 上的耗时，才能让系统整体变快
- 在 10 个 Agent 的系统中，通常只有 1-3 个 Agent 在 Critical Path 上

### 4.2 操作步骤

```bash
# 方式 1：通过启动器（自动读取 a2a_latest_trace.json）
python experiment_a2a/run_experiments.py --experiment J

# 方式 2：直接运行分析工具
python experiment_a2a/analysis/critical_path.py \
  --trace-file experiment_a2a/output/a2a_latest_trace.json \
  --output-json experiment_a2a/output/critical_path_report.json
```

### 4.3 预期输出：逐字段解读

```
⏱️  关键路径分析: 代码审查与优化建议
============================================================

总览
  总延迟:        21890ms
  Token in:      1845
  Token out:     623

🔴 Critical Path
  路径: user_request → gateway → planner → executor_code → reviewer
  瓶颈: executor_code — 10234ms (46.8%)
```

**解读**：
- `Critical Path` 列出了从用户请求到最终回复的完整调用链中耗时最长的那条路径。
- `瓶颈: executor_code` 是这条路径上延迟最高的单个 Agent。
- `46.8%` = executor_code 的延迟 10234ms / 总延迟 21890ms。这意味着**将近一半的时间花在了 executor_code 上**。

```
Agent 延迟分布
  Agent                   延迟      占比     Tokens  工具调用
  ─────────────────────────────────────────────────────────────
  gateway               1234ms    5.6%       330        0
  planner               2856ms   13.0%      3176        0
  executor_search       1522ms    7.0%      1470        1
  executor_code        10234ms   46.8%      1720        3
  reviewer              6834ms   31.2%      5514        0
```

**解读**：
- `占比` 列显示每个 Agent 在总延迟中的百分比。**所有占比相加不一定等于 100%**，因为 Executor 是并行执行的，总延迟不是它们的和。
- `工具调用` 列：executor_code 的 3 次工具调用 = 3 次 retry（包括 2 次失败 + 1 次成功）。

### 4.4 三个场景的 Critical Path 对比

| 场景 | Critical Path | Bottleneck | 占 | 优化方向 |
|------|--------------|-----------|-----|---------|
| code_review_task | gateway → planner → executor_code → reviewer | executor_code | 46.8% | 消除重试 |
| data_report_task | gateway → planner → executor_data → reviewer | executor_data | 35-40% | 精简 System Prompt + 优化工具 |
| multi_search_task | gateway → planner → [并行 executor 最慢者] → reviewer | reviewer | 30-35% | 上下文裁剪 |

### 4.5 深入分析：Critical Path 的三种典型形态

#### 形态 1：串行瓶颈（code_review_task）

```
gateway (5.6%) → planner (13%) → executor_code (46.8%) ← 🔴 → reviewer (31.2%)
                                      ↑
                                 单点延迟最大
```
**优化策略**：直接优化 executor_code（如消除重试），效果立竿见影。

#### 形态 2：双峰瓶颈（data_report_task）

```
gateway (3%) → planner (8%) → executor_data (40%) ← 🔴 → reviewer (35%)
                                ↑                    ↑
                          TTFT + 慢工具          大上下文
```
**优化策略**：需要两面出击——既减少 executor_data 的 System Prompt 和工具延迟，又裁剪 reviewer 的输入。

#### 形态 3：尾部瓶颈（multi_search_task）

```
gateway (3%) → planner (10%) → [search(5%) ∥ code(25%) ∥ data(20%)] → reviewer (35%) ← 🔴
                                  ↑
                            最慢 executor = 25%
```
**优化策略**：木桶效应——reviewer 等待最慢的 executor。优化最慢的 executor 或裁剪上下文。

### 4.6 练习任务

1. **基础**（必做）：对 3 个场景各提取 Critical Path，填写下表：

   | 场景 | Critical Path 序列 | Bottleneck | 占比 | 形态（串行/双峰/尾部） |
   |------|-------------------|-----------|------|---------------------|
   | code_review_task | | | | |
   | data_report_task | | | | |
   | multi_search_task | | | | |

2. **进阶**：在 Phoenix UI 中对比 code_review_task 的两种视图：
   - "Timeline" 视图：观察 3 次 retry 的时间线分布
   - "Span Details" 视图：查看 executor_code 的 Span Attributes，找出最大的一次工具调用延迟

3. **挑战**：打开 `critical_path.py`，找到 `analyze_task()` 函数。当前代码仅分析进程内 trace JSON。扩展它，使其也能从 Phoenix REST API (`/v1/spans`) 拉取 Span 数据，并将 Phoenix 的实际延迟与进程内记录进行对比。

---

## 5. 实验 K：Token 成本控制

### 5.1 核心概念

#### 什么是上下文膨胀？

随着多智能体链的推进，每轮对话都会向 LLM 的 Context Window 追加新的消息。到了链的末端（Reviewer），上下文可能已经包含了：

```
原始用户请求 (50 tokens)
+ Gateway 路由结果 (100 tokens)
+ Planner 完整规划 (400 tokens)
+ Executor 1 工具调用 + 输出 (800 tokens)
+ Executor 2 工具调用 + 输出 (1200 tokens)
+ Executor 3 工具调用 + 输出 (900 tokens)
= Reviewer 上下文 > 3450 tokens
```

#### Token 消耗的两大成本

| 成本类型 | 计算方式 | 影响 |
|---------|---------|------|
| **经济成本** | $/1K tokens × 总 tokens | 直接 API 计费 |
| **延迟成本** | 更大的 context → 更慢的推理（非线性）| 用户等待时间 |

#### 上下文利用率

**上下文利用率 = Completion Tokens / Prompt Tokens × 100%**

这个指标衡量的是："我传给 LLM 的内容中，有多少比例被实际用于生成有效输出？"

- 利用率 > 50%：高效，大部分输入信息被用于生成输出
- 利用率 25-50%：中等，存在一定的冗余
- 利用率 < 25%：低效，大量上下文是冗余的（如原始工具输出、历史对话等）

### 5.2 操作步骤

```bash
# 方式 1：通过启动器
python experiment_a2a/run_experiments.py --experiment K

# 方式 2：直接运行分析工具
python experiment_a2a/analysis/token_analyzer.py \
  --trace-file experiment_a2a/output/a2a_latest_trace.json
```

### 5.3 预期输出：逐字段解读

```
📊 Token 分析: 多技术方案调研对比
============================================================

Token 消耗总览
  Prompt tokens:     12,450
  Completion tokens: 2,380
  Total tokens:      14,830
  上下文利用率: 19.1%                    ← 🔴 低效
    (Completion / Prompt: 有效输出占比)
```

**解读**：`19.1%` 的上下文利用率意味着 —— 传给 LLM 的 100 个 tokens 中，只有约 19 个 tokens 被用于生成有效输出。其余 81 个 tokens 的角色更接近"上下文背景"，但它们仍然**消耗金钱和延迟**。

```
逐轮 Token 曲线
  轮次   Agent                Prompt  Completion  累积Prompt  延迟
  ───────────────────────────────────────────────────────────────
  1     gateway                245         85         245   1234ms
  2     planner              2,856        320       3,101   2856ms
  3     executor_search      1,020        450       4,121   1522ms
  4     executor_code        1,340        380       5,461  10234ms
  5     executor_data        2,100        520       7,561   5623ms
  6     reviewer             4,889        625      12,450   6834ms
```

**解读**：
- `累积Prompt` 列：从 245 → 12,450。**Reviewer 的 prompt tokens 占总量 39%**（4889/12450），因为它接收了所有 executor 的完整输出。
- `延迟` 列：注意延迟并不完全与 Prompt tokens 成正比（executor_code 的 prompt 仅 1340 但延迟 10234ms），因为延迟还受到工具调用（retry）的影响。
- **关键观察**：从 planner 到 reviewer，累积 Prompt 增长了 4 倍，但 Completion 仅增长了 2 倍 → 上下文利用率持续下降。

```
💡 优化建议
  [HIGH] [context_prune] 总上下文已达 12,450 tokens (>3000)，
         建议裁剪前 3 轮的原始工具输出
    预计节省: 约 4,000 tokens
  [MEDIUM] [prompt_optimize] 上下文利用率仅 19.1%，
         提示词中可能包含过多冗余信息
    预计节省: 约 20-30% tokens
  [MEDIUM] [system_prompt_trim] 部分 agent 的 system prompt 过长（>2000 tokens），
         建议缩减至 300-500 tokens
    预计节省: 约 1500 tokens/agent
```

**解读**：
- 三条建议对应三种 Token 浪费模式：
  1. **上下文未裁剪**：历史工具输出仍然在 context 中
  2. **提示词冗余**：System Prompt 包含过多规则
  3. **System Prompt 过长**：planner 和 executor_data 的 System Prompt 超过 800 tokens

### 5.4 Token 增长曲线分析

通常多智能体系统的 Token 增长呈现三种典型模式：

| 模式 | 特征 | 对应场景 | 风险 |
|------|------|---------|------|
| **线性增长** | 每轮增加约相同的 Token 数 | code_review_task | 可控，总 cost 可预测 |
| **加速增长** | 后几轮 Token 增长更快 | data_report_task | 末尾 Agent 成本高 |
| **指数增长** | 每轮 context 翻倍 | multi_search_task | 🔴 最危险，需立即裁剪 |

### 5.5 练习任务

1. **基础**（必做）：对 3 个场景各运行 Token 分析，填写下表：

   | 场景 | Total Tokens | 利用率 | 最高 Prompt 的 Agent | 增长模式 |
   |------|-------------|--------|---------------------|---------|
   | code_review_task | | | | |
   | data_report_task | | | | |
   | multi_search_task | | | | |

2. **进阶**：打开 `token_analyzer.py`，找到 `analyze_token_usage()` 函数。观察它是如何计算累积 Prompt 和上下文利用率的。尝试修改利用率阈值（当前为 30%），观察不同阈值下的建议变化。

3. **挑战**：在 `reviewer.py` 中实现一个实际的上下文裁剪策略。Hint：在将 executor 输出传给 reviewer 之前，只保留每个输出的前 300 个字符 + 一个 "[truncated]" 标记。重新运行实验 I 并比较 Token 分析结果。

---

## 6. 实验 L：性能优化回测

### 6.1 目标

基于实验 J（识别瓶颈）和实验 K（识别 Token 浪费）的分析发现，应用六项优化方案并量化对比效果。

### 6.2 六项优化详解

| # | 优化项 | 类别 | 优化前 | 优化后 | 实现机制 |
|---|--------|------|--------|--------|---------|
| 1 | **System Prompt 精简** | 提示工程 | planner: ~800 tokens, executor_data: ~800 tokens | 各自 ~100 tokens | `--optimize` → Agent 使用短版 System Prompt |
| 2 | **上下文裁剪** | 提示工程 | Reviewer 接收 executor 完整输出 | Reviewer 仅接收前 300 字符 | 编排器在传 Reviewer 前截断 |
| 3 | **消除重试 + 工具延迟** | 工具设计 | `run_code_check` 前 2 次返回错误 + 1.2s 延迟 | 工具首次即成功 + 跳过延迟 | optimize 模式跳过错误路径和 `time.sleep()` |
| 4 | **并行执行** | 架构 | Executors 串行 `for` 循环 | `ThreadPoolExecutor` 真并行 | 优化模式下用线程池并发执行 |
| 5 | **去除工具模拟延迟** | 工具设计 | `query_database()` 有 `time.sleep(2.0)` | 移除模拟延迟 | optimize 模式下 `_optimize_mode` 全局标志 |
| 6 | **模型分级** | 基础设施 | 所有 Agent 使用同一模型 | Gateway/Reviewer 可选用轻量模型 | 设置 `OPENAI_SMALL_MODEL` 环境变量 |

#### 优化 1 的原理

System Prompt 是每次 LLM 调用的"固定开销"。一个 800 tokens 的 System Prompt 意味着：
- 每次调用都要先处理 800 tokens 才能开始"思考"用户问题
- 在本地模型上，这 800 tokens 可能贡献 2-3 秒的首字延迟（TTFT）
- 如果 3 个 Agent 都有长 System Prompt，累积 TTFT 可能超过 6 秒

**精简后的效果**：TTFT 降低 60-80%，Agent 更快开始输出。

#### 优化 2 的原理

Reviewer 不需要 executor 的完整工具调用日志和原始输出。它只需要结果摘要。将 executor 输出从 ~1000 tokens 缩减到 ~300 字符的摘要：
- Reviewer 的 Prompt tokens 降低 40-60%
- Reviewer 的推理延迟随之降低（更小的 context → 更快的推理）

**权衡**：摘要可能丢失一些细节。但大多数情况下，executor 输出的核心结论在开头几段就已经给出。

#### 优化 3 的原理

3 次重试 = 3 次额外的 LLM 调用 + 3 次工具调用（每次含 1.2s 模拟延迟）。这意味着：
- executor_code 的延迟从 ~3s（理想情况）膨胀到 ~10s（3 次重试）
- Token 消耗增加 ~3 倍（每次重试都消耗新的 tokens）

**消除重试的方法**：确保工具的返回格式始终正确。在优化模式下：
- 工具跳过错误返回路径，直接返回成功结果
- 同时跳过 1.2s 的模拟延迟

#### 优化 4 的原理

串行 `for` 循环中，每个 executor 必须等前一个执行完才能开始。总耗时 = sum(executor 延迟)。
并行 `ThreadPoolExecutor` 下，所有 executor 同时启动。总耗时 ≈ max(executor 延迟)。

对于 multi_search_task（3 个 executor 分别耗时 1.5s、10s、5.6s）：
- 串行：总耗时 = 1.5 + 10 + 5.6 = 17.1s
- 并行：总耗时 ≈ max(1.5, 10, 5.6) = 10s（节省 41%）

#### 优化 5 的原理

`query_database()` 中的 `time.sleep(2.0)` 和 `run_code_check()` 中的 `time.sleep(1.2)` 是为教学目的设计的模拟延迟。在真实系统中，消除不必要的延迟是最直接的优化手段。在优化模式下，这些延迟被跳过。

#### 优化 6 的原理

Gateway（~100 tokens system prompt）和 Reviewer（~200 tokens system prompt）是轻量 Agent，不需要最强模型。将这两个 Agent 切换到更小/更快的模型：
- 更低的推理延迟（小模型通常更快）
- 更低的 API 成本（小模型更便宜）
- 对输出质量影响极小（Gateway 仅路由，Reviewer 仅拼装）

**使用方式**：在 `.env` 中设置 `OPENAI_SMALL_MODEL`。不设置时回退到 `OPENAI_MODEL`。

### 6.3 操作步骤

```bash
# 通过启动器（自动运行 3 轮取均值 + 对比）
python experiment_a2a/run_experiments.py --experiment L

# 或手动运行单轮对比
python experiment_a2a/a2a_orchestrator.py --task code_review_task
python experiment_a2a/a2a_orchestrator.py --task code_review_task --optimize
```

> ⚠️ 实验 L 中优化模式会**跳过 Planner**，直接使用 `scenarios/tasks.json` 中的硬编码子任务。这消除了 Planner 非确定性导致的方差，使优化效果可量化对比。

### 6.4 预期输出：优化前后对比（3 轮均值）

```
  code_review_task
──────────────────────────────────────────────────
  基线均值: 62,803ms / 3,194 tokens (n=3)
  优化均值: 59,524ms / 2,705 tokens (n=3)
  延迟改善: +5.2%   Token 改善: +15.3%
```

**解读**：
- **Token 改善 +15.3%**：可靠且稳定 —— 来自 System Prompt 精简 + 上下文裁剪 + 消除重试
- **延迟改善 +5.2%**：真实但受 LLM 方差影响 —— 来自并行执行 + 消除工具延迟
- 如果设置了 `OPENAI_SMALL_MODEL`，延迟改善可进一步提升（Gateway/Reviewer 使用更快模型）

### 6.5 重新分析优化后的 Critical Path

优化后，bottleneck 可能从 executor_code **转移**到其他 Agent。这是一个重要的工程观察：

> **性能优化的"打地鼠"效应**：优化一个瓶颈后，下一个瓶颈就会暴露出来。优化 executor_code 后，reviewer 可能成为新的瓶颈（因为延迟没有改变）。

运行实验 J 分析优化后的 trace：
```bash
python experiment_a2a/analysis/critical_path.py \
  --trace-file experiment_a2a/output/a2a_optimized_trace.json
```

观察优化后的 bottleneck 是否从 `executor_code` 转移到了 `reviewer` 或 `planner`。

### 6.6 练习任务

1. **基础**（必做）：运行实验 L，填写下表：

   | 指标 | 优化前 | 优化后 | 改善 |
   |------|--------|--------|------|
   | 总延迟 (ms) | | | |
   | 总 Tokens | | | |
   | Bottleneck Agent | | | |
   | Bottleneck 占比 | | | |
   | 上下文利用率 | | | |

2. **进阶**：在优化后的 trace 上运行实验 J（Critical Path），观察 bottleneck 是否转移。如果发生了转移，新的 bottleneck 是什么？为什么它没有被优化？

3. **挑战**：设计第七项优化方案并实施。可选方向：
   - **流式输出 (Streaming)**：让 Reviewer 边生成边输出，不等完整结果
   - **结果缓存**：对相似请求复用 Planner 的分解结果
   - **动态超时**：根据 executor 的历史耗时动态调整超时策略

---

## 7. 实验总结与综合思考

### 7.1 五个核心洞见

1. **多 Agent 延迟是可分解的 → 可优化的**
   单 LLM 调用的延迟是一个"黑箱"。但在多 Agent 系统中，每个 Agent 都是一个独立的 Span。你可以精确地说"46.8% 的延迟来自 executor_code 的 3 次重试"，而不是"系统很慢"。

2. **重试是 AI Agent 系统特有的性能杀手**
   在传统微服务中，重试通常由框架自动处理且耗时极短（毫秒级）。但在 Agent 系统中，每次重试都是一次完整的 LLM 调用——意味着 **秒级**的额外延迟和 **数百 tokens** 的额外成本。

3. **System Prompt 有隐藏的 TTFT 成本**
   每增加 100 tokens 的 System Prompt ≈ 增加 100-500ms 的首字延迟（取决于模型和基础设施）。这个成本在单 Agent 中可能不明显，但在 6 个 Agent 的链中会逐级累积。

4. **上下文窗口 = 延迟 + 成本的双重税**
   每多传 1000 tokens 的上下文：
   - 延迟增加 100-300ms（更大的 context → 更慢的推理）
   - 成本增加 $0.003-0.015（取决于模型定价）
   - 在多轮对话中，这个税是**复利**的

5. **APM 思维是 AI 工程化的必经之路**
   Jaeger/Zipkin 让你看清微服务的调用链。Phoenix 让你看清 Agent 的调用链。工具变了，方法论没变：**你不能优化你看不见的东西**。

### 7.2 四个实验的演进逻辑

| 实验 | 核心问题 | 评估维度 | APM 类比 |
|------|---------|---------|---------|
| 1. ASI 靶场 | 安全性：怎么防攻击？ | 拦截率 / 绕过率 | 安全审计日志 |
| 2. RAG 诊断 | 检索质量：怎么找对文档？ | 准确率 / 幻觉率 | 查询性能分析 |
| 3. LLM-Judge | 输出质量：怎么评好坏？ | Hallucination Rate / Correctness | 自动化测试报告 |
| **4. A2A** | **系统性能：怎么更快更省？** | **Critical Path / Token Utilization** | **分布式 APM (Jaeger/Zipkin)** |

### 7.3 实验报告要求

| 章节 | 内容要求 | 分值建议 |
|------|----------|----------|
| **1. 系统架构与设计** | 画出四层 Agent 拓扑图，标注每个 Agent 的角色和性能陷阱。附 Phoenix Trace 截图。 | 15% |
| **2. Critical Path 分析** | 对 3 个场景各标注 Critical Path 序列，识别 bottleneck 和占比，分析瓶颈根因。 | 25% |
| **3. Token 成本分析** | 绘制至少一个场景的 Token 增长曲线，标注上下文利用率，给出至少两条裁剪建议。 | 20% |
| **4. 优化方案与回测** | 列出六项优化的实施细节和原理，对比优化前后的延迟和 Token 变化（3 轮均值）。如有 bottleneck 转移，分析原因。 | 25% |
| **5. 改进建议与思考** | 基于实验数据，提出至少两条进一步优化的建议（不仅限于已实施的三项）。 | 15% |

### 7.4 扩展阅读

- "Google Dapper: A Large-Scale Distributed Systems Tracing Infrastructure" (2010) — 分布式追踪的奠基论文
- "Jaeger: Uber's Distributed Tracing System" — 开源 APM 的工业实践
- Arize Phoenix Traces 文档: https://docs.arize.com/phoenix/tracing/overview
- "LLM Inference Performance: TTFT, TPOT, and Throughput" (Anyscale, 2024)
- "Agent-to-Agent (A2A) Protocol" (Google, 2025) — 多 Agent 通信的开放协议

---

## 附录 A：代码导航指南

### A.1 如果你想修改 Agent 行为

| 你想做的事 | 修改哪个文件 | 修改什么 |
|-----------|------------|---------|
| 改变 Gateway 的路由逻辑 | `agents/gateway.py` | `GATEWAY_SYSTEM_PROMPT` 或 `run_gateway()` 的 prompt |
| 改变 Planner 的任务分解策略 | `agents/planner.py` | `PLANNER_SYSTEM_PROMPT` 或 `PLANNER_SYSTEM_PROMPT_OPTIMIZED` |
| 调整重试次数 | `agents/executor_code.py` | `run_code_check()` 中的 `if _retry_count <= 2` 改为其他值 |
| 改变慢工具的延迟 | `agents/executor_data.py` | `query_database()` 中的 `time.sleep(2.0)` 改为其他值 |
| 改变 Reviewer 的审核标准 | `agents/reviewer.py` | `REVIEWER_SYSTEM_PROMPT` |
| 改变上下文裁剪的长度 | `a2a_orchestrator.py` | `eo["output"][:300]` 中的 `300` 改为其他值 |

### A.2 如果你想添加新的分析维度

| 你想分析什么 | 修改哪个文件 | 怎么做 |
|------------|------------|--------|
| TTFT（首字延迟） | `analysis/critical_path.py` | 在 `analyze_task()` 中从 span 提取 `ttft_ms` 属性 |
| TPOT（每 token 延迟） | `analysis/critical_path.py` | 计算 `latency_ms / completion_tokens` |
| 成本估算（$） | `analysis/token_analyzer.py` | 在 `analyze_token_usage()` 中添加 `cost = tokens * $0.00015` |
| Agent 可用性（成功率） | `analysis/critical_path.py` | 从 trace JSON 中读取 `error` 字段统计失败率 |

### A.3 关键函数的调用链

```
run_experiments.py: experiment_I_build_system()
  └→ subprocess: a2a_orchestrator.py --task=all
       └→ main()
            └→ run_a2a_pipeline(task, client)
                 ├→ agents/gateway.run_gateway(user_query, client)
                 ├→ agents/planner.run_planner(user_query, routing, client)
                 ├→ agents/executor_*.run_executor_*(subtask, client)
                 └→ agents/reviewer.run_reviewer(query, plan, outputs, client)
       └→ 写 output/a2a_latest_trace.json

run_experiments.py: experiment_J_critical_path()
  └→ subprocess: analysis/critical_path.py
       └→ build_span_tree(trace_data)
       └→ find_critical_path(spans)
       └→ analyze_task(trace_data)

run_experiments.py: experiment_K_token_control()
  └→ subprocess: analysis/token_analyzer.py
       └→ analyze_token_usage(trace_data_list)

run_experiments.py: experiment_L_optimization()
  └→ subprocess: a2a_orchestrator.py --optimize --task=code_review_task
       └→ run_a2a_pipeline(task, client, optimize=True)
            ├→ agents/planner.run_planner(..., optimize=True) ← 用短版 System Prompt
            ├→ agents/executor_code.run_executor_code(..., optimize=True) ← 跳过重试
            ├→ agents/executor_data.run_executor_data(..., optimize=True) ← 用短版 System Prompt
            └→ [上下文裁剪] → agents/reviewer.run_reviewer(...)
  └→ 对比 baseline vs optimized trace
```

---

## 附录 B：性能指标详解

### B.1 延迟相关指标

| 指标 | 全称 | 计算方式 | 意义 | 正常范围 |
|------|------|---------|------|----------|
| **TTFT** | Time To First Token | LLM 收到完整 prompt → 输出第 1 个 token 的时间 | 衡量"模型开始思考"的延迟。受 System Prompt 长度、模型大小、基础设施影响最大。 | 云端: 0.3-1s; 本地: 1-5s |
| **TPOT** | Time Per Output Token | 从第 1 个 token 到最后一个 token 的时间 / token 数 | 衡量"模型输出速度"。受模型大小、生成长度影响。 | 云端: 10-50ms/tok; 本地: 50-200ms/tok |
| **E2E Latency** | End-to-End Latency | 从 Gateway 开始到 Reviewer 结束的墙上时钟时间 | 用户感知的总延迟。受 Critical Path 上的所有 Agent 影响。 | < 30s (可接受); < 10s (良好) |
| **Agent Latency** | 单 Agent 延迟 | 从 LLM 调用开始到收到完整回复的时间 | 单个 Agent 的响应时间。= TTFT + TPOT × output_tokens + tool_execution_time | 取决于 Agent 复杂度 |

### B.2 Token 相关指标

| 指标 | 计算方式 | 意义 | 正常范围 |
|------|---------|------|----------|
| **Prompt Tokens** | 每次 LLM 调用的输入 token 数 | 衡量上下文大小。越大 = 越贵 + 越慢。 | < 3000 (良好) |
| **Completion Tokens** | 每次 LLM 调用的输出 token 数 | 衡量输出长度。也影响成本。 | 50-500 (正常) |
| **上下文利用率** | Completion / Prompt × 100% | 衡量信息密度。低利用率 = 浪费。 | > 30% (健康) |
| **Token 增长率** | (本轮 Prompt - 上轮 Prompt) / 上轮 Prompt | 衡量上下文膨胀速度。 | < 50%/轮 (可控) |

### B.3 架构相关指标

| 指标 | 计算方式 | 意义 |
|------|---------|------|
| **Agent 数量** | 一次完整 trace 中的 Agent 数量 | 多 = 更灵活但更慢 |
| **并行度** | 可并行 executor 数 / 总 executor 数 | 高 = 更好的资源利用 |
| **工具调用深度** | 单 Agent 的平均工具调用次数 | 高 = 更复杂但更慢（尤其有重试时） |
| **Bottleneck 占比** | 最慢 Agent 延迟 / 总延迟 | > 40% = 显著瓶颈，优先优化 |

---

## 附录 C：三个场景的详细设计

### C.1 code_review_task — 重试风暴场景

**设计意图**：演示 Agent 自修正重试对延迟的放大效应。

**触发机制**：
```python
# agents/executor_code.py: run_code_check()
_retry_count = 0

def run_code_check(code, check_type):
    global _retry_count
    _retry_count += 1
    time.sleep(1.2)  # 模拟工具执行时间

    if _retry_count <= 2:
        return FORMAT_ERROR  # 前两次失败
    return SUCCESS  # 第三次成功
```

**在 Phoenix 中的表现**：
- 3 个连续的 `run_code_check` Span（前两个标记为 error）
- executor_code 的 Span 比其他 executor 明显更宽（时间线视图中）

**定量预期**：
- 无重试的理想延迟: ~3000ms
- 有重试的实际延迟: ~10000ms
- 重试放大系数: 3.3×

### C.2 data_report_task — 双延迟来源场景

**设计意图**：区分 TTFT（首字延迟）和工具响应延迟这两个独立的延迟来源。

**触发机制**：
- **高 TTFT**: `executor_data.py` 的 `EXECUTOR_DATA_SYSTEM_PROMPT` = ~800 tokens（8 条详尽规则）
- **慢工具**: `query_database()` 中有 `time.sleep(2.0)`

**在 Phoenix 中的表现**：
- executor_data Span 的 TTFT 显著高于其他 executor
- tool_call Span 的持续时间明显长于 LLM 推理 Span

**定量预期**：
- TTFT 贡献: ~2-3s（取决于模型）
- 工具延迟贡献: ~2s（time.sleep）
- 总 executor_data 延迟: ~5-6s

### C.3 multi_search_task — 并行瓶颈 + 上下文爆炸场景

**设计意图**：演示并行执行中的木桶效应和 Reviewer 的上下文膨胀。

**触发机制**：
- 3 个 executor 被规划为不同类型：search（~1.5s）、code_review（~10s 重试）、data_analysis（~5.6s）
- "并行"执行由 `for` 循环实现（串行），这是当前版本的一个局限
- Reviewer 接收所有 3 个 executor 的完整输出

**在 Phoenix 中的表现**：
- 3 个 executor Span 在时间线上有重叠（如果并行）
- Reviewer Span 的 prompt_tokens 是所有 Span 中最高的

**定量预期**：
- 并行情况下总 executor 延迟 = max(1.5s, 10s, 5.6s) = 10s（木桶效应）
- Reviewer 的 prompt tokens 约为其他 Agent 的 2-3 倍

---

## 附录 D：故障排查

### D.1 环境与配置问题

#### Phoenix 拓扑树不显示

1. 确认 `ENABLE_PHOENIX_TRACING=true` 在 `.env` 中设置
2. Docker Phoenix 容器必须运行：`docker ps | grep cs599-phoenix`
3. 运行实验 I 后，**刷新** Phoenix UI 页面 (http://localhost:6006)
4. 在 Phoenix UI 的 Traces 页面，选择"All Traces"（而非"Latency"等筛选视图）
5. 如果仍不显示，检查 Phoenix 日志：`docker logs cs599-phoenix-asi`

#### `TypeError: Object of type URL is not JSON serializable`

**原因**：代码中错误地将 `client.base_url`（URL 对象）传给了 `model` 参数。

**修复**：已在新版本中修复。如果仍遇到，确保 `agents/*.py` 中的 LLM 调用使用 `model=MODEL` 而非 `model=client.base_url`。

#### LLM API 连接失败

```bash
# 检查环境变量
echo $OPENAI_API_KEY
echo $OPENAI_BASE_URL

# 测试连接
curl $OPENAI_BASE_URL/models
```

### D.2 性能与超时问题

#### 实验 I 中某个场景超时

本实验默认超时配置：
- 单次 Agent 运行: 无超时（依赖 LLM API 自身的超时）
- 单个场景编排器: run_cmd 中 timeout=600s（10 分钟）
- 完整实验 L: timeout=900s（15 分钟）

**如果使用本地模型导致超时**：
1. 只运行单个场景：`python experiment_a2a/a2a_orchestrator.py --task code_review_task`
2. 减少 Planner 的子任务上限：修改 `planner.py` 中 `"最多 4 个"` → `"最多 2 个"`
3. 减少慢工具的延迟：修改 `executor_data.py` 中 `time.sleep(2.0)` → `time.sleep(0.5)`
4. 使用更快的 API（如 gpt-4o-mini）

#### 优化后对比显示 0% 改善

**原因**：实验 L 无法读取到有效的优化后 trace 文件（可能被上一轮超时覆盖）。

**解决**：
```bash
# 1. 清理旧文件
rm experiment_a2a/output/a2a_optimized_trace.json

# 2. 先确保基线 trace 存在
python experiment_a2a/a2a_orchestrator.py --task code_review_task

# 3. 运行优化版
python experiment_a2a/a2a_orchestrator.py --task code_review_task --optimize \
  --output-json experiment_a2a/output/a2a_optimized_trace.json

# 4. 手动对比
python experiment_a2a/analysis/critical_path.py \
  --trace-file experiment_a2a/output/a2a_latest_trace.json
python experiment_a2a/analysis/critical_path.py \
  --trace-file experiment_a2a/output/a2a_optimized_trace.json
```

### D.3 数据与解析问题

#### Token 分析文件不存在

```bash
# 确保实验 I 已经运行并生成了 trace 文件
ls -la experiment_a2a/output/a2a_latest_trace.json
```

#### critical_path.py 报 KeyError

**原因**：trace JSON 的格式与 `build_span_tree()` 期望的格式不匹配。

**解决**：检查 `output/a2a_latest_trace.json` 中的 `agent_traces` 数组是否包含 `agent` 和 `latency_ms` 字段。如果某个任务运行失败（如 LLM 超时），对应的条目可能缺少这些字段。

#### Planner 产生过多子任务

**原因**：Planner 的 System Prompt 中没有明确约束子任务数量上限。

**解决**：当前版本的 Planner Prompt 已包含"最多 4 个"约束。如果仍有问题，在 `planner.py` 中进一步降低上限。

### D.4 与其他实验的交互

#### 实验之间的依赖关系

```
实验 I (生成 trace) → 实验 J (分析 critical path)
                     → 实验 K (分析 tokens)
                     → 实验 L (优化回测，也依赖 I 的基线数据)
```

**注意**：实验 J 和 K 都依赖实验 I 的输出。如果直接运行 `--experiment J` 而尚未运行 I，启动器会自动先运行 I。

#### 多个实验共享 Phoenix 容器

所有四个实验共用 `docker/docker-compose.yml` 中的同一个 Phoenix 容器。不同实验的 trace 会在 Phoenix UI 中混合显示。建议按实验标签（如 `task_id`）筛选 trace。
