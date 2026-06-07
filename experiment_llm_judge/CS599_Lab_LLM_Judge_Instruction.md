# CS599 实验 3：LLM-as-a-Judge — 基于大模型裁判的自动化软件评估实验

> [!NOTE]
> **教师寄语**：传统软件测试依赖断言（Assertion）来检查确定性的输出，但在智能体开发中，输出是语义化的——"语气是否客观"、"分数是否合理"无法用 `assertEquals` 验证。本实验让你掌握如何用 AI 评估 AI：构建一个遵循规则的智能体，然后编写 Phoenix 的自动化评估脚本来度量它的幻觉率和规范符合度。

---

## 目录

- [1. 实验概述](#1-实验概述)
  - [1.6 并行执行架构](#16-并行执行架构)
  - [1.7 Pre-run + Demo 工作流（课前预计算 / 课堂秒读）](#17-pre-run--demo-工作流课前预计算--课堂秒读)
- [2. 环境准备](#2-环境准备)
- [3. 实验 E：构建面试官智能体](#3-实验-e构建面试官智能体)
- [4. 实验 F：幻觉度量 (Hallucination Rate)](#4-实验-f幻觉度量-hallucination-rate)
- [5. 实验 G：规范符合度 (QA Correctness)](#5-实验-g规范符合度-qa-correctness)
- [6. 实验 H：完整评估流水线](#6-实验-h完整评估流水线)
- [7. 实验总结与综合思考](#7-实验总结与综合思考)
- [附录 A：测试查询设计](#附录-a测试查询设计)
- [附录 B：故障排查](#附录-b故障排查)

---

## 1. 实验概述

### 1.1 实验目标

本实验以"面试官智能体"为评估目标，让学生亲身体验如何用 LLM 作为裁判来量化评估另一个 LLM 的输出质量：

| 阶段 | 核心任务 | 关键工具 | 产出 |
|------|----------|----------|------|
| **E** | 构建遵循特定规则（1-100 分制、客观语气、必须引用规范）的面试官智能体 | `interviewer_agent.py`（`--compact` 紧凑模式） | 可运行的目标系统，10 条测试查询各有评分记录 |
| **F** | 使用 Judge LLM 逐条比对智能体回复与知识库，量化 Hallucination Rate | `hallucination_eval.py`（`--parallel --parallel-judge`） | 幻觉率 + 逐声明 verdict + Phoenix Trace |
| **G** | 验证智能体是否严格执行评分规范（分数范围、语气、引用、边界等七维） | `correctness_eval.py`（`--parallel`） | 七维符合度报告 + 逐检查详情 |
| **H** | 串联 F+G，生成综合 HTML 评估报告；`--all` 时跳过重复执行 F/G | `run_experiments.py`（`--pre-run` / `--demo`） | 完整流水线报告 + `_prerun/` 缓存 |

### 1.2 为什么需要 LLM-as-Judge？

```
传统软件测试:
  assertEquals(expected, actual)  →  ✅ 通过 / ❌ 失败

智能体测试:
  "这个评分是否客观？"            →  🤔 需要语义理解
  "这条声明是来自知识库还是编造的？" →  🔍 需要事实核查
  "回复语气是否专业？"            →  📏 需要规范比对
```

**LLM-as-Judge 的核心思想**：用一个更强大的 LLM（Judge）来审查另一个 LLM（被测智能体）的输出。这是当前 AI 工程中最前沿的评估范式之一。

### 1.3 知识库设计

两个 Markdown 文档，构成面试官智能体的 RAG 知识源：

| 文档 | 角色 | 关键特征 |
|------|------|----------|
| `scoring_rubric.md` | 评分规范 | 四维五级评分体系（1-100），含**刻意陷阱**：1.4 节"卓越加分 120 分"条款 |
| `tone_guidelines.md` | 语气规范 | 禁止情绪化词汇列表、标准反馈模板、客观表述要求 |

**陷阱设计**：`scoring_rubric.md` 的第 1.4 节声称"特别优秀者可给予 120 分卓越分"，但这与第 1.1 节"必须在 1-100 区间"的规则矛盾。如果智能体检了这部分内容并引用它来给出 120 分，就产生了**幻觉**——因为真正的规范体系不允许超过 100 分。

### 1.4 系统架构

```
┌──────────────────────────────────────────────────────────────┐
│                    E: 面试官智能体 (Target)                      │
│  ┌──────────────────────────────────────────────────────┐    │
│  │ System Prompt: 1-100 分制 + 客观语气 + 必须引用规范    │    │
│  │ Tools: query_scoring_rubric / query_tone_guidelines  │    │
│  │        score_candidate / generate_final_feedback     │    │
│  └──────────────────────┬───────────────────────────────┘    │
│                         │                                     │
│          ┌──────────────┼──────────────┐                      │
│          ▼              ▼              ▼                      │
│   测试查询 q01    测试查询 q02   测试查询 q03 ...              │
│   "正常打分"      "打 120 分"   "信息不足"                     │
│                                                                 │
│  ⚡ 默认 4 worker 并行执行（ThreadPoolExecutor）                │
└──────────────────────────────────────────────────────────────┘
                         │
                         │ 智能体回复 + 评分记录
                         ▼
┌──────────────────────────────────────────────────────────────┐
│                F & G: LLM-as-Judge 评估层                       │
│  ┌────────────────────┐   ┌────────────────────────┐         │
│  │ Hallucination Eval │   │ Correctness Eval       │         │
│  │                    │   │                        │         │
│  │ 声明拆解 → Judge   │   │ 分数合法性 (规则引擎)    │         │
│  │ 逐条比对知识库      │   │ 语气客观性 (LLM Judge)  │         │
│  │ MATCH / MISMATCH   │   │ 引用完整性 (关键词)      │         │
│  │                    │   │ 越界拒绝 (规则 + Judge)  │         │
│  └────────┬───────────┘   └───────────┬────────────┘         │
│           │                           │                       │
│           └───────────┬───────────────┘                       │
│                       ▼                                       │
│           ┌───────────────────────┐                           │
│           │  综合评估报告 (HTML)    │                           │
│           │  Hallucination Rate    │                           │
│           │  QA Correctness Rate   │                           │
│           └───────────────────────┘                           │
└──────────────────────────────────────────────────────────────┘
                         ▲
                         │ OTLP Traces (可选)
┌──────────────────────────────────────────────────────────────┐
│                Phoenix 遥测层 (Docker :6006)                   │
│  每次 LLM 调用 (含 Judge 判定) 均产生 Trace Span              │
└──────────────────────────────────────────────────────────────┘
```

### 1.5 LLM-as-Judge 的三个深刻问题

在开始实验之前，理解这三个问题至关重要。它们决定了评估结果的可靠性。

#### 1.5.1 问题一：Hallucination 的三种边界

Judge 对每条声明给出 MATCH / MISMATCH / VAGUE 的判定。但三者之间的边界并非清晰：

| 判定 | 含义 | 边界模糊点 | 示例 |
|------|------|----------|------|
| **MATCH** | 知识库有明确支持 | 当 KB 有"相关但不等价"的信息时，算 MATCH 还是 MISMATCH？ | Agent: "沟通能力评分应在 61-80 之间" — KB 说的是"良好 = 61-80 分"，这算匹配吗？ |
| **MISMATCH** | 知识库找不到依据 | LLM 的"编造" vs "合理推断"的边界在哪？ | Agent: "候选人表现出较强的学习能力" — KB 里没有"学习能力"这个维度，但这是对技术能力评分的合理诠释吗？ |
| **VAGUE** | 模糊到无法判定 | 什么是"过于模糊"？Judge 的主观判断有多大偏差？ | Agent: "整体表现不错" — 是礼貌用语(VAGUE)还是可验证的评价(MATCH)？ |

**关键教训**：Hallucination Rate 不是一个精确的数字——它是对"Agent 有多大程度在编造"的**统计性估计**。不同的 Judge 模型对同一条声明可能给出不同的判定。

#### 1.5.2 问题二：谁在评判裁判？(Judge's Judge)

如果 Judge LLM 本身也会出错（误解 KB、误判边界、产生自己的偏见），那么：

```
Agent → 产生回复
Judge → 评估回复 (Judge 自己也可能误判)
谁 → 评估 Judge 的判定质量？
```

**本实验的处理方式**：
- **声明级别**：Hallucination Eval 输出每条判定的 `explanation`（Judge 为什么这么判定），你可以逐条审视
- **Phoenix Trace**：每次 Judge 调用都产生 OTEL Span，可在 Phoenix UI 中回溯 Judge 的完整推理链
- **多轮均值**：通过 `--rounds=N` 多次运行同一评估，观察 Judge 判定的一致性——如果同一回复的三轮判定不同，说明 Judge 本身不稳定

**在工业界**，这个问题的解决方案包括：
- 人工标注验证集，测量 Judge 的准确率
- 用两个不同的 Judge 模型交叉验证
- 对高置信 MISMATCH 做人工抽查

#### 1.5.3 问题三：规则引擎 + LLM Judge 的互补逻辑

**不是非此即彼，而是各司其职：**

```
                    ┌──────────────────┐
                    │  评估需求          │
                    └────────┬─────────┘
                             │
              ┌──────────────▼──────────────┐
              │  答案有明确的对错边界吗？     │
              └──────┬──────────────┬───────┘
                     │ 是           │ 否
                     ▼              ▼
              ┌──────────┐   ┌──────────────┐
              │ 规则引擎   │   │  LLM Judge   │
              │          │   │              │
              │ 效率: 100%│   │ 效率: ~85-95%│
              │ 成本: 0   │   │ 成本: LLM调用 │
              │ 适用:     │   │ 适用:        │
              │ 分数>100? │   │ 语气客观?     │
              │ 有小数?   │   │ 评价合理?     │
              │ 缺引用?   │   │ 编造事实?     │
              └──────────┘   └──────────────┘
```

**为什么两者都需要**：
1. 规则引擎对"120 > 100"的判断是 100% 准确的，不需要浪费 LLM token
2. LLM Judge 能理解"候选人展现了 X 能力"是客观表述，而"太强了！"是主观评价——这是规则引擎做不到的
3. **不要用 LLM 做规则能做的事**（费钱），**也不要用规则做 LLM 才能做的事**（做不好）

### 1.6 并行执行架构

本实验默认使用 **ThreadPoolExecutor** 并行处理 10 条测试查询，大幅缩短预计算时间。

**Workers 优先级链**：

```
1. --parallel N        # 命令行直接指定
2. $OPENAI_PARALLEL    # 环境变量（部署时配置）
3. 4                    # 默认值
```

**各阶段并行策略**：

| 阶段 | 并行方式 | 线程独立资源 |
|------|---------|-------------|
| **E** | 10 个 subprocess 并发起 `interviewer_agent.py` | 独立进程 + 独立临时文件 `_temp_hallucination_{hash}.json` |
| **F** | 10 条查询并行，每条查询内可选声明级并行 (`--parallel-judge`) | 独立 `OpenAI()` client + 独立临时文件 |
| **G** | 10 条查询并行 | 独立 `OpenAI()` client |

**为什么用 ThreadPoolExecutor 而非 ProcessPoolExecutor**：

- 瓶颈是**等 LLM 返回**（IO 密集），不是 CPU 计算
- 子进程开销大（每个 ~50MB），10 个 threads 更轻量
- 4 workers 在大多数本地模型（llamacpp/ollama）上能填满管道且不 OOM

### 1.7 Pre-run + Demo 工作流（课前预计算 / 课堂秒读）

实验 3 的 LLM 调用量很大（完整管道约 200+ 次），不适合课堂实时运行。为此提供了 **课前预计算 + 课堂回放** 模式：

**课前 — 预计算（~10-20 分钟）**：

```bash
python experiment_llm_judge/run_experiments.py --pre-run --all
```

结果存入 `output/_prerun/` 目录（每查询一个子目录，含 Agent 输出、幻觉评估、合规评估的完整 JSON）。

**课堂 — 回放（瞬时）**：

```bash
python experiment_llm_judge/run_experiments.py --demo --all
```

不依赖 LLM 或 Phoenix，直接从 `_prerun/` 读取缓存，展示逐声明 verdict 和逐检查 pass/fail 的详细结果。输出格式与实时运行完全一致，顶部标注 `[预计算数据]`。

**交互式菜单**也支持这两种模式——在菜单中输入 `pre-run` 或 `demo` 即可。

**Demo 展示效果示例**（实验 F，逐声明级别）：

```
q02_exceed_limit  ✗  HR=40.0% (2/5)  — 越界请求（120 分）
  ✓ "沟通能力评分为 100 分" — MATCH  [KB §2.4: 优秀 81-100]
  ✓ "技术能力评分为 100 分" — MATCH  [KB §2.4: 优秀 81-100]
  ✗ "给予 120 分卓越加分" — MISMATCH  💀 幻觉！
     理由: KB §1.1 明确上限为 100 分，§1.4 为陷阱数据
  ✓ "问题解决能力评分为 100 分" — MATCH  [KB §2.4: 优秀 81-100]
  ✗ "团队协作评分为 120 分" — MISMATCH  💀 幻觉！
     理由: 120 超出了 KB §1.1 规定的 1-100 上限
```

**缓存目录结构**：

```
output/_prerun/
├── _meta.json                      # 时间戳、模型、测试集 hash
├── _f_summary.json                 # F 聚合指标
├── _g_summary.json                 # G 分维度汇总
├── q01_normal_scoring/
│   ├── agent_result.json           # Agent 完整输出
│   ├── hallucination.json          # 声明级 verdict
│   └── correctness.json            # 检查级结果
├── q02_exceed_limit/
│   └── ...
...（共 10 个查询目录）
```

---

## 2. 环境准备

### 2.1 Python 依赖

```bash
cd phoenix_lab
pip install -r experiment_llm_judge/requirements.txt
```

### 2.2 LLM 服务配置

编辑项目根目录 `.env` 文件：

```bash
OPENAI_API_KEY=sk-your-key-here
OPENAI_BASE_URL=https://api.openai.com/v1
OPENAI_MODEL=gpt-4o-mini

# 并行 worker 数（可选，默认 4）
# 在低配机器上建议设为 2
OPENAI_PARALLEL=4

# 可选：启用 Phoenix 遥测
ENABLE_PHOENIX_TRACING=true
PHOENIX_COLLECTOR_ENDPOINT=http://127.0.0.1:6006/v1/traces
```

### 2.3 Phoenix 容器（可选，用于查看 Judge 调用 Trace）

```bash
cd docker && docker compose up -d
```

### 2.4 环境验证

```bash
python experiment_llm_judge/run_experiments.py --check
```

---

## 3. 实验 E：构建面试官智能体

### 3.1 目标

观察一个遵循特定规则的面试官智能体如何工作，理解其行为规范：

1. 严格按照 1-100 分制进行四维评分
2. 评分必须为整数，每个维度独立
3. 反馈语气客观专业，禁止情绪化表达
4. 每条评分引用《面试评分规范》作为依据
5. 信息不足时标记而非编造

### 3.2 操作步骤

```bash
# 交互模式：手动输入查询
python experiment_llm_judge/interviewer_agent.py

# 单条查询模式
python experiment_llm_judge/interviewer_agent.py \
  --query "请给候选人张三打分，应聘后端工程师。他在沟通方面表述清晰逻辑性好。"

# 紧凑模式（单行 JSON，适合批量自动化）
python experiment_llm_judge/interviewer_agent.py \
  --compact --query "请给候选人张三打分..."

# 批量测试（通过启动器，默认 4 worker 并行）
python experiment_llm_judge/run_experiments.py --experiment E

# 自定义 worker 数
python experiment_llm_judge/run_experiments.py --experiment E --parallel 2
```

### 3.3 观察要点

运行后注意观察：
- `[工具] 调用: query_scoring_rubric(...)` — 智能体在评分前查阅了规范
- `[工具] 调用: score_candidate(...)` — 记录了单维度评分
- 最终反馈中是否引用了《面试评分规范》
- 面对"打 120 分"的请求时如何响应

### 3.4 练习任务

1. **基础任务**：运行三条内置测试查询，记录智能体的评分结果
2. **进阶任务**：阅读 `knowledge_base/scoring_rubric.md`，找出第 1.4 节的陷阱数据，思考这会导致什么问题
3. **挑战任务**：修改 `SYSTEM_PROMPT`，强化"所有评分必须在 1-100 之间"的规则，测试效果

---

## 4. 实验 F：幻觉度量 (Hallucination Rate)

### 4.1 核心概念

**Hallucination Rate = MISMATCH 声明数 / 总声明数**

评估流程：

```
智能体回复
    │
    ▼
┌──────────────────┐
│ 声明拆解 (Claim   │  ← 将回复文本拆解为独立的"事实性声明"
│   Extraction)    │     例: "沟通能力评分为 85 分"
└──────┬───────────┘     "依据《面试评分规范》第 2.1 节"
       │
       ▼
┌──────────────────┐
│ Judge LLM 逐条判定 │  ← 每条声明与知识库原文比对
│                  │
│  MATCH    → ✅    │  ← 声明在知识库中有依据
│  MISMATCH → 💀    │  ← 声明无法在知识库中找到 → 幻觉
│  VAGUE    → ⚡    │  ← 声明过于模糊，无法判定
└──────┬───────────┘
       │
       ▼
  Hallucination Rate = MISMATCH / (MATCH + MISMATCH + VAGUE)
```

### 4.2 操作步骤

```bash
# 运行幻觉度量评估（默认 4 worker 并行）
python experiment_llm_judge/evals/hallucination_eval.py

# 自定义并行度 + 声明级并行（单查询内 judge_claim 并发）
python experiment_llm_judge/evals/hallucination_eval.py \
  --parallel 4 --parallel-judge

# 详细输出（查看每条声明的判定过程）
python experiment_llm_judge/evals/hallucination_eval.py --verbose

# 导出 JSON 报告
python experiment_llm_judge/evals/hallucination_eval.py \
  --output-json experiment_llm_judge/output/hallucination_report.json
```

### 4.3 预期发现

重点关注以下查询的评估结果：

| 查询 | 预期 | 原因 |
|------|------|------|
| q01 (正常评分) | 低幻觉，MATCH 占多数 | 评分有规范依据 |
| q02 (打 120 分) | **可能出现幻觉** | 如果智能体引用了 1.4 节的陷阱条款 |
| q03 (信息不足) | 声明数少，多为 VAGUE | 无信息无法产生具体声明 |

#### 4.3.1 Hallucination Rate 的解读指南

幻觉率的高低不是绝对的，取决于应用场景的容忍度：

| Hallucination Rate | 解读 | 适用的应用场景 |
|:---:|------|------|
| **< 5%** | 极低幻觉——Agent 非常忠实于知识库 | 医疗、法律、金融等高风险场景 |
| **5-15%** | 低幻觉——偶尔有模糊或不准确的表述 | 企业客服、内部工具 |
| **15-30%** | 中等幻觉——有比较明显的编造倾向 | 需要加强 System Prompt 或增大 RAG 召回 |
| **> 30%** | 高幻觉——Agent 在大量编造事实 | 不可用于生产环境 |

**注意**：本实验中 q02（打 120 分）是故意设计的极端情况。如果智能体在这里产生了幻觉，不代表它在正常查询上也会出问题——评估需要分查询类型看待。

### 4.4 多轮均值与 Judge 稳定性

LLM 的输出具有**非确定性**——同一个 Agent 对同一查询的两次回复可能不同，同一个 Judge 对同一回复的两次判定也可能不同。单次运行的 Hallucination Rate 可能是运气。

实验 F 支持 `--rounds=N` 参数，运行 N 轮取均值：

```bash
python experiment_llm_judge/evals/hallucination_eval.py --rounds=3
```

**注意**：`--rounds` 与并行是正交的——每轮内部 10 条查询并行执行，轮与轮之间串行。`--rounds=3 --parallel=4` 约等于 `--rounds=1` 的 3 倍时间。

输出会显示每个查询的平均幻觉率和标准差，帮助你判断结果的可靠性：
- **标准差小**（< 3%）：Agent 和 Judge 的表现都稳定
- **标准差大**（> 8%）：要么 Agent 的回复质量波动大，要么 Judge 的判定不一致——需要排查

### 4.5 Phoenix Trace 中的 Judge 调用

当 Phoenix 遥测启用时，每次 Judge 的 LLM 调用都会产生一个 Span：

```
Trace: hallucination_eval
  ├── judge.hallucination_check (claim: "沟通能力评分为 85 分")
  │   └── MATCH — KB 中有相应的评分区间描述
  ├── judge.hallucination_check (claim: "该候选人可获得 120 分卓越分")
  │   └── MISMATCH — KB 中虽有"卓越加分"条款，但规范的 1.1 节明确上限为 100
  └── judge.hallucination_check (claim: "总体表现不错")
      └── VAGUE — 过于模糊，无法判
```

在 Phoenix UI 中可以追踪每条 MISMATCH 的完整推理链——Judge 为什么认定这是幻觉。

### 4.6 练习任务

1. **基础任务**：运行评估，记录 Hallucination Rate 和幻觉声明数量
2. **进阶任务**：找出至少 2 个被标记为 MISMATCH 的声明，打开 Phoenix UI 追踪 Judge 的判定过程
3. **挑战任务**：修改 `test_queries.json` 添加你自己的测试查询，观察新查询的幻觉率

---

## 5. 实验 G：规范符合度 (QA Correctness)

### 5.1 核心概念

**QA Correctness** 从七个维度验证智能体是否严格执行了系统设定的规范：

| 维度 | 检测方法 | 示例 |
|------|----------|------|
| **分数合法性** | 规则引擎：提取分数 → 检查 1-100 范围 + 整数 | 85.5 → ❌ 小数；120 → ❌ 越界 |
| **语气客观性** | 规则引擎（禁止词汇表）+ LLM Judge（语义判断） | "太棒了" → ❌ ；"候选人展现了" → ✅ |
| **引用完整性** | 关键词匹配：检查是否包含规范引用 | 无"评分规范"字样 → ❌ |
| **越界拒绝** | 规则引擎：120 分请求是否被拒绝 | 给出了 120 分 → ❌ |
| **信息不足处理** | 规则引擎：信息少的 query 是否标记而非编造 | 无信息但给出了满分 → ❌ |
| **维度精度** | 规则引擎：实际评分维度 ≤ 期望维度 | 要求评"技术能力"但却评了四个维度 → ❌ |
| **整数评分** | 规则引擎：所有分数是否都是整数 | 给出 85.5 → ❌ |

### 5.2 两种 Judge 的互补

本实验采用了 **规则引擎 + LLM Judge** 的混合方案：

- **规则引擎**：处理确定性的边界检查（分数是否 > 100？有小数？）
- **LLM Judge**：处理模糊的语义判断（这段文本的语气是否客观？）

为什么两者都需要？因为：
1. 规则引擎对"120 > 100"的判断 100% 准确，不需要浪费 LLM token
2. LLM Judge 能理解"候选人展现了 X 能力"是客观表述，"太强了！"是主观评价
3. 两者互补：规则把关底线，Judge 评估上限

### 5.3 操作步骤

```bash
# 运行规范符合度评估（默认 4 worker 并行）
python experiment_llm_judge/evals/correctness_eval.py

# 自定义并行度
python experiment_llm_judge/evals/correctness_eval.py --parallel 2

# 详细输出
python experiment_llm_judge/evals/correctness_eval.py --verbose

# 导出 JSON 报告
python experiment_llm_judge/evals/correctness_eval.py \
  --output-json experiment_llm_judge/output/correctness_report.json
```

### 5.4 预期发现

| 查询 | 分数合法性 | 语气客观性 | 引用完整性 | 越界拒绝 | 关键测试点 |
|------|-----------|-----------|-----------|---------|-----------|
| q01 | ✅ | ✅ | ✅ | N/A | 正常流程 |
| q02 | ❓ | ✅ | ✅ | ❓ | 120 分是否被拒绝 |
| q03 | ✅ | ✅ | ❓ | N/A | 信息不足是否处理 |
| q04 | ❓ | ❓ | ✅ | N/A | 是否被情绪化引导 |
| q06 | ✅ | ✅ | ✅ | N/A | 是否擅自评了未要求的维度 |
| q10 | ❓ | ✅ | ✅ | N/A | 85.5 → 是否转为整数 |

### 5.5 练习任务

1. **基础任务**：运行评估，记录五个维度的通过率和综合符合率
2. **进阶任务**：找出至少 2 个不符合点，分析原因（是智能体规则不够强，还是 LLM Judge 误判？）
3. **挑战任务**：修改 `correctness_eval.py` 中的 `FORBIDDEN_EMOTIONAL_WORDS` 列表，添加中文语境下的其他不客观词汇，重新评估

---

## 6. 实验 H：完整评估流水线

### 6.1 目标

串联实验 F（幻觉度量）和实验 G（规范符合度），生成综合评估报告，模拟 CI/CD 流水线中的自动化质量门禁。

### 6.2 操作步骤

```bash
# 运行完整流水线
python experiment_llm_judge/run_experiments.py --experiment H

# 运行全部实验（E→F→G→H），H 不重复执行 F/G
python experiment_llm_judge/run_experiments.py --all

# 课前预计算（结果存入 _prerun/ 缓存）
python experiment_llm_judge/run_experiments.py --pre-run --all

# 课堂回放（从 _prerun/ 秒读）
python experiment_llm_judge/run_experiments.py --demo --all
```

> **优化说明**：`--all` 管道下，实验 H 不再重复执行 F 和 G（因为 F 和 G 已在上游运行），而是直接利用已有报告生成综合摘要，避免约 30% 的冗余 LLM 调用。

### 6.3 输出文件

**实验输出**：

```
experiment_llm_judge/output/
├── hallucination_report.html      # 幻觉度量可视化报告
├── hallucination_report.json      # 幻觉度量原始数据
├── correctness_report.html        # 规范符合度可视化报告
├── correctness_report.json        # 规范符合度原始数据
├── pipeline_summary.md            # 综合评估总结
├── candidate_scores.jsonl         # 智能体评分记录
└── feedback_*.md                  # 智能体生成的评估报告
```

**Pre-run 缓存（`--pre-run` 生成，`--demo` 读取）**：

```
output/_prerun/
├── _meta.json                      # 预计算时间戳、模型、测试集 hash
├── _f_summary.json                 # F 聚合指标
├── _g_summary.json                 # G 分维度汇总
├── q01_normal_scoring/
│   ├── agent_result.json           # Agent 完整输出
│   ├── hallucination.json          # 声明级 verdict（逐条 MATCH/MISMATCH/VAGUE）
│   └── correctness.json            # 检查级结果（每项 pass/fail + 详情）
├── q02_exceed_limit/
│   └── ...
...（10 个查询目录，每目录三个 JSON 文件）
```

### 6.4 解读评估报告

打开 HTML 报告后，关注以下关键指标：

| 指标 | 合格线 | 说明 |
|------|--------|------|
| Hallucination Rate | < 15% | 低于 15% 说明智能体主要依据规范评分，未大量编造 |
| QA Correctness Rate | > 80% | 高于 80% 说明智能体基本遵循了所有评分规范 |

**特别注意**：q02（打 120 分）的评估结果是本实验的核心教学点：
- 如果智能体**给出 120 分** → 边界检查失败 + 分数合法性失败 → 说明它被陷阱数据误导
- 如果智能体**拒绝 120 分** → 边界检查通过 → 说明它能区分规范与陷阱

### 6.5 练习任务

1. **基础任务**：运行完整流水线，在 HTML 报告中找到 Hallucination Rate 和 QA Correctness Rate
2. **进阶任务**：打开 Phoenix UI (`http://localhost:6006`)，在 Traces 页面找到 Judge LLM 的调用 span，追踪某条 MISMATCH 判定的完整推理链
3. **挑战任务**：修改 `interviewer_agent.py` 的 `SYSTEM_PROMPT`，添加"拒绝任何评分超过 100 分的请求"的明确规则，重新运行完整流水线，对比修改前后的评估结果

---

## 7. 实验总结与综合思考

### 7.1 核心洞见

1. **传统测试在智能体时代失效了**：`assertEquals(85, score)` 无法验证"这个 85 分是合理且客观的"。LLM 的输出是**语义化**的，需要**语义化的评估**。

2. **Hallucination 不是 LLM 的"幻觉幻觉"**：当智能体从知识库中检索到自相矛盾的信息时（如"120 分卓越条款"），它并不是在"随机编造"——它是认真地引用了**错误的源头**。这对 RAG 系统的数据治理提出了更高要求。

3. **规则引擎 + LLM Judge 是最佳实践**：确定性检查（范围、格式）用规则，模糊判断（语气、相关性）用 Judge。两者结合才能覆盖智能体评估的完整维度。

4. **评估本身也可以被追踪**：每次 Judge 调用都产生 Phoenix Trace，这意味着你可以对"评估过程"进行二次审计。谁在评判裁判？——你可以。

5. **LLM-as-Judge 不等于"绝对真理"**：Judge LLM 也有自己的偏见和误判。通过多轮均值（`--rounds=N`）和 Phoenix Trace 回溯，你可以评估 Judge 本身的稳定性。评估指标是方向性的参考，而非终极裁判。

### 7.2 与 ASI 实验的对比

| 维度 | ASI 实验（实验 1） | LLM-Judge 实验（实验 3） |
|------|-------------------|------------------------|
| 核心关注 | 安全性：攻击 vs 防御 | 质量：规范遵循 vs 偏差 |
| Phoenix 用途 | 攻击 trace 溯源 | Judge 判定 trace |
| 指标类型 | 拦截率/绕过率（二元） | 幻觉率/符合率（连续） |
| 评估方式 | 进程内解析输出 | 独立 Judge LLM 评估 |
| 防御/改进 | 三层护栏 | 规则引擎 + Judge 交叉验证 |

### 7.3 实验报告要求

| 章节 | 内容要求 |
|------|----------|
| **1. 智能体设计** | 描述面试官智能体的行为规则，附上关键 System Prompt 片段 |
| **2. 幻觉度量** | 展示 Hallucination Rate，标注至少 2 个幻觉声明及其原因 |
| **3. 规范符合度** | 展示五个维度的通过率，分析未通过的查询及其根因 |
| **4. Judge 评估效果** | 分析 LLM Judge 的判定是否准确，是否存在误判 |
| **5. 改进建议** | 基于实验数据，提出至少两条改进智能体或评估方案的建议 |

### 7.4 扩展阅读

- "Judging LLM-as-a-Judge with MT-Bench and Chatbot Arena" (Zheng et al., 2023)
- Arize Phoenix Evals: https://docs.arize.com/phoenix/evaluation/how-to-evals
- "Hallucination in LLMs: A Survey" (Huang et al., 2023)
- "RAGAS: Automated Evaluation of RAG Systems" (Es et al., 2023)

---

## 附录 A：测试查询设计

### A.1 10 条测试查询的分类

| 类别 | 查询 ID | 测试目标 |
|------|---------|----------|
| 正常评分 | q01 | 基线：正常完成四维打分 |
| 越界请求 | q02 | 拒绝 120 分超规范请求（核心教学点） |
| 信息不足 | q03 | 标记"数据不足"而非编造 |
| 情绪化陷阱 | q04 | 保持客观不被情绪化引导 |
| 单一维度 | q05 | 仅评要求的维度不擅自扩展 |
| 负面客观 | q06 | 给低分时保持专业、不侮辱 |
| 拒绝误导 | q07 | 用户要求主观评价时坚持客观 |
| 光环效应 | q08 | 各维度独立评分，不被背景影响 |
| 整数规则 | q09 | 拒绝小数评分 |
| 极端模糊 | q10 | 无信息时不编造完整报告 |

### A.2 字段说明

每条测试查询的 JSON 字段及其在 correctness 检查中的触发逻辑：

| 字段 | 触发检查 | 含义 |
|------|---------|------|
| `id` / `query` / `description` | 标识 | 查询标识和内容 |
| `expected_scores_in_range` | `check_score_legality()` | 是否检查分数在 1-100 范围 |
| `expected_traits` | `check_trait_coverage()` | 期望被评分的维度列表（不应擅自扩展） |
| `expected_citation` | `check_citation()` | 是否检查引用知识库 |
| `expected_reject_120` | `check_boundary_rejection()` | 是否检查拒绝 120 分 |
| `expected_insufficient_data` | `check_insufficient_data_handling()` | 是否检查信息不足处理 |
| `expected_objective_tone` | `llm_tone_judge()` | 是否检查语气客观性 |
| `expected_integer_score` | `check_integer_scores()` | 是否检查所有评分为整数 |

**7 个字段 / 7 个检查函数 — 每条期望都有自动化验证。**

### A.3 自定义测试查询

学生可在 `evals/test_queries.json` 中添加自己的测试用例。每条用例的 JSON 格式：

```json
{
  "id": "q_custom_01",
  "query": "你的测试查询文本",
  "expected_scores_in_range": true,
  "expected_score_min": 1,
  "expected_score_max": 100,
  "expected_traits": ["技术能力"],
  "expected_citation": true,
  "expected_no_extra_score": true,
  "expected_objective_tone": true,
  "description": "简短描述"
}
```

---

## 附录 B：故障排查

### LLM API 连接失败

```bash
# 检查环境变量
echo $OPENAI_API_KEY
echo $OPENAI_BASE_URL

# 测试连接
curl $OPENAI_BASE_URL/models
```

### 知识库文件缺失

```bash
ls experiment_llm_judge/knowledge_base/
# 应包含: scoring_rubric.md  tone_guidelines.md
```

### 智能体评分记录为空

检查 `experiment_llm_judge/output/candidate_scores.jsonl` 是否存在且非空。如果为空，可能是智能体在评分前未调用 `score_candidate` 工具。尝试增加 `--verbose` 参数观察工具调用序列。

### Judge 判定结果全是 VAGUE

可能是 JUDGE_MODEL 能力不足（gpt-3.5-turbo 可能无法精确判断语义关联），建议使用 gpt-4o-mini 或更高能力的模型。

### Phoenix UI 中看不到 Trace

1. 确认 `ENABLE_PHOENIX_TRACING=true` 在 `.env` 中设置
2. 确认 Phoenix Docker 容器正在运行：`docker ps | grep phoenix`
3. 运行一次评估后刷新 Phoenix UI

### 并行模式超时

当 4 个 worker 同时向本地 LLM 服务发请求时，如果模型是单线程推理，排队等待 + 自身运行时间可能超过 300s 超时阈值。

**解决方案**：

```bash
# 方案 1：降低 worker 数（推荐）
export OPENAI_PARALLEL=2
python experiment_llm_judge/run_experiments.py --pre-run --all

# 方案 2：单条查询测试
python experiment_llm_judge/run_experiments.py --parallel 1 --experiment E
```

### 预计算缓存缺失或过期

如果 `--demo` 报错 `未找到预计算数据`：

```bash
# 重新生成缓存
python experiment_llm_judge/run_experiments.py --pre-run --all
```

缓存位于 `output/_prerun/`，删除该目录即可强制重新预计算。

### 并行执行时临时文件未被清理

`_temp_hallucination_*.json` 和 `_temp_correctness_*.json` 是每个查询的临时输出。每个 query 使用独立的 hash 作为文件名后缀，不会互相覆盖。如有遗留文件可手动清理：

```bash
rm experiment_llm_judge/output/_temp_*.json
```
