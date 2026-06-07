#!/usr/bin/env python3
"""
CS599 RAG 实验统一启动器

四个实验阶段:
  2A: Embedding 降维可视化 (UMAP + Plotly 交互式 3D)
  2B: 检索失效诊断 (Retrieval Failure Diagnosis)
  2C: 分块策略对比 (Chunking Strategy Comparison)
  2D: 重排效果对比 (Reranking Impact Analysis)
  2E: 混合检索对比与参数实验 (Dense/Sparse/Hybrid + RRF + 网格搜索)

用法:
  python run_experiments.py              # 交互式菜单
  python run_experiments.py -e 2A        # 直接运行实验 2A
  python run_experiments.py --all        # 运行全部实验
"""

import os
import subprocess
import sys
from pathlib import Path

BASE_DIR = Path(__file__).parent.parent
PROJECT_DIR = BASE_DIR

# 确保 PROJECT_DIR 在 sys.path 中
sys.path.insert(0, str(PROJECT_DIR))

# ── 自动检测 venv ──
_VENV_DIR = PROJECT_DIR / "venv"
_VENV_PYTHON = _VENV_DIR / "bin" / "python"

if _VENV_PYTHON.exists() and sys.executable != str(_VENV_PYTHON):
    print(f"\033[93m[提示] 检测到 venv: {_VENV_DIR}")
    print(f"  当前 Python: {sys.executable}")
    print(f"  venv Python:  {_VENV_PYTHON}")
    print(f"  正在切换到 venv 重新执行...\033[0m\n")
    os.execv(str(_VENV_PYTHON), [str(_VENV_PYTHON)] + sys.argv)
    sys.exit(0)


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
║  CS599 实验 X：RAG 瓶颈诊断与向量空间聚类                  ║
║  Embedding 可视化 | 检索失效分析 | 分块策略 | 重排对比    ║
╚══════════════════════════════════════════════════════════╝{Colors.RESET}
""")


def run_step(description: str, cmd: list[str], timeout: int = 180):
    print(f"\n{Colors.CYAN}▶ {description}{Colors.RESET}")
    print(f"  {' '.join(cmd)}")
    try:
        proc = subprocess.run(cmd, cwd=str(PROJECT_DIR), text=True, timeout=timeout)
        return proc.returncode == 0
    except subprocess.TimeoutExpired:
        print(f"  {Colors.RED}超时！{Colors.RESET}")
        return False
    except KeyboardInterrupt:
        print(f"\n  {Colors.YELLOW}已取消{Colors.RESET}")
        return False


def check_setup():
    print(f"\n{Colors.BOLD}环境检查...{Colors.RESET}")

    py = sys.executable

    checks = {
        "sentence-transformers": "sentence_transformers",
        "chromadb": "chromadb",
    }
    for name, mod in checks.items():
        try:
            __import__(mod)
            print(f"  {name}: {Colors.GREEN}✓{Colors.RESET}")
        except ImportError:
            print(f"  {name}: {Colors.RED}✗ 未安装{Colors.RESET} — pip install {name}")

    try:
        from chromadb.utils import embedding_functions
        from experiment_rag.config import EMBEDDING_MODEL
        print(f"  嵌入模型 {EMBEDDING_MODEL}: {Colors.GREEN}已配置{Colors.RESET}")
    except Exception as e:
        print(f"  嵌入模型: {Colors.YELLOW}⚠ {e}{Colors.RESET}")

    has_collections = False
    try:
        import chromadb
        from experiment_rag.config import CHROMA_DB_PATH
        client = chromadb.PersistentClient(path=CHROMA_DB_PATH)
        colls = client.list_collections()
        if colls:
            has_collections = True
            print(f"  ChromaDB 知识库: {Colors.GREEN}✓ ({len(colls)} 个集合){Colors.RESET}")
    except Exception:
        pass
    if not has_collections:
        print(f"  ChromaDB 知识库: {Colors.YELLOW}⚠ 未构建{Colors.RESET} — 将自动构建")


def experiment_2A():
    print(f"\n{Colors.MAGENTA}{Colors.BOLD}{'=' * 60}")
    print("实验 2A：Embedding 降维可视化 (UMAP 3D)")
    print(f"{'=' * 60}{Colors.RESET}")
    print("""
观察文档块和查询在向量空间中的聚集状态:
  - MCP v1/v2/Transport 三个目标文档 → 应形成紧致簇
  - 噪声文档 (REST API, WebSocket) → 应为离群簇
  - 查询点 → 可能落入噪声簇（揭示检索陷阱）
