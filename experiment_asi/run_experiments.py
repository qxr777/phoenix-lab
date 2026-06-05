#!/usr/bin/env python3
"""
CS599 实验启动器：ASI 靶场 — 提示词攻击与反注入防御实验

本脚本是实验的统一入口，按顺序引导学生完成四个实验阶段：
  A: 红队攻击 — 生成下毒作业文件并测试攻击效果
  B: 思维链熔断分析 — 通过 Phoenix Trace 观察注入渗透过程
  C: 蓝队防御 — 实现并测试三层防御护栏
  D: 安全审计 — 量化拦截率、防线贡献度与 OWASP ASI 映射

用法:
  python run_experiments.py                # 交互式菜单
  python run_experiments.py --experiment A # 直接运行实验 A
  python run_experiments.py --experiment D # 直接运行实验 D
  python run_experiments.py --all          # 运行全部实验（非交互）
"""

import os
import subprocess
import sys
from pathlib import Path

# ── 自动检测 venv ──
_VENV_DIR = Path(__file__).parent.parent / "venv"
_VENV_PYTHON = _VENV_DIR / "bin" / "python"

if _VENV_PYTHON.exists() and sys.executable != str(_VENV_PYTHON):
    print(f"\033[93m[提示] 检测到 venv: {_VENV_DIR}")
    print(f"  当前 Python: {sys.executable}")
    print(f"  venv Python:  {_VENV_PYTHON}")
    print(f"  正在切换到 venv 重新执行...\033[0m\n")
    os.execv(str(_VENV_PYTHON), [str(_VENV_PYTHON)] + sys.argv)
    sys.exit(0)

# ──────────────────────────────────────────────
#  配置
# ──────────────────────────────────────────────

BASE_DIR = Path(__file__).parent.parent

from dotenv import load_dotenv
load_dotenv(BASE_DIR / ".env")
VENV_DIR = BASE_DIR / "venv_cs599_phoenix_lab"
PHOENIX_DOCKER = BASE_DIR / "docker" / "docker-compose.yml"
SUBMISSIONS_DIR = BASE_DIR / "submissions"


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
{Colors.CYAN}{Colors.BOLD}╔══════════════════════════════════════════════════════════╗
║  CS599 实验 X：ASI 靶场                                  ║
║  提示词攻击与反注入防御实验                                ║
║  OWASP ASI 2026 框架 + Phoenix 深度追踪                   ║
╚══════════════════════════════════════════════════════════╝{Colors.RESET}
""")


def run_cmd(cmd: list[str], description: str = "", timeout: int = 300) -> bool:
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
        print(f"  .env 配置: {Colors.RED}✗ 缺失{Colors.RESET} — 请先配置 .env")
        return False

    llm_key = os.getenv("OPENAI_API_KEY", "")
    llm_base = os.getenv("OPENAI_BASE_URL", "")
    if llm_key and llm_base:
        print(f"  LLM 服务: {Colors.GREEN}✓{Colors.RESET} ({llm_base})")
    else:
        print(f"  LLM 服务: {Colors.YELLOW}⚠ 未配置{Colors.RESET} — 请在 .env 中设置")
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
            print(f"  Phoenix 连接: {Colors.YELLOW}⚠ 未运行{Colors.RESET} — 运行 docker compose up -d 启动")
    else:
        print(f"  Phoenix 遥测: {Colors.YELLOW}未启用{Colors.RESET}")

    ensure_dirs = [
        BASE_DIR / "data",
        BASE_DIR / "submissions" / "normal",
        BASE_DIR / "submissions" / "poisoned",
    ]
    for d in ensure_dirs:
        d.mkdir(parents=True, exist_ok=True)

    return True


def ensure_poisoned_samples():
    print(f"\n{Colors.BOLD}生成攻击样本...{Colors.RESET}")
    samples = [
        ("experiment_asi/red_team/samples/poisoned_grade_hijack.md", "experiment_asi/submissions/poisoned/poisoned_grade_hijack.md"),
        ("experiment_asi/red_team/samples/poisoned_deletion.md", "experiment_asi/submissions/poisoned/poisoned_deletion.md"),
        ("experiment_asi/red_team/samples/poisoned_stealth.md", "experiment_asi/submissions/poisoned/poisoned_stealth.md"),
        ("experiment_asi/red_team/samples/normal_homework.md", "experiment_asi/submissions/normal/normal_homework.md"),
    ]

    import shutil
    for src, dst in samples:
        src_path = BASE_DIR / src
        dst_path = BASE_DIR / dst
        if not dst_path.exists():
            shutil.copy2(src_path, dst_path)
            print(f"  ✓ 复制: {dst}")


def experiment_A_red_team():
    print(f"\n{Colors.RED}{Colors.BOLD}{'=' * 60}")
    print("实验 A：红队攻击 — 间接提示词注入")
    print(f"{'=' * 60}{Colors.RESET}")
    print("""
