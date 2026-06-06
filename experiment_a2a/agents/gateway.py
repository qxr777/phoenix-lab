#!/usr/bin/env python3
"""Gateway Agent — 前端网关。接收用户请求，解析意图，路由到 Planner。

角色: 轻量级入口，不进行复杂推理。
延迟目标: <1.5s
陷阱: 无（此 Agent 作为正常基线）
"""

import json
import time
from typing import Any

import os

from openai import OpenAI

MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")

GATEWAY_SYSTEM_PROMPT = """你是 API 网关路由层。你的唯一任务是根据用户请求，提取关键信息并返回路由指令。

请以 JSON 格式返回:
{
  "intent": "用户的意图摘要（一句话）",
  "route_to": "planner",
  "priority": "normal" | "urgent",
  "extracted_info": {
    "task_type": "数据分析" | "代码审查" | "技术调研" | "其他",
    "keywords": ["关键词1", "关键词2"]
  }
}

只返回 JSON，不要其他内容。"""


def run_gateway(user_query: str, client: OpenAI) -> dict[str, Any]:
    t_start = time.time()

    response = client.chat.completions.create(
        model=MODEL,
        messages=[
            {"role": "system", "content": GATEWAY_SYSTEM_PROMPT},
            {"role": "user", "content": user_query},
        ],
        temperature=0,
    )

    content = response.choices[0].message.content or "{}"
    content = content.strip()
    if content.startswith("```"):
        lines = content.split("\n")
        content = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])

    try:
        routing = json.loads(content)
    except json.JSONDecodeError:
        routing = {"intent": "unknown", "route_to": "planner", "priority": "normal"}

    usage = response.usage
    token_data = {
        "agent": "gateway",
        "prompt_tokens": usage.prompt_tokens if usage else 0,
        "completion_tokens": usage.completion_tokens if usage else 0,
        "total_tokens": usage.total_tokens if usage else 0,
    }
    latency = round((time.time() - t_start) * 1000)

    return {
        "agent": "gateway",
        "routing": routing,
        "latency_ms": latency,
        "token_data": token_data,
    }
