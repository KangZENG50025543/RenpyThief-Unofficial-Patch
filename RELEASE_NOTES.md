# v1.0.4.0 — 测试版：关掉 Ren'Py 脚本层，试通用注入器明文路由

这是**测试版**，不是正式版。正式可用版本仍是 `v1.0.3`。本号用来验证：不写 `00unofficial_bridge.rpy` 时，原版会不会改走 `RenpyInjector`，从而让新的 `injectroute` 接到明文。

## 普通用户请下载这里

只需二选一，**不要两个都下载**：

| 文件 | 用途 |
|---|---|
| **`RenpyThiefPatch-v1.0.4.0-setup-x64.exe`** | 测试安装版 |
| **`RenpyThiefPatch-v1.0.4.0-portable-x64.zip`** | 测试便携版；完整解压后运行 `RenpyThiefPatch.exe` |

`SHA256SUMS.txt` 用于校验。第三方锁定源码仍与 [v0.1.2](https://github.com/KangZENG50025543/RenpyThief-Unofficial-Patch/releases/tag/v0.1.2) 相同。

当前只实测 **RenpyThief 6.7.8（x86 / Qt 5.15.2）**。

## 这次测什么

- **Ren'Py 脚本层已关闭**：不会再把 `00unofficial_bridge.rpy` 写入游戏；若游戏 `game\` 里还留着上一版的该文件，拖入时会删掉。
- **通用注入器明文路由**：若原版拉起 `RenpyInjector-x86.exe`，补丁会注入 `injectroute.dll`，把本机 JSON 明文转到你的 API。官方密文包仍 fail-closed，不解密、不喂模型。
- 关掉脚本层**不等于**原版一定改走通用注入器。Ren'Py 仍可能走 `RenpyHook` 密文内嵌。这次就是要看实际走哪条。

## 测试步骤

1. 完全退出旧补丁、RenpyThief 和游戏。
2. 用本测试包启动，选「我的 API」，等到“已就绪”。
3. 拖入 PatchSmokeVN 或其它 Ren'Py 游戏。弹出「请选择翻译样式」时仍选「使用内嵌样式」。
4. 看任务管理器：
   - 出现 `RenpyInjector-x86.exe`，控制台有 `Routed generic injector plaintext`，游戏里有中文 → 通用方法对 Ren'Py 可能成立。
   - 只有 `RenpyInject32/64` / Hook，游戏没有中文 → 原版仍走专用内嵌，还不能废脚本层。
5. 把上述现象告诉维护者即可，不必发密钥或完整台词。

## 已知限制

- 正式版 `v1.0.3` 的 Ren'Py 脚本层在本号是故意关掉的，Ren'Py 内嵌可能暂时没有中文。
- `injectroute` 目前只支持 32 位 `RenpyInjector-x86.exe`。
- 「翻译弹窗样式」仍走官方 `sendTranslate` 密文，不能用你的 API 填窗。
