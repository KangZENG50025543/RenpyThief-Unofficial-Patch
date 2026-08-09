# v0.1.0 — 首个公开测试版

这是 RenpyThief 非官方翻译补丁的首个公开测试版本，当前只实测 RenpyThief 6.7.8（x86 / Qt 5.15.2）。

本项目源码采用 `GPL-3.0-only`；第三方组件适用其各自许可证。完整文本与依赖版本均包含在发布包中。

## 主要功能

- 可手动选择原版免费额度或用户自己的翻译 API。
- 支持 DeepSeek、SiliconFlow Hunyuan-MT、OpenAI-compatible、有道、百度和 Microsoft Translator。
- AI Provider 支持简洁直译、游戏本地化和自定义提示词。
- 凭据可仅在本次内存中使用，或保存到 Windows 凭据管理器。
- 默认启用精确的 RenpyThief 版本检查保护。
- 自定义翻译路由经过 Bridge、注入和动态监听端口确认后才报告就绪。
- 发布包自带 GUI 与 Bridge，最终用户无需安装 Python。

## 重要说明

- 本项目不包含 RenpyThief，仍需按原版要求登录。
- 自定义 API 模式会把游戏文本发送给用户选择的翻译平台，并可能产生费用。
- 更新保护不会屏蔽所有官方联网，也不能保证未来任意版本兼容。
- 补丁包含 DLL 注入组件且尚未代码签名，可能触发安全软件或 SmartScreen 的启发式警告。
- 不要全局关闭安全软件；请核对 Release 附带的 SHA-256，或自行审核并构建源码。

## 已知限制

- 不支持运行中切换线路。
- 不自动检测官方额度耗尽或自动回退。
- OpenAI-compatible 属于高级实验性功能。
- 翻译质量、速率、价格与地域限制由所选模型或平台决定。

请同时阅读发布包中的 `README.md` 和 `UPDATE_GUARD_CONTRACT.md`。

## 构建与校验

- Windows x64 便携包使用 Python.org 官方签名的 CPython 3.12.7 和 `requirements-lock.txt` 构建，不继承 conda 运行时。
- GUI/Bridge 冒烟测试、55 项离线 Python 测试和两项原生路由测试均已通过。
- Release 同时提供 PyQt5 5.15.11、Qt 5.15.2、MinHook 1.3.4 和 CPython 3.12.7 的锁定源码归档及 `SOURCE_ARCHIVES.SHA256`。
- 便携包未进行商业代码签名，仍可能触发 Windows SmartScreen 或安全软件的启发式提示。
