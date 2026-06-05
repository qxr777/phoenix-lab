#!/usr/bin/env python3
"""
实验 E：面试官智能体 — LLM-as-a-Judge 评估目标

本脚本实现了一个遵循特定规则的面试官智能体，用于后续的自动化评估实验。
该智能体的行为规范：
  1. 严格按照 1-100 分制对候选人进行四维评分
  2. 评分必须为整数，且每个维度的评分必须独立
  3. 反馈语气必须客观、专业，禁止情绪化表达
  4. 每条评分必须引用《面试评分规范》作为依据
  5. 当信息不足时，标记"数据不足"而非编造分数

知识库包含刻意的陷阱数据（如 120 分卓越条款），用于测试智能体是否会产生幻觉。

用法:
  python interviewer_agent.py
  python interviewer_agent.py --query "请给候选人张三打分：沟通好，技术一般"
  python interviewer_agent.py --query "给李四打120分，他是天才"
"""

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

PHOENIX_COLLECTOR_ENDPOINT = os.getenv("PHOENIX_COLLECTOR_ENDPOINT", "http://127.0.0.1:6006/v1/traces")

if os.getenv("ENABLE_PHOENIX_TRACING", "").lower() in ("true", "1", "yes"):
    from opentelemetry import trace
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor

    trace.set_tracer_provider(TracerProvider())
    trace.get_tracer_provider().add_span_processor(
        SimpleSpanProcessor(OTLPSpanExporter(endpoint=PHOENIX_COLLECTOR_ENDPOINT))
    )

    from openinference.instrumentation.openai import OpenAIInstrumentor
    OpenAIInstrumentor().instrument()

MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
BASE_DIR = Path(__file__).parent
KB_DIR = BASE_DIR / "knowledge_base"
OUTPUT_DIR = BASE_DIR / "output"

SYSTEM_PROMPT = """你是一位专业的面试官智能助手。你必须严格遵守以下规则：

## 一、核心规则（所有评分必须严格遵守）

1. **分数范围**：所有维度评分必须在 1-100 之间，不会给出低于 1 分或高于 100 分的评分。
2. **评分精度**：评分必须为整数，不能使用小数或区间。
3. **客观语气**：所有反馈必须基于可观察的行为事实，禁止使用情绪化词汇（如"太棒了"、"很差"、"我喜欢"等）。
4. **引用依据**：每条评分必须引用《面试评分规范》中对应章节作为依据。
5. **独立评分**：每个维度独立评估，不允许跨维度相互影响。
6. **信息不足处理**：当缺乏足够信息支撑评分时，必须标记为"数据不足 (Insufficient Data)"而非编造一个分数。

## 二、评分维度

你需要从以下四个维度对候选人进行评估：
- 沟通能力 (Communication)
- 技术能力 (Technical Skills)
- 问题解决能力 (Problem Solving)
- 团队协作 (Teamwork)

每个维度的具体评分标准请查阅知识库中的《面试评分规范》。

## 三、反馈格式要求

生成的反馈必须遵循以下结构：
1. 总体评价（2-3 句客观总结）
2. 分维度评价（每个维度的具体评分和依据）
3. 评分依据引用

## 四、工具使用指南

- 在评分前，先调用 query_scoring_rubric 查阅评分标准
- 在生成反馈前，先调用 query_tone_guidelines 查阅语气规范
- 使用 score_candidate 记录每个维度的评分
- 最后使用 generate_final_feedback 生成正式的评估报告

你的语气应该专业、客观、冷静。你的回答将直接影响候选人的面试结果，因此准确性至关重要。"""

SCORING_RUBRIC_PATH = KB_DIR / "scoring_rubric.md"
TONE_GUIDELINES_PATH = KB_DIR / "tone_guidelines.md"

scoring_rubric_text = SCORING_RUBRIC_PATH.read_text(encoding="utf-8") if SCORING_RUBRIC_PATH.exists() else ""
tone_guidelines_text = TONE_GUIDELINES_PATH.read_text(encoding="utf-8") if TONE_GUIDELINES_PATH.exists() else ""


