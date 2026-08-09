# RenpyThief 非官方翻译补丁

一个面向 RenpyThief 的独立社区启动器：既可以继续使用原版免费额度，也可以把翻译请求路由到用户自己的 AI API 或专业机器翻译平台。

> [!IMPORTANT]
> 本项目与 RenpyThief 官方没有隶属、授权或合作关系，不包含也不重新分发 RenpyThief。使用本补丁仍需自行合法取得原版软件，并遵守原软件、游戏和翻译平台的服务条款。

> [!WARNING]
> 当前版本只实测了 **RenpyThief 6.7.8（x86 / Qt 5.15.2）**。其他版本可能无法启动、无法保护版本检查或无法激活翻译路由。本补丁不会绕过登录、注册、额度或付费授权。

## 功能概览

- 在“官方免费额度”和“我的 API”之间手动选择，不会静默切换线路或产生意外费用。
- 自定义线路只有在本地 Bridge、RenpyThief 和进程级路由全部确认后，才会提示拖入游戏。
- 支持 DeepSeek、SiliconFlow Hunyuan-MT 和 OpenAI-compatible AI 服务。
- 支持有道智云、百度翻译开放平台和 Microsoft Translator 专用接口。
- AI 翻译提供“简洁直译”“游戏本地化”和自定义提示词。
- 自定义提示词框默认预填模板 1，并支持 `{source}`、`{target}`、`{text}`。
- API 凭据可仅在本次运行中使用，也可保存到 Windows 凭据管理器。
- 默认精确保护 RenpyThief 已知版本检查；关闭前会明确警告。
- 不修改系统代理、DNS、hosts、证书、防火墙或全局网络设置。
- 默认日志不记录游戏原文、译文、Authorization、Cookie 或 API Key。

## 下载与安装

面向普通用户时，建议使用 GitHub Releases 中的便携编译版，而不是直接下载仓库源码。

1. 打开仓库右侧的 **Releases**。
2. 下载最新的 `RenpyThiefPatch-vX.Y.Z-windows-x64.zip` 和 `SHA256SUMS.txt`。
3. 校验 ZIP 的 SHA-256。
4. 将 ZIP 完整解压到当前用户可写目录，例如 `D:\Tools\RenpyThiefPatch`。
5. 运行 `RenpyThiefPatch.exe`；也可以双击 `LaunchPatch.cmd`。

便携版已经包含 GUI 和翻译 Bridge，最终用户不需要安装 Python。请勿直接在压缩包内运行，也不建议放入 `Program Files`。补丁最好与 RenpyThief 分开放置，避免原版更新器影响补丁文件。

校验 SHA-256：

```powershell
(Get-FileHash .\RenpyThiefPatch-vX.Y.Z-windows-x64.zip -Algorithm SHA256).Hash
```

## 使用要求

- 64 位 Windows；补丁内同时包含面向 32 位 RenpyThief 的原生组件。
- 用户自行安装或解压的 `RenpyThief.exe`。
- 按原版要求完成登录；本补丁不绕过原版登录。
- 使用“我的 API”时，需要对应平台的有效凭据。
- 本地端口 `127.0.0.1:19899` 未被其他程序占用。
- 补丁目录可写。

## 快速开始

1. 启动 `RenpyThiefPatch.exe`。
2. 选择你自己的 `RenpyThief.exe`。
3. 建议保持“拦截 RenpyThief 自动更新”开启。
4. 选择翻译来源：

   - 想先使用原版额度：选择“官方免费额度”。
   - 想使用自己的翻译服务：选择“我的 API”。

5. 使用自己的 API 时，选择 Provider 并填写凭据。
6. 可先点击“测试 API”。测试只发送固定文本 `こんにちは`，不会发送游戏内容。
7. 点击启动。
8. 等待状态明确显示“已就绪，可以拖入游戏”。
9. 此时再把游戏拖入 RenpyThief。

切换线路前，请先关闭游戏并正常关闭 RenpyThief，再从补丁中重新启动。当前版本不支持运行中热切换。