""")

    print("\nStep 1: 构建知识库")
    run_step("构建知识库 (chunk_size=512)",
             [sys.executable, "experiment_rag/knowledge_base/build_kb.py",
              "--chunk-size", "512"])

    print("\nStep 2: 生成 Embedding 可视化")
    run_step("生成 UMAP 3D 可视化",
             [sys.executable, "experiment_rag/embedding_viz.py"])

    print(f"\n{Colors.GREEN}✅ 可视化已生成{Colors.RESET}")
    print(f"   UMAP 3D: experiment_rag/output/embedding_umap_3d.html (浏览器打开)")


def experiment_2B():
    print(f"\n{Colors.RED}{Colors.BOLD}{'=' * 60}")
    print("实验 2B：检索失效分析")
    print(f"{'=' * 60}{Colors.RESET}")
    print("""
使用 LLM-as-Judge 检测"相似 ≠ 相关"的检索失效案例:
  1. 运行 8 个陷阱查询
  2. 对每个检索块评判真实相关性 (0-10)
  3. 标记"高相似度但低相关性"为检索失效
  4. 分析失效根因: 噪声 / 分块 / 关键词误导
""")

    print("\nStep 1: 确保知识库已构建")
    if not _has_collections():
        run_step("构建知识库",
                 [sys.executable, "experiment_rag/knowledge_base/build_kb.py",
                  "--chunk-size", "512"])

    print("\nStep 2: 运行检索失效诊断")
    run_step("检索失效诊断",
             [sys.executable, "experiment_rag/retrieval_diagnosis.py",
              "--chunk-size", "512",
              "--output", "output/diagnosis_report.json"],
             timeout=600)

    print(f"\n{Colors.YELLOW}💡 思考:{Colors.RESET}")
    print("  哪些噪声文档被错误检索？为什么它们的相似度高？")
    print("  分块边界是否切断了关键信息？")


def experiment_2C():
    print(f"\n{Colors.GREEN}{Colors.BOLD}{'=' * 60}")
    print("实验 2C：分块策略对比")
    print(f"{'=' * 60}{Colors.RESET}")
    print("""
对比 4 种 chunk_size 对检索质量和答案准确率的影响:
  chunk_size=256  → 碎片化，上下文不完整
  chunk_size=512  → 平衡点
  chunk_size=1024 → 较大上下文，可能稀释
  chunk_size=2048 → 过度宽泛，噪声风险高
""")

    print("\nStep 1: 为所有尺寸构建知识库")
    run_step("构建全部尺寸的知识库",
             [sys.executable, "experiment_rag/knowledge_base/build_kb.py",
              "--all-sizes", "--strategy", "fixed"])

    print("\nStep 2: 运行分块策略对比")
    run_step("分块策略对比实验",
             [sys.executable, "experiment_rag/chunking/compare.py",
              "--output", "output/chunking_comparison.json"])

    print(f"\n{Colors.YELLOW}💡 思考:{Colors.RESET}")
    print("  哪个 chunk_size 准确率最高？为什么？")
    print("  chunk 太小和太大的问题分别是什么？")


def experiment_2D():
    print(f"\n{Colors.BLUE}{Colors.BOLD}{'=' * 60}")
    print("实验 2D：重排效果对比")
    print(f"{'=' * 60}{Colors.RESET}")
    print("""
对比"有重排"和"无重排"两种模式:
  无重排: 检索 Top-5 → 直接生成
  有重排: 检索 Top-20 → Cross-Encoder 重排 → 取 Top-5 生成
""")

    if not _has_collections():
        run_step("构建知识库",
                 [sys.executable, "experiment_rag/knowledge_base/build_kb.py",
                  "--chunk-size", "512"])

    run_step("重排效果对比实验",
             [sys.executable, "experiment_rag/reranking_test.py",
              "--chunk-size", "512", "--output", "output/reranking_report.json"],
             timeout=900)

    print(f"\n{Colors.YELLOW}💡 思考:{Colors.RESET}")
    print("  重排拯救了多少个查询？")
    print("  为什么 Cross-Encoder 比 Bi-Encoder 更准确但更慢？")


def experiment_2E():
    print(f"\n{Colors.YELLOW}{Colors.BOLD}{'=' * 60}")
    print("实验 2E：混合检索对比 — Dense vs Sparse vs Hybrid")
    print(f"{'=' * 60}{Colors.RESET}")
    print("""