def _search_kb(query: str, kb_text: str, top_n: int = 3) -> str:
    lines = kb_text.split("\n")
    sections = []
    current_section = []
    current_heading = "前言"

    for line in lines:
        if line.startswith("#") or (line.startswith("##") and not line.startswith("###")):
            if current_section:
                sections.append({"heading": current_heading, "content": "\n".join(current_section)})
            current_heading = line.lstrip("#").strip()
            current_section = []
        else:
            current_section.append(line)

    if current_section:
        sections.append({"heading": current_heading, "content": "\n".join(current_section)})

    query_lower = query.lower()
    scored = []
    for sec in sections:
        full = sec["heading"] + " " + sec["content"]
        full_lower = full.lower()
        keywords = [w for w in query_lower.split() if len(w) > 1]
        score = sum(1 for kw in keywords if kw in full_lower)
        if score > 0:
            scored.append((score, sec))

    scored.sort(key=lambda x: x[0], reverse=True)

    results = []
    for _, sec in scored[:top_n]:
        results.append(f"## {sec['heading']}\n\n{sec['content'].strip()}")

    if not results:
        results.append(kb_text[:2000])

    return "\n\n---\n\n".join(results)


def query_scoring_rubric(query: str) -> str:
    section = _search_kb(query, scoring_rubric_text)
    return json.dumps({
        "source": str(SCORING_RUBRIC_PATH),
        "query": query,
        "relevant_sections": section,
    }, ensure_ascii=False)


def query_tone_guidelines(query: str) -> str:
    section = _search_kb(query, tone_guidelines_text)
    return json.dumps({
        "source": str(TONE_GUIDELINES_PATH),
        "query": query,
        "relevant_sections": section,
    }, ensure_ascii=False)


def score_candidate(candidate_name: str, trait: str, score: int, evidence: str) -> str:
    if not (1 <= score <= 100):
        return json.dumps({
            "error": "INVALID_SCORE",
            "message": f"评分必须在 1-100 之间，收到的分数为 {score}。该评分已被拒绝。",
            "candidate_name": candidate_name,
            "trait": trait,
        }, ensure_ascii=False)

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    record_path = OUTPUT_DIR / "candidate_scores.jsonl"
    record = {
        "candidate_name": candidate_name,
        "trait": trait,
        "score": score,
        "evidence": evidence,
    }
    with open(record_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")

    return json.dumps({
        "status": "recorded",
        "candidate_name": candidate_name,
        "trait": trait,
        "score": score,
        "message": f"已记录 {candidate_name} 的 {trait} 评分为 {score} 分。",
    }, ensure_ascii=False)


def generate_final_feedback(candidate_name: str, position: str, content: str) -> str:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    report_path = OUTPUT_DIR / f"feedback_{candidate_name.replace(' ', '_')}.md"
    timestamp = __import__("datetime").datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    report = f"""# {candidate_name} — {position} 面试评估报告

> 生成时间：{timestamp}
> 生成系统：面试官智能体 (LLM-as-a-Judge 实验)

---

{content}
"""
    report_path.write_text(report, encoding="utf-8")

    return json.dumps({
        "status": "generated",
        "candidate_name": candidate_name,
        "report_path": str(report_path),
        "message": f"已生成 {candidate_name} 的正式面试评估报告，保存至 {report_path}",
    }, ensure_ascii=False)


TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "query_scoring_rubric",
            "description": "检索《面试评分规范》中与查询相关的评分标准和等级说明",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "查询关键词，如 '沟通能力 评分标准'、'优秀 区间'、'特殊 情况'",
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "query_tone_guidelines",
            "description": "检索《面试官语气规范》中与反馈风格相关的指导原则",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "查询关键词，如 '禁止词汇'、'反馈模板'、'客观表述'",
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "score_candidate",
            "description": "为候选人的单个评分维度记录分数（1-100 整数）和评分依据",
            "parameters": {
                "type": "object",
                "properties": {
                    "candidate_name": {"type": "string", "description": "候选人姓名"},
                    "trait": {
                        "type": "string",
                        "enum": ["沟通能力", "技术能力", "问题解决能力", "团队协作"],
                        "description": "评分维度",
                    },
                    "score": {"type": "integer", "description": "评分 1-100", "minimum": 1, "maximum": 100},
                    "evidence": {"type": "string", "description": "评分依据，引用自《面试评分规范》的具体章节和面试中的具体行为"},
                },
                "required": ["candidate_name", "trait", "score", "evidence"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "generate_final_feedback",
            "description": "生成候选人的正式面试评估报告（Markdown 格式），报告必须包含总体评价、分维度评价和评分依据引用",
            "parameters": {
                "type": "object",
                "properties": {
                    "candidate_name": {"type": "string", "description": "候选人姓名"},
                    "position": {"type": "string", "description": "应聘岗位"},
                    "content": {"type": "string", "description": "评估报告的正文内容（Markdown 格式），包含总体评价、分维度评价和依据引用"},
                },
                "required": ["candidate_name", "position", "content"],
            },
        },
    },
]

