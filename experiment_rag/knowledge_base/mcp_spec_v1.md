# MCP 协议 v1 规范

## 概述

Model Context Protocol (MCP) v1 是一个开放标准协议，用于在 LLM 应用和外部工具/数据源之间建立结构化的通信通道。

## 核心概念

### JSON-RPC 传输层

MCP v1 基于 JSON-RPC 2.0 协议进行所有通信。每个消息都包含以下标准字段：

- `jsonrpc`: 固定值 "2.0"
- `id`: 请求标识符，用于匹配请求和响应
- `method`: 方法名称（如 `tools/list`、`tools/call`）
- `params`: 方法参数

### 生命周期管理

MCP v1 定义了严格的生命周期状态机：

1. **初始化阶段** (initialize)
   - 客户端发送 `initialize` 请求，声明协议版本和能力
   - 服务器响应 `capabilities` 信息
   - 客户端发送 `initialized` 通知，进入正常运行状态

2. **运行阶段** (operational)
   - 客户端可以发送 `tools/list`、`resources/list`、`prompts/list` 等方法
   - 服务器返回对应资源的列表
   - 客户端通过 `tools/call` 执行具体的工具调用

3. **关闭阶段** (shutdown)
   - 客户端发送 `shutdown` 请求
   - 服务器清理资源后响应
   - 连接关闭

### 工具发现 (Tool Discovery)

工具发现是 MCP v1 的核心功能之一：

```json
// 请求
{"jsonrpc": "2.0", "id": 1, "method": "tools/list"}

// 响应
{
  "jsonrpc": "2.0",
  "id": 1,
  "result": {
    "tools": [
      {
        "name": "get_weather",
        "description": "获取指定城市的天气信息",
        "inputSchema": {
          "type": "object",
          "properties": {
            "city": {"type": "string"}
          },
          "required": ["city"]
        }
      }
    ]
  }
}
```

### 资源管理 (Resource Management)

除了工具，MCP v1 还支持资源（Resources）和提示模板（Prompts）：

- **Resources**: 静态数据源，如文件、数据库记录。通过 `resources/list` 和 `resources/read` 访问。
- **Prompts**: 预定义的提示模板，通过 `prompts/list` 和 `prompts/get` 获取。

### 传输机制

MCP v1 支持两种传输方式：

1. **stdio** (标准输入输出)
   - 客户端以子进程方式启动 MCP 服务器
   - 通过标准输入输出流进行 JSON-RPC 通信
   - 适用于本地工具调用场景

2. **HTTP + SSE** (Server-Sent Events)
   - 服务器运行在 HTTP 端口上
   - 使用 SSE 实现服务器向客户端的推送
   - 适用于远程服务调用场景

## 安全考虑

MCP v1 的安全模型基于以下原则：
- 服务器对客户端进行能力声明
- 客户端负责权限控制和用户授权
- 不支持工具级别的细粒度权限

## 版本特性

- 协议版本标识：`"2024-11-05"`
- 不支持流式响应 (Streaming)
- 不支持会话持久化
- 工具调用为同步模式
