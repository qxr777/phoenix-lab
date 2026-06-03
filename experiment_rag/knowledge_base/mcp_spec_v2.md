# MCP 协议 v2 规范

## 概述

MCP v2 是 Model Context Protocol 的第二代协议，在 v1 的基础上引入了流式响应、会话管理和增强的安全模型。协议版本标识为 `"2025-06-01"`。

## 新增特性

### 流式响应 (Streaming)

与 v1 的同步模式不同，v2 引入了服务器流式响应机制：

- 工具调用结果通过 SSE (Server-Sent Events) 流式传输
- 每个 `tools/call` 请求可以返回多个事件片段
- 客户端通过 `Content-Type: text/event-stream` 接收数据

### 会话管理 (Session Management)

v2 新增了会话（Session）概念：

1. 初始化时，服务器返回 `session_id`
2. 后续请求可以携带 `session_id` 以维持上下文
3. 会话支持超时和自动续期机制
4. 一个客户端可以同时管理多个会话

### 增强安全模型

v2 的安全模型在 v1 基础上增加了以下能力：

- **工具级权限控制**: 服务器可以为每个工具声明所需权限
- **审计日志**: 所有工具调用记录到结构化日志
- **速率限制**: 支持基于令牌桶的请求频率控制
- **用户身份传递**: 客户端可以传递用户身份信息进行授权

### 协议初始化 (New Handshake)

v2 的初始化流程升级为扩展协商模式：

```
Client -> Server: initialize (capabilities: {streaming: true, sampling: true})
Server -> Client: initialize_result (capabilities: {streaming: true, logging: true})
Client -> Server: initialized (negotiated_capabilities: {streaming: true})
```

双方通过能力协商确定最终使用的功能集，不匹配的能力被优雅降级。

## 传输层改进

v2 对传输层进行了重大改进：

### 新增 stdio 帧协议

在 stdio 传输模式下，v2 引入了二进制帧（Frame）协议：

- 每个 JSON-RPC 消息前有 4 字节的长度头
- 支持消息分片和重组
- 支持心跳帧（PING/PONG）用于连接保活

### HTTP 传输增强

SSE 传输现在支持：
- POST 请求用于客户端到服务器的消息
- GET 请求用于服务器到客户端的 SSE 流
- 自动重连和断点续传

## 工具调用增强

### 确认机制 (Confirmation)

v2 引入了工具调用确认机制：

```json
// 服务器返回待确认状态
{
  "content": [
    {"type": "text", "text": "此操作需要用户确认"}
  ],
  "status": "pending_confirmation"
}
```

### 进度报告 (Progress)

长时间运行的工具调用可以通过 `progress` 通知报告进度：

```json
{"jsonrpc": "2.0", "method": "notifications/progress", "params": {"progress": 0.5, "total": 1.0}}
```

## 向后兼容

v2 设计为向后兼容 v1：
- v1 客户端连接 v2 服务器时，服务器降级为 v1 模式
- v2 新特性通过能力协商机制优雅启用
- v1 的核心工具发现流程保持不变