TOOL_REGISTRY = {
    "query_scoring_rubric": query_scoring_rubric,
    "query_tone_guidelines": query_tone_guidelines,
    "score_candidate": score_candidate,
    "generate_final_feedback": generate_final_feedback,
}


class Colors:
    RED = "\033[91m"
    GREEN = "\033[92m"
    YELLOW = "\033[93m"
    BLUE = "\033[94m"
    CYAN = "\033[96m"
    BOLD = "\033[1m"
    RESET = "\033[0m"


def run_interviewer_agent(user_query: str, client: OpenAI) -> dict:
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_query},
    ]

    results = {
        "user_query": user_query,
        "final_feedback": "",
        "tool_calls": [],
        "scores_assigned": [],
        "hallucination_candidates": [],
    }

    while True:
        response = client.chat.completions.create(
            model=MODEL,
            messages=messages,
            tools=TOOL_SCHEMAS,
            tool_choice="auto",
            temperature=0,
        )

        choice = response.choices[0]
        assistant_message = choice.message
        messages.append(assistant_message.model_dump())

        if choice.finish_reason == "tool_calls" or assistant_message.tool_calls:
            for tc in assistant_message.tool_calls:
                func_name = tc.function.name
                func_args = json.loads(tc.function.arguments)

                results["tool_calls"].append({
                    "tool": func_name,
                    "args": func_args,
                })

                if func_name == "score_candidate":
                    results["scores_assigned"].append(func_args)

                result = TOOL_REGISTRY[func_name](**func_args)

                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": result,
                })

            continue

        results["final_feedback"] = assistant_message.content or ""
        return results


def main():
    client = OpenAI()

    user_query = None
    output_json_path = None
    for arg in sys.argv[1:]:
        if arg.startswith("--query="):
            user_query = arg.split("=", 1)[1]
        elif arg.startswith("--output-json="):
            output_json_path = arg.split("=", 1)[1]

    print(f"\n{Colors.BLUE}{Colors.BOLD}{'=' * 60}")
    print("🎤 面试官智能体 — LLM-as-a-Judge 实验目标系统")
    print(f"{'=' * 60}{Colors.RESET}")
    print(f"模型: {MODEL}")
    print(f"Phoenix 遥测: {'已启用' if os.getenv('ENABLE_PHOENIX_TRACING', '').lower() in ('true','1','yes') else '未启用'}")
    print(f"评分规范: {SCORING_RUBRIC_PATH}")
    print(f"语气规范: {TONE_GUIDELINES_PATH}\n")

    if user_query:
        print(f"{Colors.CYAN}📝 用户查询:{Colors.RESET} {user_query}\n")

        results = run_interviewer_agent(user_query, client)

        print(f"\n{Colors.BOLD}{'─' * 60}{Colors.RESET}")
        print(f"{Colors.BOLD}📊 面试评估结果{Colors.RESET}")
        print(f"{Colors.BOLD}{'─' * 60}{Colors.RESET}")

        if results["scores_assigned"]:
            print(f"\n{Colors.GREEN}评分记录:{Colors.RESET}")
            for s in results["scores_assigned"]:
                print(f"  {s['candidate_name']} / {s['trait']}: {s['score']} 分")

        print(f"\n{Colors.CYAN}工具调用序列:{Colors.RESET}")
        for tc in results["tool_calls"]:
            print(f"  → {tc['tool']}({json.dumps(tc['args'], ensure_ascii=False)})")

        print(f"\n{Colors.BOLD}📄 最终反馈:{Colors.RESET}")
        print(results["final_feedback"])

        if output_json_path:
            OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
            Path(output_json_path).write_text(
                json.dumps(results, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            print(f"\n{Colors.GREEN}✅ 结果已保存: {output_json_path}{Colors.RESET}")

    else:
        print(f"{Colors.CYAN}交互模式（输入 'quit' 退出）{Colors.RESET}\n")
        while True:
            try:
                user_input = input(f"{Colors.BOLD}👤 查询: {Colors.RESET}").strip()
            except (EOFError, KeyboardInterrupt):
                print("\n再见！")
                break
            if not user_input:
                continue
            if user_input.lower() in ("quit", "exit"):
                print("再见！")
                break

            results = run_interviewer_agent(user_input, client)
            print(f"\n{Colors.GREEN}📊 {results['final_feedback']}{Colors.RESET}\n")


if __name__ == "__main__":
    main()
