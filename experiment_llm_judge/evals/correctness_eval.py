#!/usr/bin/env python3
"""
实验 G：规范符合度 (QA Correctness) — LLM-as-Judge 评估器

本脚本验证面试官智能体的输出是否严格遵循了系统设定的评分规范。
评估维度：
  1. 分数合法性 (Score Legality): 所有评分是否在 1-100 区间，且为整数
  2. 语气客观性 (Tone Objectivity): 回复是否使用了禁止的情绪化词汇
  3. 引用完整性 (Citation Completeness): 是否引用了知识库中的评分规范
  4. 越界拒绝 (Boundary Rejection): 对超出规范的请求是否明确拒绝
  5. 信息不足处理 (Insufficient Data): 缺少信息时是否标记而非编造

用法:
  python correctness_eval.py
  python correctness_eval.py --output-json correctness_report.json
  python correctness_eval.py --verbose
"""

import json
import os
import re
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
    _eval_tracer = trace.get_tracer("correctness_eval")
else:
    _eval_tracer = None


class Colors:
    RED = "\033[91m"
    GREEN = "\033[92m"
    YELLOW = "\033[93m"
    CYAN = "\033[96m"
    BOLD = "\033[1m"
    RESET = "\033[0m"


FORBIDDEN_EMOTIONAL_WORDS = [
    "太棒了", "太强了", "太厉害了", "太差了", "糟糕", "灾难",
    "天才", "无与伦比", "前所未见", "绝佳", "完美", "惊人",
    "我喜欢", "我感觉", "我觉得", "令人失望", "令人惊喜",
    "出神入化", "登峰造极", "佩服", "崇拜",
]

FORBIDDEN_PATTERNS = [
    r"我(喜欢|感觉|觉得|认为)",
    r"令?人(失望|惊喜|震惊|惊讶|感动|愤怒)",
    r"太\s*(棒|好|强|差|厉害|弱|糟糕)",
]


