# GitHub 发布流程

本文面向项目维护者。所有 Git 操作都必须在独立的 `UnofficialPatch` 仓库中执行；不要在上级原软件目录或整个 Windows 用户目录中执行。

## 1. v0.1.1 发布目标

本版本同时提供两个面向最终用户的 Windows x64 资产：

- `RenpyThiefPatch-v0.1.1-setup-x64.exe`：推荐安装版；包含开始菜单快捷方式、默认勾选的可选桌面快捷方式、使用说明入口和卸载项。
- `RenpyThiefPatch-v0.1.1-portable-x64.zip`：开箱即用便携版；完整解压后运行，不需要安装 Python。

两者必须由同一提交构建、功能一致，并对应同一个不可变标签 `v0.1.1`。不得把原版 RenpyThief、游戏文件、用户凭据或分析资料打进任何资产。

## 2. 发布前准备

- 确认版本号、README、包内 `QUICK_START.txt`、安装器显示和文件名均为 `0.1.1`。
- 确认项目仍采用 `GPL-3.0-only`，根目录 `LICENSE`、`COPYRIGHT` 和 `THIRD_PARTY_NOTICES.md` 完整存在。
- 按 `SOURCE_AVAILABILITY.md` 确认标签公开完整补丁源码，并落实 Qt、PyQt 等第三方组件的精确对应源码。
- 运行 `scripts\prepare_release_sources.ps1` 下载四个锁定源码归档，再以 `-VerifyOnly` 复核。
- 使用干净的 Python 3.12 虚拟环境安装 `requirements-lock.txt`，编译并测试 `native\` 中的 x86 组件。
- 确认 README 声明的 RenpyThief 支持范围与实际测试一致。
- 确认仓库、安装版和便携版中没有 API Key、用户数据、原版程序、游戏文件、PDB、抓包、分析日志或游戏正文日志。
- 确认 Release 首屏把安装版、便携版、第三方对应源码和 GitHub 自动生成的源码包区分清楚。

## 3. 构建发布资产

构建虚拟环境必须位于纯英文路径；项目源码目录可以包含中文：

```powershell
$buildVenv = Join-Path $env:LOCALAPPDATA 'RenpyPatchBuild\venv-0.1.1'
python -m venv $buildVenv
& "$buildVenv\Scripts\python.exe" -m pip install -r .\requirements-lock.txt
.\build_release.ps1 -Version 0.1.1 -Python "$buildVenv\Scripts\python.exe" `
  -PublicRelease -IsccPath 'C:\path\to\Inno Setup 6\ISCC.exe'
```

应生成或准备好：

```text
release\RenpyThiefPatch-v0.1.1-portable-x64\
release\RenpyThiefPatch-v0.1.1-portable-x64.zip
release\RenpyThiefPatch-v0.1.1-setup-x64.exe
release\SHA256SUMS.txt
release\source-assets-v0.1.1\PyQt5-5.15.11.tar.gz
release\source-assets-v0.1.1\qt-everywhere-src-5.15.2.tar.xz
release\source-assets-v0.1.1\MinHook-1.3.4-c3fcafdc10146beb5919319d0683e44e3c30d537.zip
release\source-assets-v0.1.1\Python-3.12.7.tar.xz
release\source-assets-v0.1.1\SOURCE_ARCHIVES.SHA256
```

准备并复核第三方对应源码附件：

```powershell
.\scripts\prepare_release_sources.ps1 -Version 0.1.1 `
  -OutputDirectory .\release\source-assets-v0.1.1
.\scripts\prepare_release_sources.ps1 -Version 0.1.1 `
  -OutputDirectory .\release\source-assets-v0.1.1 -VerifyOnly
```

依赖版本没有变化时可以复用已验证且散列完全相同的第三方源码字节，但仍须在 v0.1.1 目录执行 `-VerifyOnly`；不得复用旧版补丁 EXE、安装器或 ZIP。

## 4. 包内容与冒烟测试

便携包和安装后的程序目录至少应包含：