## 两种翻译来源

| 模式 | 翻译由谁提供 | 用户凭据 | 自定义翻译路由 |
|---|---|---|---|
| 官方免费额度 | RenpyThief 原版服务 | 不读取 | 不启用 |
| 我的 API | 用户选择的第三方平台 | 需要 | 启用本地 Bridge 与进程级路由 |

更新保护是独立选项。即使使用官方额度，开启更新保护时仍会加载专门的版本检查保护组件，但不会启用自定义翻译路由。

补丁不会自动检测官方额度是否耗尽，也不会在官方线路失败时自动切换到用户 API。

## 支持的翻译平台

| Provider | 所需凭据 | 默认设置与说明 |
|---|---|---|
| DeepSeek | API Key | `deepseek-v4-flash`；极速模式关闭思考，高质量模式启用高强度思考 |
| SiliconFlow · Hunyuan-MT | API Key | `tencent/Hunyuan-MT-7B`；实验性的专用翻译模型 |
| OpenAI-compatible | API Key、Base URL、模型名 | 服务必须兼容 `/chat/completions` 请求与响应格式 |
| 有道智云 | 应用 ID、应用密钥 | 请求在本机按有道规则签名 |
| 百度翻译开放平台 | APP ID、密钥 | 当前按标准版保守限制为单并发、约 1 QPS |
| Microsoft Translator | 订阅密钥、可选 Region | 全局单服务资源通常可以不填 Region；其他资源按 Azure 配置填写 |

服务是否收费、免费额度、地域可用性及数据处理政策均由各平台决定。请在对应平台控制台确认价格和配额。

## AI 提示词

AI Provider 支持三种提示词模式：

- 模板 1：简洁、直接，尽量保持原文结构。
- 模板 2：更偏向自然的游戏本地化。
- 自定义：自行编辑完整提示词。

自定义框默认显示模板 1，支持以下占位符：

- `{source}`：源语言。
- `{target}`：目标语言。
- `{text}`：待翻译文本。

如果自定义提示词中没有 `{text}`，Bridge 会自动把原文追加到提示词末尾。专用翻译平台不使用 AI 提示词。

自定义提示词会保存到本机设置文件，因此不要在提示词中填写 API Key、Cookie 或其他秘密。

## 更新保护

更新保护默认开启。它会在 RenpyThief 启动早期精确识别当前已知的版本检查，并返回与本地程序相容的响应。启动器只有在观察到真实版本检查已被处理后，才会报告保护成功。

它不会：

- 修改或替换 `RenpyThief.exe`。
- 屏蔽所有官方网络请求。
- 修改登录、注册、额度、翻译授权或游戏配置结果。
- 保证任意未来版本都自动兼容。
- 修改系统代理、hosts、防火墙或证书。

如果保护组件加载失败，或在限定时间内没有确认真实拦截，受保护启动会中止，不会留下一个看似正常但实际上未受保护的 RenpyThief 进程。

关闭更新保护后，RenpyThief 可能自动升级。新版可能导致路由失效；如果补丁文件被放进原版目录，更新器还可能覆盖或清除兼容组件。因此只有明确接受风险时才应关闭。

更精确的接口匹配与失败条件见 [UPDATE_GUARD_CONTRACT.md](UPDATE_GUARD_CONTRACT.md)。

## 隐私、凭据与网络边界

普通设置保存在：

```text
%LocalAppData%\RenpyThiefUnofficialPatch\settings.json
```

该文件不会保存 API Key，但会保存 Provider、模型、Base URL、并发、缓存和自定义提示词。

勾选“使用 Windows 凭据管理器安全保存”时，凭据按 Provider 分别保存到当前 Windows 用户的凭据管理器。未勾选时，凭据仅供当前运行使用，并会删除该 Provider 先前保存的凭据。

启动过程中，凭据只通过本地进程环境交给 Bridge，不写入命令行或普通设置；补丁还会在启动 RenpyThief 和游戏前剔除相关环境变量。密钥在发出请求时仍会短暂存在于 Bridge 进程内存中。

