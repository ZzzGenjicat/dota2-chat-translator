# v0.3.2 简易安装包验证

目标为 Windows 10/11 x64 安装 EXE、macOS 14+ Apple 芯片和 Intel DMG。三个版本均包含离线模型、分词、词库和运行环境。

本地 Windows 冻结程序已通过含中文和空格的移动目录离线自检：实际 CPU INT8 双向推理、词库粗话强度、Steam/GSI 本机回环、中文 GUI 和俄语复制。运行时移除源码 PYTHONPATH 和开发环境 PATH，显式检查携带 Python 与 MSVC 运行库。

Windows 真实安装包已在临时中文目录中通过安装、重复安装后的实际离线推理、快捷方式与说明文件检查，以及卸载后的程序/模型/快捷方式/注册信息移除检查。测试使用独立 AppId 与快捷方式目录，确认正式 AppId 未改变、安装目录外的用户数据样例保留。211 项 Windows 回归全部通过；测试已隔离本机旧窗口位置，并覆盖较小屏幕与 125% 缩放下的完整按钮可见性。发布流程语法检查通过。

GitHub 原生构建 [37670598841](https://github.com/ZzzGenjicat/dota2-chat-translator/actions/runs/37670598841) 的 Windows x64、Mac arm64、Mac x86_64 构建与验证均通过，源代码为 `e1d0f65554f6696b0e4892b60af1e0f85ab10207`。Mac 使用独立进程运行 26 个 GUI 用例；8 个 Windows 专用用例跳过，其余 203 项通过。两个 Mac 也通过真实源码/冻结应用离线推理、中文 GUI、复制、GSI、移动路径和应用签名检查。

首次构建发现 Windows 小屏幕下“复制俄语”被聊天列表挤压，发布被阻止；修复为优先保留发言区、复制按钮和状态栏空间并压缩侧栏间距，新增回归由失败变为通过。修正后重新构建三个平台，未发布失败构建。

已发布 [v0.3.2 安装包](https://github.com/ZzzGenjicat/dota2-chat-translator/releases/tag/v0.3.2)，发布任务通过。三个安装包的匿名公开下载均返回 HTTP 200；下载的三份校验文件与 GitHub 对应资产的 SHA256 摘要一致，三份公开离线报告 `passed=true`，Windows 安装生命周期报告的全部检查为 true。公开附件共 10 个，另有 GitHub 自动生成的源码归档。

| 安装包 | 文件字节数 | SHA256 |
| --- | ---: | --- |
| Windows x64 EXE | 509869728 | `a3227cbfb4d7715af12e1c4da56e3c5aa6efb0740fa2ae624bb9e9beae67fe6e` |
| Mac arm64 DMG | 489423992 | `45feede0b5154a7fc2bb56e2477001fb058e4b106d9a39e9c5c5f73f095ade26` |
| Mac x86_64 DMG | 506068107 | `9302886f1d85150cf4c70ad8981cbd61eb03071205a83eb0e8f3b32a18ab6b82` |

独立只读代码审查已请求一次，但因账户额度未能运行，没有独立审查结论。已逐项检查完整变更并以原生程序、实际安装生命周期及三个平台自动验证作为发布证据。

本记录不代表真实 Dota 2 对局中的事件推送或帧率表现。Windows 发布者签名、Mac Apple Developer ID 签名与公证尚未提供。
