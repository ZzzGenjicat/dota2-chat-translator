# Dota 2 本地聊天翻译

接收 Dota 2 游戏聊天，把俄语玩家消息译成中文；你输入的中文会译成俄语，复制后自行发送。翻译后端为 **M2M100 418M · CTranslate2 INT8 · CPU**，模型与分词都在本机运行。启动和翻译不会调用 Google、MyMemory、ChatGPT 或其他翻译服务，也不会自动下载文件。旧联网设置会迁移为离线。

## 开始使用

1. Windows 首次使用先准备本地模型和运行环境（见下方安装步骤），之后双击 `launch_app.vbs` 即可使用。若已安装或正在运行旧版，无需重复下载；关闭后重新打开即可。
2. 每次打开会在后台检查游戏直读配置：已安装则隐藏安装按钮；缺失或异常时显示“安装游戏直读配置”或“修复游戏直读配置”，并给出说明。通常只需安装一次，安装或修复后重启 Dota 2，再点击“开始抓取”。检查不会改写游戏文件；找不到游戏时可点击标题旁的“选择目录”，选择 Dota 2 安装目录（`dota 2 beta`、`game` 或 `game/dota`），会验证并记住这个选择。游戏把聊天事件发送到本机 `127.0.0.1:47854`。原文和译文按时间显示；聊天轮盘和不含俄文的消息会过滤。
3. 程序启动会后台预热模型；可看左侧状态或手动点击“预热本地模型”。下方输入中文，停顿 450 毫秒后自动翻译，点击“复制俄语”再粘贴到游戏。
4. “更多设置”中可选择模型文件夹和 1–4 个 CPU 线程，默认 2。点击“应用本地设置 / 重载词库”后生效。也可调整置顶、透明度、字号并清空文字或翻译缓存。

游戏直读使用原有 GSI 配置，不读取游戏进程内存；聊天正文不会发往远程翻译服务。游戏直读是否收到消息仍取决于 Dota 2 实际推送的事件。

## Mac 版本

