# MCP 传输层文档

## 概述

本文档描述 MCP 协议在不同传输机制下的通信细节，包括连接建立、消息格式、错误处理和重连策略。

## stdio 传输

### 连接建立

在 stdio 传输中，MCP 客户端以子进程方式启动服务器：

```
Client (parent process)
  ├── spawn MCP Server (child process)
  ├── stdin  → 向服务器发送 JSON-RPC 请求
  └── stdout ← 从服务器接收 JSON-RPC 响应
```

连接建立流程：
1. 客户端启动服务器子进程
2. 服务器向 stdout 输出就绪标记（可选）
3. 客户端通过 stdin 发送 `initialize` 请求

### v1 vs v2: stdio 消息格式对比

**v1 格式** (纯文本 JSON):
```
{"jsonrpc":"2.0","id":1,"method":"tools/list"}
```

**v2 格式** (帧协议):
```
[4-byte length header][JSON payload bytes]
```

v2 的帧协议优势：
- 支持消息边界检测（不会因 JSON 换行而误断）
- 支持二进制数据传输
- 支持心跳保活

### 错误处理

stdio 传输中的常见错误及处理：

| 错误类型 | 原因 | 处理方式 |
|---------|------|---------|
| 连接断开 | 服务器进程崩溃 | 客户端重启服务器并重新初始化 |
| 消息格式错误 | JSON 解析失败 | 返回 ParseError，记录日志 |
| 方法不支持 | 服务器不支持请求的方法 | 返回 MethodNotFound |

### 重连策略

v2 新增了自动重连机制：
1. 客户端检测到连接断开（stdin/stdout 管道关闭）
2. 等待退避间隔（初始 1s，最多 30s，指数增长）
3. 重新启动服务器子进程
4. 发送 `initialize` 请求，携带 `session_id` 恢复会话

## SSE 传输 (HTTP)

### 架构

在 SSE 传输中，MCP 服务器运行在 HTTP 端口上：

```
Client ──POST /mcp──▶ Server (发送 JSON-RPC 请求)
Client ◀──SSE stream── Server (接收响应和通知)
```

### 消息流

SSE 传输的消息流：

1. **请求**: 客户端通过 HTTP POST 发送 JSON-RPC 请求到 `/mcp` 端点
2. **响应**: 服务器通过 SSE 流返回响应，格式为：
   ```
   event: message
   data: {"jsonrpc":"2.0","id":1,"result":{...}}
   ```
3. **通知**: 服务器可以主动推送通知（如进度更新）：
   ```
   event: progress
   data: {"progress": 0.8, "total": 1.0}
   ```

### 端点定义

MCP SSE 传输使用以下 HTTP 端点：

| 端点 | 方法 | 用途 |
|------|------|------|
| `/mcp` | POST | 客户端发送 JSON-RPC 请求 |
| `/sse` | GET | 建立 SSE 连接，接收服务器推送 |
| `/health` | GET | 健康检查 |

### 连接保活

SSE 传输的连接保活机制：

- 服务器每 30 秒发送一个 SSE 注释帧
- 格式：`": heartbeat\n\n"`
- 客户端 60 秒无响应视为断开
- 支持 `Last-Event-Id` 头实现断点续传

## 传输层对比

| 特性 | stdio | SSE (HTTP) |
|------|-------|------------|
| 网络需求 | 无（本地进程） | 需要 HTTP 连通 |
| 双向通信 | 通过 stdin/stdout | POST + SSE |
| 流式响应 | v2 支持 | 原生支持 |
| 连接恢复 | 子进程重启 | HTTP 自动重连 |
| 适用场景 | 本地开发、CLI 工具 | 远程服务、Web 应用 |
