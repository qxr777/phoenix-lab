# WebSocket 实时通信协议

## 概述

WebSocket 是一种在单个 TCP 连接上进行全双工通信的协议。
它允许客户端和服务器在握手后持续交换消息，无需反复发起 HTTP 请求。

## 连接建立（握手）

### HTTP Upgrade 握手

WebSocket 连接通过 HTTP Upgrade 机制建立：

```
# 客户端请求
GET /chat HTTP/1.1
Host: server.example.com
Upgrade: websocket
Connection: Upgrade
Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==
Sec-WebSocket-Version: 13

# 服务器响应
HTTP/1.1 101 Switching Protocols
Upgrade: websocket
Connection: Upgrade
Sec-WebSocket-Accept: s3pPLMBiTxaQ9kYGzzhZRbK+xOo=
```

握手成功后，TCP 连接升级为 WebSocket 连接，双方开始交换帧（Frame）。

## 消息帧 (Frame) 协议

WebSocket 消息以帧为单位传输。每个帧的结构：

```
0 1 2 3 4 5 6 7 8 9 0 1 2 3 4 5 6 7 8 9 0 1 2 3 4 5 6 7 8 9 0 1
+-+-+-+-+-------+-+-------------+-------------------------------+
|F|R|R|R| opcode|M| Payload len |    Extended payload length    |
|I|S|S|S|  (4)  |A|     (7)     |             (16/64)           |
|N|V|V|V|       |S|             |                               |
+-+-+-+-+-------+-+-------------+-------------------------------+
```

帧类型（opcode）：
- `0x1`: 文本帧 (Text)
- `0x2`: 二进制帧 (Binary)
- `0x8`: 连接关闭帧 (Close)
- `0x9`: 心跳帧 — PING
- `0xA`: 心跳响应帧 — PONG

## 心跳保活 (Ping/Pong)

WebSocket 使用 PING/PONG 帧实现连接保活：

```
Client → Server: PING (opcode 0x9)
Server → Client: PONG (opcode 0xA)
```

- 客户端或服务器都可以发起 PING
- 接收方必须尽快回复 PONG
- 如果在超时时间内未收到 PONG，认为连接已断开

## 连接断开与重连

### 优雅关闭

```
Client → Server: CLOSE (包含状态码和原因)
Server → Client: CLOSE (确认关闭)
TCP 连接关闭
```

### 自动重连策略

WebSocket 客户端通常实现自动重连：

1. 检测到连接断开（onclose 事件触发）
2. 等待退避延迟（如 1s → 2s → 4s → 8s → 最大 30s）
3. 重新发起 HTTP Upgrade 握手
4. 握手成功后恢复消息交换

## 消息格式

WebSocket 不强制消息格式，常见约定：

### JSON 格式
```json
{"type": "message", "content": {"text": "Hello", "user": "Alice"}}
```

### 二进制
适用于游戏、音视频流等场景

## 应用场景

- 实时聊天应用
- 协作编辑（如 Google Docs）
- 实时数据仪表盘
- 多人在线游戏
- 金融行情推送

## 安全考虑

- 使用 `wss://` (WebSocket Secure) 替代 `ws://`
- 验证 `Origin` 头部防止跨站 WebSocket 劫持
- 实现消息频率控制防止洪水攻击
- 认证令牌通过首条消息传递或 URL 参数传递
