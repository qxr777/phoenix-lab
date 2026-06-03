# RESTful API 设计最佳实践

## 概述

REST (Representational State Transfer) 是目前最流行的 Web API 架构风格。
本文档涵盖 REST API 设计的核心原则和最佳实践。

## HTTP 方法标准

### GET — 查询资源

```
GET /api/v1/resource
GET /api/v1/resource/{id}
```

- 安全方法（不会修改服务器状态）
- 幂等性：多次请求返回相同结果
- 响应码：200 OK, 404 Not Found

### POST — 创建资源

```
POST /api/v1/resource
Content-Type: application/json

{"name": "new_item", "value": 42}
```

- 非幂等：多次请求可能创建多个资源
- 响应码：201 Created, 400 Bad Request

### PUT — 完整更新

```
PUT /api/v1/resource/{id}
Content-Type: application/json

{"name": "updated_item", "value": 100}
```

- 幂等性：多次请求结果一致
- 需要提供完整的资源表示

### DELETE — 删除资源

```
DELETE /api/v1/resource/{id}
```

- 幂等性：删除后的再次删除返回 404
- 响应码：204 No Content

## 协议握手 (Handshake)

REST API 的"握手"不同于底层网络协议的握手。
在 REST 中，握手指的是客户端与服务端建立信任和会话的过程：

### 认证握手 (Authentication Handshake)

```
# 第一步：客户端发送凭证
POST /api/v1/auth/login
{"username": "user", "password": "pass"}

# 第二步：服务端返回令牌
200 OK
{"access_token": "eyJhbG...", "token_type": "Bearer"}

# 第三步：客户端在后续请求中携带令牌
GET /api/v1/resource
Authorization: Bearer eyJhbG...
```

## 常见模式

### 分页

```
GET /api/v1/resource?page=1&limit=20
```

### 搜索和过滤

```
GET /api/v1/resource?q=keyword&status=active&sort=created_at
```

### 错误响应

```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "参数不合法",
    "details": [{"field": "email", "issue": "格式不正确"}]
  }
}
```

## 与传统协议的区别

REST API 与类似 JSON-RPC（如 MCP 使用的传输协议）的区别：
- REST 是面向资源的（名词中心）
- JSON-RPC 是面向操作的（动词中心）
- REST 使用 HTTP 语义（GET/POST/PUT/DELETE）
- JSON-RPC 所有操作通过 POST 一个端点完成

## 版本管理

URL 版本化：
```
GET /api/v1/resource
GET /api/v2/resource
```

Header 版本化：
```
Accept: application/vnd.company.api-v2+json
```
