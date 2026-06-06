#!/usr/bin/env python3
"""
CS599 实验启动器：A2A 多智能体分布式追溯与性能瓶颈优化实验

本脚本是实验的统一入口，按顺序引导学生完成四个实验阶段：
  I: 构建多智能体系统 — 搭建 Gateway → Planner → Executors → Reviewer 拓扑
  J: 关键路径分析 — 从 Phoenix span 中识别 Critical Path 和性能瓶颈
  K: Token 成本控制 — 分析上下文膨胀曲线，给出裁剪建议
  L: 性能优化回测 — 对比优化前后的延迟和 Token 消耗

用法:
  python run_experiments.py                # 交互式菜单
  python run_experiments.py --experiment I # 直接运行实验 I
  python run_experiments.py --experiment J # 直接运行实验 J
  python run_experiments.py --all          # 运行全部实验（非交互）
"""

import os
import subprocess
import sys
from pathlib import Path

_VENV_DIR = Path(__file__).parent.parent / "venv"
_VENV_PYTHON = _VENV_DIR / "bin" / "python"

if _VENV_PYTHON.exists() and sys.executable != str(_VENV_PYTHON):
    _os = __import__("os")
    print(f"\033[93m[提示] 检测到 venv: {_VENV_DIR}")
    print(f"  当前 Python: {sys.executable}")
    print(f"  venv Python:  {_VENV_PYTHON}")
    print(f"  正在切换到 venv 重新执行...\033[0m\n")
    _os.execv(str(_VENV_PYTHON), [str(_VENV_PYTHON)] + sys.argv)
    sys.exit(0)

BASE_DIR = Path(__file__).parent.parent
EXPERIMENT_DIR = Path(__file__).parent

from dotenv import load_dotenv
load_dotenv(BASE_DIR / ".env")


class Colors:
    RED = "\033[91m"
    GREEN = "\033[92m"
    YELLOW = "\033[93m"
    BLUE = "\033[94m"
    MAGENTA = "\033[95m"
    CYAN = "\033[96m"
    BOLD = "\033[1m"
    RESET = "\033[0m"


def print_banner():
    print(f"""
{Colors.MAGENTA}{Colors.BOLD}╔══════════════════════════════════════════════════════════╗
║  CS599 实验 4：A2A 多智能体分布式追溯                        ║
║  关键路径分析 + Token 成本控制 + Phoenix 拓扑树               ║
║  APM 思维 × AI 工程 = 企业级性能调优                         ║
╚══════════════════════════════════════════════════════════╝{Colors.RESET}
""")


def run_cmd(cmd: list[str], description: str = "", timeout: int = 900) -> bool:
    if description:
        print(f"\n{Colors.CYAN}▶ {description}{Colors.RESET}")
    print(f"  执行: {' '.join(cmd)}")
    try:
        proc = subprocess.run(cmd, cwd=str(BASE_DIR), text=True, timeout=timeout)
        return proc.returncode == 0
    except subprocess.TimeoutExpired:
        print(f"  {Colors.RED}超时！{Colors.RESET}")
        return False
    except KeyboardInterrupt:
        print(f"\n  {Colors.YELLOW}已取消{Colors.RESET}")
        return False


def check_prerequisites() -> bool:
    print(f"\n{Colors.BOLD}环境检查...{Colors.RESET}")

    dotenv_path = BASE_DIR / ".env"
    if dotenv_path.exists():
        print(f"  .env 配置: {Colors.GREEN}✓{Colors.RESET}")
    else:
        print(f"  .env 配置: {Colors.RED}✗ 缺失{Colors.RESET}")
        return False

    llm_key = os.getenv("OPENAI_API_KEY", "")
    llm_base = os.getenv("OPENAI_BASE_URL", "")
    if llm_key and llm_base:
        print(f"  LLM 服务: {Colors.GREEN}✓{Colors.RESET} ({llm_base})")
    else:
        print(f"  LLM 服务: {Colors.YELLOW}⚠ 未配置{Colors.RESET}")
        return False

    phoenix_enabled = os.getenv("ENABLE_PHOENIX_TRACING", "").lower() in ("true", "1", "yes")
    if phoenix_enabled:
        print(f"  Phoenix 遥测: {Colors.GREEN}已启用{Colors.RESET}")
        try:
            import urllib.request
            phoenix_url = os.getenv("PHOENIX_COLLECTOR_ENDPOINT", "http://127.0.0.1:6006/v1/traces")
            host = phoenix_url.replace("/v1/traces", "").rstrip("/")
            urllib.request.urlopen(f"{host}/health", timeout=3)
            print(f"  Phoenix 连接: {Colors.GREEN}✓{Colors.RESET} ({host})")
        except Exception:
            print(f"  Phoenix 连接: {Colors.YELLOW}⚠ 未运行{Colors.RESET}")
    else:
        print(f"  Phoenix 遥测: {Colors.YELLOW}未启用{Colors.RESET}")

    tasks = EXPERIMENT_DIR / "scenarios" / "tasks.json"
    print(f"  场景文件: {Colors.GREEN}✓{Colors.RESET}" if tasks.exists() else f"  场景文件: {Colors.RED}✗{Colors.RESET}")

    return True


