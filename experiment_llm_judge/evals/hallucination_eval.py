#!/usr/bin/env python3
"""
实验 F：幻觉度量 (Hallucination Rate) — LLM-as-Judge 评估器

本脚本以另一个 LLM（Judge）的身份，逐条审查面试官智能体的回复，
判断其中的事实声明是否能在知识库中找到依据。
无法找到依据的声明被标记为"幻觉 (Hallucination)"。

评估流程:
  1. 读取测试查询集 (test_queries.json)
  2. 运行面试官智能体，获取每条查询的回复
  3. 将回复拆解为原子声明 (claims)
  4. 对每条 claim，Judge LLM 比对知识库原文
  5. 输出 Hallucination Rate 及逐条证据

用法:
  python hallucination_eval.py
  python hallucination_eval.py --output-json hallucination_report.json
  python hallucination_eval.py --verbose
"""

import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
WORKERS = max(1, int(os.getenv("OPENAI_PARALLEL", "4")))
BASE_DIR = Path(__file__).parent
EXPERIMENT_DIR = BASE_DIR.parent
KB_DIR = EXPERIMENT_DIR / "knowledge_base"
OUTPUT_DIR = EXPERIMENT_DIR / "output"

PHOENIX_ENABLED = os.getenv("ENABLE_PHOENIX_TRACING", "").lower() in ("true", "1", "yes")
if PHOENIX_ENABLED:
    from opentelemetry import trace
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    trace.set_tracer_provider(TracerProvider())
    trace.get_tracer_provider().add_span_processor(
        SimpleSpanProcessor(OTLPSpanExporter(endpoint=os.getenv("PHOENIX_COLLECTOR_ENDPOINT", "http://127.0.0.1:6006/v1/traces")))
    )
    _eval_tracer = trace.get_tracer("hallucination_eval")
else:
    _eval_tracer = None

scoring_rubric_text = (KB_DIR / "scoring_rubric.md").read_text(encoding="utf-8")
tone_guidelines_text = (KB_DIR / "tone_guidelines.md").read_text(encoding="utf-8")
full_kb_text = f"""# 面试评分规范\n\n{scoring_rubric_text}\n\n# 语气规范\n\n{tone_guidelines_text}"""


class Colors:
    RED = "\033[91m"
    GREEN = "\033[92m"
    YELLOW = "\033[93m"
    CYAN = "\033[96m"
    BOLD = "\033[1m"
    RESET = "\033[0m"


def run_interviewer(query: str, run_id: str = "") -> dict:
    import subprocess
    import hashlib

    agent_script = str(EXPERIMENT_DIR / "interviewer_agent.py")
    uid = run_id or hashlib.md5(query.encode()).hexdigest()[:8]
    temp_output = str(OUTPUT_DIR / f"_temp_hallucination_{uid}.json")
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    cmd = [sys.executable, agent_script, f"--query={query}", f"--output-json={temp_output}"]
    try:
        proc = subprocess.run(
            [sys.executable, agent_script, f"--query={query}", f"--output-json={temp_output}"],
            cwd=str(Path(__file__).parent.parent),
            capture_output=True,
            text=True,
            timeout=300,
            env={**os.environ, "PYTHONIOENCODING": "utf-8"},
        )

        result = {
            "query": query,
            "stdout": proc.stdout,
            "stderr": proc.stderr,
            "return_code": proc.returncode,
            "final_feedback": "",
            "tool_calls": [],
            "scores_assigned": [],
        }

        try:
            agent_result = json.loads(Path(temp_output).read_text(encoding="utf-8"))
            result["final_feedback"] = agent_result.get("final_feedback", "")
            result["tool_calls"] = agent_result.get("tool_calls", [])
            result["scores_assigned"] = agent_result.get("scores_assigned", [])
        except (FileNotFoundError, json.JSONDecodeError):
            result["error"] = "解析面试官输出失败"
    except subprocess.TimeoutExpired:
        result = {
            "query": query,
            "stdout": "",
            "stderr": "subprocess timed out after 300s",
            "return_code": -1,
            "final_feedback": "",
            "tool_calls": [],
            "scores_assigned": [],
            "error": "timeout",
        }
    except Exception as e:
        result = {
            "query": query,
            "stdout": "",
            "stderr": str(e),
            "return_code": -1,
            "final_feedback": "",
            "tool_calls": [],
            "scores_assigned": [],
            "error": str(e),
        }

    return result


