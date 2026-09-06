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

## 自定义 API 下的会话与配置兼容

「我的 API」启动链会把 `versionguard.dll` 复制到隔离 runtime，并写入：

```ini
[versionguard]
mode=lock
local_version=auto
session_compat=lock
config_compat=deny
translate_compat=lock
```

仓库内默认 ini 仍是 `session_compat=observe`、`config_compat=pass`、`translate_compat=pass`，只保护版本检查。启动器不再提供官方额度模式，因此这条默认配置不会被启动器启用。

`session_compat=lock` 时，下列官方接口在 Qt `QNetworkAccessManager` 层被替换为本地 `data:` JSON，不把请求发到 `api.renpy.fun`：

- `signIn`
- `checkUserUnReadMessageCount`
- `pingTest`
- `submitInject`
- `submitEndGame`

合成 JSON 只使用固定字段名和本地 EXE 文件版本；若原版目录存在非空 `user` 文件，仅读取 `username=` 且必须是安全 ASCII token。日志只写 `username_present=true/false`，不写用户名、口令或 Cookie。

`session_compat=lock` 时，`guardlaunch` 若发现原版目录中的 `user` **不存在或大小为 0**，会在启动前写入一份仅用于本机的会话标记，让全新安装也能通过原版拖入闸门。已有非空 `user` 文件不会被覆盖；官方额度模式（`session_compat=observe`）不会写入。该标记不是官方账号，不能用来领取官方额度。

`config_compat=deny` 时，已知游戏配置/补齐接口（如 `getGameConfig`、`getUnityHook`、`getV8`）在同一钩子内失败关闭，避免官方配置下载。未知官方路径保持透传，不做猜测。

`translate_compat=lock` 时，`sendTranslate` / `sendMenuTranslate` / `sendOCRTranslate` 不再发到 `api.renpy.fun`。同一套 Qt `QNetworkAccessManager` 钩子把请求改写到本机 Bridge 的 `http://127.0.0.1:19899/official-translate/<endpoint>`。封袋前若桌上还带 `text` + `translateType`，versionguard 只抽出这份明文，附到本机 URL 的 `text=`。`v1.1.0` 的 Bridge 读取 URL `text=`，调用用户 API，按官方信封 `{status,msg,data.text}` 回写译文。URL 上的 `text=` 优先于 POST 密文。只有密文、没有明文时仍 400。默认 Bridge 日志不记正文；`versionguard.log` 仍可能记下短台词片段（去程 `hub_plaintext` / `hub_reroute`，回程 `hub_inbound`），都不写官方 password / username。启动器不再提供官方额度模式；`translate_compat=pass` 仍可把这三条接口透传给官方，但不由本启动器启用。

`v1.1.0` 默认不注入 `ipcroute` / `injectroute`：第一跳密文回到主进程，由它解出桌上的 `text` + `translateType`。`Test-FirstHopHijackEnabled` 为 false 时不要提前截走三连口。脚本层继续关闭，不写 `00unofficial_bridge.rpy`。

版本检查的 `hook_ready` / `blocked_check` 契约不变：guardlaunch 仍只等待 `getVersionInfo` 被拦截。会话短路发生在主线程恢复之后，不作为启动器握手条件。