def experiment_I_build_system():
    print(f"\n{Colors.MAGENTA}{Colors.BOLD}{'=' * 60}")
    print("实验 I：构建多智能体系统")
    print(f"{'=' * 60}{Colors.RESET}")
    print("""
搭建一个四层多智能体协作系统：

拓扑:
  Gateway (前端网关) → Planner (规划智能体) → Executors × N (执行子智能体) → Reviewer (审核智能体)

每个 Agent 的调用都通过 OTEL span 标记，所有 trace 发往 Phoenix。

三个预定义场景:
  1. code_review_task  — 代码审查（触发 executor_code 的 3 次重试陷阱）
  2. data_report_task  — 数据周报（触发 executor_data 的高 TTFT + 慢工具）
  3. multi_search_task — 多方案对比（触发并联木桶效应 + reviewer 上下文膨胀）
""")

    print(f"{Colors.CYAN}运行所有 3 个场景...{Colors.RESET}\n")
    cmd = [
        sys.executable, "experiment_a2a/a2a_orchestrator.py",
        "--task=all",
        f"--output-json={EXPERIMENT_DIR / 'output' / 'a2a_latest_trace.json'}",
    ]
    run_cmd(cmd, "运行 A2A 多智能体编排器", timeout=600)

    print(f"\n{Colors.YELLOW}💡 下一步:{Colors.RESET}")
    print("  - 运行实验 J 进行关键路径分析")
    print("  - 打开 Phoenix UI (http://localhost:6006) 查看拓扑树")


def experiment_J_critical_path():
    print(f"\n{Colors.RED}{Colors.BOLD}{'=' * 60}")
    print("实验 J：关键路径分析 (Critical Path)")
    print(f"{'=' * 60}{Colors.RESET}")
    print("""
核心概念: 分布式 APM × AI

像 Jaeger/Zipkin 分析微服务调用链一样，分析多智能体的执行时序:
  1. 从进程内 trace 和 Phoenix API 两个数据源获取 span 数据
  2. 构建 span 树，计算每个 agent 的延迟和占比
  3. 标注 Critical Path = 从根到叶的最长路径
  4. 揪出瓶颈 (bottleneck) 并进行占比分析

教学价值:
  - 将微服务 APM 的 Critical Path 概念平移到 AI Agent 系统
  - 理解"重试风暴"和"高 TTFT"对端到端延迟的影响
  - 学会从 Phoenix 拓扑树中直观定位性能瓶颈
""")

    trace_file = str(EXPERIMENT_DIR / "output" / "a2a_latest_trace.json")
    if not Path(trace_file).exists():
        print(f"{Colors.YELLOW}⚠️  未找到 trace 文件，先运行实验 I...{Colors.RESET}\n")
        experiment_I_build_system()

    json_path = str(EXPERIMENT_DIR / "output" / "critical_path_report.json")
    cmd = [
        sys.executable, "experiment_a2a/analysis/critical_path.py",
        f"--trace-file={trace_file}",
        f"--output-json={json_path}",
    ]
    run_cmd(cmd, "运行关键路径分析")

    print(f"\n{Colors.GREEN}✅ 分析完成!{Colors.RESET}")
    print(f"  HTML 报告: {EXPERIMENT_DIR / 'output' / 'critical_path_report.html'}")


