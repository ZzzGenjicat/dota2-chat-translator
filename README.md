# Dota 2 本地聊天翻译

把 Dota 2 里的俄语聊天译成中文，也能把你输入的中文译成俄语，复制后发到游戏中。翻译在本机完成，支持 Windows 和 Mac；词库优先保留原话语气、粗话强度和常见游戏口语，复杂黑话仍可能误译。

本分支是使用 Hy-MT2 本地模型的独立预览版。复杂黑话、长句和粗话引用仍可能误译，需要核对；正式源码保留原有模型。

## 下载和安装

[预览版安装包下载页](https://github.com/ZzzGenjicat/dota2-chat-translator/releases/tag/v0.4.1)

| 电脑 | 下载 | 安装方法 |
| --- | --- | --- |
| Windows 10/11，64 位 | [Windows 安装版](https://github.com/ZzzGenjicat/dota2-chat-translator/releases/download/v0.4.1/Dota2ChatTranslator-0.4.1-windows-x64-setup.exe) | 双击后点“安装” |
| Apple 芯片 Mac | [Apple 芯片版](https://github.com/ZzzGenjicat/dota2-chat-translator/releases/download/v0.4.1/Dota2ChatTranslator-0.4.1-macos-arm64.dmg) | 打开 DMG，把程序拖到“应用程序” |
| Intel Mac | [Intel 版](https://github.com/ZzzGenjicat/dota2-chat-translator/releases/download/v0.4.1/Dota2ChatTranslator-0.4.1-macos-x86_64.dmg) | 打开 DMG，把程序拖到“应用程序” |

安装包包含运行环境，不用安装 Python。首次打开会下载并校验一次约 17 GB 的模型，之后翻译全程离线。需要至少 32 GB 总内存和 22 GB 当前可用内存；Mac 需要 macOS 14 或更新版本。在苹果菜单“关于本机”查看芯片类型。Windows ARM 暂不支持。

**普通用户下载 EXE 或 DMG，不要点 Download ZIP 或 Source code。** 校验文件无需下载。

## 使用

1. 打开程序，首次按提示安装一次游戏直读配置，然后重启 Dota 2。找不到游戏时点击“选择目录”。
2. 点击“开始抓取”，俄语聊天会显示中文译文。每次打开都会自动检查配置，已安装后无需重复点击安装。选人和赛后聊天目前可能漏收，尚未解决；程序不使用屏幕识别。
3. 下方输入中文，自动译成俄语；点击“复制俄语”，在游戏中粘贴发送。Windows 用 Ctrl+V，Mac 用 Command+V。

模型会自动预热，无需每次手动点击。默认使用 2 个 CPU 线程，空闲 3 分钟释放模型；“更多设置”可调整显示、线程数和清空缓存。

本版本暂未取得系统认可的发布签名。首次被拦截时，Windows 查看该文件“更多信息 → 仍要运行”；Mac 尝试打开一次后，查看“系统设置 → 隐私与安全性 → 仍要打开”。不需要关闭系统整体保护。

[600 条前后翻译评测与逐句结果](eval/chat600/results/report.md)：模拟聊天辅助评分中，可用结果由 242/600 提高到 335/600；中文长句转俄语仍不稳定。

## 本地模型安装

源码开发版使用 **Hy-MT2 30B-A3B Q4** 本地模型，参考最近几条聊天理解当前句子。模型下载约 17 GB，运行约占 17 GB 内存，需要至少 32 GB 总内存和 22 GB 当前可用内存。它仍会误解省略、错字和复杂黑话；以下方法用于源码试用，暂不作为正式版本推荐。

先安装官方 Python 3.12（含 Tk；Windows 选择 64 位），再安装一次：

- Windows：运行 `install_offline.ps1`，完成后双击 `launch_app.vbs`。可用 `-PythonPath` 指定 Python 路径。
- Mac：双击 `install_offline.command`，完成后双击 `launch_app.command`。

只下载并安装模型，也可运行：

```console
python tools/install_offline_model.py
python tools/install_llama_runtime.py
```

源码安装阶段需要联网下载依赖和模型，安装完成后翻译全程离线。

[模型来源与许可](packaging/MODEL_NOTICE.txt)
