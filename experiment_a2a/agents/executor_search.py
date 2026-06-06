#!/usr/bin/env python3
"""Executor Search Agent — 信息检索执行智能体。

角色: 执行搜索类子任务，调用模拟的 MCP 搜索工具。
延迟: 正常基线 ~1.5s（无陷阱）
用作与其他 executor 的性能对比基线。
"""

import json
import os
import time
from typing import Any

from openai import OpenAI

MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")

EXECUTOR_SEARCH_SYSTEM_PROMPT = """你是一个信息检索执行智能体。请根据子任务描述执行搜索并汇总结果。

你可以使用以下工具:
- mcp_search(query): 搜索相关信息源

返回格式:
{
  "subtask_id": "原子任务 ID",
  "status": "completed" | "partial" | "failed",
  "summary": "检索结果的简要总结",
  "sources": ["来源1", "来源2"],
  "details": "详细的检索结果"
}
"""


def mcp_search(query: str) -> str:
    time.sleep(0.8)
    return json.dumps({
        "results": [
            {"title": f"搜索结果 1: {query}", "snippet": f"关于 {query} 的详细说明...", "relevance": 0.95},
            {"title": f"搜索结果 2: {query} 最佳实践", "snippet": f"{query} 的行业最佳实践包括...", "relevance": 0.88},
            {"title": f"搜索结果 3: {query} 性能对比", "snippet": f"不同方案在 {query} 上的性能表现...", "relevance": 0.82},
        ],
        "total_found": 42,
    }, ensure_ascii=False)


TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "mcp_search",
            "description": "搜索相关信息源",
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string", "description": "搜索查询"}},
                "required": ["query"],
            },
        },
    },
]


def run_executor_search(subtask: dict, client: OpenAI) -> dict[str, Any]:
    t_start = time.time()
    subtask_id = subtask.get("id", "unknown")

    messages = [
        {"role": "system", "content": EXECUTOR_SEARCH_SYSTEM_PROMPT},
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

                if func_name == "mcp_search":
                    result = mcp_search(**func_args)
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
        "agent": "executor_search",
        "subtask_id": subtask_id,
        "prompt_tokens": usage.prompt_tokens if usage else 0,
        "completion_tokens": usage.completion_tokens if usage else 0,
        "total_tokens": usage.total_tokens if usage else 0,
    }
    latency = round((time.time() - t_start) * 1000)

    return {
        "agent": "executor_search",
        "subtask_id": subtask_id,
        "output": final_content,
        "tool_calls": tool_calls_log,
        "latency_ms": latency,
        "token_data": token_data,
    }
