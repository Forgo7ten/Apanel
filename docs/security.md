# 安全说明

本文记录当前代码已经实现的安全边界，以及部署时必须补足的运行环境约束。

## 凭据与环境文件

- 根目录 `.env` 被 `.gitignore` 忽略；只提交 `.env.example` 模板。
- `POSTGRES_PASSWORD` 会拼接到数据库 URL，应使用 URL-safe 字符，避免连接 URL 解析错误。
- `JWT_SECRET_KEY`、`INTERNAL_API_TOKEN` 和生产数据库密码必须是每个环境独立生成的随机值。
- 不要把 `INTERNAL_API_TOKEN` 传给前端；Compose 只将它传给行情服务。
- `APP_ENV=production` 时，后端拒绝已知默认数据库密码、已知默认或少于 32 字节的 JWT secret，并拒绝显式的 `REFRESH_COOKIE_SECURE=false`。行情服务要求配置内部 token。

## 用户密码与邀请

- 用户密码使用 Argon2id 哈希，不保存明文。
- 登录查询不到用户时仍使用 dummy password hash 做验证，避免简单的用户枚举时间差。
- 邀请 token 使用高熵 URL-safe 随机值；数据库只保存 SHA-256 哈希，原始 token 只在创建邀请的响应中返回一次。
- 注册在事务中锁定并消费邀请，邀请状态可为 `PENDING`、`ACCEPTED`、`REVOKED` 或 `EXPIRED`。
- 用户名和邮箱使用大小写不敏感的唯一索引；注册时邀请邮箱和提交邮箱必须匹配。

## 会话与授权

- access token 是带 `iss`、`aud`、`iat`、`exp`、`jti` 和 `token_type=access` claims 的 HS256 JWT，默认有效期 900 秒。
- refresh token 是高熵 opaque token，只以哈希形式写入 `refresh_sessions`；原始值只通过 HttpOnly Cookie 传输。
- refresh token 每次刷新都会轮换。已使用、过期或被撤销的 token 被再次使用时，服务端会撤销同一 family，阻断重放。
- Cookie 默认使用 `SameSite=Lax`，路径为 `/api/v1/auth`；生产 HTTPS 应启用 `Secure`。
- 前端把 access token 保存在进程内会话状态，不把 refresh token 放入 JavaScript 可读存储；多标签页通过协调器避免正常刷新竞争。
- `/auth/me` 每次从数据库读取当前激活用户；管理员权限由数据库中的 `role` 检查，不信任 JWT 中的角色字段。
- 创建邀请需要激活的管理员；注册、登录和刷新不接受客户端提供的 `user_id` 作为权限依据。

## 内部行情接口

- `/internal/sync/daily` 和 `/internal/sync/securities` 必须提供 `X-Internal-Token`，或使用 Bearer 形式提供相同 token。
- token 比较使用常量时间比较函数；缺少 token、错误 token 和未配置 token 分别映射为稳定错误。
- Compose 不发布行情服务 `8001`，Nginx 也不代理 `/internal`，降低外部直接访问面。
- 当前行情读取接口不要求内部 token，安全性依赖于 Docker 网络边界；若将 `8001` 暴露到其他网络，应在入口层增加认证和访问控制。

## HTTP 与部署

当前 Compose 由 Nginx 提供 HTTP，不包含 TLS 证书或 HTTPS 终止。生产部署应在可信反向代理/负载均衡层终止 HTTPS，并将外部请求限制到该入口。只有在 HTTPS 下启用安全 Cookie 才能避免浏览器拒绝带 `Secure` 的刷新 Cookie。

Nginx 当前添加 `X-Content-Type-Options: nosniff` 和 `X-Frame-Options: SAMEORIGIN`，但没有实现完整的生产安全响应头策略。部署方应按自身 CSP、HSTS、审计和速率限制要求补充边缘配置。

## 错误、日志与数据暴露

- 后端校验错误只返回位置、类型和稳定消息，不把被拒绝的原始输入、密码或内部上下文放入公共响应。
- 用户响应只包含 `id`、`username`、`email`、`role` 和 `status`，不会返回 password hash 或 refresh token。
- provider、数据库和连接异常在行情 API 层被映射为通用错误码；部署时应把详细故障留在受控日志中。
- PostgreSQL 和 Redis 使用 Compose 命名卷；备份、加密、保留周期和访问审计由部署环境负责。

## 当前安全限制

- 仓库没有独立的速率限制、审计日志、密码找回或管理员撤销邀请 HTTP API。
- Cookie 使用 SameSite 策略，但当前没有单独的 CSRF token 机制；应保持前后端同源并在 HTTPS 入口后部署。
- 行情读取接口依赖内部网络隔离；不要在未增加认证和网关策略的情况下直接发布 `8001`。