- `RenpyThiefPatch.exe`
- `LaunchPatch.cmd`
- `QUICK_START.txt`
- `router\translate_bridge.exe`
- `router\ipcroute.dll`
- `router\netinject.exe`
- `router\guardlaunch.exe`
- `router\versionguard.dll`
- `router\versionguard.ini`
- `README.md`、`UPDATE_GUARD_CONTRACT.md`
- `LICENSE`、`COPYRIGHT`、`THIRD_PARTY_NOTICES.md`
- `SOURCE_AVAILABILITY.md`、`THIRD_PARTY_SOURCE_MANIFEST.txt`
- `DEPENDENCIES.txt` 和 `licenses\` 下的许可证文本

先测试便携版：

```powershell
.\release\RenpyThiefPatch-v0.1.1-portable-x64\RenpyThiefPatch.exe --smoke-test
.\release\RenpyThiefPatch-v0.1.1-portable-x64\router\translate_bridge.exe --help
```

上面的 `build_release.ps1 -PublicRelease` 已经使用 Inno Setup 6.7.3 构建安装器。仅在调试安装器或便携目录未变化时，才单独运行：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\build_installer.ps1 `
  -SourceDirectory .\release\RenpyThiefPatch-v0.1.1-portable-x64 `
  -OutputDirectory .\release
```

安装器定义会在编译期严格检查 Inno Setup Compiler（ISCC）版本必须为 6.7.3；若未安装在自动检测路径，须用 `-IsccPath 'D:\path\to\ISCC.exe'` 显式指定。

再在干净的 Windows 用户环境或虚拟机中测试安装器：

1. 安装到默认目录，确认开始菜单、使用说明和卸载项存在。
2. 确认桌面快捷方式默认勾选且可以取消。
3. 从快捷方式启动并完成 GUI 冒烟测试。
4. 从 Windows“已安装的应用”执行卸载，确认只移除本项目安装文件，不删除用户的其他程序或凭据。
5. 测试覆盖安装/升级路径，并确认不会捆绑或查找未经用户选择的原版 RenpyThief。

运行统一发布门禁，并核对其同时验证安装器和便携包：

```powershell
.\scripts\preflight_release.ps1 `
  -Version 0.1.1 `
  -Python "$buildVenv\Scripts\python.exe"
```

`SHA256SUMS.txt` 必须严格包含按文件名排序的安装器和便携 ZIP 两行散列；第三方源码归档由 `SOURCE_ARCHIVES.SHA256` 单独校验。发布前使用 `Get-FileHash -Algorithm SHA256` 独立抽查。

## 5. 提交、标签与 GitHub Release

先提交 v0.1.1 修改并推送 `main`，确认工作树干净、CI 通过，再检查和标记该提交：

```powershell
git status --short
git diff --check
git log -1 --show-signature
git tag -a v0.1.1 -m "v0.1.1"
git push origin v0.1.1
```

不要覆盖、移动或重新使用已经发布的标签。若标签后发现构建错误，应修复后使用新版本号。

在 GitHub Release 页面：

1. 选择标签 `v0.1.1`，标题填写 `v0.1.1 — 安装版与开箱即用便携版`。
2. 以 `RELEASE_NOTES.md` 为说明；确认折叠前即可看到“普通用户请下载这里”和两个准确文件名。
3. 上传安装器、便携 ZIP、`SHA256SUMS.txt`、四个锁定第三方源码归档和 `SOURCE_ARCHIVES.SHA256`。
4. 视项目稳定程度保留 **Pre-release** 标记。
5. 发布前检查附件名、大小和散列，尤其不能把本机 RenpyThief 或内部 ZIP 误传。

GitHub CLI 示例：

```powershell
gh release create v0.1.1 `
  .\release\RenpyThiefPatch-v0.1.1-setup-x64.exe `
  .\release\RenpyThiefPatch-v0.1.1-portable-x64.zip `
  .\release\SHA256SUMS.txt `
  .\release\source-assets-v0.1.1\PyQt5-5.15.11.tar.gz `
  .\release\source-assets-v0.1.1\qt-everywhere-src-5.15.2.tar.xz `
  .\release\source-assets-v0.1.1\MinHook-1.3.4-c3fcafdc10146beb5919319d0683e44e3c30d537.zip `
  .\release\source-assets-v0.1.1\Python-3.12.7.tar.xz `
  .\release\source-assets-v0.1.1\SOURCE_ARCHIVES.SHA256 `
  --title "v0.1.1 — 安装版与开箱即用便携版" `
  --notes-file .\RELEASE_NOTES.md `
  --prerelease
```

## 6. 发布后验证

- 使用未登录浏览器打开 Release，确认普通用户无需展开说明即可选对安装版或便携版。
- 重新下载两个用户包和校验文件，复核远程文件的 SHA-256。
- 在干净机器上分别执行一次安装、启动、卸载和便携版解压启动。
- 确认 GitHub Actions 对标签对应提交通过，标签源码可下载，第三方对应源码资产持续可用。
- 若下载说明或校验值错误，先撤下有问题的二进制并发布更正版本，不要静默替换同名资产。