对比三种检索模式:
  Dense  — ChromaDB + MiniLM 语义向量（处理语义改写）
  Sparse — BM25 字符 bigram（精确术语匹配）
  Hybrid — RRF/加权RRF/分数融合（取长补短）

参数敏感性:
  RRF k 值:   10(偏Top)/ 60(均衡)/ 120(偏宽泛)
  权重 α:     0.3(偏sparse)/ 0.5(均衡)/ 0.7(偏dense)
""")

    if not _has_collections():
        run_step("构建知识库",
                 [sys.executable, "experiment_rag/knowledge_base/build_kb.py",
                  "--chunk-size", "512"])

    print("\nStep 1: 运行混合检索对比")
    run_step("混合检索对比",
             [sys.executable, "experiment_rag/hybrid_search.py",
              "--chunk-size", "512", "--output", "output/hybrid_report.json"])

    print("\nStep 2: 参数敏感性分析")
    run_step("参数敏感性分析",
             [sys.executable, "experiment_rag/hybrid_search.py",
              "--chunk-size", "512", "--sensitivity"])

    print(f"\n{Colors.YELLOW}💡 思考:{Colors.RESET}")
    print("  哪些查询被混合检索'拯救'了？（Dense 有噪声 → Hybrid 消除）")
    print("  什么时候 Dense 更好、什么时候 Sparse 更好？")
    print("  RRF 的 k 值对结果有什么影响？α 权重如何调节？")


def _has_collections() -> bool:
    try:
        import chromadb
        from experiment_rag.config import CHROMA_DB_PATH
        client = chromadb.PersistentClient(path=CHROMA_DB_PATH)
        return len(client.list_collections()) > 0
    except Exception:
        return False


def show_menu():
    while True:
        print(f"""
{Colors.BOLD}{'=' * 60}
  CS599 RAG 实验菜单
{'=' * 60}{Colors.RESET}

  {Colors.MAGENTA}2A{Colors.RESET}: Embedding 降维可视化 (UMAP + Plotly 交互式 3D)
  {Colors.RED}2B{Colors.RESET}: 检索失效诊断 (Retrieval Failure Analysis)
  {Colors.GREEN}2C{Colors.RESET}: 分块策略对比 (Chunking Strategy Comparison)
  {Colors.BLUE}2D{Colors.RESET}: 重排效果对比 (Reranking Impact)
  {Colors.YELLOW}2E{Colors.RESET}: 混合检索对比 (Dense vs Sparse vs Hybrid RRF)
  {Colors.YELLOW}all{Colors.RESET}: 运行全部实验
  {Colors.YELLOW}setup{Colors.RESET}: 首次构建知识库（一次性）
  {Colors.YELLOW}check{Colors.RESET}: 环境检查
  {Colors.YELLOW}quit{Colors.RESET}: 退出

  请输入实验编号:""")
        try:
            choice = input(f"  {Colors.BOLD}> {Colors.RESET}").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print("\n再见！")
            break

        if choice == "2a":
            experiment_2A()
        elif choice == "2b":
            experiment_2B()
        elif choice == "2c":
            experiment_2C()
        elif choice == "2d":
            experiment_2D()
        elif choice == "2e":
            experiment_2E()
        elif choice == "all":
            experiment_2A()
            experiment_2B()
            experiment_2C()
            experiment_2D()
            experiment_2E()
            break
        elif choice == "setup":
            run_step("构建全部知识库",
                     [sys.executable, "experiment_rag/knowledge_base/build_kb.py",
                      "--all-sizes"])
        elif choice == "check":
            check_setup()
        elif choice in ("quit", "exit", "q"):
            print("再见！")
            break
        else:
            print(f"  {Colors.RED}无效选择{Colors.RESET}")


def main():
    print_banner()
    check_setup()

    import argparse
    p = argparse.ArgumentParser(description="RAG 实验启动器")
    p.add_argument("--experiment", "-e", choices=["2A", "2B", "2C", "2D", "2E"])
    p.add_argument("--all", "-a", action="store_true")
    args = p.parse_args()

    exp_map = {"2A": experiment_2A, "2B": experiment_2B,
               "2C": experiment_2C, "2D": experiment_2D, "2E": experiment_2E}

    if args.experiment:
        exp_map[args.experiment]()
    elif args.all:
        for exp in ["2A", "2B", "2C", "2D", "2E"]:
            exp_map[exp]()
    else:
        show_menu()


if __name__ == "__main__":
    main()
