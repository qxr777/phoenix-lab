#!/usr/bin/env python3
"""
实验 J：关键路径分析 (Critical Path Analysis) — Phoenix 分布式追踪时序分析

本工具从进程内的 trace 数据中分析多智能体的执行时序，
计算每个 agent 的延迟占比，并标注 Critical Path（最慢路径）。

当 Phoenix 可用时，还会尝试从 Phoenix REST API 拉取 span 数据
进行交叉验证。

用法:
  python analysis/critical_path.py
  python analysis/critical_path.py --trace-file output/a2a_latest_trace.json
  python analysis/critical_path.py --output-json critical_path_report.json
"""

import json
import os
import socket
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from dotenv import load_dotenv

load_dotenv()

socket.setdefaulttimeout(5)

BASE_DIR = Path(__file__).parent.parent
OUTPUT_DIR = BASE_DIR / "output"
PHOENIX_HOST = os.getenv("PHOENIX_COLLECTOR_ENDPOINT", "http://127.0.0.1:6006/v1/traces").replace("/v1/traces", "").rstrip("/")


class Colors:
    RED = "\033[91m"
    GREEN = "\033[92m"
    YELLOW = "\033[93m"
    CYAN = "\033[96m"
    MAGENTA = "\033[95m"
    BOLD = "\033[1m"
    RESET = "\033[0m"


def build_span_tree(trace_data: dict) -> dict:
    spans = []
    for agent_trace in trace_data.get("agent_traces", []):
        agent_name = agent_trace.get("agent", "unknown")
        latency = agent_trace.get("latency_ms", 0)
        token_data = agent_trace.get("token_data", {})

        children = []
        for tc in agent_trace.get("tool_calls", []):
            tool_name = tc.get("tool", "unknown")
            attempt = tc.get("attempt", 1)
            child_name = f"{agent_name}.{tool_name}" if attempt == 1 else f"{agent_name}.{tool_name}[attempt_{attempt}]"
            children.append({
                "name": child_name,
                "latency_ms": 0,
                "type": "tool_call",
                "note": f"attempt {attempt}" if attempt > 1 else "",
            })

        span = {
            "name": agent_name,
            "latency_ms": latency,
            "type": "agent",
            "tokens_in": token_data.get("prompt_tokens", 0),
            "tokens_out": token_data.get("completion_tokens", 0),
            "children": children,
        }
        spans.append(span)

    return {
        "task_id": trace_data.get("task_id", "unknown"),
        "task_name": trace_data.get("task_name", "unknown"),
        "total_latency_ms": trace_data.get("total_latency_ms", 0),
        "spans": spans,
    }


def find_critical_path(spans: list[dict]) -> list[str]:
    critical = []
    max_latency = 0
    bottleneck = ""

    for span in spans:
        if span["latency_ms"] > max_latency:
            max_latency = span["latency_ms"]
            bottleneck = span["name"]

    path = ["user_request"] + [s["name"] for s in spans]
    return path, bottleneck, max_latency


def analyze_task(trace_data: dict) -> dict:
    tree = build_span_tree(trace_data)
    spans = tree["spans"]
    total = tree["total_latency_ms"]

    path, bottleneck, max_latency = find_critical_path(spans)

    analysis = {
        "task_id": tree["task_id"],
        "task_name": tree["task_name"],
        "total_latency_ms": total,
        "critical_path": " → ".join(path),
        "bottleneck_agent": bottleneck,
        "bottleneck_latency_ms": max_latency,
        "bottleneck_pct": round(max_latency / total * 100, 1) if total > 0 else 0,
        "agent_breakdown": [],
        "total_tokens_in": trace_data.get("total_tokens_in", 0),
        "total_tokens_out": trace_data.get("total_tokens_out", 0),
        "executor_duration_ms": trace_data.get("executor_duration_ms", 0),
    }

    for span in spans:
        pct = round(span["latency_ms"] / total * 100, 1) if total > 0 else 0
        analysis["agent_breakdown"].append({
            "agent": span["name"],
            "latency_ms": span["latency_ms"],
            "pct": pct,
            "tokens_in": span.get("tokens_in", 0),
            "tokens_out": span.get("tokens_out", 0),
            "tool_calls": len(span.get("children", [])),
        })

    return analysis


def query_phoenix_spans() -> dict | None:
    try:
        import urllib.request
        url = f"{PHOENIX_HOST}/v1/spans"
        req = urllib.request.Request(url)
        response = urllib.request.urlopen(req, timeout=10)
        data = json.loads(response.read().decode())
        return data
    except Exception:
        return None