def extract_claims(text: str, client: OpenAI) -> list[str]:
    if not text.strip():
        return []

    prompt = f"""以下是一段面试官对候选人的评估文本。请将其中包含的事实声明逐条提取出来。
每条声明应该是可以独立验证的断言（如"评分为 X 分"、"某维度表现为 Y"、"根据规范第 Z 节"）。

请以 JSON 数组格式返回，每个元素是一个字符串。如果没有可验证的声明，返回空数组。

评估文本:
---
{text}
---

只返回 JSON 数组，不要其他内容。"""

    try:
        response = client.chat.completions.create(
            model=MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0,
        )
        content = response.choices[0].message.content or "[]"
        content = content.strip()
        if content.startswith("```"):
            lines = content.split("\n")
            content = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])
        return json.loads(content)
    except Exception:
        return [text[:200]]


def judge_claim(claim: str, kb_text: str, client: OpenAI) -> dict:
    prompt = f"""你是一个严格的事实核查员 (Fact Checker)。你的任务是比对以下声明与知识库原文，
判断该声明是否能在知识库中找到支持依据。

知识库:
---
{kb_text[:4000]}
---

待核查的声明:
"{claim}"

请按以下 JSON 格式返回结果:
{{
  "verdict": "MATCH" | "MISMATCH" | "VAGUE",
  "explanation": "判断理由（一段话）",
  "kb_reference": "知识库中相关的原文片段（如为 MATCH）或空字符串（如为 MISMATCH/VAGUE）"
}}

规则:
- MATCH: 声明内容可以在知识库中找到明确支持
- MISMATCH: 声明内容与知识库矛盾，或知识库中完全没有相关内容（即幻觉）
- VAGUE: 声明过于模糊，无法判定（如纯主观的礼貌用语）

只返回 JSON，不要其他内容。"""

    try:
        if _eval_tracer:
            with _eval_tracer.start_as_current_span("judge.hallucination_check", attributes={"claim_length": len(claim)}) as span:
                response = client.chat.completions.create(
                    model=MODEL,
                    messages=[{"role": "user", "content": prompt}],
                    temperature=0,
                )
        else:
            response = client.chat.completions.create(
                model=MODEL,
                messages=[{"role": "user", "content": prompt}],
                temperature=0,
            )
        content = response.choices[0].message.content or "{}"
        content = content.strip()
        if content.startswith("```"):
            lines = content.split("\n")
            content = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])
        return json.loads(content)
    except Exception as e:
        return {"verdict": "ERROR", "explanation": str(e), "kb_reference": ""}


def _process_query_hallucination(tq: dict, idx: int, total: int, rounds: int, parallel_judge: bool) -> dict:
    client = OpenAI()
    qid = tq["id"]
    round_rates = []

    for r in range(rounds):
        label = f"{qid} r{r+1}/{rounds}" if rounds > 1 else qid
        print(f"\r{Colors.CYAN}  Hallucination Eval [{idx + 1}/{total}]: {label}...{Colors.RESET}", end="")
        sys.stdout.flush()

        run_id = f"{qid}_r{r}"
        query_result = run_interviewer(tq["query"], run_id=run_id)
        claims = extract_claims(query_result["final_feedback"], client)

        if parallel_judge and len(claims) > 1:
            with ThreadPoolExecutor(max_workers=min(len(claims), 6)) as pool:
                futures = {pool.submit(judge_claim, claim, full_kb_text, client): claim for claim in claims}
                ordered = [None] * len(claims)
                claim_index = {c: i for i, c in enumerate(claims)}
                for future in as_completed(futures):
                    claim = futures[future]
                    ordered[claim_index[claim]] = {"claim": claim, **future.result()}
                claim_results = ordered
        else:
            claim_results = []
            for claim in claims:
                verdict = judge_claim(claim, full_kb_text, client)
                claim_results.append({"claim": claim, **verdict})

        match = sum(1 for c in claim_results if c["verdict"] == "MATCH")
        mismatch = sum(1 for c in claim_results if c["verdict"] == "MISMATCH")
        vague = sum(1 for c in claim_results if c["verdict"] == "VAGUE")

        if len(claims) > 0:
            round_rates.append(round(mismatch / len(claims) * 100, 1))

    return {
        "query_id": qid,
        "query": tq["query"],
        "description": tq.get("description", ""),
        "final_feedback": query_result["final_feedback"],
        "scores_assigned": query_result.get("scores_assigned", []),
        "claims": claim_results,
        "summary": {"match": match, "mismatch": mismatch, "vague": vague, "total": len(claims)},
        "round_rates": round_rates,
    }