网络边界：

- 官方额度模式仍由 RenpyThief 与其官方服务通信。
- “我的 API”模式会把待翻译文本发送给用户选择的翻译平台。
- RenpyThief 仍可能访问登录、配置和其他原版接口；本补丁不是完全离线替代品。
- 补丁不会把一个 Provider 的密钥发送给另一个 Provider。

默认日志只记录运行阶段、耗时、字符数量和不可逆文本指纹，不记录正文或响应错误体。成功译文会在 Bridge 进程内存中进行有上限的会话缓存；进程退出后缓存消失，不持久化到磁盘。

“本地不记录正文”不代表翻译平台看不到正文。请根据所选平台的隐私政策决定是否发送敏感内容。

## 高级设置

默认值适合大多数用户：

- 本地并发：64。
- 上游并发：4。
- 内存缓存条目：2048。
- 内存缓存上限：16 MiB。

百度标准版会在 Bridge 内进一步限制为单并发。盲目提高上游并发可能增加限流、延迟或费用；如果不了解对应平台限制，建议保持默认值。

## 常见问题

### 为什么仍然出现登录页面？

这是预期行为。本补丁保留原版登录和授权流程。未满足原版登录条件时，RenpyThief 仍可能拒绝拖入游戏或停止后续注入。

### 为什么一定要等待“已就绪，可以拖入游戏”？

路由需要先确认 Bridge、注入组件和 RenpyThief 动态监听端口全部就绪。过早拖入游戏可能使第一批请求仍走原版线路。

### 可以先用免费额度，再切换到自己的 API 吗？

可以，但不能热切换。请先关闭游戏和 RenpyThief，然后选择另一模式重新启动。

### 更新保护开启后，未来版本一定能用吗？

不能。它只保护当前已知版本检查流程。若官方更换端点、网络栈或本地监听实现，补丁会明确失败，需要发布兼容更新。

### 为什么安全软件或 SmartScreen 可能报警？

补丁需要把路由和版本保护 DLL 加载到 RenpyThief 进程中，这类行为可能触发启发式检测；当前公开测试版也没有代码签名。

不要因为本项目而全局关闭安全软件。请只从本项目 GitHub Release 下载、核对 SHA-256，或者自行审核并构建源码。如果仍不信任，请不要运行。

### 密钥会写进日志或命令行吗？

GUI 启动流程不会把凭据写入设置文件、日志或命令行。凭据只会提供给本地 Bridge，并在请求时发送到所选 Provider。

### 为什么提示端口 19899 被占用？

先关闭旧的补丁或 Bridge 进程，并确认没有其他程序监听该端口，然后重新启动。补丁不会复用不受控制的已有监听器。

## 已知限制

- 目前仅实测 RenpyThief 6.7.8 x86。
- 仍依赖原版登录、引擎识别、游戏资源部署和注入流程。
- 不支持运行中切换线路。
- 不自动检测官方额度耗尽。
- 不自动回退到其他 Provider。
- OpenAI-compatible 属于高级实验性功能。
- 翻译质量主要由所选模型或平台决定。
- 不同平台可能存在请求长度、速率、费用和地域限制。
- 更新保护无法预测官方未来的全新实现。
- 当前没有代码签名，Windows 或安全软件可能显示未知发布者。
- 补丁本身不修改游戏配置，但原版 RenpyThief 在正常流程中仍可能按其自身逻辑部署游戏侧文件。

## 从源码运行

