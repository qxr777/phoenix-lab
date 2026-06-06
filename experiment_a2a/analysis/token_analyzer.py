#!/usr/bin/env python3
"""
实验 K：Token 成本控制 — 上下文膨胀分析与裁剪建议

本工具分析多智能体系统中每轮的 Token 消耗，绘制上下文膨胀曲线，
并给出上下文裁剪建议。

分析维度:
  1. Token 增长曲线: 按对话轮次统计 prompt_tokens / completion_tokens
  2. 上下文利用率: 有效信息 tokens / 总上下文 tokens
  3. 裁剪建议: 基于利用率阈值，建议哪些轮次的上下文可以裁剪

用法:
  python analysis/token_analyzer.py
  python analysis/token_analyzer.py --trace-file output/a2a_latest_trace.json
  python analysis/token_analyzer.py --output-json token_report.json
"""

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).parent.parent
OUTPUT_DIR = BASE_DIR / "output"


class Colors:
    RED = "\033[91m"
    GREEN = "\033[92m"
    YELLOW = "\033[93m"
    CYAN = "\033[96m"
    BOLD = "\033[1m"
    RESET = "\033[0m"


def analyze_token_usage(trace_data_list: list[dict]) -> list[dict]:
    analyses = []

    for trace_data in trace_data_list:
        agent_traces = trace_data.get("agent_traces", [])
        task_name = trace_data.get("task_name", "unknown")

        rounds = []
        cumulative_prompt = 0
        cumulative_completion = 0
        total_prompt = 0
        total_completion = 0

        for i, at in enumerate(agent_traces):
            td = at.get("token_data", {})
            prompt = td.get("prompt_tokens", 0)
            completion = td.get("completion_tokens", 0)
            total = td.get("total_tokens", 0)

            cumulative_prompt += prompt
            cumulative_completion += completion
            total_prompt += prompt
            total_completion += completion

            rounds.append({
                "round": i + 1,
                "agent": at.get("agent", "unknown"),
                "prompt_tokens": prompt,
                "completion_tokens": completion,
                "total_tokens": total,
                "cumulative_prompt": cumulative_prompt,
                "cumulative_completion": cumulative_completion,
                "latency_ms": at.get("latency_ms", 0),
            })

        utilization = round(total_completion / max(total_prompt, 1) * 100, 1)

        suggestions = []
        if cumulative_prompt > 3000:
            suggestions.append({
                "type": "context_prune",
                "severity": "high",
                "detail": f"总上下文已达 {cumulative_prompt} tokens (>3000)，建议裁剪前 {max(1, len(rounds) // 2)} 轮的原始工具输出",
                "estimated_savings": f"约 {cumulative_prompt // 3} tokens",
            })
        if total > 0 and utilization < 30:
            suggestions.append({
                "type": "prompt_optimize",
                "severity": "medium",
                "detail": f"上下文利用率仅 {utilization}%，提示词中可能包含过多冗余信息",
                "estimated_savings": "约 20-30% tokens",
            })
        if any(r["prompt_tokens"] > 2000 for r in rounds):
            suggestions.append({
                "type": "system_prompt_trim",
                "severity": "medium",
                "detail": "部分 agent 的 system prompt 过长（>2000 tokens），建议缩减至 300-500 tokens",
                "estimated_savings": "约 1500 tokens/agent",
            })

        if not suggestions:
            suggestions.append({
                "type": "healthy",
                "severity": "low",
                "detail": "当前 Token 消耗处于健康水平，暂无优化建议",
                "estimated_savings": "0",
            })

        analyses.append({
            "task_id": trace_data.get("task_id", "unknown"),
            "task_name": task_name,
            "total_prompt_tokens": total_prompt,
            "total_completion_tokens": total_completion,
            "total_tokens": total_prompt + total_completion,
            "context_utilization_pct": utilization,
            "rounds": rounds,
            "suggestions": suggestions,
        })

    return analyses


def print_analysis(analyses: list[dict]):
    for a in analyses:
        print(f"\n{Colors.BOLD}{'=' * 60}")
        print(f"📊 Token 分析: {a['task_name']}")
        print(f"{'=' * 60}{Colors.RESET}")

        print(f"\n{Colors.BOLD}Token 消耗总览{Colors.RESET}")
        print(f"  Prompt tokens:     {a['total_prompt_tokens']:,}")
        print(f"  Completion tokens: {a['total_completion_tokens']:,}")
        print(f"  Total tokens:      {a['total_tokens']:,}")

        util_color = Colors.GREEN if a['context_utilization_pct'] > 50 else (
            Colors.YELLOW if a['context_utilization_pct'] > 25 else Colors.RED)
        print(f"  {Colors.BOLD}上下文利用率:{Colors.RESET} {util_color}{a['context_utilization_pct']}%{Colors.RESET}")
        print(f"    (Completion / Prompt: 有效输出占比)")

        print(f"\n{Colors.BOLD}逐轮 Token 曲线{Colors.RESET}")
        print(f"  {'轮次':<5} {'Agent':<20} {'Prompt':>8} {'Completion':>8} {'累积Prompt':>10} {'延迟':>6}")
        print(f"  {'─' * 65}")
        for r in a["rounds"]:
            bar_len = min(int(r["prompt_tokens"] / max(1, a["total_prompt_tokens"]) * 20), 20)
            bar = "█" * bar_len
            print(f"  {r['round']:<5} {r['agent']:<20} {r['prompt_tokens']:>6} {bar} "
                  f"{r['completion_tokens']:>8} {r['cumulative_prompt']:>10} {r['latency_ms']:>4}ms")

        print(f"\n{Colors.BOLD}💡 优化建议{Colors.RESET}")
        for s in a["suggestions"]:
            sev_color = Colors.RED if s["severity"] == "high" else (Colors.YELLOW if s["severity"] == "medium" else Colors.GREEN)
            print(f"  {sev_color}[{s['severity'].upper()}]{Colors.RESET} [{s['type']}] {s['detail']}")
            if s["estimated_savings"] != "0":
                print(f"    预计节省: {s['estimated_savings']}")
    print()


