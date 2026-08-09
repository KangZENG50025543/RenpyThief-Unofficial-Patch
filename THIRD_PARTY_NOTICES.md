# 第三方组件与许可说明

本文件记录补丁直接使用或由便携构建打包的主要第三方组件。已锁定的 Python 依赖版本见 `requirements-lock.txt`，当前收集到的许可证原文见 `licenses/`。正式发布者仍应检查最终 ZIP 中的每一个依赖。

当前 Windows 构建基线：Python 3.12.7、PyQt5 5.15.11、Qt 5.15.2、keyring 25.7.0、PyInstaller 6.22.0 和 PyInstaller hooks contrib 2026.6。

## PyQt5

GUI 使用 PyQt5。Riverbank Computing 将 PyQt 以 GNU GPL v3 或商业许可证双重许可；PyQt 不提供 LGPL 版本。当前构建使用 GPL 版 PyQt5，本项目因此选择兼容的 `GPL-3.0-only`，并随源码和发布包提供根目录 `LICENSE`。

- 官方说明：https://riverbankcomputing.com/software/pyqt
- 官方许可 FAQ：https://riverbankcomputing.com/commercial/license-faq

## Qt 5

PyQt5 二进制 wheel 会包含相应的 Qt 库。Qt 的开源版本包含 LGPL v3/GPL v3 组件；发布者必须按实际打包模块履行通知、许可证文本、对应源码和可替换/重新链接等义务。当前 wheel 随附文本已保存为 `licenses/Qt-5.15.2-LICENSE.txt`。

- 官方开源许可说明：https://www.qt.io/development/download-open-source
- 官方 LGPL/GPL 义务说明：https://www.qt.io/development/open-source-lgpl-obligations

## MinHook

`ipcroute.dll` 和 `versionguard.dll` 静态包含 MinHook 及其 HDE32 代码。二进制再分发必须在文档或随附材料中保留 MinHook/HDE 的版权、条件和免责声明。完整文本见 `licenses/MinHook-LICENSE.txt`。

- 上游项目：https://github.com/TsudaKageyu/minhook

## PyInstaller

便携版由 PyInstaller 生成。PyInstaller 的 GPL 许可带有用于分发所构建应用的特殊例外；最终应用仍必须遵守自身代码和其他依赖的许可证。相关文本见 `licenses/PyInstaller-6.22.0-COPYING.txt` 和 `licenses/PyInstaller-hooks-contrib-2026.6-LICENSE.txt`。

- 官方说明：https://pyinstaller.org/en/stable/license.html

## keyring 与 Python

GUI 使用 `keyring` 访问 Windows 凭据管理器，便携版同时包含 Python 运行时。相应文本见 `licenses/keyring-25.7.0-LICENSE.txt` 和 `licenses/Python-3.12-LICENSE.txt`。

- keyring：https://github.com/jaraco/keyring
- Python：https://docs.python.org/3/license.html

## 其他锁定依赖

`licenses/` 还保存了 PyQt5-sip、altgraph、jaraco、more-itertools、packaging、pefile、pywin32-ctypes 和 setuptools 的随包许可证。部分只在构建阶段使用，但仍统一保留，便于审计和复现。

## 发布前检查

公开分发前仍应确认最终 ZIP 与 Git 标签一致，并按 `SOURCE_AVAILABILITY.md` 为本项目及 Qt/PyQt 等 GPL/LGPL 组件提供对应源码或符合法律文本的获取方式。本说明不是法律意见；若对义务有疑问，应咨询熟悉开源许可证的专业人士。