红队任务：设计并测试间接提示词注入攻击。

攻击向量：学生在提交的作业文件中嵌入恶意指令，
当助教智能体读取作业文件时，恶意指令被拼入 Context Window 并劫持控制流。

OWASP ASI 风险映射:
  ASI-001: Prompt Injection (Indirect)
  ASI-003: Excessive Agency
  ASI-005: Supply Chain Attack (poisoned submissions)

本实验将:
  1. 展示三种攻击载荷模板（评分劫持、成绩删除、隐写注入）
  2. 运行攻击，观察脆弱系统的行为
  3. 引导你到 Phoenix UI 查看攻击的完整 trace
""")

    ensure_poisoned_samples()

    attacks = [
        ("评分劫持 (Grade Hijack)", "experiment_asi/submissions/poisoned/poisoned_grade_hijack.md"),
        ("成绩删除 (Deletion)", "experiment_asi/submissions/poisoned/poisoned_deletion.md"),
        ("隐写注入 (Stealth)", "experiment_asi/submissions/poisoned/poisoned_stealth.md"),
    ]

    for name, attack_file in attacks:
        print(f"\n{Colors.RED}▶ 攻击类型: {name}{Colors.RESET}")
        cmd = [sys.executable, "experiment_asi/smart_ta_agent.py", f"--attack-file={attack_file}"]
        run_cmd(cmd, f"执行 {name}")

    print(f"\n{Colors.YELLOW}💡 下一步: 打开 Phoenix UI ({os.getenv('PHOENIX_COLLECTOR_ENDPOINT', 'http://localhost:6006').replace('/v1/traces', '')})")
    print("   查看 Traces 面板，观察攻击的完整执行路径。{Colors.RESET}")


def experiment_B_cot_analysis():
    print(f"\n{Colors.MAGENTA}{Colors.BOLD}{'=' * 60}")
    print("实验 B：思维链熔断分析 — CoT Fuse Analysis")
    print(f"{'=' * 60}{Colors.RESET}")
    print("""
蓝队任务 B：通过 Phoenix Trace 面板，追踪恶意指令如何逐步渗透智能体的内部推理链。

分析步骤:
  1. 在 Phoenix UI 中找到实验 A 生成的 trace
  2. 展开 trace，查看每个 span 的详细信息:
     - Span 1: LLM 接收用户请求 → 决定调用 read_submission_file
     - Span 2: read_submission_file 返回作业文件内容（含恶意载荷）
     - Span 3: 恶意内容进入 messages 历史，拼入 Context Window
     - Span 4: LLM 再次推理 → 受恶意指令影响，决定调用 batch_grade
     - Span 5: batch_grade 执行 → 攻击成功

  3. 识别"污染点"（Contamination Point）:
     - 在第几次 LLM 调用时推理链被污染？
     - Context Window 中哪段内容触发了工具调用？
     - 如果使用了 Spotlighting 隔离，污染还会发生吗？

需要记录（用于实验报告）:
  - Trace ID
  - 污染发生的 span 编号
  - 触发高危工具的 prompt 片段
  - 如果思维链中有"犹豫"或"矛盾"，记录下来

打开 Phoenix UI 进行分析:
""")
    phoenix_host = os.getenv("PHOENIX_COLLECTOR_ENDPOINT", "http://localhost:6006").replace("/v1/traces", "")
    print(f"  {Colors.CYAN}🔗 {phoenix_host}{Colors.RESET}")
    print(f"\n  在 Traces 页面中找到最近的 trace（按时间排序），展开查看详情。")
    print(f"  每个 OpenAI API 调用都会生成一个包含 I/O 内容的独立 span。")


def experiment_C_blue_team_defense():
    print(f"\n{Colors.GREEN}{Colors.BOLD}{'=' * 60}")
    print("实验 C：蓝队防御 — 三层护栏实现")
    print(f"{'=' * 60}{Colors.RESET}")
    print("""
蓝队任务 C：实现并测试多层防御护栏，通过 A/B 对照验证效果。

三层防御（由内到外）:
  防线 1: Spotlighting 数据定界符
    — 将作业文件内容用 <external_untrusted> 标签包裹
    — 加固 System Prompt 明确要求忽略标签内的指令

  防线 2: 双重意图验证
    — 在工具调用前，独立 LLM 判断用户原始请求是否授权此操作
    — 如果用户只请求"看作业"，batch_grade 会被拒绝

  防线 3: HITL 人在回路
    — 高危工具（batch_grade, delete_all_grades）执行前强制人工确认
    — 模拟操作员拒绝不合规的调用

