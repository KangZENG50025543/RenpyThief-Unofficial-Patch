# 更新保护契约

更新保护只处理 RenpyThief 的版本检查，不修改登录、注册、额度、游戏配置或翻译接口。

## 匹配范围

请求必须同时满足：

- HTTPS；
- 主机名严格等于 `api.renpy.fun`（不接受子域名或带凭据的 URL）；
- 路径严格等于 `/renpythief/getVersionInfo`；
- 请求方法为 GET、HEAD 或 POST。

显式端口可以变化或省略，查询参数和片段不参与版本接口判定。路径尾斜杠、其他版本/更新路径、百分号编码变体和其他官方接口均不会被截停。

Qt 5.15.2 x86 下同时保护两个入口：

- `QNetworkAccessManager::get`；
- `QNetworkAccessManager::createRequest` 的 GET、HEAD 或 POST Operation。

替代请求通过原始 trampoline 以 GET 方式发送到本地 `data:` URL，不会递归经过原官方 URL，也不会携带 POST 的 `outgoingData`。PUT、DELETE、Custom 或其他 Operation 始终透传；其他路径（包括 `signIn`）无论使用什么方法都不会匹配。

## 两阶段确认

启动器不会把“DLL 已加载”等同于“版本检查已拦截”：

1. `hook_ready`：RenpyThief 主线程仍挂起；GET、`createRequest` 和 JSON 观察钩子均成功启用。
2. 启动器恢复主线程，日志记录 `blocked_check=pending`。
3. `blocked_check`：真实版本检查已被替换响应，拦截计数至少为 1。
4. 第三步完成后，`guardlaunch` 向 GUI/PowerShell 报告更新保护已确认。

恢复后 20 秒内没有发生 `blocked_check`，但钩子没有报告失败且 RenpyThief 仍在运行时，启动器会发出“更新保护未确认”警告并继续启动。钩子仍驻留在进程内；已知版本接口如果稍后出现，仍会按上述规则拦截。此警告不表示更新请求已经放行，也不表示保护已经成功，只表示确认窗口内没有观察到已知接口。

DLL 注入失败、`hook_ready` 失败、钩子运行期失败、等待 API 失败或 RenpyThief 提前退出仍会中止启动并返回错误。日志不记录查询参数内容，只记录是否存在查询、端口是否显式、入口类型和计数。

## 已知边界

- 若未来版本不再使用 Qt `QNetworkAccessManager`、由派生类完全绕过基类入口，或把版本检查改为全新的主机/路径，本规则不会猜测或扩大匹配。
- 对全新且未知的更新 URL，启动器会因“未观察到 blocked_check”而发出警告后继续；无法证明未知请求在这段运行窗口内没有产生副作用。因此看到此警告时，更新保护状态就是未确认，未知版本仍需哈希/兼容性验证，不能宣称永久兼容。
- `hook_ready` 和 `blocked_check` 在 `versionguard.log` 中是不同状态，排障时不可混用。

`version_endpoint_test.exe` 覆盖端口、查询参数、大小写和相邻接口的正反例；RenpyThief 6.7.8 已完成真实启动测试，并确认日志最终出现 `state=blocked_check`。
