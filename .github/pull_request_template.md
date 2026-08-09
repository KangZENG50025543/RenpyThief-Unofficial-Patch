## 改动说明

请简要说明要解决的问题、采用的方案以及用户可见的变化。

关联 Issue：

## 验证

- [ ] 已在 Windows / Python 3.12 上运行 `python -m unittest discover -s tests -v`
- [ ] GUI 相关改动已设置 `QT_QPA_PLATFORM=offscreen` 并运行 `python .\run_patch.py --smoke-test`
- [ ] 原生组件改动已按 `native/README.md` 在 x86 配置下构建并运行对应测试（不适用时可注明）

## 提交检查

- [ ] 改动聚焦于一个明确问题，必要的文档和测试已同步更新
- [ ] 未提交 API Key、Cookie、账号信息、用户设置、完整日志或游戏正文
- [ ] 未提交 RenpyThief、游戏文件、第三方专有二进制或内部分析目录
- [ ] 我有权提交这些内容，并同意贡献按仓库的 `GPL-3.0-only` 许可证发布
