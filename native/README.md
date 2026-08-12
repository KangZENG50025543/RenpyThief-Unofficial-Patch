# 原生组件构建

此目录只包含补丁自有的 C/C++ 源码；不会包含 RenpyThief、Qt 或 MinHook 源码。

运行时组件必须编译为 **x86**，因为当前验证目标 RenpyThief 6.7.8 是 32 位程序。GUI 和本地翻译 Bridge 可以运行在 64 位 Windows 上。

## 依赖

- Visual Studio 2022，安装“使用 C++ 的桌面开发”和 x86 工具链；
- MinHook 源码树（当前发布基线已验证 v1.3.4）；
- 与目标程序 ABI 匹配的 Qt 5.15.2 头文件。

`versionguard.dll` 通过本目录中的最小 `.def` 文件生成 Qt 导入库；运行时使用 RenpyThief 自带的 Qt DLL。本项目不会重新分发 RenpyThief 的 Qt DLL。

## 构建

```powershell
.\native\build_native.cmd C:\src\minhook C:\Qt\5.15.2\msvc2019\include
```

正式运行文件会写入 `router\`：

- `ipcroute.dll`
- `netinject.exe`
- `guardlaunch.exe`
- `versionguard.dll`

三个原生测试程序会写入 `native\build\x86\`。这些产物均已由 `.gitignore` 排除，不应提交到源码仓库；它们只应出现在经过验证的 GitHub Release 中。

运行原生测试：

```powershell
.\native\build\x86\version_endpoint_test.exe
.\native\build\x86\ipcroute_test.exe .\router\ipcroute.dll
.\native\build\x86\guardlaunch_policy_test.exe
```

编译或分发包含 MinHook 的二进制文件时，必须保留其许可证声明，参见 `THIRD_PARTY_NOTICES.md`。
