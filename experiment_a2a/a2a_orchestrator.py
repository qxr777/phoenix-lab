#!/usr/bin/env python3
"""
A2A 多智能体编排器 — 分布式追溯与性能瓶颈优化实验

本脚本是一个多智能体系统的编排入口。它按拓扑顺序协调以下 Agent:
  Gateway → Planner → [Executor × N] → Reviewer

每个 Agent 之间的通信通过 Python 函数调用来完成，
并通过 OpenTelemetry 手动创建 Span 标记 Agent 边界。

拓扑:
  user_request
    └── gateway.route
          └── planner.decompose
                ├── executor_search.search      (正常基线 ~1.5s)
                ├── executor_code.code_review   (重试陷阱 ~10s)
                └── executor_data.data_analysis (高TTFT+慢工具 ~5.6s)
                      └── reviewer.assemble     (大上下文)

用法:
  python a2a_orchestrator.py
  python a2a_orchestrator.py --task code_review_task
  python a2a_orchestrator.py --task all --output-json results.json
"""

import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
SMALL_MODEL = os.getenv("OPENAI_SMALL_MODEL", MODEL)  # 轻量模型，用于 Gateway/Reviewer
BASE_DIR = Path(__file__).parent
OUTPUT_DIR = BASE_DIR / "output"

PHOENIX_COLLECTOR_ENDPOINT = os.getenv("PHOENIX_COLLECTOR_ENDPOINT", "http://127.0.0.1:6006/v1/traces")
PHOENIX_ENABLED = os.getenv("ENABLE_PHOENIX_TRACING", "").lower() in ("true", "1", "yes")

if PHOENIX_ENABLED:
    from opentelemetry import trace
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor

    trace.set_tracer_provider(TracerProvider())
    trace.get_tracer_provider().add_span_processor(
        SimpleSpanProcessor(OTLPSpanExporter(endpoint=PHOENIX_COLLECTOR_ENDPOINT))
    )
    tracer = trace.get_tracer("a2a_orchestrator")

    from openinference.instrumentation.openai import OpenAIInstrumentor
    OpenAIInstrumentor().instrument()
else:
    from opentelemetry import trace as _trace
    from contextlib import contextmanager

    class _NoopSpan:
        def set_attribute(self, key, value): pass
        def end(self): pass

    class _NoopTracer:
        @contextmanager
        def start_as_current_span(self, name, attributes=None):
            yield _NoopSpan()

    tracer = _NoopTracer()


from experiment_a2a.agents.gateway import run_gateway
from experiment_a2a.agents.planner import run_planner
from experiment_a2a.agents.executor_search import run_executor_search
from experiment_a2a.agents.executor_code import run_executor_code, reset_retry_counter
from experiment_a2a.agents.executor_data import run_executor_data
from experiment_a2a.agents.reviewer import run_reviewer

if SMALL_MODEL != MODEL:
    _small_client = OpenAI(
        base_url=os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1"),
        api_key=os.getenv("OPENAI_API_KEY", "not-needed"),
    )
else:
    _small_client = None


class Colors:
    RED = "\033[91m"
    GREEN = "\033[92m"
    YELLOW = "\033[93m"
    CYAN = "\033[96m"
    MAGENTA = "\033[95m"
    BOLD = "\033[1m"
    RESET = "\033[0m"


