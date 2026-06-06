#!/usr/bin/env python3
"""Reviewer Agent — 审核智能体。汇总所有 Executor 输出，进行质量检查，生成最终回复。

角色: 质量把关层 + 最终用户输出层。
陷阱: 上下文窗口包含全部 executor 的完整输出，token 数量和延迟同时达到峰值。
"""

import json
import os
import time
from typing import Any

from openai import OpenAI

MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")

REVIEWER_SYSTEM_PROMPT = """你是一个质量审核智能体（QA Reviewer）。你的任务是审核执行智能体的输出，检查质量并拼装最终的用户回复。

## 审核维度

1. **完整性**: 所有子任务是否都已完成？是否有遗漏？
2. **一致性**: 不同 executor 的输出之间是否有矛盾？
3. **准确性**: 关键数据和结论是否有依据？
4. **可读性**: 最终输出是否清晰、结构化、适合用户阅读？
5. **格式规范**: 输出是否符合要求的格式（Markdown / JSON）？

## 输出要求

将最终回复组织为以下结构:
1. **执行摘要** (Executive Summary): 2-3 句话概括核心结论
2. **详细分析** (Detailed Analysis): 按子任务逐项展开
3. **风险与限制** (Risks & Limitations): 注明不确定性和假设
4. **下一步建议** (Next Steps): 建议用户后续可采取的行动

用中文回复，保持专业、客观的语气。
"""


def run_reviewer(
    user_query: str,
    plan: dict,
    executor_outputs: list[dict],
    client: OpenAI,
) -> dict[str, Any]:
    t_start = time.time()

    executor_summary = []
    for eo in executor_outputs:
        executor_summary.append({
            "agent": eo.get("agent", "unknown"),
            "subtask_id": eo.get("subtask_id", "unknown"),
            "latency_ms": eo.get("latency_ms", 0),
            "output": eo.get("output", ""),
        })

    context = f"""用户原始请求: {user_query}

执行规划:
{json.dumps(plan, ensure_ascii=False, indent=2)}

各执行智能体输出:
{json.dumps(executor_summary, ensure_ascii=False, indent=2)}

请审核以上所有输出，进行质量检查，并生成最终的用户回复。"""

    response = client.chat.completions.create(
        model=MODEL,
        messages=[
            {"role": "system", "content": REVIEWER_SYSTEM_PROMPT},
            {"role": "user", "content": context},
        ],
        temperature=0,
    )

    final_content = response.choices[0].message.content or ""

    usage = response.usage
    token_data = {
        "agent": "reviewer",
        "prompt_tokens": usage.prompt_tokens if usage else 0,
        "completion_tokens": usage.completion_tokens if usage else 0,
        "total_tokens": usage.total_tokens if usage else 0,
        "executor_count": len(executor_outputs),
        "context_note": "包含所有 executor 的完整输出",
    }
    latency = round((time.time() - t_start) * 1000)

    return {
        "agent": "reviewer",
        "output": final_content,
        "executors_reviewed": len(executor_outputs),
        "latency_ms": latency,
        "token_data": token_data,
    }