建议使用独立的 Python 3.12 虚拟环境：

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python .\run_patch.py
```

也可以双击 `启动非官方补丁.cmd`。

源码仓库不提交预编译的 `.exe` 或 `.dll`。GUI 本身可以从源码打开；如需真正启动路由，还应先按 [native/README.md](native/README.md) 编译 x86 原生组件，使四个运行文件出现在 `router\` 中。

运行全部 Python 测试：

```powershell
$env:QT_QPA_PLATFORM = 'offscreen'
python -m unittest discover -s tests -v
```

## 构建便携版

先准备好 `router\` 中的四个原生运行文件。由于 PyInstaller 的 Qt 收集逻辑在部分版本中不能正确处理含中文的虚拟环境路径，**发布构建用的 venv 必须放在纯英文路径**：

```powershell
$buildVenv = Join-Path $env:LOCALAPPDATA 'RenpyPatchBuild\venv-0.1.0'
python -m venv $buildVenv
& "$buildVenv\Scripts\python.exe" -m pip install -r .\requirements-lock.txt
.\build_release.ps1 -Version 0.1.0 -Python "$buildVenv\Scripts\python.exe"
```

脚本会生成：

```text
release\RenpyThiefPatch-v0.1.0-windows-x64\
release\RenpyThiefPatch-v0.1.0-windows-x64.zip
release\SHA256SUMS.txt
```

正式公开发布时应使用 `-PublicRelease`；如果项目根目录还没有 `LICENSE`，脚本会拒绝生成公开发布包：

```powershell
.\build_release.ps1 -Version 0.1.0 -Python "$buildVenv\Scripts\python.exe" -PublicRelease
```

建议在干净虚拟环境中构建，并记录最终依赖版本。`build\`、`dist\`、`release\` 和原生二进制不应提交进 Git 历史。

## 仓库结构

```text
UnofficialPatch/
├─ src/renpy_patch/          GUI、配置、凭据与启动控制
├─ router/                   翻译 Bridge、PowerShell supervisor 与运行配置
├─ native/                   x86 路由/更新保护源码和构建脚本
├─ tests/                    GUI、配置、Provider 与 Bridge 离线测试
├─ packaging/               便携版启动文件
├─ licenses/                可再分发第三方组件的许可证文本
├─ build_release.ps1        GUI、Bridge、ZIP 与 SHA-256 构建脚本
└─ UPDATE_GUARD_CONTRACT.md 更新保护的精确契约
```

`router\*.exe` 和 `router\*.dll` 只是本地构建产物，不属于源码提交内容。

## 提交问题

普通故障与功能建议请使用仓库的 Issue 模板；参与开发前请阅读 [CONTRIBUTING.md](CONTRIBUTING.md)。可能涉及密钥泄露、任意代码执行或其他可利用安全问题时，不要公开技术细节，请按 [SECURITY.md](SECURITY.md) 使用 GitHub 私有漏洞报告。

Issue 中请尽量提供：

- 补丁版本。
- Windows 版本。
- RenpyThief 文件版本和 32/64 位信息。
- 使用的 Provider 名称。
- 失败发生在哪个阶段。
- “打开诊断目录”中经过人工检查、确认不含隐私的相关日志。

请勿上传 API Key、Cookie、账号信息、完整游戏文本、`user`、`hwid`、原版程序或未经检查的整份日志。

## 许可证与声明

除另有说明的第三方组件外，本项目源码采用 [GNU General Public License v3.0 only](LICENSE)，SPDX 标识为 `GPL-3.0-only`。

你可以自由运行、研究、修改和再分发本项目。若向他人分发本项目或修改版的二进制文件，必须按 GPL v3 同时提供相应源码、保留许可证与版权声明，并允许接收者继续按相同权利使用和再分发。

GPL 不禁止商业使用或收费分发；它保护的是用户取得源码、修改和再分发的自由。本项目维护者当前计划免费发布，但这不会额外限制其他人在 GPL 条款内使用或分发。

本仓库维护者计划让官方源码和官方构建始终免费提供；这是项目承诺，不是对 GPL 接收者增加的“禁止商业使用”限制。

第三方组件继续适用其各自许可证，实际版本与许可证文本见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)、[SOURCE_AVAILABILITY.md](SOURCE_AVAILABILITY.md)、`DEPENDENCIES.txt` 和 `licenses/`。

本项目是独立社区项目，不代表 RenpyThief 或任何翻译平台。RenpyThief 名称及相关权利归其权利人所有。本项目不提供 RenpyThief、游戏文件、原版注入器、用户凭据或翻译平台额度。