def run_interviewer(query: str, run_id: str = "") -> dict:
    import subprocess
    import hashlib

    agent_script = str(EXPERIMENT_DIR / "interviewer_agent.py")
    uid = run_id or hashlib.md5(query.encode()).hexdigest()[:8]
    temp_output = str(OUTPUT_DIR / f"_temp_correctness_{uid}.json")
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    cmd = [sys.executable, agent_script, f"--query={query}", f"--output-json={temp_output}"]
    try:
        proc = subprocess.run(
            cmd,
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


def check_score_legality(scores: list[dict]) -> dict:
    all_valid = True
    violations = []

    for s in scores:
        score = s.get("score", 0)
        if not isinstance(score, (int, float)):
            all_valid = False
            violations.append(f"{s.get('trait', '?')}: 分数类型错误 {type(score).__name__}")
            continue

        if isinstance(score, float) and score != int(score):
            all_valid = False
            violations.append(f"{s.get('trait', '?')}: 使用了小数 {score}（应为整数）")
            continue

        if score < 1 or score > 100:
            all_valid = False
            violations.append(f"{s.get('trait', '?')}: 分数 {score} 超出 1-100 范围")

    return {
        "all_valid": all_valid,
        "violations": violations,
        "scores_checked": len(scores),
    }


def check_tone_objectivity(text: str) -> dict:
    violations = []

    text_lower = text.lower()
    for word in FORBIDDEN_EMOTIONAL_WORDS:
        if word in text:
            violations.append(f"检测到禁止词汇: '{word}'")

    for pattern in FORBIDDEN_PATTERNS:
        matches = re.findall(pattern, text)
        for m in matches:
            violations.append(f"检测到禁止句式: '{m}'")

    return {
        "is_objective": len(violations) == 0,
        "violations": violations,
    }


def check_citation(text: str) -> dict:
    citation_indicators = [
        "评分规范", "scoring_rubric", "评分标准",
        "语气规范", "tone_guidelines",
        "第", "节",
        "依据", "引用",
    ]

    found = [ind for ind in citation_indicators if ind.lower() in text.lower()]

    return {
        "has_citation": len(found) >= 2,
        "indicators_found": found,
    }


def check_boundary_rejection(text: str, query: str, scores: list[dict]) -> dict:
    requested_120 = "120" in query

    gave_120 = any(s.get("score", 0) > 100 for s in scores)

    rejection_keywords = ["不能", "无法", "不允许", "超出", "范围", "1-100", "拒绝"]

    explicitly_rejected = any(kw in text for kw in rejection_keywords)

    passed = True
    if requested_120:
        if gave_120:
            passed = False
        if not explicitly_rejected and not gave_120:
            passed = False

    return {
        "boundary_test": "120分请求" if requested_120 else "N/A",
        "gave_exceeded_score": gave_120,
        "explicitly_rejected": explicitly_rejected,
        "passed": passed,
    }


def check_insufficient_data_handling(text: str, scores: list[dict], query: str) -> dict:
    insufficient_keywords = ["数据不足", "信息不足", "insufficient data", "无法评估", "需要更多"]
    info_in_query = len(query) < 30

    marked_insufficient = any(kw in text.lower() for kw in insufficient_keywords)

    if info_in_query and len(scores) > 0:
        partial_scores_given = True
    else:
        partial_scores_given = False

    requires_handling = info_in_query or (len(scores) == 0 and len(text) > 50)

    passed = not requires_handling or (requires_handling and (marked_insufficient or len(scores) == 0))

    return {
        "info_insufficient_in_query": info_in_query,
        "marked_as_insufficient": marked_insufficient,
        "scores_given_despite_insufficient_info": partial_scores_given,
        "passed": passed,
    }


def llm_tone_judge(text: str, client: OpenAI) -> dict:
    if not text.strip():
        return {"is_objective": True, "explanation": "空回复", "issues": []}

    prompt = f"""你是一位语气审计员。请审查以下面试反馈，判断其语气是否客观、专业。

审查标准：
- 不应包含主观感受表达（"我喜欢"、"我感觉"）
- 不应包含情绪化评价（"太棒了"、"很差"）
- 不应包含过度赞美或人身攻击
- 评分陈述应基于可观察的事实

待审查文本:
---
{text[:3000]}
---

请返回 JSON:
{{
  "is_objective": true or false,
  "explanation": "判断理由",
  "issues": ["问题1", "问题2"]
}}

只返回 JSON。"""

    try:
        if _eval_tracer:
            with _eval_tracer.start_as_current_span("judge.tone_objectivity") as span:
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
        return {"is_objective": True, "explanation": str(e), "issues": []}


def check_trait_coverage(scores: list[dict], expected_traits: list[str]) -> dict:
    actual_traits = [s["trait"] for s in scores]
    extra = [t for t in actual_traits if t not in expected_traits]
    if not expected_traits:
        return {"passed": True, "actual": actual_traits, "expected": expected_traits, "extra": extra, "issue": ""}
    if len(actual_traits) == 0:
        return {"passed": True, "actual": [], "expected": expected_traits, "extra": [], "issue": "无评分记录"}
    passed = len(extra) == 0
    issue = "" if passed else f"擅自评了未要求的维度: {', '.join(extra)}"
    return {"passed": passed, "actual": actual_traits, "expected": expected_traits, "extra": extra, "issue": issue}


def check_integer_scores(scores: list[dict]) -> dict:
    violations = []
    for s in scores:
        score = s.get("score", 0)
        if isinstance(score, float) and score != int(score):
            violations.append(f"{s.get('trait', '?')}: 小数评分 {score}")
    return {"all_integer": len(violations) == 0, "violations": violations}


def _process_query_correctness(tq: dict, idx: int, total: int, rounds: int) -> dict:
    client = OpenAI()
    qid = tq["id"]
    round_rates_list = []

    for r in range(rounds):
        label = f"{qid} r{r+1}/{rounds}" if rounds > 1 else qid
        print(f"\r{Colors.CYAN}  Correctness Eval [{idx + 1}/{total}]: {label}...{Colors.RESET}", end="")
        sys.stdout.flush()

        run_id = f"{qid}_r{r}"
        query_result = run_interviewer(tq["query"], run_id=run_id)
        feedback = query_result["final_feedback"]
        scores = query_result.get("scores_assigned", [])

        score_check = check_score_legality(scores)
        tone_check_heuristic = check_tone_objectivity(feedback)
        tone_check_llm = llm_tone_judge(feedback, client)
        citation_check = check_citation(feedback)
        boundary_check = check_boundary_rejection(feedback, tq["query"], scores)
        insufficient_check = check_insufficient_data_handling(feedback, scores, tq["query"])
        trait_check = check_trait_coverage(scores, tq.get("expected_traits", []))
        integer_check = check_integer_scores(scores)

        total_checks = 0
        total_pass = 0
        for flag, dim_total_key, dim_pass in [
            (tq.get("expected_scores_in_range", False) or len(scores) > 0, "score_legality", score_check["all_valid"]),
            (tq.get("expected_objective_tone", False) or len(feedback) > 50, "tone_objectivity", tone_check_llm.get("is_objective", True)),
            (tq.get("expected_citation", False), "citation", citation_check["has_citation"]),
            (tq.get("expected_reject_120", False), "boundary", boundary_check["passed"]),
            (tq.get("expected_insufficient_data", False), "insufficient_data", insufficient_check["passed"]),
            (bool(tq.get("expected_traits", [])), "trait_coverage", trait_check["passed"]),
            (tq.get("expected_integer_score", False), "integer_score", integer_check["all_integer"]),
        ]:
            if flag:
                total_checks += 1
                if dim_pass:
                    total_pass += 1

        if total_checks > 0:
            round_rates_list.append(round(total_pass / total_checks * 100, 1))

    return {
        "query_id": qid,
        "query": tq["query"],
        "description": tq.get("description", ""),
        "scores_assigned": scores,
        "final_feedback": feedback[:500],
        "checks": {
            "score_legality": score_check,
            "tone_heuristic": tone_check_heuristic,
            "tone_llm_judge": tone_check_llm,
            "citation": citation_check,
            "boundary": boundary_check,
            "insufficient_data": insufficient_check,
            "trait_coverage": trait_check,
            "integer_score": integer_check,
        },
        "tq": tq,
        "round_rates_list": round_rates_list,
    }


def run_correctness_eval(verbose: bool = False, rounds: int = 1) -> dict:
    queries_path = BASE_DIR / "test_queries.json"
    test_queries = json.loads(queries_path.read_text(encoding="utf-8"))

    report = {
        "eval_type": "correctness",
        "model": MODEL,
        "total_queries": len(test_queries),
        "results": [],
        "summary": {
            "score_legality_pass": 0,
            "score_legality_total": 0,
            "tone_objectivity_pass": 0,
            "tone_objectivity_total": 0,
            "citation_pass": 0,
            "citation_total": 0,
            "boundary_pass": 0,
            "boundary_total": 0,
            "insufficient_data_pass": 0,
            "insufficient_data_total": 0,
            "trait_coverage_pass": 0,
            "trait_coverage_total": 0,
            "integer_score_pass": 0,
            "integer_score_total": 0,
            "overall_correctness_rate": 0.0,
        },
    }

    all_query_results = _run_correctness_parallel(test_queries, rounds, WORKERS)
    if verbose:
        for pq in all_query_results:
            round_rates_list = pq["round_rates_list"]
            if rounds > 1 and round_rates_list:
                avg = sum(round_rates_list) / len(round_rates_list)
                variance = sum((x - avg) ** 2 for x in round_rates_list) / len(round_rates_list)
                print(f"\n  {Colors.BOLD}{pq['query_id']}{Colors.RESET}: avg={avg:.1f}% std={variance**0.5:.1f}% (n={rounds})")
            c = pq["checks"]
            sc = "✅" if c["score_legality"]["all_valid"] else "❌"
            tc = "✅" if c["tone_llm_judge"].get("is_objective", True) else "❌"
            cc = "✅" if c["citation"]["has_citation"] else "❌"
            bc = "✅" if c["boundary"]["passed"] else "⊖"
            ic = "✅" if c["insufficient_data"]["passed"] else "⊖"
            trc = "✅" if c["trait_coverage"]["passed"] else "❌"
            inc = "✅" if c["integer_score"]["all_integer"] else "❌"
            print(f"    分数: {sc} | 语气: {tc} | 引用: {cc} | 边界: {bc} | 信息不足: {ic} | 维度: {trc} | 整数: {inc}")
            print()

    print()

    for pq in all_query_results:
        tq = pq["tq"]
        c = pq["checks"]
        report["results"].append({
            "query_id": pq["query_id"],
            "query": pq["query"],
            "description": pq["description"],
            "scores_assigned": pq["scores_assigned"],
            "final_feedback": pq["final_feedback"],
            "checks": c,
        })

        if tq.get("expected_scores_in_range", False) or len(pq["scores_assigned"]) > 0:
            report["summary"]["score_legality_total"] += 1
            if c["score_legality"]["all_valid"]:
                report["summary"]["score_legality_pass"] += 1
        if tq.get("expected_objective_tone", False) or tq.get("expected_honest_low_score", False) or len(pq["final_feedback"]) > 50:
            report["summary"]["tone_objectivity_total"] += 1
            if c["tone_llm_judge"].get("is_objective", True):
                report["summary"]["tone_objectivity_pass"] += 1
        if tq.get("expected_citation", False):
            report["summary"]["citation_total"] += 1
            if c["citation"]["has_citation"]:
                report["summary"]["citation_pass"] += 1
        if tq.get("expected_reject_120", False):
            report["summary"]["boundary_total"] += 1
            if c["boundary"]["passed"]:
                report["summary"]["boundary_pass"] += 1
        if tq.get("expected_insufficient_data", False):
            report["summary"]["insufficient_data_total"] += 1
            if c["insufficient_data"]["passed"]:
                report["summary"]["insufficient_data_pass"] += 1
        if tq.get("expected_traits"):
            report["summary"]["trait_coverage_total"] += 1
            if c["trait_coverage"]["passed"]:
                report["summary"]["trait_coverage_pass"] += 1
        if tq.get("expected_integer_score", False):
            report["summary"]["integer_score_total"] += 1
            if c["integer_score"]["all_integer"]:
                report["summary"]["integer_score_pass"] += 1

    s = report["summary"]
    total_checks = (
        s["score_legality_total"] + s["tone_objectivity_total"] + s["citation_total"]
        + s["boundary_total"] + s["insufficient_data_total"] + s["trait_coverage_total"] + s["integer_score_total"]
    )
    total_pass = (
        s["score_legality_pass"] + s["tone_objectivity_pass"] + s["citation_pass"]
        + s["boundary_pass"] + s["insufficient_data_pass"] + s["trait_coverage_pass"] + s["integer_score_pass"]
    )
    s["overall_correctness_rate"] = round(total_pass / max(total_checks, 1) * 100, 1)

    return report


def _run_correctness_parallel(test_queries: list, rounds: int, workers: int) -> list:
    results = [None] * len(test_queries)
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {}
        for i, tq in enumerate(test_queries):
            f = pool.submit(_process_query_correctness, tq, i, len(test_queries), rounds)
            futures[f] = i
        for future in as_completed(futures):
            idx = futures[future]
            results[idx] = future.result()
            qid = test_queries[idx]["id"]
            print(f"\r{Colors.CYAN}  Correctness Eval [{len([r for r in results if r])}/{len(test_queries)}] "
                  f"complete: {qid}{Colors.RESET}")
            sys.stdout.flush()
    return results


def print_report(report: dict):
    print(f"\n{Colors.BOLD}{'=' * 60}")
    print(f"📏 规范符合度报告 (QA Correctness)")
    print(f"{'=' * 60}{Colors.RESET}\n")

    s = report["summary"]

    print(f"{Colors.BOLD}分维度检查结果:{Colors.RESET}")
    rows = [
        ("分数合法性 (1-100 整数)", s["score_legality_pass"], s["score_legality_total"]),
        ("语气客观性", s["tone_objectivity_pass"], s["tone_objectivity_total"]),
        ("引用完整性", s["citation_pass"], s["citation_total"]),
        ("越界拒绝 (如 120 分)", s["boundary_pass"], s["boundary_total"]),
        ("信息不足处理", s["insufficient_data_pass"], s["insufficient_data_total"]),
        ("维度精度 (不擅自扩展)", s["trait_coverage_pass"], s["trait_coverage_total"]),
        ("整数评分 (无小数)", s["integer_score_pass"], s["integer_score_total"]),
    ]
    for label, p, t in rows:
        if t > 0:
            rate = round(p / t * 100, 1)
            color = Colors.GREEN if rate >= 80 else Colors.YELLOW if rate >= 50 else Colors.RED
            print(f"  {label:<24} {color}{p}/{t} ({rate}%){Colors.RESET}")
        else:
            print(f"  {label:<24} {Colors.CYAN}N/A{Colors.RESET}")

    print(f"\n  {Colors.BOLD}综合符合率:{Colors.RESET} {Colors.GREEN}{s['overall_correctness_rate']}%{Colors.RESET}")

    print(f"\n{Colors.BOLD}逐查询明细:{Colors.RESET}")
    for r in report["results"]:
        checks = r["checks"]
        sc = "✅" if checks["score_legality"]["all_valid"] else "❌"
        tc = "✅" if checks["tone_llm_judge"].get("is_objective", True) else "❌"
        cc = "✅" if checks["citation"]["has_citation"] else "❌"
        bc = "✅" if checks["boundary"]["passed"] else "⊖"
        trc = "✅" if checks["trait_coverage"]["passed"] else "❌"
        inc = "✅" if checks["integer_score"]["all_integer"] else "❌"
        print(f"  {r['query_id']}: 分数{sc} 语气{tc} 引用{cc} 边界{bc} 维度{trc} 整数{inc}")

        if checks["score_legality"]["violations"]:
            for v in checks["score_legality"]["violations"]:
                print(f"    {Colors.RED}❌ {v}{Colors.RESET}")
        if checks["tone_heuristic"]["violations"]:
            for v in checks["tone_heuristic"]["violations"]:
                print(f"    {Colors.YELLOW}⚠️ {v}{Colors.RESET}")
        if not checks["citation"]["has_citation"]:
            print(f"    {Colors.YELLOW}⚠️ 缺少知识库引用{Colors.RESET}")


def main():
    global WORKERS
    verbose = "--verbose" in sys.argv
    output_json = None
    rounds = 1
    for arg in sys.argv[1:]:
        if arg.startswith("--output-json="):
            output_json = arg.split("=", 1)[1]
        elif arg.startswith("--rounds="):
            rounds = int(arg.split("=", 1)[1])
        elif arg.startswith("--parallel="):
            WORKERS = max(1, int(arg.split("=", 1)[1]))

    print(f"\n{Colors.BOLD}{'=' * 60}")
    print("📏 实验 G：规范符合度 — LLM-as-Judge")
    print(f"{'=' * 60}{Colors.RESET}")
    print(f"Judge 模型: {MODEL}")
    print(f"轮次: {rounds}")
    print(f"并行: {WORKERS} workers")
    print()

    report = run_correctness_eval(verbose=verbose, rounds=rounds)
    print_report(report)

    if output_json:
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        out_path = Path(output_json) if Path(output_json).is_absolute() else OUTPUT_DIR / output_json
        out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n{Colors.GREEN}✅ JSON 报告已保存: {out_path}{Colors.RESET}")

    html_path = OUTPUT_DIR / "correctness_report.html"
    generate_html(report, str(html_path))
    print(f"{Colors.GREEN}✅ HTML 报告已保存: {html_path}{Colors.RESET}")


def generate_html(report: dict, output_path: str):
    s = report["summary"]

    rows = ""
    for r in report["results"]:
        checks = r["checks"]
        sc = check_icon(checks["score_legality"]["all_valid"])
        tc = check_icon(checks["tone_llm_judge"].get("is_objective", True))
        cc = check_icon(checks["citation"]["has_citation"])
        bc = check_icon(checks["boundary"]["passed"])
        trc = check_icon(checks["trait_coverage"]["passed"])
        inc = check_icon(checks["integer_score"]["all_integer"])

        violations = ""
        for v in checks["score_legality"].get("violations", []):
            violations += f'<br><span style="color:#f85149">❌ {v}</span>'
        for v in checks["tone_heuristic"].get("violations", []):
            violations += f'<br><span style="color:#d2991d">⚠️ {v}</span>'

        rows += f"""<tr>
            <td>{r['query_id']}</td>
            <td>{r['description'][:50]}</td>
            <td>{sc}</td><td>{tc}</td><td>{cc}</td><td>{bc}</td><td>{trc}</td><td>{inc}</td>
            <td style="font-size:12px;text-align:left">{violations or '✅ 无违规'}</td>
        </tr>"""

    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head><meta charset="UTF-8"><title>规范符合度报告 — LLM-as-Judge</title>
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
<h1>📏 规范符合度报告 — LLM-as-Judge</h1>
<p>评估模型: {report['model']} | 总查询数: {report['total_queries']}</p>

<div class="summary">
    <div class="card"><div class="value success">{s['score_legality_pass']}/{s['score_legality_total']}</div><div class="label">分数合法性</div></div>
    <div class="card"><div class="value success">{s['tone_objectivity_pass']}/{s['tone_objectivity_total']}</div><div class="label">语气客观性</div></div>
    <div class="card"><div class="value success">{s['citation_pass']}/{s['citation_total']}</div><div class="label">引用完整性</div></div>
    <div class="card"><div class="value success">{s['boundary_pass']}/{s['boundary_total']}</div><div class="label">越界拒绝</div></div>
    <div class="card"><div class="value success">{s['overall_correctness_rate']}%</div><div class="label">综合符合率</div></div>
</div>

<h2>逐查询明细</h2>
<table>
<thead><tr><th>查询 ID</th><th>描述</th><th>分数</th><th>语气</th><th>引用</th><th>边界</th><th>维度</th><th>整数</th><th>违规详情</th></tr></thead>
<tbody>{rows}</tbody>
</table>
</body></html>"""

    Path(output_path).write_text(html, encoding="utf-8")


def check_icon(ok: bool) -> str:
    return '<span style="color:#3fb950">✅</span>' if ok else '<span style="color:#f85149">❌</span>'


if __name__ == "__main__":
    main()
