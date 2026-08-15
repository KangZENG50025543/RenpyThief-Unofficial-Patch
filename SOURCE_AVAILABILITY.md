# 对应源码获取与发布要求

本文件记录安装版与便携版二进制的源码边界。它用于帮助发布者履行 GPL/LGPL
义务，不构成法律意见。

## 补丁自身源码

每个公开二进制版本必须对应一个不可变的 Git 标签。GitHub Release 的标签、
源码归档和二进制文件必须使用相同版本号；该标签必须包含生成发布包所需的
Python、PowerShell、C/C++ 源码、构建脚本和锁定依赖清单。

## 主要第三方源码

当前 `v1.0.2` 构建基线如下；依赖版本相对 `v0.1.2` 未发生变化：

| 组件 | 版本 | 锁定源码归档 | 摘要依据 |
|---|---:|---|---|
| PyQt5 | 5.15.11 | `PyQt5-5.15.11.tar.gz` | PyPI JSON API 发布的 SHA-256 |
| Qt | 5.15.2 | `qt-everywhere-src-5.15.2.tar.xz` | Qt 官方 MirrorBrain 页面发布的 SHA-256 |
| MinHook | 1.3.4 | `MinHook-1.3.4-c3fcafdc10146beb5919319d0683e44e3c30d537.zip` | 固定提交归档的独立双次下载校验值；上游未发布摘要 |
| Python | 3.12.7 | `Python-3.12.7.tar.xz` | Python.org Sigstore bundle 中的 SHA-256 |

精确 URL、字节数、SHA-256 和校验依据统一记录在
`THIRD_PARTY_SOURCE_MANIFEST.txt`。任何版本、URL 或摘要发生变化，都必须重新
审计并显式更新清单；不要在散列失败后直接用新下载结果覆盖旧值。

其余 Python 组件的精确版本见 `DEPENDENCIES.txt`（源码仓库中为
`requirements-lock.txt`），实际许可证文本见 `licenses/`。

## 生成 v1.0.2 源码附件

在仓库根目录运行：

```powershell
# 只解析清单、核对锁定版本并显示下载计划；不联网、不写文件
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\prepare_release_sources.ps1 `
  -Version 1.0.2 -OutputDirectory .\release\source-assets-v1.0.2 -DryRun

# 下载四个精确归档并逐个限制大小、校验 SHA-256
# v1.0.2 发布目录为 release\source-assets-v1.0.2，总下载量约 582 MiB，Qt 占绝大部分
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\prepare_release_sources.ps1 `
  -Version 1.0.2 -OutputDirectory .\release\source-assets-v1.0.2

# 上传前再次进行纯本地校验
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\prepare_release_sources.ps1 `
  -Version 1.0.2 -OutputDirectory .\release\source-assets-v1.0.2 -VerifyOnly
```

下载脚本具有以下门禁：

- 只接受清单中的 HTTPS 上游地址和完整 SHA-256；缺失、占位或格式错误会停止；
- 下载流不得超过锁定字节数，已有文件不会被覆盖；
- 已有文件、重定向结果、最终长度或 SHA-256 不符时立即停止；
- 成功后生成 `SOURCE_ARCHIVES.SHA256`。该文件与四个源码归档一起，在**首次**锁定这些字节的 GitHub Release（当前为 v0.1.2）中提供。后续版本只要清单散列不变，就引用该基线 Release，不要把约 560 MiB 的 Qt 归档再传一遍。

MinHook 的上游没有为 GitHub 自动生成的源码归档发布摘要。本仓库将
`v1.3.4` 固定到提交 `c3fcafdc10146beb5919319d0683e44e3c30d537`，并记录了
2026-08-09 两次独立 HTTPS 下载一致的 SHA-256。GitHub 将来若改变自动归档的
封装方式，脚本会安全失败；发布者应检查归档树仍对应该提交后再有意更新清单。

Qt 归档约 560 MiB。依赖未变时不要每次把该文件重新上传到新的 Release。
本机构建或审计仍可用 `-VerifyOnly` 核对已有副本；公开发布则引用
`v0.1.2` 已托管的同一组字节，直到清单散列发生变化。

## 构建来源风险

`PyQt5-Qt5==5.15.2` wheel 会向便携程序提供 Qt 库。Qt 官方 5.15.2 源码归档
是已知基线；若 wheel 构建者应用了额外补丁或不同构建配方，还必须同时归档这些
补丁/配方，官方基线归档本身不能证明与二进制逐字可复现。

当前构建记录写明 Python 3.12.7。若最终发布程序实际使用 Anaconda/conda 或其他
发行版的 Python 运行时，应额外记录其精确发行包、构建号、recipe 和补丁；
Python.org 的 CPython 源码归档只记录上游基线。

## 公开二进制发布门禁

公开发布者必须在上传二进制文件前完成以下事项：

1. 推送与二进制版本一致的完整 Git 标签，并保持源码可获取。
2. 记录最终构建实际使用的依赖版本；不得用“相近版本”代替。
3. 为随包分发的 GPL/LGPL 组件提供精确对应源码，或落实许可证正文允许的
   其他等效提供方式。仅记录一个可能消失的第三方链接不自动保证合规。
4. 每次发布安装版或便携版时，必须让对应源码保持可获取：清单未变则引用首次发布这些字节的
   GitHub Release（当前为 `v0.1.2`）；清单变化则把新的归档和 `SOURCE_ARCHIVES.SHA256`
   作为新 Release 的附加资产，并更新引用基线。
5. 二进制仍可下载期间，应持续保证相应源码获取方式有效；不要删除仍被后续版本引用的基线附件。

项目发布包会随附本文件、顶层 `LICENSE`、`THIRD_PARTY_NOTICES.md`、
`DEPENDENCIES.txt` 和 `licenses/`，但发布者仍需核对 Release 页面上的源码资产。