逐层验证防御效果:
""")

    defense_tests = [
        ("无防御 (脆弱基线)", ["--all-off"]),
        ("仅 Spotlighting", ["--skip-intent", "--skip-hitl"]),
        ("Spotlighting + 意图验证", ["--skip-hitl"]),
        ("全防御 (3 层)", []),
    ]

    attack_file = "experiment_asi/submissions/poisoned/poisoned_grade_hijack.md"

    for label, flags in defense_tests:
        print(f"\n{Colors.GREEN}▶ 防御方案: {label}{Colors.RESET}")
        cmd = [sys.executable, "experiment_asi/blue_team/ta_defended.py", f"--attack-file={attack_file}"] + flags
        run_cmd(cmd, f"测试 {label}")

    print(f"\n{Colors.YELLOW}💡 提示: 在 Phoenix UI 中对比不同防御方案的 trace")
    print("   观察 Spotlighting 版本中 LLM 是否仍然产生高危工具调用。{Colors.RESET}")


def experiment_D_audit():
    print(f"\n{Colors.CYAN}{Colors.BOLD}{'=' * 60}")
    print("实验 D：安全审计 — 拦截率量化与 Phoenix 遥测")
    print(f"{'=' * 60}{Colors.RESET}")
    print("""
蓝队任务 D：通过自动化的审计工具，量化防御效果并生成分析报告。

审计指标:
  - 拦截率 (Interception Rate): 被拦截攻击 / 总攻击数
  - 绕过率 (Bypass Rate):   成功突破防御的攻击比例
  - 综合防护率 (Prevention Rate): (拦截 + 未触发) / 总攻击数
  - 误报率 (False Positive Rate): 正常请求被拦截的比例
  - 防线贡献度: 各防线独立拦截的攻击数

生成审计报告:
""")

    json_path = "audit_report.json"
    html_path = "audit_report.html"

    cmd = [
        sys.executable, "experiment_asi/blue_team/harness_auditor.py",
        "--full-audit",
        f"--output-json={json_path}",
        f"--output-html={html_path}",
        "--rounds=3",
    ]
    run_cmd(cmd, "运行完整审计（3 轮/攻击类型）", timeout=1200)

    print(f"\n{Colors.GREEN}✅ 报告已生成:{Colors.RESET}")
    if (BASE_DIR / json_path).exists():
        print(f"  JSON: {json_path}")
    if (BASE_DIR / html_path).exists():
        print(f"  HTML: {html_path} (可在浏览器中打开查看可视化图表)")


def show_menu():
    while True:
        print(f"""
{Colors.BOLD}{'=' * 60}
  CS599 ASI 靶场实验菜单
{'=' * 60}{Colors.RESET}

  {Colors.RED}A{Colors.RESET}: 红队攻击 — 间接提示词注入
  {Colors.MAGENTA}B{Colors.RESET}: 蓝队分析 — 思维链熔断分析 (Phoenix UI)
  {Colors.GREEN}C{Colors.RESET}: 蓝队防御 — 三层护栏实现与 A/B 测试
  {Colors.CYAN}D{Colors.RESET}: 蓝队审计 — 拦截率量化与 Phoenix 遥测
  {Colors.YELLOW}all{Colors.RESET}: 运行全部实验
  {Colors.YELLOW}check{Colors.RESET}: 检查实验环境
  {Colors.YELLOW}quit{Colors.RESET}: 退出

  请输入实验编号:""")
        try:
            choice = input(f"  {Colors.BOLD}> {Colors.RESET}").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print("\n再见！")
            break

        if choice == "a":
            experiment_A_red_team()
        elif choice == "b":
            experiment_B_cot_analysis()
        elif choice == "c":
            experiment_C_blue_team_defense()
        elif choice == "d":
            experiment_D_audit()
        elif choice == "all":
            experiment_A_red_team()
            experiment_B_cot_analysis()
            experiment_C_blue_team_defense()
            experiment_D_audit()
            break
        elif choice == "check":
            check_prerequisites()
        elif choice in ("quit", "exit", "q"):
            print("再见！")
            break
        else:
            print(f"  {Colors.RED}无效选择{Colors.RESET}")


# ──────────────────────────────────────────────
#  入口
# ──────────────────────────────────────────────

def main():
    print_banner()

    if not check_prerequisites():
        print(f"\n{Colors.RED}环境检查未通过。请修复上述问题后重试。{Colors.RESET}")
        sys.exit(1)

    import argparse
    parser = argparse.ArgumentParser(description="CS599 ASI 靶场实验启动器")
    parser.add_argument(
        "--experiment", "-e",
        choices=["A", "B", "C", "D"],
        help="直接运行指定实验",
    )
    parser.add_argument(
        "--all", "-a",
        action="store_true",
        help="运行全部实验",
    )
    args = parser.parse_args()

    ensure_poisoned_samples()

    if args.experiment:
        exp_map = {
            "A": experiment_A_red_team,
            "B": experiment_B_cot_analysis,
            "C": experiment_C_blue_team_defense,
            "D": experiment_D_audit,
        }
        exp_map[args.experiment]()
    elif args.all:
        experiment_A_red_team()
        experiment_B_cot_analysis()
        experiment_C_blue_team_defense()
        experiment_D_audit()
    else:
        show_menu()


if __name__ == "__main__":
    main()