def run_a2a_pipeline(task: dict, client: OpenAI, optimize: bool = False, verbose: bool = True) -> dict:
    user_query = task["user_query"]
    task_id = task["id"]
    task_name = task.get("name", task_id)

    if verbose:
        print(f"\n{Colors.BOLD}{'─' * 60}{Colors.RESET}")
        print(f"{Colors.BOLD}🚀 A2A Pipeline: {task_name}{Colors.RESET}")
        print(f"{Colors.BOLD}{'─' * 60}{Colors.RESET}")
        print(f"查询: {user_query[:80]}...\n")

    trace_data = {
        "task_id": task_id,
        "task_name": task_name,
        "user_query": user_query,
        "total_latency_ms": 0,
        "total_tokens_in": 0,
        "total_tokens_out": 0,
        "agent_traces": [],
    }

    pipeline_start = time.time()

    # ── Root Span: wraps entire pipeline so all child spans share one Trace ID ──
    with tracer.start_as_current_span("a2a.pipeline", attributes={"task_id": task_id, "task_name": task_name}) as root_span:

        # ── Layer 1: Gateway ──
        if verbose:
            print(f"{Colors.CYAN}  [1/4] Gateway: 解析请求...{Colors.RESET}", end=" ")
            sys.stdout.flush()

        with tracer.start_as_current_span("gateway.route") as span:
            if span is not None:
                span.set_attribute("task_id", task_id)
                span.set_attribute("optimize", optimize)
            gw_client = _small_client if (optimize and _small_client) else client
            gateway_result = run_gateway(user_query, gw_client)

        trace_data["agent_traces"].append(gateway_result)
        if verbose:
            print(f"{Colors.GREEN}✓{Colors.RESET} ({gateway_result['latency_ms']}ms)")

        # ── Layer 2: Planner ──
        if optimize and "subtasks" in task:
            if verbose:
                print(f"{Colors.CYAN}  [2/4] Planner: [跳过 — 使用硬编码子任务]{Colors.RESET}", end=" ")
                sys.stdout.flush()
            subtasks = task["subtasks"]
            plan = {"overall_plan": "使用预定义子任务", "subtasks": subtasks}
            planner_result = {
                "agent": "planner",
                "plan": plan,
                "latency_ms": 0,
                "token_data": {"agent": "planner", "prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
            }
            if verbose:
                print(f"{Colors.GREEN}✓{Colors.RESET} (0ms — skipped, {len(subtasks)} 个子任务)")
        else:
            if verbose:
                print(f"{Colors.CYAN}  [2/4] Planner: 分解任务...{Colors.RESET}", end=" ")
                sys.stdout.flush()
            with tracer.start_as_current_span("planner.decompose") as span:
                if span is not None:
                    span.set_attribute("task_id", task_id)
                planner_result = run_planner(user_query, gateway_result.get("routing", {}), client, optimize=optimize)
            plan = planner_result.get("plan", {})
            subtasks = plan.get("subtasks", [])
            if verbose:
                print(f"{Colors.GREEN}✓{Colors.RESET} ({planner_result['latency_ms']}ms, {len(subtasks)} 个子任务)")

        trace_data["agent_traces"].append(planner_result)

        # ── Layer 3: Executors ──
        if verbose:
            mode_str = "并行" if optimize else "串行"
            print(f"{Colors.CYAN}  [3/4] Executors: {mode_str}执行 {len(subtasks)} 个子任务...{Colors.RESET}")
            sys.stdout.flush()

        executor_results = [None] * len(subtasks)
        executor_start = time.time()

        def _run_one(idx, subtask):
            stype = subtask.get("type", "search")
            st_id = subtask.get("id", f"subtask_{idx+1}")
            span_name = f"executor.{stype}"
            with tracer.start_as_current_span(span_name) as span:
                if span is not None:
                    span.set_attribute("subtask_id", st_id)
                    span.set_attribute("subtask_type", stype)
                if stype in ("code_review", "code_generate"):
                    reset_retry_counter()
                    return idx, st_id, stype, run_executor_code(subtask, client, optimize=optimize)
                elif stype in ("data_analysis", "report_generate", "transform"):
                    return idx, st_id, stype, run_executor_data(subtask, client, optimize=optimize)
                else:
                    return idx, st_id, stype, run_executor_search(subtask, client)

        if optimize and len(subtasks) > 1:
            if PHOENIX_ENABLED:
                from opentelemetry import context as otel_context
                ctx = otel_context.get_current()
            else:
                ctx = None
            with ThreadPoolExecutor(max_workers=len(subtasks)) as pool:
                def _run_with_context(idx, subtask):
                    if ctx is not None:
                        from opentelemetry import context as otel_ctx
                        otel_ctx.attach(ctx)
                    return _run_one(idx, subtask)
                futures = {pool.submit(_run_with_context, i, st): i for i, st in enumerate(subtasks)}
                for future in as_completed(futures):
                    idx, st_id, stype, result = future.result()
                    executor_results[idx] = result
                    status = "✓" if result.get("output") else "✗"
                    retry_info = f", {result.get('retry_count', 0)} retries" if result.get("retry_count", 0) > 1 else ""
                    if verbose:
                        print(f"         ├─ {st_id} ({stype})... {Colors.GREEN}{status}{Colors.RESET} ({result['latency_ms']}ms{retry_info})")
        else:
            for i, subtask in enumerate(subtasks):
                idx, st_id, stype, result = _run_one(i, subtask)
                executor_results[idx] = result
                status = "✓" if result.get("output") else "✗"
                retry_info = f", {result.get('retry_count', 0)} retries" if result.get("retry_count", 0) > 1 else ""
                if verbose:
                    print(f"         ├─ {st_id} ({stype})... {Colors.GREEN}{status}{Colors.RESET} ({result['latency_ms']}ms{retry_info})")

        executor_duration = round((time.time() - executor_start) * 1000)
        serial_total = sum(r["latency_ms"] for r in executor_results if r is not None)
        trace_data["agent_traces"].extend([r for r in executor_results if r is not None])
        trace_data["executor_serial_total_ms"] = serial_total

        if verbose:
            if len(subtasks) > 1:
                mode = "并行" if (optimize and len(subtasks) > 1) else "串行"
                alt_mode = "串行" if mode == "并行" else "并行"
                saved = serial_total - executor_duration
                saved_pct = round(saved / max(serial_total, 1) * 100, 1)
                print(f"         └─ {mode}耗时: {executor_duration}ms  ({alt_mode}需: {serial_total}ms, 节省: {saved}ms / {saved_pct}%)")
            else:
                print(f"         └─ 耗时: {executor_duration}ms")

        # ── Layer 4: Reviewer ──
        if verbose:
            print(f"{Colors.CYAN}  [4/4] Reviewer: 审核 + 拼装...{Colors.RESET}", end=" ")
            sys.stdout.flush()

        if optimize:
            for eo in executor_results:
                full_out = eo.get("output", "")
                eo["output"] = full_out[:300] + ("..." if len(full_out) > 300 else "")
            if verbose:
                print(f"(上下文已裁剪)", end=" ")
            sys.stdout.flush()

        with tracer.start_as_current_span("reviewer.assemble") as span:
            if span is not None:
                span.set_attribute("task_id", task_id)
                span.set_attribute("executor_count", len(executor_results))
            rv_client = _small_client if (optimize and _small_client) else client
            reviewer_result = run_reviewer(user_query, plan, executor_results, rv_client)

        trace_data["agent_traces"].append(reviewer_result)
        if verbose:
            print(f"{Colors.GREEN}✓{Colors.RESET} ({reviewer_result['latency_ms']}ms)")

    # ── End of root span ──

    pipeline_end = time.time()
    trace_data["total_latency_ms"] = round((pipeline_end - pipeline_start) * 1000)
    trace_data["executor_duration_ms"] = executor_duration

    for t in trace_data["agent_traces"]:
        td = t.get("token_data", {})
        trace_data["total_tokens_in"] += td.get("prompt_tokens", 0)
        trace_data["total_tokens_out"] += td.get("completion_tokens", 0)

    if verbose:
        print(f"\n{Colors.BOLD}  总结:{Colors.RESET}")
        print(f"    总延迟: {Colors.MAGENTA}{trace_data['total_latency_ms']}ms{Colors.RESET}")
        print(f"    Token in: {trace_data['total_tokens_in']}, out: {trace_data['total_tokens_out']}")
        print(f"    Bottleneck: {Colors.RED}{task.get('bottleneck', 'N/A')}{Colors.RESET}")
        print(f"    Teaching point: {task.get('teaching_point', 'N/A')}")

    return trace_data


def load_tasks() -> list[dict]:
    tasks_path = BASE_DIR / "scenarios" / "tasks.json"
    return json.loads(tasks_path.read_text(encoding="utf-8"))


def main():
    client = OpenAI()

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    task_id = None
    output_json = None
    optimize = False
    for arg in sys.argv[1:]:
        if arg.startswith("--task="):
            task_id = arg.split("=", 1)[1]
        elif arg.startswith("--output-json="):
            output_json = arg.split("=", 1)[1]
        elif arg == "--optimize":
            optimize = True

    all_tasks = load_tasks()

    print(f"\n{Colors.BOLD}{'=' * 60}")
    print("🔗 A2A 多智能体编排器")
    print(f"{'=' * 60}{Colors.RESET}")
    print(f"模型: {MODEL}")
    print(f"Phoenix 遥测: {'已启用' if PHOENIX_ENABLED else '未启用'}")
    print(f"优化模式: {'已启用' if optimize else '未启用'}")
    print(f"可用场景: {len(all_tasks)} 个\n")

    if task_id and task_id != "all":
        tasks_to_run = [t for t in all_tasks if t["id"] == task_id]
        if not tasks_to_run:
            print(f"{Colors.RED}未找到任务: {task_id}{Colors.RESET}")
            print(f"可用: {[t['id'] for t in all_tasks]}")
            sys.exit(1)
    else:
        tasks_to_run = all_tasks

    all_results = []
    for task in tasks_to_run:
        result = run_a2a_pipeline(task, client, optimize=optimize)
        all_results.append(result)
        print()

    if output_json:
        output_path = Path(output_json) if Path(output_json).is_absolute() else OUTPUT_DIR / output_json
        output_path.write_text(json.dumps(all_results, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"{Colors.GREEN}✅ 结果已保存: {output_path}{Colors.RESET}")
    else:
        trace_path = OUTPUT_DIR / "a2a_latest_trace.json"
        trace_path.write_text(json.dumps(all_results, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"{Colors.GREEN}✅ Trace 数据已保存: {trace_path}{Colors.RESET}")


if __name__ == "__main__":
    main()
