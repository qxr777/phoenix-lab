#!/usr/bin/env python3
"""Planner Agent — 任务规划智能体。将用户请求分解为可执行的子任务。

角色: 核心调度层，产出结构化任务列表。
延迟陷阱: System Prompt 故意设计为 ~800 tokens，制造高 TTFT（首字延迟）。
典型 TTFT: 2.0-3.0s（本地模型）/ 0.5-1.0s（云端 API）
"""

import json
import os
import time
from typing import Any

from openai import OpenAI

MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")

PLANNER_SYSTEM_PROMPT = """你是一个高级任务规划师（Task Planner）。将用户的复杂请求分解为可并行或串行执行的子任务（最多 4 个）。

## 规划原则
1. **原子性**: 每个子任务是一个独立的可执行工作单元。
2. **并行优先**: 无数据依赖的子任务标记为可并行。
3. **输出格式**: 子任务包含类型(search/code_review/data_analysis)、描述、复杂度(low/medium/high)。
4. **上限**: 最多生成 4 个子任务。

## 子任务类型

可规划的子任务类型包括但不限于:
- search: 信息检索与汇总
- code_review: 代码审查与静态分析
- code_generate: 代码生成与优化
- data_analysis: 数据查询、统计与趋势分析
- report_generate: 报告模板生成与格式化
- verify: 结果验证与质量检查
- transform: 数据格式转换与清洗

## 输出格式

请严格按照以下 JSON 格式返回规划结果:

{
  "overall_plan": "整体执行策略（一句话）",
  "subtasks": [
    {
      "id": "subtask_1",
      "type": "search",
      "description": "子任务描述",
      "complexity": "low" | "medium" | "high",
      "dependencies": [],
      "expected_output": "期望的输出格式"
    }
  ],
  "estimated_total_latency_s": 预估总延迟秒数
}

只返回 JSON，不要其他内容。"""

PLANNER_SYSTEM_PROMPT_OPTIMIZED = """你是任务规划师。将用户请求分解为可并行执行的子任务。

输出 JSON:
{
  "overall_plan": "策略（一句话）",
  "subtasks": [{"id": "subtask_1", "type": "search|code_review|data_analysis", "description": "...", "complexity": "low|medium|high", "dependencies": []}]
}
只返回 JSON。"""


def run_planner(user_query: str, routing_info: dict, client: OpenAI, optimize: bool = False) -> dict[str, Any]:
    t_start = time.time()

    system_prompt = PLANNER_SYSTEM_PROMPT_OPTIMIZED if optimize else PLANNER_SYSTEM_PROMPT

    context = f"用户原始请求: {user_query}\n\n网关路由分析:\n{json.dumps(routing_info, ensure_ascii=False)}"

    response = client.chat.completions.create(
        model=MODEL,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": context},
        ],
        temperature=0,
    )

    content = response.choices[0].message.content or "{}"
    content = content.strip()
    if content.startswith("```"):
        lines = content.split("\n")
        content = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])

    try:
        plan = json.loads(content)
    except json.JSONDecodeError:
        plan = {
            "overall_plan": "无法解析规划",
            "subtasks": [{"id": "subtask_1", "type": "search", "description": user_query, "complexity": "medium", "dependencies": []}],
            "estimated_total_latency_s": 5,
        }

    usage = response.usage
    token_data = {
        "agent": "planner",
        "prompt_tokens": usage.prompt_tokens if usage else 0,
        "completion_tokens": usage.completion_tokens if usage else 0,
        "total_tokens": usage.total_tokens if usage else 0,
    }
    latency = round((time.time() - t_start) * 1000)

    return {
        "agent": "planner",
        "plan": plan,
        "latency_ms": latency,
        "token_data": token_data,
    }
