# Dota 2 聊天翻译

俄语聊天译成中文，输入中文可译成俄语。离线运行，安装包自带模型，无需安装 Python。

## 下载

| 系统 | 安装包 |
| --- | --- |
| Windows 10/11，64 位 | [下载安装版 EXE](https://github.com/ZzzGenjicat/dota2-chat-translator/releases/download/v0.4.5/Dota2ChatTranslator-0.4.5-windows-x64-setup.exe) |
| Apple 芯片 Mac，macOS 14+ | [下载 DMG](https://github.com/ZzzGenjicat/dota2-chat-translator/releases/download/v0.4.5/Dota2ChatTranslator-0.4.5-macos-arm64.dmg) |
| Intel Mac，macOS 14+ | [下载 DMG](https://github.com/ZzzGenjicat/dota2-chat-translator/releases/download/v0.4.5/Dota2ChatTranslator-0.4.5-macos-x86_64.dmg) |

Windows 双击安装；Mac 打开 DMG，拖到“应用程序”。请下载上面的安装包，Source code 是源码。

## 使用

1. 打开程序，按提示安装游戏直读配置，然后重启 Dota 2。
2. 点击“开始抓取”，查看俄语聊天的中文译文。
3. 输入中文，点击“复制俄语”，粘贴到游戏发送。

默认使用 2 个 CPU 线程，不用显卡；缓存总量上限 10MB。长句、黑话仍可能误译，选人和赛后聊天可能漏收。

安装包暂未取得系统认可的发布签名。首次被拦截时，Windows 可查看“更多信息 → 仍要运行”；Mac 可在“系统设置 → 隐私与安全性”允许打开。

## 源码运行

需要 Python 3.12（含 Tk）。Windows 运行 `install_offline.ps1`，随后双击 `launch_app.vbs`；Mac 运行 `install_offline.command`，随后双击 `launch_app.command`。

[模型来源与许可](packaging/MODEL_NOTICE.txt)
