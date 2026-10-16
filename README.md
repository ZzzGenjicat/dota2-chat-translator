# Dota 2 本地聊天翻译

把 Dota 2 的俄语聊天译成中文，也能把输入的中文译成俄语，复制后发到游戏。**0.4.4 轻量版**支持 Windows、Apple 芯片 Mac 和 Intel Mac，内置约 494MB 的 M2M100 INT8 模型，安装后全程离线使用。

## 下载和安装

[最新安装包下载页](https://github.com/ZzzGenjicat/dota2-chat-translator/releases/tag/v0.4.4)

| 电脑 | 下载 | 安装方法 |
| --- | --- | --- |
| Windows 10/11，64 位 | [Windows 安装版](https://github.com/ZzzGenjicat/dota2-chat-translator/releases/download/v0.4.4/Dota2ChatTranslator-0.4.4-windows-x64-setup.exe) | 双击安装 |
| Apple 芯片 Mac，macOS 14+ | [Apple 芯片版](https://github.com/ZzzGenjicat/dota2-chat-translator/releases/download/v0.4.4/Dota2ChatTranslator-0.4.4-macos-arm64.dmg) | 拖到“应用程序” |
| Intel Mac，macOS 14+ | [Intel 版](https://github.com/ZzzGenjicat/dota2-chat-translator/releases/download/v0.4.4/Dota2ChatTranslator-0.4.4-macos-x86_64.dmg) | 拖到“应用程序” |

安装包已包含模型和运行环境，无需另装 Python 或首次下载模型。普通用户下载 EXE 或 DMG，`.sha256` 是校验文件。Windows ARM 暂不支持。

## 使用

1. 首次按程序提示安装游戏直读配置，重启 Dota 2。找不到游戏时点击“选择目录”。
2. 点击“开始抓取”，收到的俄语聊天会显示中文译文。
3. 下方输入中文，翻译后点击“复制俄语”，粘贴到游戏。Windows 用 Ctrl+V，Mac 用 Command+V。

默认使用 2 个 CPU 线程，日常翻译不用 GPU，模型空闲 3 分钟自动释放。不使用屏幕识别。用户设置与翻译缓存在用户数据目录保存，重新安装不会将训练环境带入安装包。

## 0.4.4 更新

- 1247 组整句预设、896 组术语或别名、22 个模板和 64 条拉丁输入白名单，主要优化俄语→中文。
- 改善购买／别购买、肉山／魔方切换、技能状态与部分连续指令，例如 `go tormentor ne rosh` →“打魔方，别打肉山”。
- 修正部分英雄外号、词形、主客体及粗口强度。歧义外号只在明确英雄语境中识别，词库改变后自动隔离旧缓存。
- 默认继续使用原始小模型。新语料的实验微调候选仍有新增粗口、指代和否定错误，尚未加入安装包；原始训练文档和逐句审计留在本地。

复杂黑话、错拼、自由长句、否定和引用仍可能误译。生成检查不能检出所有语义错误；选人和赛后聊天漏收尚未解决。主要优化俄语→中文，中文→俄语保留。

Windows x64、Mac arm64、Mac x86_64 分别由 GitHub 原生运行器构建，发布前进行回归、离线推理、移动路径、界面和剪贴板检查；Windows 另测安装、重装和卸载。

## 从源码运行

使用 Python 3.12 或更新版本，安装脚本会准备本地运行环境与轻量模型；安装阶段需要联网，此后离线运行。

- Windows：运行 `install_offline.ps1`，完成后双击 `launch_app.vbs`。可用 `-PythonPath` 指定 Python 路径。
- Mac：双击 `install_offline.command`，完成后双击 `launch_app.command`。

本机后台回归排除整个 `test_translator_ui_text` 模块。完整界面和原生安装包验证由 GitHub Actions 执行。

暂未取得系统认可的发布签名。Windows 首次可能需要“更多信息 → 仍要运行”；Mac 首次可能需要在“系统设置 → 隐私与安全性”允许打开。无需关闭系统整体保护。
