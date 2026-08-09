# 参与贡献

感谢你愿意改进 RenpyThief 非官方翻译补丁。项目欢迎修复、测试、文档和兼容性改进，但不会接受绕过登录、注册、额度或付费授权，以及重新分发 RenpyThief 或其他第三方专有文件的改动。

## 报告问题

- 普通故障和兼容性问题请使用 Bug 模板。
- 功能建议请使用功能建议模板。
- 可能泄露凭据、允许任意代码执行或破坏安全边界的问题，请按 [SECURITY.md](SECURITY.md) 私下报告，不要公开细节。
- 提交日志前必须人工检查并删除 API Key、Cookie、账号、路径中的个人信息、游戏正文和译文。
- 不要上传 RenpyThief 原版程序、游戏文件、注入器、内部分析资料、用户数据目录或未经检查的日志压缩包。

## 本地开发

项目以 Windows 和 Python 3.12 为主要开发环境：

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

运行公开源码测试：

```powershell
$env:QT_QPA_PLATFORM = 'offscreen'
python -m unittest discover -s tests -v
python .\run_patch.py --smoke-test
```

这些测试不需要 API Key、原版 RenpyThief、游戏文件或预编译路由组件。测试不得连接真实翻译账户或产生费用。

原生 x86 组件的环境、构建和测试方式见 [native/README.md](native/README.md)。请不要把本机构建出的 `.exe`、`.dll`、`.lib`、`.pdb` 或 `native/build/` 提交到源码仓库。

## Pull Request

1. 先搜索现有 Issue 和 Pull Request；较大的功能建议先开 Issue 讨论边界。
2. 每个 Pull Request 聚焦一个问题，避免同时进行无关重构。
3. 为行为变化补充或更新测试；同步更新用户可见文档。
4. 提交前运行上述离线测试，并在 Pull Request 中说明未能运行的检查。
5. 不要修改或提交用户凭据、原版程序、发布构建产物和内部分析资料。

维护者可能要求调整设计，以维持“明确选择线路、默认不泄露正文或凭据、失败时不静默降级”的安全边界。

## 许可证

除已注明的第三方内容外，本项目采用 `GPL-3.0-only`。提交贡献即表示你有权提供相关内容，并同意按同一许可证发布。请保留现有版权和第三方许可证声明；引入新依赖时应同时说明来源、精确版本、许可证和对应源码获取方式。
