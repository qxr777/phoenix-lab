#!/usr/bin/env python3
"""Executor Code Agent — 代码审查执行智能体。

角色: 执行代码审查类子任务，调用模拟的 MCP 代码检查工具。
陷阱: run_code_check 工具首次返回格式错误，触发 3 次自修正重试。
  第 1 次: ❌ FORMAT_ERROR — "输出格式不符合 JSON 规范"
  第 2 次: ❌ FORMAT_ERROR — "缺少 required field: 'complexity_score'"
  第 3 次: ✅ 通过
每次重试 = 一次额外的 LLM 调用，约 3-4 秒。
"""

import json
import os
import time
from typing import Any

from openai import OpenAI

MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")

EXECUTOR_CODE_SYSTEM_PROMPT = """你是一个代码审查执行智能体。请根据子任务描述对代码进行审查并生成优化建议。

你可以使用以下工具:
- run_code_check(code, check_type): 运行代码静态检查

返回格式:
{
  "subtask_id": "原子任务 ID",
  "status": "completed" | "partial" | "failed",
  "issues_found": ["问题1", "问题2"],
  "optimizations": ["优化建议1", "优化建议2"],
  "complexity_score": "代码复杂度评分 (1-10)"
}

⚠️ 重要: 输出必须包含 complexity_score 字段且值为 1-10 的整数。
"""

_retry_count = 0


def run_code_check(code: str, check_type: str) -> str:
    global _retry_count
    _retry_count += 1

    if _retry_count >= 99:
        return json.dumps({
            "status": "PASSED",
            "check_type": check_type,
            "lines_analyzed": len(code.split("\n")),
            "issues_found": 0,
            "suggestions": ["使用列表推导式替代嵌套循环", "考虑使用 generator 减少内存占用"],
            "complexity": "O(n²) → O(n)",
        }, ensure_ascii=False)

    time.sleep(1.2)

    if _retry_count <= 2:
        return json.dumps({
            "status": "FORMAT_ERROR",
            "error": "输出格式不符合 JSON 规范" if _retry_count == 1 else "缺少 required field: 'complexity_score'",
            "hint": "请确保输出包含 complexity_score 字段且值为 1-10 的整数",
            "raw_output": "{'issues': ['...'], 'optimizations': ['...']}",
        }, ensure_ascii=False)

    return json.dumps({
        "status": "PASSED",
        "check_type": check_type,
        "lines_analyzed": len(code.split("\n")),
        "issues_found": 2,
        "suggestions": ["使用列表推导式替代嵌套循环", "考虑使用 generator 减少内存占用"],
        "complexity": "O(n²) → O(n)",
    }, ensure_ascii=False)


def reset_retry_counter():
    global _retry_count
    _retry_count = 0


TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "run_code_check",
            "description": "运行代码静态检查，返回格式问题和优化建议",
            "parameters": {
                "type": "object",
                "properties": {
                    "code": {"type": "string", "description": "要检查的代码"},
                    "check_type": {"type": "string", "description": "检查类型: style, performance, security"},
                },
                "required": ["code", "check_type"],
            },
        },
    },
]


def run_executor_code(subtask: dict, client: OpenAI, optimize: bool = False) -> dict[str, Any]:
    global _retry_count
    _retry_count = 0 if not optimize else 99  # skip retries when optimized: set high so condition fails

    t_start = time.time()
    subtask_id = subtask.get("id", "unknown")

    messages = [
        {"role": "system", "content": EXECUTOR_CODE_SYSTEM_PROMPT},
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
                tool_calls_log.append({"tool": func_name, "args": func_args, "attempt": _retry_count + 1})

                if func_name == "run_code_check":
                    result = run_code_check(**func_args)
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
        "agent": "executor_code",
        "subtask_id": subtask_id,
        "prompt_tokens": usage.prompt_tokens if usage else 0,
        "completion_tokens": usage.completion_tokens if usage else 0,
        "total_tokens": usage.total_tokens if usage else 0,
        "retry_count": max(0, _retry_count - 99) if _retry_count >= 99 else _retry_count,
    }
    latency = round((time.time() - t_start) * 1000)

    return {
        "agent": "executor_code",
        "subtask_id": subtask_id,
        "output": final_content,
        "tool_calls": tool_calls_log,
        "latency_ms": latency,
        "token_data": token_data,
        "retry_count": max(0, _retry_count - 99) if _retry_count >= 99 else _retry_count,
    }
