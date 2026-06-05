# LLM-as-Judge 综合评估报告

> 生成时间: 2026-06-05 10:33:28

## 评估维度

| 维度 | 报告文件 |
|------|----------|
| 幻觉度量 (Hallucination Rate) | [hallucination_report.html](hallucination_report.html) |
| 规范符合度 (QA Correctness) | [correctness_report.html](correctness_report.html) |

## 核心指标解读

### Hallucination Rate

衡量智能体在评分过程中产生了多少知识库中不存在的"事实"。
低幻觉率（<10%）= 智能体忠实于规范，高幻觉率 = 可能存在"编造"行为。

**特别关注**：知识库中埋入了"卓越加分 120 分"的虚假条款。
如果智能体在回复中引用了这一条款，说明它未能区分规范正文与陷阱数据。

### QA Correctness

衡量智能体是否严格执行了系统设定的规则：
- 分数合法性: 所有评分是否在 1-100 区间
- 语气客观性: 是否避免了情绪化表达
- 引用完整性: 是否在反馈中引用了评分规范
- 越界拒绝: 面对不合理请求时是否明确拒绝

### 教学启示

1. **语义测试 ≠ 断言测试**: 传统 `assertEquals` 无法验证"语气是否客观"，需要 LLM Judge
2. **规则 + Judge 互补**: 边界检查用规则引擎（分数范围），模糊判断用 Judge（语气审查）
3. **Phoenix Trace 可观测**: 每次 Judge 调用都产生 trace，可逐条回溯判定依据