从 [GitHub Releases](https://github.com/ZzzGenjicat/dota2-chat-translator/releases) 下载 Mac 安装包：Apple 芯片选 `macos-arm64.dmg`，Intel Mac 选 `macos-x86_64.dmg`。需要 **macOS 14 或更新版本**。打开 DMG，把 `Dota2ChatTranslator` 拖到 Applications（应用程序），随后打开即可。模型和运行环境已经包含，无需 Python、Homebrew 或首次启动下载。

第一次使用仍需按界面提示安装一次游戏直读配置并重启 Dota 2。复制俄语后，在游戏中用 **Command+V** 粘贴。不需要屏幕录制或辅助功能权限。中文字体、Steam 自定义游戏库和用户数据目录按 Mac 处理，默认 CPU INT8、2 线程、空闲 3 分钟卸载模型。

Mac 预览版使用临时本地签名，尚未使用 Apple Developer ID 签名或公证。首次启动被系统阻止时，尝试打开一次后，到“系统设置 → 隐私与安全性”查看针对这个应用的“仍要打开”；不要关闭系统整体保护。DMG 内附完整首次使用说明。真实 Dota 2 对局的聊天推送与帧率影响仍需实机验证。

需要从源码运行时，安装 python.org 的 Python 3.12（含 Tk），双击 `install_offline.command` 一次，之后双击 `launch_app.command`。源码安装阶段会下载依赖和校验模型；日常运行不会联网翻译。也可通过 `DOTA2_TRANSLATOR_PYTHON` 指定 Python 3.12 路径。

## 低资源策略与实测

收发共用一份模型，一次只运行一个推理任务。默认 CPU INT8、1 个推理 worker、2 个 CPU 线程、greedy 解码；不占用 GPU。Windows 进程使用较低优先级，OpenMP 空闲线程不主动自旋，关闭额外的 packed GEMM 权重副本。空闲 3 分钟释放模型，下一条需要模型的消息再自动加载。已知短语和缓存可以直接返回。

2026-10-06，在本机 i7-13700KF、2 线程、禁止网络连接的真实模型测试中：

| 项目 | 实测 |
| --- | --- |
| 初次加载并预热 | 约 0.9 秒 |
| 普通短句推理 | 约 0.25–0.37 秒 |
| 词库短句，首次写缓存 | 约 6–7 毫秒 |
| 内存缓存命中 | 小于 1 毫秒 |
| 模型加载后 RSS | 约 550 MiB |
| 释放模型后的进程 RSS | 约 40 MiB |

这些是翻译后端耗时；中文自动翻译另有输入防抖和最多 250 毫秒的界面轮询等待。长句、多个未命中片段或游戏占满 CPU 时会更慢。单条原文限制 192 token、最多 8 个模型片段和 128 个输出 token，超限会要求拆成短句，不静默截断。尚未测量真实 Dota 2 对局的帧率影响。

CPU 性能选择参考 [CTranslate2 性能指南](https://opennmt.net/CTranslate2/performance.html) 和 [线程配置](https://opennmt.net/CTranslate2/parallel.html)。

## 聊天词库与粗话

可编辑 `data/dota_chat_lexicon.json`，包括固定短语、Dota 术语、英雄别名、俄语 slang 与粗话。`phrases` 是整句或句中短语；`terms` 是词条。每项的 `zh`、`ru` 为对应译文，`zh_aliases`、`ru_aliases` 是等义变体，`strength` 标记原话强度（0 中性、1 轻度、2 粗话、3 强烈）。增加变体时必须保持相同含义与强度。不要添加模糊单字或把“他妈”等普通语句片段直接作为粗话别名。修改后在界面重新应用本地设置。

| 中文 | 俄语 |
| --- | --- |
| 别送了 | не фидь |
| 你是白痴 | ты идиот |
| 你是傻逼 | ты долбоёб |
| 你他妈的别送了 | не фидь, блять |
| 先开BKB然后打肉山 | сначала жми бкб, потом го рошу |

词库命中的粗话直接保留对应表达，程序没有额外审查、屏蔽或弱化规则，也不会给中性句凭空补骂人话。未命中部分交给 M2M100。通用模型可能误译新 slang、变形粗话、否定句或复杂语境；**不能保证词库外任意句子的攻击强度和俄语口语自然度绝对准确**。可把实际遇到的错误加到固定短语库中。输出保留原文用于核对。

双向缓存使用 SQLite，最多 2000 条记录，只在内存保留最多 256 条热点记录。模型文件变化、词库内容变化或翻译逻辑版本变化会切换独立缓存，不复用旧联网译文。“清空翻译缓存”也涵盖中文→俄语，并阻止清空前的推理回填旧缓存。

## 在其他 Windows 电脑安装

需要官方 **64 位 CPython 3.12（含 tkinter）**。Inkscape 自带的 Python 使用不同的原生扩展格式，不能用它加载这些推理库。

```powershell
./install_offline.ps1 -PythonPath 'C:/Path/To/Python312/python.exe'
```

安装器把推理依赖放到项目 `vendor/offline`，记住所选 Python 路径供启动器使用（程序内部路径保存为相对路径），下载约 468 MiB 的 INT8 权重及分词文件到 `models/m2m100-418m-ct2-int8`。这是一次性联网安装；文件固定版本且校验 SHA256，下载失败不会用未完成的权重覆盖原文件。两个 Windows 启动器使用同一套路径选择逻辑，优先使用随程序携带的运行环境；旧机器记录的绝对路径失效时仍可使用当前目录的运行环境。

模型来自 [固定版本的 CTranslate2 INT8 转换包](https://huggingface.co/JustFrederik/m2m_100_418m_ct2_int8/tree/1aeed44db4dd61a486bba44acf54c76507082a2c)，基础模型是 [Meta M2M100 418M](https://huggingface.co/facebook/m2m100_418M)。运行时只需 CTranslate2、SentencePiece 和本地文件，无需 PyTorch / Transformers。

仅检查已有模型、不联网：

```powershell
python tools/install_offline_model.py --verify-only
```

模型、词库或依赖缺失会显示具体原因；启动失败也会显示错误，并在用户数据目录的 `logs/gui_error.log` 保留日志，不会回退联网翻译。

## 目录与迁移

游戏查找不绑定开发者的盘符或用户名。Windows 读取 Steam 注册表位置与当前系统的安装目录；macOS 使用当前用户的 Steam 目录；随后解析新旧格式 `libraryfolders.vdf` 和 Dota 2 的 `appmanifest_570.acf`，覆盖自定义游戏库、外置盘及含中文或空格的路径。保存过的游戏目录每次启动都会重新验证，搬动或失效后重新自动查找；自动查找失败时可以手动选择。查找不会遍历整个硬盘。

模型、词库和运行依赖随程序目录定位，与启动时所在文件夹无关。程序内部的模型路径保存为相对路径；用户手动选择的外部模型和游戏目录只记录在自己的设置中。有旧程序内运行环境记录作依据时，失效的旧程序默认模型路径可恢复为当前程序携带的默认模型；其他缺失的外部模型仍保留原选择并提示缺失。

设置、缓存和日志使用当前用户的数据目录，避免向程序安装目录或 Mac 应用包内写入：

| 系统 | 默认用户数据目录 |
| --- | --- |
| Windows | `%LOCALAPPDATA%/Dota2ChatTranslator` |
| macOS | `~/Library/Application Support/Dota2ChatTranslator` |
| Linux | `$XDG_DATA_HOME/Dota2ChatTranslator`，未设置时使用 `~/.local/share/Dota2ChatTranslator` |

其中 `config/translator_settings.json` 保存设置，`cache/` 保存翻译缓存，`logs/` 保存启动日志。首次读取时兼容程序目录内的旧设置和匹配模型、词库版本的旧 SQLite 缓存；原文件保留，新用户数据存在后优先使用它。程序目录搬动不会单独改变内置模型的缓存标识；模型文件信息、词库或翻译逻辑变更仍会切换缓存。

测试或特殊部署可设置 `DOTA2_TRANSLATOR_DATA_DIR` 指定用户数据目录，`DOTA2_TRANSLATOR_STEAM_DIR` 指定 Steam 目录，`DOTA2_TRANSLATOR_DOTA_DIR` 指定游戏目录。运行时记录和个人设置不应放进通用发行包。

## 开发验证与平台接口

```powershell
python -m unittest discover -s tests -q
```

项目专用 Python 测试时设置 `PYTHONPATH=vendor/offline;src`。真实模型性能测试使用 `tools/benchmark_offline.py`，先运行 `.runtime/python/python.exe -m pip install --target vendor/offline -r requirements-dev.txt` 安装可选 `psutil`；该脚本封锁 Python 网络连接入口，输出耗时、RSS 和译例到 `docs/offline_benchmark.json`。记录中的译例用于展示实际质量，不能代表所有游戏聊天。

`M2M100Engine`、`OfflineTranslationProvider`、SQLite 缓存和词库层共用；资源策略单独放在 `platform_runtime.py`。Mac 使用固定较低优先级，重复加载不会继续累加。应用安装目录只读，配置和缓存写入当前用户目录。

Mac 构建在原生 Apple Silicon 和 Intel 的 GitHub Actions 机器上分别进行。`tools/build_macos.py --arch arm64`（Intel 使用 `x86_64`）先校验本地模型，再用 PyInstaller 打包 Python/Tk/原生库与权重，检查应用签名，将应用复制到含中文和空格的其他目录执行离线自检，最后生成 DMG。需先安装 `requirements-build.txt` 并运行模型安装器。不会交叉打包或在每次打开时解压模型。

`run.py --self-test --report smoke.json` 使用实际模型做双向 INT8 推理、词库强度、Steam/GSI、GUI 和复制检查；阻止 Python 对外网络连接，只允许本机回环。版本标签 `v*` 会运行 Windows 回归测试、两种 Mac 的源码和应用包验证；全部通过后发布安装包、SHA256 和自检报告。该自动验证不包含真实 Dota 2 对局。