def run_hallucination_eval(verbose: bool = False, rounds: int = 1, parallel_judge: bool = False) -> dict:
    queries_path = BASE_DIR / "test_queries.json"
    test_queries = json.loads(queries_path.read_text(encoding="utf-8"))

    report = {
        "eval_type": "hallucination",
        "model": MODEL,
        "total_queries": len(test_queries),
        "rounds": rounds,
        "total_claims": 0,
        "match_count": 0,
        "mismatch_count": 0,
        "vague_count": 0,
        "hallucination_rate": 0.0,
        "hallucination_rate_std": 0.0,
        "results": [],
    }

    all_query_results = _run_parallel(test_queries, rounds, WORKERS, parallel_judge)
    if verbose:
        for pq in all_query_results:
            round_rates = pq["round_rates"]
            if rounds > 1 and round_rates:
                avg = sum(round_rates) / len(round_rates)
                variance = sum((x - avg) ** 2 for x in round_rates) / len(round_rates)
                print(f"\n  {Colors.BOLD}{pq['query_id']}{Colors.RESET}: avg={avg:.1f}% std={variance**0.5:.1f}% (n={rounds})")

    print()

    for pq in all_query_results:
        s = pq["summary"]
        report["total_claims"] += s["total"]
        report["match_count"] += s["match"]
        report["mismatch_count"] += s["mismatch"]
        report["vague_count"] += s["vague"]
        report["results"].append({
            "query_id": pq["query_id"],
            "query": pq["query"],
            "description": pq["description"],
            "final_feedback": pq["final_feedback"],
            "scores_assigned": pq["scores_assigned"],
            "claims": pq["claims"],
            "summary": s,
        })

    total = report["total_claims"]
    if total > 0:
        report["hallucination_rate"] = round(report["mismatch_count"] / total * 100, 1)

    return report


def _run_parallel(test_queries: list, rounds: int, workers: int, parallel_judge: bool) -> list:
    results = [None] * len(test_queries)
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {}
        for i, tq in enumerate(test_queries):
            f = pool.submit(_process_query_hallucination, tq, i, len(test_queries), rounds, parallel_judge)
            futures[f] = i
        for future in as_completed(futures):
            idx = futures[future]
            results[idx] = future.result()
            qid = test_queries[idx]["id"]
            print(f"\r{Colors.CYAN}  Hallucination Eval [{len([r for r in results if r])}/{len(test_queries)}] "
                  f"complete: {qid}{Colors.RESET}")
            sys.stdout.flush()
    return results


def print_report(report: dict):
    print(f"\n{Colors.BOLD}{'=' * 60}")
    print(f"🔍 幻觉度量报告 (Hallucination Rate)")
    print(f"{'=' * 60}{Colors.RESET}\n")

    print(f"  总查询数:       {report['total_queries']}")
    print(f"  总声明数:       {report['total_claims']}")
    print(f"  {Colors.GREEN}匹配 (MATCH):     {report['match_count']}{Colors.RESET}")
    print(f"  {Colors.RED}幻觉 (MISMATCH):   {report['mismatch_count']}{Colors.RESET}")
    print(f"  {Colors.YELLOW}模糊 (VAGUE):     {report['vague_count']}{Colors.RESET}")
    print(f"\n  {Colors.BOLD}幻觉率:{Colors.RESET} {Colors.RED}{report['hallucination_rate']}%{Colors.RESET}")

    print(f"\n{Colors.BOLD}逐查询明细:{Colors.RESET}")
    for r in report["results"]:
        s = r["summary"]
        status = Colors.GREEN if s["mismatch"] == 0 else Colors.RED
        print(f"  {r['query_id']}: {status}{s['match']}✓ {s['mismatch']}✗ {s['vague']}~{Colors.RESET} — {r['description'][:60]}")

        for cr in r["claims"]:
            if cr["verdict"] == "MISMATCH":
                print(f"    {Colors.RED}💀 幻觉声明:{Colors.RESET} {cr['claim'][:150]}")
                print(f"    {Colors.YELLOW}   原因:{Colors.RESET} {cr['explanation'][:120]}")


