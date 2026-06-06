#!/usr/bin/env python3
"""Executor Data Agent — 数据分析执行智能体。

角色: 执行数据分析类子任务，调用模拟的慢速 MCP 数据库查询工具。
陷阱 1: System Prompt 故意设计为 ~800 tokens，制造高 TTFT（2.0-3.0s）。
陷阱 2: query_database MCP 工具加 2s 人工延迟，模拟慢速数据库查询。
"""

import json
import os
import time
from typing import Any

from openai import OpenAI

MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")

_optimize_mode = False

EXECUTOR_DATA_SYSTEM_PROMPT = """你是一个数据分析执行智能体（Data Analysis Executor）。你的任务是查询数据库、分析趋势并生成数据分析摘要。

## 数据分析方法论

1. **数据质量检查**: 在进行分析之前，首先检查数据的完整性和一致性。缺失值、异常值必须在报告中注明。
2. **多维度分析**: 从时间维度（同比/环比）、空间维度（区域对比）、指标维度（营收/增长/退货）三个角度进行分析。
3. **趋势识别**: 使用移动平均、季节性分解等方法识别数据中的长期趋势和周期性模式。
4. **统计显著性**: 对于观察到的变化，评估其是否具有统计显著性（p值、置信区间）。
5. **因果推断**: 区分相关性和因果性，避免将偶然关联误判为因果关系。
6. **可视化建议**: 为每个关键发现推荐最合适的可视化类型（折线图、柱状图、热力图等）。
7. **业务解读**: 将数据发现转化为业务语言，说明对决策的实际影响。
8. **不确定性声明**: 对于数据不足或方法局限导致的推断不确定性，必须在报告中明确声明。

## 可用工具

- query_database(sql): 执行 SQL 查询并返回结构化数据
- generate_chart(data, chart_type): 生成数据可视化配置

## 输出格式

{
  "subtask_id": "原子任务 ID",
  "status": "completed" | "partial" | "failed",
  "data_summary": {"总记录数": ..., "时间范围": "...", "关键指标": {...}},
  "trends": ["趋势1", "趋势2"],
  "recommendations": ["建议1", "建议2"],
  "charts": [{"type": "line", "title": "...", "insight": "..."}]
}
"""

EXECUTOR_DATA_SYSTEM_PROMPT_OPTIMIZED = """你是数据分析执行智能体。查询数据库并生成摘要。

工具: query_database(sql) - 执行 SQL 查询

输出 JSON:
{
  "subtask_id": "...",
  "status": "completed|partial|failed",
  "data_summary": {...},
  "trends": [...],
  "recommendations": [...]
}
"""


def query_database(sql: str) -> str:
    if not _optimize_mode:
        time.sleep(2.0)
    return json.dumps({
        "status": "SUCCESS",
        "query": sql,
        "rows_affected": 12450,
        "sample_data": [
            {"quarter": "Q1", "revenue": 1_250_000, "customer_growth": 0.12, "return_rate": 0.035},
            {"quarter": "Q2", "revenue": 1_380_000, "customer_growth": 0.09, "return_rate": 0.041},
            {"quarter": "Q3", "revenue": 1_520_000, "customer_growth": 0.15, "return_rate": 0.028},
            {"quarter": "Q4", "revenue": 1_710_000, "customer_growth": 0.11, "return_rate": 0.033},
        ],
        "execution_time_ms": 1847,
    }, ensure_ascii=False)


TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "query_database",
            "description": "执行 SQL 查询并返回结构化数据",
            "parameters": {
                "type": "object",
                "properties": {"sql": {"type": "string", "description": "SQL 查询语句"}},
                "required": ["sql"],
            },
        },
    },
]


def run_executor_data(subtask: dict, client: OpenAI, optimize: bool = False) -> dict[str, Any]:
    global _optimize_mode
    _optimize_mode = optimize
    t_start = time.time()
    subtask_id = subtask.get("id", "unknown")

    system_prompt = EXECUTOR_DATA_SYSTEM_PROMPT_OPTIMIZED if optimize else EXECUTOR_DATA_SYSTEM_PROMPT

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": f"请执行以下子任务:\n{json.dumps(subtask, ensure_ascii=False)}"},
    ]

    tool_calls_log = []
    final_content = ""

    while True:
        response = client.chat.completions.create(
            model=MODEL,
            messages=messages,
            tools=TOOL_SCHEMAS,
            tool_choice="auto",
            temperature=0,
        )

        choice = response.choices[0]
        assistant_msg = choice.message
        messages.append(assistant_msg.model_dump())

        if choice.finish_reason == "tool_calls" or assistant_msg.tool_calls:
            for tc in assistant_msg.tool_calls:
                func_name = tc.function.name
                func_args = json.loads(tc.function.arguments)
                tool_calls_log.append({"tool": func_name, "args": func_args})

                if func_name == "query_database":
                    result = query_database(**func_args)
                else:
                    result = json.dumps({"error": f"Unknown tool: {func_name}"})

                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": result,
                })
            continue

        final_content = assistant_msg.content or ""
        break

    usage = response.usage
    token_data = {
        "agent": "executor_data",
        "subtask_id": subtask_id,
        "prompt_tokens": usage.prompt_tokens if usage else 0,
        "completion_tokens": usage.completion_tokens if usage else 0,
        "total_tokens": usage.total_tokens if usage else 0,
    }
    latency = round((time.time() - t_start) * 1000)

    return {
        "agent": "executor_data",
        "subtask_id": subtask_id,
        "output": final_content,
        "tool_calls": tool_calls_log,
        "latency_ms": latency,
        "token_data": token_data,
    }