def experiment_K_token_control():
    print(f"\n{Colors.GREEN}{Colors.BOLD}{'=' * 60}")
    print("实验 K：Token 成本控制")
    print(f"{'=' * 60}{Colors.RESET}")
    print("""
核心概念: 上下文窗口膨胀

随着多智能体协作轮次增加，每个 agent 的 system prompt + 历史消息 + 工具输出
会累积到 context window 中，导致:
  1. Token 消耗线性（甚至指数）增长
  2. 延迟随之增加（更大的 context = 更慢的推理）
  3. 上下文利用率下降（大量冗余信息）

分析维度:
  - Token 增长曲线: 按轮次统计 prompt/completion tokens
  - 上下文利用率 = Completion / Prompt（有效输出占比）
  - 给出裁剪建议: 哪些轮次的上下文可以安全裁剪
""")

    trace_file = str(EXPERIMENT_DIR / "output" / "a2a_latest_trace.json")
    if not Path(trace_file).exists():
        print(f"{Colors.YELLOW}⚠️  未找到 trace 文件，先运行实验 I...{Colors.RESET}\n")
        experiment_I_build_system()

    json_path = str(EXPERIMENT_DIR / "output" / "token_report.json")
    cmd = [
        sys.executable, "experiment_a2a/analysis/token_analyzer.py",
        f"--trace-file={trace_file}",
        f"--output-json={json_path}",
    ]
    run_cmd(cmd, "运行 Token 成本分析")

    print(f"\n{Colors.GREEN}✅ 分析完成!{Colors.RESET}")
    print(f"  HTML 报告: {EXPERIMENT_DIR / 'output' / 'token_report.html'}")


def experiment_L_optimization():
    print(f"\n{Colors.BLUE}{Colors.BOLD}{'=' * 60}")
    print("实验 L：性能优化回测")
    print(f"{'=' * 60}{Colors.RESET}")
    print("""
基于实验 J（关键路径）和实验 K（Token 分析）的发现，
通过独立运行对比三项优化的实际效果。

优化项（共 6 项）:
  1. System Prompt 精简: planner/executor_data 从 ~800 → ~100 tokens
  2. 上下文裁剪: reviewer 只接收 executor 前 300 字输出
  3. 消除重试: executor_code 跳过 2 次失败重试 + 工具延迟
  4. 并行执行: executors 从串行 for 改为 ThreadPoolExecutor 真并行
  5. 去除工具模拟延迟: executor_code/executor_data 的 time.sleep() 移除
  6. 模型分级: Gateway/Reviewer 使用轻量模型（需设置 OPENAI_SMALL_MODEL）

为避免 LLM 非确定性偏差，每项任务运行 3 轮取均值。
优化模式下还会跳过 Planner 直接使用硬编码子任务，消除规划差异。""")

    rounds = 3
    baseline_path = EXPERIMENT_DIR / "output" / "a2a_latest_trace.json"

    for task_id in ["code_review_task", "data_report_task", "multi_search_task"]:
        baseline_lats = []
        baseline_tokens = []
        optimized_lats = []
        optimized_tokens = []

        print(f"\n{Colors.BOLD}{'─' * 50}{Colors.RESET}")
        print(f"{Colors.BOLD}  {task_id}{Colors.RESET}")
        print(f"{'─' * 50}")

        for r in range(rounds):
            sys.stdout.write(f"\r  基线 第 {r+1}/{rounds} 轮...")
            sys.stdout.flush()
            proc = subprocess.run(
                [sys.executable, "experiment_a2a/a2a_orchestrator.py", f"--task={task_id}"],
                cwd=str(BASE_DIR), capture_output=True, text=True, timeout=600,
                env={**os.environ, "PYTHONIOENCODING": "utf-8"},
            )
            out = str(EXPERIMENT_DIR / "output" / "a2a_latest_trace.json")
            if Path(out).exists():
                import json as _json
                data = _json.loads(Path(out).read_text(encoding="utf-8"))
                if isinstance(data, list):
                    data = data[0] if data else {}
                baseline_lats.append(data.get("total_latency_ms", 0))
                baseline_tokens.append(data.get("total_tokens_in", 0) + data.get("total_tokens_out", 0))

            sys.stdout.write(f"\r  优化 第 {r+1}/{rounds} 轮...")
            sys.stdout.flush()
            proc = subprocess.run(
                [sys.executable, "experiment_a2a/a2a_orchestrator.py", f"--task={task_id}", "--optimize"],
                cwd=str(BASE_DIR), capture_output=True, text=True, timeout=600,
                env={**os.environ, "PYTHONIOENCODING": "utf-8"},
            )
            out = str(EXPERIMENT_DIR / "output" / "a2a_latest_trace.json")
            if Path(out).exists():
                import json as _json
                data = _json.loads(Path(out).read_text(encoding="utf-8"))
                if isinstance(data, list):
                    data = data[0] if data else {}
                optimized_lats.append(data.get("total_latency_ms", 0))
                optimized_tokens.append(data.get("total_tokens_in", 0) + data.get("total_tokens_out", 0))

        sys.stdout.write("\r" + " " * 40 + "\r")

        if baseline_lats and optimized_lats:
            bl = sum(baseline_lats) // len(baseline_lats)
            op = sum(optimized_lats) // len(optimized_lats)
            bt = sum(baseline_tokens) // len(baseline_tokens)
            ot = sum(optimized_tokens) // len(optimized_tokens)
            lat_improve = round((bl - op) / max(bl, 1) * 100, 1)
            tok_improve = round((bt - ot) / max(bt, 1) * 100, 1)

            color = Colors.GREEN if lat_improve > 0 else Colors.RED
            print(f"  基线均值: {bl:,}ms / {bt:,} tokens (n={len(baseline_lats)})")
            print(f"  优化均值: {op:,}ms / {ot:,} tokens (n={len(optimized_lats)})")
            print(f"  {Colors.BOLD}延迟改善: {color}{lat_improve:+.1f}%{Colors.RESET}  Token改善: {color}{tok_improve:+.1f}%{Colors.RESET}")

            if lat_improve <= 0:
                print(f"  {Colors.YELLOW}  ⚠️ 改善不明显或为负 — LLM 非确定性可能导致偏差。{Colors.RESET}")
                print(f"  {Colors.YELLOW}  建议: 增大 rounds 值、使用温度更低的模型、或检查 --optimize 是否正确传递{Colors.RESET}")
        else:
            print(f"  {Colors.RED}✗ 无有效数据{Colors.RESET}")
        print()

    print(f"{Colors.GREEN}✅ 回测完成{Colors.RESET}")


