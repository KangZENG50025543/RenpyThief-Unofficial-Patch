# GitHub 发布流程

本文面向项目维护者。所有 Git 操作都必须在 `UnofficialPatch` 目录中执行，不能在它的上级原软件目录或整个 Windows 用户目录中执行。

## 1. 发布前准备

- 确认项目仍采用 `GPL-3.0-only`，根目录 `LICENSE` 完整存在。
- 确认根目录 `COPYRIGHT` 与实际贡献者信息没有冲突。
- 完成 `THIRD_PARTY_NOTICES.md` 中的许可证清单和随包文本。
- 按 `SOURCE_AVAILABILITY.md` 确认 Git 标签公开完整补丁源码，并落实 Qt/PyQt 等第三方组件的精确对应源码获取方式。
- 运行 `scripts\prepare_release_sources.ps1` 完整下载四个锁定源码归档，再以 `-VerifyOnly` 复核；不得只上传二进制 ZIP。
- 使用干净 Python 3.12 虚拟环境安装 `requirements-lock.txt`。
- 编译并测试 `native\` 中的 x86 组件。
- 确认只支持/实测的 RenpyThief 版本与 README 一致。
- 确认测试全部通过。
- 确认仓库和发布包中没有密钥、用户数据、原版程序、游戏文件或分析日志。

首个公开版本建议使用标签 `v0.1.0`，并在 GitHub 中标记为 **Pre-release**。

## 2. 建立独立仓库

先在 GitHub 创建一个空仓库，例如 `RenpyThief-Unofficial-Patch`。不要勾选自动生成 README、许可证或 `.gitignore`，避免首次推送冲突。

在本机执行：

```powershell
Set-Location 'D:\path\to\UnofficialPatch'
git init -b main
git rev-parse --show-toplevel
```

第二条命令必须输出 `...\UnofficialPatch`。如果输出整个 Windows 用户目录，立即停止，不要暂存任何文件。

首次提交前先检查：

```powershell
git status --short --ignored
git add .
git status --short
git diff --cached --stat
```

暂存区中不得出现：

```text
API.txt
API_siliconflow.txt
user
hwid
*.log
settings.json
RenpyThief.exe
RenpyUpdater.exe
router/*.exe
router/*.dll
build/
dist/
release/
FLOW_AND_INDEPENDENCE.md
```

确认后再提交和推送：

```powershell
git commit -m "Initial public beta"
git remote add origin https://github.com/你的用户名/你的仓库名.git
git push -u origin main
```

## 3. 构建发布资产

```powershell
$buildVenv = Join-Path $env:LOCALAPPDATA 'RenpyPatchBuild\venv-0.1.0'
python -m venv $buildVenv
& "$buildVenv\Scripts\python.exe" -m pip install -r .\requirements-lock.txt
.\build_release.ps1 -Version 0.1.0 -Python "$buildVenv\Scripts\python.exe" -PublicRelease
```

构建 venv 必须使用纯英文路径；项目源码目录可以包含中文。

应生成：

```text
release\RenpyThiefPatch-v0.1.0-windows-x64.zip
release\SHA256SUMS.txt
release\source-assets-v0.1.0\PyQt5-5.15.11.tar.gz
release\source-assets-v0.1.0\qt-everywhere-src-5.15.2.tar.xz
release\source-assets-v0.1.0\MinHook-1.3.4-c3fcafdc10146beb5919319d0683e44e3c30d537.zip
release\source-assets-v0.1.0\Python-3.12.7.tar.xz
release\source-assets-v0.1.0\SOURCE_ARCHIVES.SHA256
```

准备并复核第三方对应源码附件：

```powershell
.\scripts\prepare_release_sources.ps1
.\scripts\prepare_release_sources.ps1 -VerifyOnly
```

解压 ZIP 做一次最终冒烟测试，并核对以下内容存在：

```powershell
.\release\RenpyThiefPatch-v0.1.0-windows-x64\RenpyThiefPatch.exe --smoke-test
.\release\RenpyThiefPatch-v0.1.0-windows-x64\router\translate_bridge.exe --help
```

- `RenpyThiefPatch.exe`
- `LaunchPatch.cmd`
- `router\translate_bridge.exe`
- `router\ipcroute.dll`
- `router\netinject.exe`
- `router\guardlaunch.exe`
- `router\versionguard.dll`
- `router\versionguard.ini`
- `README.md`
- `UPDATE_GUARD_CONTRACT.md`
- `LICENSE`
- `COPYRIGHT`
- `THIRD_PARTY_NOTICES.md`
- `SOURCE_AVAILABILITY.md`
- `THIRD_PARTY_SOURCE_MANIFEST.txt`
- `DEPENDENCIES.txt`
- `licenses\` 下的第三方许可证文本

同时确认 ZIP 中不存在密钥、用户数据、原版程序、游戏文件、PDB、抓包数据或正文日志。

独立 Git 仓库建立并暂存正确文件后，可用统一门禁复核源码、测试、ZIP 清单和对应源码附件：

```powershell
.\scripts\preflight_release.ps1 `
  -Version 0.1.0 `
  -Python "$buildVenv\Scripts\python.exe"
```

## 4. 用 GitHub 网页发布

1. 进入仓库的 **Releases**。
2. 点击 **Draft a new release**。
3. 新建标签 `v0.1.0`，目标选择 `main`。
4. 标题填写 `v0.1.0 — 首个公开测试版`。
5. 将 `RELEASE_NOTES.md` 的内容粘贴到说明中。
6. 上传补丁 ZIP、`SHA256SUMS.txt`、四个锁定第三方源码归档和 `SOURCE_ARCHIVES.SHA256`。
7. 勾选 **Set as a pre-release**。
8. 发布前再次检查附件名称、哈希和警告说明。

## 5. 用 GitHub CLI 发布

安装并登录 GitHub CLI：

```powershell
winget install --id GitHub.cli
gh auth login
```

然后在项目目录运行：

```powershell
gh release create v0.1.0 `
  .\release\RenpyThiefPatch-v0.1.0-windows-x64.zip `
  .\release\SHA256SUMS.txt `
  .\release\source-assets-v0.1.0\PyQt5-5.15.11.tar.gz `
  .\release\source-assets-v0.1.0\qt-everywhere-src-5.15.2.tar.xz `
  .\release\source-assets-v0.1.0\MinHook-1.3.4-c3fcafdc10146beb5919319d0683e44e3c30d537.zip `
  .\release\source-assets-v0.1.0\Python-3.12.7.tar.xz `
  .\release\source-assets-v0.1.0\SOURCE_ARCHIVES.SHA256 `
  --title "v0.1.0 — 首个公开测试版" `
  --notes-file .\RELEASE_NOTES.md `
  --prerelease
```

发布后用无登录浏览器重新下载一次附件，并按 `SHA256SUMS.txt` 复核哈希。

## 6. 后续版本

- 先更新 `src\renpy_patch\__init__.py` 中的版本号。
- 运行完整测试并重新生成所有二进制，禁止复用旧 ZIP。
- 使用新的 Git 标签；已经发布的标签和附件不要覆盖。
- Release Notes 中明确列出兼容的 RenpyThief 版本、重要修复、已知限制和哈希。