def main():
    trace_file = None
    output_json = None
    for arg in sys.argv[1:]:
        if arg.startswith("--trace-file="):
            trace_file = arg.split("=", 1)[1]
        elif arg.startswith("--output-json="):
            output_json = arg.split("=", 1)[1]

    if not trace_file:
        trace_file = str(OUTPUT_DIR / "a2a_latest_trace.json")

    trace_path = Path(trace_file)
    if not trace_path.exists():
        print(f"{Colors.RED}Trace 文件不存在: {trace_file}{Colors.RESET}")
        print("请先运行 a2a_orchestrator.py 生成 trace 数据。")
        sys.exit(1)

    trace_data_list = json.loads(trace_path.read_text(encoding="utf-8"))
    if isinstance(trace_data_list, dict):
        trace_data_list = [trace_data_list]

    print(f"\n{Colors.BOLD}{'=' * 60}")
    print("📊 Token 成本控制 — 上下文膨胀分析")
    print(f"{'=' * 60}{Colors.RESET}")
    print(f"数据源: {trace_file} ({len(trace_data_list)} 个任务)")

    analyses = analyze_token_usage(trace_data_list)
    print_analysis(analyses)

    if output_json:
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        out_path = Path(output_json) if Path(output_json).is_absolute() else OUTPUT_DIR / output_json
        out_path.write_text(json.dumps(analyses, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"{Colors.GREEN}✅ JSON 报告已保存: {out_path}{Colors.RESET}")

    html_path = OUTPUT_DIR / "token_report.html"
    generate_html(analyses, str(html_path))
    print(f"{Colors.GREEN}✅ HTML 报告已保存: {html_path}{Colors.RESET}")


def generate_html(analyses: list[dict], output_path: str):
    sections = ""
    for a in analyses:
        round_rows = ""
        for r in a["rounds"]:
            round_rows += f"""<tr>
                <td>{r['round']}</td>
                <td>{r['agent']}</td>
                <td>{r['prompt_tokens']:,}</td>
                <td>{r['completion_tokens']:,}</td>
                <td>{r['cumulative_prompt']:,}</td>
                <td>{r['latency_ms']}ms</td>
            </tr>"""

        suggestions_list = "".join(
            f'<li style="color:#{"f85149" if s["severity"]=="high" else ("d2991d" if s["severity"]=="medium" else "3fb950")}">'
            f'[{s["type"]}] {s["detail"]} (预计节省: {s["estimated_savings"]})</li>'
            for s in a["suggestions"]
        )

        util_color = "#3fb950" if a["context_utilization_pct"] > 50 else ("#d2991d" if a["context_utilization_pct"] > 25 else "#f85149")

        sections += f"""<h2>{a['task_name']}</h2>
<div class="summary">
    <div class="card"><div class="value">{a['total_prompt_tokens']:,}</div><div class="label">Prompt Tokens</div></div>
    <div class="card"><div class="value">{a['total_completion_tokens']:,}</div><div class="label">Completion Tokens</div></div>
    <div class="card"><div class="value">{a['total_tokens']:,}</div><div class="label">Total Tokens</div></div>
    <div class="card"><div class="value" style="color:{util_color}">{a['context_utilization_pct']}%</div><div class="label">上下文利用率</div></div>
</div>
<h3>逐轮 Token 曲线</h3>
<table>
<thead><tr><th>轮次</th><th>Agent</th><th>Prompt</th><th>Completion</th><th>累积 Prompt</th><th>延迟</th></tr></thead>
<tbody>{round_rows}</tbody>
</table>
<h3>优化建议</h3>
<ul>{suggestions_list}</ul>
<hr>"""

    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head><meta charset="UTF-8"><title>Token 成本分析 — A2A 多智能体</title>
<style>
body{{font-family:-apple-system,sans-serif;max-width:1100px;margin:40px auto;padding:0 20px;background:#0d1117;color:#c9d1d9}}
h1{{color:#58a6ff;border-bottom:1px solid #30363d;padding-bottom:10px}}
h2{{color:#f0883e;margin-top:30px}}
h3{{color:#c9d1d9}}
table{{width:100%;border-collapse:collapse;margin:20px 0}}
th,td{{border:1px solid #30363d;padding:10px 14px;text-align:center}}
th{{background:#161b22;color:#58a6ff}}
tr:hover{{background:#1c2129}}
.summary{{display:grid;grid-template-columns:repeat(4,1fr);gap:15px;margin:20px 0}}
.card{{background:#161b22;border:1px solid #30363d;border-radius:8px;padding:20px;text-align:center}}
.card .value{{font-size:28px;font-weight:bold}}
.card .label{{font-size:13px;color:#8b949e;margin-top:6px}}
ul{{line-height:1.8}}
</style></head>
<body>
<h1>📊 Token 成本控制 — A2A 多智能体</h1>
{sections}
</body></html>"""

    Path(output_path).write_text(html, encoding="utf-8")
