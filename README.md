# Dota 2 本地聊天翻译

把 Dota 2 的俄语聊天译成中文，也能把输入的中文译成俄语，复制后发到游戏。支持 Windows 和 Mac，翻译全程离线，不使用屏幕识别。

**0.4.3 轻量版：内置约 494MB 的 M2M100 INT8 模型，补入整理的 Dota 词库，预设词句优先，其他消息由小模型翻译。** 不再自动下载或加载 17GB 模型。旧大模型文件保留，不自动删除。

## 下载和安装

[轻量版安装包下载页](https://github.com/ZzzGenjicat/dota2-chat-translator/releases/tag/v0.4.3)

| 电脑 | 下载 | 安装方法 |
| --- | --- | --- |
| Windows 10/11，64 位 | [Windows 安装版](https://github.com/ZzzGenjicat/dota2-chat-translator/releases/download/v0.4.3/Dota2ChatTranslator-0.4.3-windows-x64-setup.exe) | 双击后点“安装” |
| Apple 芯片 Mac | [Apple 芯片版](https://github.com/ZzzGenjicat/dota2-chat-translator/releases/download/v0.4.3/Dota2ChatTranslator-0.4.3-macos-arm64.dmg) | 打开后拖到“应用程序” |
| Intel Mac | [Intel 版](https://github.com/ZzzGenjicat/dota2-chat-translator/releases/download/v0.4.3/Dota2ChatTranslator-0.4.3-macos-x86_64.dmg) | 打开后拖到“应用程序” |

安装包已包含模型和运行环境，无需安装 Python，也无需首次下载模型。Mac 需要 macOS 14 或更新版本；在苹果菜单“关于本机”查看芯片类型。Windows ARM 暂不支持。

普通用户下载 EXE 或 DMG；Download ZIP 和 Source code 是源码，校验文件无需下载。

## 使用

1. 打开程序，首次按提示安装一次游戏直读配置，然后重启 Dota 2。找不到游戏时点击“选择目录”。每次打开都会检查配置，已装好无需重复安装。
2. 点击“开始抓取”，收到的俄语聊天会显示中文译文。
3. 下方输入中文，自动译成俄语；点击“复制俄语”，粘贴到游戏发送。

词库包含 572 组整句预设、759 个术语或别名组，以及 14 个带英雄、装备或数字的模板。本次新增 1706 种俄语词句和别名写法，主要补充俄语→中文；`чет изи` 会直接译成“怎么这么轻松”。可识别白名单中的 `cyka`、`idi nahui` 等拉丁字母俄语。命中预设直接返回，缓存避免重复翻译；复杂黑话、自由长句、否定和引用仍可能误译。选人和赛后聊天漏收尚未解决。

默认使用 2 个 CPU 线程、不使用 GPU，空闲 3 分钟释放模型。Windows 后台资源测试中模型进程内存峰值约 0.6GB；实际占用和速度随电脑、消息长度及系统负载变化，Mac 尚未进行同等资源实测。

本版本暂未取得系统认可的发布签名。首次被拦截时，Windows 查看该文件“更多信息 → 仍要运行”；Mac 尝试打开一次后，查看“系统设置 → 隐私与安全性 → 仍要打开”。无需关闭系统整体保护。

## 本地模型安装

以下仅供源码运行。先安装官方 Python 3.12（含 Tk；Windows 选择 64 位），再安装一次：

- Windows：运行 `install_offline.ps1`，完成后双击 `launch_app.vbs`。可用 `-PythonPath` 指定 Python 路径。
- Mac：双击 `install_offline.command`，完成后双击 `launch_app.command`。

仅安装小模型可运行 `python tools/install_offline_model.py`。源码安装阶段需要联网下载依赖和模型，安装完成后翻译全程离线。

预设内容保存在[源码词库](data/dota_chat_lexicon.json)，修改后重新打包即可更新安装版。已发布的 0.4.1 大模型预览源码单独保留在 `codex/context-local-model` 分支。

[模型来源与许可](packaging/MODEL_NOTICE.txt)
