# CS599 Phoenix 实验套件

> OWASP ASI 2026 + Phoenix 深度追踪驱动的 LLM 安全与性能实验

## 目录结构

```
phoenix_lab/
├── .env                          # 共享 LLM + Phoenix 配置
├── requirements.txt              # 共享基础依赖
├── docker/
│   └── docker-compose.yml        # Phoenix 容器（三个实验共用）
│
├── experiment_asi/               # 实验一：ASI 靶场
│   ├── run_experiments.py        #   提示词攻击与反注入防御
│   ├── smart_ta_agent.py         #   红队攻击 → 蓝队防御 → Phoenix 审计
│   └── ...
│
├── experiment_rag/               # 实验二：RAG 瓶颈诊断
│   ├── run_rag_experiments.py    #   Embedding UMAP 3D → 检索失效 → 分块/重排
│   └── ...
│
└── experiment_llm_judge/         # 实验三：LLM-as-a-Judge
    ├── run_experiments.py        #   幻觉度量 + 规范符合度 + Phoenix 遥测
    └── ...
```

## 快速开始

```bash
# 1. 安装依赖
pip install -r experiment_asi/requirements.txt  # ASI 实验
pip install -r experiment_rag/requirements.txt  # RAG 实验

# 2. 启动 Phoenix（可选）
cd docker && docker compose up -d && cd ..

# 3. 配置 .env 中的 LLM 连接

# 4. 运行实验

# ASI 实验（红蓝对抗）
python experiment_asi/run_experiments.py

# RAG 实验（检索诊断）
python experiment_rag/run_rag_experiments.py

# LLM-as-Judge 实验（自动化评估）
python experiment_llm_judge/run_experiments.py
```

## 实验概述

| 实验 | 主题 | 核心技术 |
|------|------|----------|
| **ASI 靶场** | 提示词攻击与反注入防御 | OTLP Traces、Spotlighting、意图验证、HITL |
| **RAG 诊断** | Embedding 可视化与检索失效分析 | UMAP 3D、ChromaDB、Cross-Encoder Reranker |
| **LLM-Judge** | 基于大模型裁判的自动化评估 | Hallucination Rate、QA Correctness、规则引擎+Judge |