def print_analysis(analysis: dict, phoenix_data: dict | None = None):
    print(f"\n{Colors.BOLD}{'=' * 60}")
    print(f"⏱️  关键路径分析: {analysis['task_name']}")
    print(f"{'=' * 60}{Colors.RESET}")

    print(f"\n{Colors.BOLD}总览{Colors.RESET}")
    print(f"  总延迟:        {Colors.MAGENTA}{analysis['total_latency_ms']}ms{Colors.RESET}")
    print(f"  Token in:      {analysis['total_tokens_in']}")
    print(f"  Token out:     {analysis['total_tokens_out']}")

    print(f"\n{Colors.BOLD}🔴 Critical Path{Colors.RESET}")
    print(f"  路径: {Colors.YELLOW}{analysis['critical_path']}{Colors.RESET}")
    print(f"  瓶颈: {Colors.RED}{Colors.BOLD}{analysis['bottleneck_agent']}{Colors.RESET} — "
          f"{analysis['bottleneck_latency_ms']}ms ({analysis['bottleneck_pct']}%)")

    print(f"\n{Colors.BOLD}Agent 延迟分布{Colors.RESET}")
    print(f"  {'Agent':<25} {'延迟':>8} {'占比':>7} {'Tokens':>10} {'工具调用':>8}")
    print(f"  {'─' * 60}")
    for a in analysis["agent_breakdown"]:
        pct_color = Colors.RED if a["pct"] > 25 else (Colors.YELLOW if a["pct"] > 10 else Colors.GREEN)
        print(f"  {a['agent']:<25} {a['latency_ms']:>6}ms {pct_color}{a['pct']:>6.1f}%{Colors.RESET} "
              f"{a['tokens_in'] + a['tokens_out']:>8} {a['tool_calls']:>8}")

    if phoenix_data:
        print(f"\n{Colors.BOLD}Phoenix 遥测{Colors.RESET}")
        spans = phoenix_data.get("spans", [])
        print(f"  获取到 {len(spans)} 个 span")
        if spans:
            durations = {}
            for s in spans:
                name = s.get("name", "unknown")
                duration_ms = (s.get("end_time", 0) - s.get("start_time", 0)) * 1000
                if name not in durations:
                    durations[name] = duration_ms
            for name, dur in sorted(durations.items(), key=lambda x: x[1], reverse=True):
                print(f"    {name}: {dur:.0f}ms")

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
    print("⏱️  关键路径分析 — Critical Path Analysis")
    print(f"{'=' * 60}{Colors.RESET}")
    print(f"数据源: {trace_file} ({len(trace_data_list)} 个任务)")

    phoenix_data = query_phoenix_spans() if PHOENIX_HOST else None

    all_analyses = []
    for td in trace_data_list:
        analysis = analyze_task(td)
        all_analyses.append(analysis)
        print_analysis(analysis, phoenix_data)

    if output_json:
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        out_path = Path(output_json) if Path(output_json).is_absolute() else OUTPUT_DIR / output_json
        out_path.write_text(json.dumps(all_analyses, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"{Colors.GREEN}✅ JSON 报告已保存: {out_path}{Colors.RESET}")

    html_path = OUTPUT_DIR / "critical_path_report.html"
    generate_html(all_analyses, str(html_path))
    print(f"{Colors.GREEN}✅ HTML 报告已保存: {html_path}{Colors.RESET}")


def generate_html(analyses: list[dict], output_path: str):
    rows = ""
    for a in analyses:
        agent_rows = ""
        for ag in a["agent_breakdown"]:
            bar_width = min(int(ag["pct"] * 3), 100)
            bar_color = "#f85149" if ag["pct"] > 25 else ("#d2991d" if ag["pct"] > 10 else "#3fb950")
            agent_rows += f"""<tr>
                <td>{ag['agent']}</td>
                <td>{ag['latency_ms']}ms</td>
                <td>
                    <div style="background:#161b22;border-radius:4px;height:20px;width:100%">
                        <div style="background:{bar_color};height:20px;width:{bar_width}%;border-radius:4px"></div>
                    </div>
                    {ag['pct']}%
                </td>
                <td>{ag['tokens_in'] + ag['tokens_out']}</td>
            </tr>"""

        rows += f"""<tr style="border-top:2px solid #30363d">
            <td colspan="5" style="font-weight:bold;color:#58a6ff">{a['task_name']} (总延迟: {a['total_latency_ms']}ms)</td>
        </tr>
        <tr><td colspan="5">
            🔴 <b>Critical Path:</b> {a['critical_path']}<br>
            🔴 <b>Bottleneck:</b> {a['bottleneck_agent']} ({a['bottleneck_latency_ms']}ms / {a['bottleneck_pct']}%)
        </td></tr>
        {agent_rows}"""

    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head><meta charset="UTF-8"><title>关键路径分析 — A2A 多智能体</title>
<style>
body{{font-family:-apple-system,sans-serif;max-width:1100px;margin:40px auto;padding:0 20px;background:#0d1117;color:#c9d1d9}}
h1{{color:#58a6ff;border-bottom:1px solid #30363d;padding-bottom:10px}}
table{{width:100%;border-collapse:collapse;margin:20px 0}}
th,td{{border:1px solid #30363d;padding:10px 14px;text-align:left}}
th{{background:#161b22;color:#58a6ff}}
tr:hover{{background:#1c2129}}
</style></head>
<body>
<h1>⏱️ 关键路径分析 — A2A 多智能体分布式追溯</h1>
<table>
<thead><tr><th>Agent</th><th>延迟</th><th>占比</th><th>Tokens</th></tr></thead>
<tbody>{rows}</tbody>
</table>
</body></html>"""

    Path(output_path).write_text(html, encoding="utf-8")