def main():
    global WORKERS
    verbose = "--verbose" in sys.argv
    output_json = None
    rounds = 1
    parallel_judge = False
    for arg in sys.argv[1:]:
        if arg.startswith("--output-json="):
            output_json = arg.split("=", 1)[1]
        elif arg.startswith("--rounds="):
            rounds = int(arg.split("=", 1)[1])
        elif arg.startswith("--parallel="):
            WORKERS = max(1, int(arg.split("=", 1)[1]))
        elif arg == "--parallel-judge":
            parallel_judge = True

    print(f"\n{Colors.BOLD}{'=' * 60}")
    print("🔍 实验 F：幻觉度量 — LLM-as-Judge")
    print(f"{'=' * 60}{Colors.RESET}")
    print(f"Judge 模型: {MODEL}")
    print(f"轮次: {rounds}")
    print(f"并行: {WORKERS} workers" + (" + 声明级并行" if parallel_judge else ""))
    print(f"知识库: {KB_DIR}\n")

    report = run_hallucination_eval(verbose=verbose, rounds=rounds, parallel_judge=parallel_judge)
    print_report(report)

    if output_json:
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        out_path = Path(output_json) if Path(output_json).is_absolute() else OUTPUT_DIR / output_json
        out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n{Colors.GREEN}✅ JSON 报告已保存: {out_path}{Colors.RESET}")

    html_path = OUTPUT_DIR / "hallucination_report.html"
    generate_html(report, str(html_path))
    print(f"{Colors.GREEN}✅ HTML 报告已保存: {html_path}{Colors.RESET}")


def generate_html(report: dict, output_path: str):
    rows = ""
    for r in report["results"]:
        s = r["summary"]
        color = "#3fb950" if s["mismatch"] == 0 else "#f85149"
        mismatch_claims = ""
        for cr in r["claims"]:
            if cr["verdict"] == "MISMATCH":
                mismatch_claims += f'<li style="color:#f85149">💀 {cr["claim"][:200]}<br><small>{cr["explanation"][:200]}</small></li>'
            elif cr["verdict"] == "MATCH":
                mismatch_claims += f'<li style="color:#3fb950">✅ {cr["claim"][:150]}</li>'

        rows += f"""<tr>
            <td>{r['query_id']}</td>
            <td>{r['description'][:60]}</td>
            <td style="color:{color};font-weight:bold">{s['match']} ✓</td>
            <td style="color:#f85149">{s['mismatch']} ✗</td>
            <td>{s['vague']}</td>
            <td style="font-size:12px;text-align:left"><ul>{mismatch_claims}</ul></td>
        </tr>"""

    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head><meta charset="UTF-8"><title>幻觉度量报告 — LLM-as-Judge</title>
<style>
body{{font-family:-apple-system,sans-serif;max-width:1200px;margin:40px auto;padding:0 20px;background:#0d1117;color:#c9d1d9}}
h1{{color:#58a6ff;border-bottom:1px solid #30363d;padding-bottom:10px}}
h2{{color:#f0883e}}
table{{width:100%;border-collapse:collapse;margin:20px 0}}
th,td{{border:1px solid #30363d;padding:10px 14px;text-align:center}}
th{{background:#161b22;color:#58a6ff}}
tr:hover{{background:#1c2129}}
.summary{{display:grid;grid-template-columns:repeat(4,1fr);gap:15px;margin:20px 0}}
.card{{background:#161b22;border:1px solid #30363d;border-radius:8px;padding:20px;text-align:center}}
.card .value{{font-size:28px;font-weight:bold}}
.card .label{{font-size:13px;color:#8b949e;margin-top:6px}}
.success{{color:#3fb950}}.danger{{color:#f85149}}.warning{{color:#d2991d}}
</style></head>
<body>
<h1>🔍 幻觉度量报告 — LLM-as-Judge</h1>
<p>Judge 模型: {report['model']} | 总查询数: {report['total_queries']}</p>

<div class="summary">
    <div class="card"><div class="value">{report['total_claims']}</div><div class="label">总声明数</div></div>
    <div class="card"><div class="value success">{report['match_count']}</div><div class="label">匹配声明</div></div>
    <div class="card"><div class="value danger">{report['mismatch_count']}</div><div class="label">幻觉声明</div></div>
    <div class="card"><div class="value danger">{report['hallucination_rate']}%</div><div class="label">幻觉率</div></div>
</div>

<h2>逐查询明细</h2>
<table>
<thead><tr><th>查询 ID</th><th>描述</th><th>匹配</th><th>幻觉</th><th>模糊</th><th>声明详情</th></tr></thead>
<tbody>{rows}</tbody>
</table>
</body></html>"""

    Path(output_path).write_text(html, encoding="utf-8")


if __name__ == "__main__":
    main()
