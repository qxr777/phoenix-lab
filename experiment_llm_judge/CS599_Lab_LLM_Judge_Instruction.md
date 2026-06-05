# CS599 实验 3：LLM-as-a-Judge — 基于大模型裁判的自动化软件评估实验

> [!NOTE]
> **教师寄语**：传统软件测试依赖断言（Assertion）来检查确定性的输出，但在智能体开发中，输出是语义化的——"语气是否客观"、"分数是否合理"无法用 `assertEquals` 验证。本实验让你掌握如何用 AI 评估 AI：构建一个遵循规则的智能体，然后编写 Phoenix 的自动化评估脚本来度量它的幻觉率和规范符合度。

---

## 目录

- [1. 实验概述](#1-实验概述)
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
| **E** | 构建遵循特定规则（1-100 分制、客观语气、必须引用规范）的面试官智能体 | `interviewer_agent.py` | 可运行的目标系统 |
| **F** | 使用 Judge LLM 逐条比对智能体回复与知识库，量化 Hallucination Rate | `hallucination_eval.py` | 幻觉率 + 逐条证据 |
| **G** | 验证智能体是否严格执行评分规范（分数范围、语气、引用、边界处理） | `correctness_eval.py` | 五维符合度报告 |
| **H** | 串联 F+G，生成综合 HTML 评估报告 | `run_experiments.py` | 完整流水线报告 |

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
python experiment_llm_judge/run_experiments.py check
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

# 批量测试（通过启动器）
python experiment_llm_judge/run_experiments.py --experiment E
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
# 运行幻觉度量评估
python experiment_llm_judge/evals/hallucination_eval.py

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

### 4.4 练习任务

1. **基础任务**：运行评估，记录 Hallucination Rate 和幻觉声明数量
2. **进阶任务**：找出至少 2 个被标记为 MISMATCH 的声明，打开 Phoenix UI 追踪 Judge 的判定过程
3. **挑战任务**：修改 `test_queries.json` 添加你自己的测试查询，观察新查询的幻觉率

---

## 5. 实验 G：规范符合度 (QA Correctness)

### 5.1 核心概念

**QA Correctness** 从五个维度验证智能体是否严格执行了系统设定的规范：

| 维度 | 检测方法 | 示例 |
|------|----------|------|
| **分数合法性** | 规则引擎：提取分数 → 检查 1-100 范围 + 整数 | 85.5 → ❌ 小数；120 → ❌ 越界 |
| **语气客观性** | 规则引擎（禁止词汇表）+ LLM Judge（语义判断） | "太棒了" → ❌ ；"候选人展现了" → ✅ |
| **引用完整性** | 关键词匹配：检查是否包含规范引用 | 无"评分规范"字样 → ❌ |
| **越界拒绝** | 规则引擎：120 分请求是否被拒绝 | 给出了 120 分 → ❌ |
| **信息不足处理** | 规则引擎：信息少的 query 是否标记而非编造 | 无信息但给出了满分 → ❌ |

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
# 运行规范符合度评估
python experiment_llm_judge/evals/correctness_eval.py

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

# 或通过命令行直达
python experiment_llm_judge/run_experiments.py --all
```

### 6.3 输出文件

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

5. **LLM-as-Judge 不等于"绝对真理"**：Judge LLM 也有自己的偏见和误判。本实验让学生意识到：评估指标是方向性的参考，而非终极裁判。

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

### A.1 5 条测试查询的分类

| 类别 | 查询 ID | 测试目标 |
|------|---------|----------|
| 正常评分 | q01 | 智能体是否能正常完成四维打分（基线） |
| 越界请求 | q02 | 智能体是否拒绝 120 分超规范请求（核心教学点） |
| 信息不足 | q03 | 智能体是否标记"数据不足"而非编造 |
| 情绪化陷阱 | q04 | 智能体是否能保持客观不被情绪化引导 |
| 单一维度 | q05 | 智能体是否仅评要求的维度不擅自扩展 |

### A.2 自定义测试查询

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