def show_menu():
    while True:
        print(f"""
{Colors.BOLD}{'=' * 60}
  CS599 A2A 多智能体实验菜单
{'=' * 60}{Colors.RESET}

  {Colors.MAGENTA}I{Colors.RESET}: 构建多智能体系统 — 搭建 Gateway → Planner → Executors → Reviewer
  {Colors.RED}J{Colors.RESET}: 关键路径分析 — 从 Phoenix Span 树中揪出性能瓶颈
  {Colors.GREEN}K{Colors.RESET}: Token 成本控制 — 上下文膨胀曲线分析与裁剪建议
  {Colors.BLUE}L{Colors.RESET}: 性能优化回测 — 应用优化方案，对比前后指标
  {Colors.YELLOW}all{Colors.RESET}: 运行全部实验
  {Colors.YELLOW}check{Colors.RESET}: 检查实验环境
  {Colors.YELLOW}quit{Colors.RESET}: 退出

  请输入实验编号:""")
        try:
            choice = input(f"  {Colors.BOLD}> {Colors.RESET}").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print("\n再见！")
            break

        if choice == "i":
            experiment_I_build_system()
        elif choice == "j":
            experiment_J_critical_path()
        elif choice == "k":
            experiment_K_token_control()
        elif choice == "l":
            experiment_L_optimization()
        elif choice == "all":
            experiment_I_build_system()
            experiment_J_critical_path()
            experiment_K_token_control()
            experiment_L_optimization()
            break
        elif choice == "check":
            check_prerequisites()
        elif choice in ("quit", "exit", "q"):
            print("再见！")
            break
        else:
            print(f"  {Colors.RED}无效选择{Colors.RESET}")


def main():
    print_banner()

    if not check_prerequisites():
        print(f"\n{Colors.RED}环境检查未通过。请修复上述问题后重试。{Colors.RESET}")
        sys.exit(1)

    import argparse
    parser = argparse.ArgumentParser(description="CS599 A2A 多智能体实验启动器")
    parser.add_argument(
        "--experiment", "-e",
        choices=["I", "J", "K", "L"],
        help="直接运行指定实验",
    )
    parser.add_argument(
        "--all", "-a",
        action="store_true",
        help="运行全部实验",
    )
    args = parser.parse_args()

    if args.experiment:
        exp_map = {
            "I": experiment_I_build_system,
            "J": experiment_J_critical_path,
            "K": experiment_K_token_control,
            "L": experiment_L_optimization,
        }
        exp_map[args.experiment]()
    elif args.all:
        experiment_I_build_system()
        experiment_J_critical_path()
        experiment_K_token_control()
        experiment_L_optimization()
    else:
        show_menu()


if __name__ == "__main__":
    main()
