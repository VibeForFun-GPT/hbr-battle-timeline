# Source Release 验证记录

验证日期：2026-10-15。范围为 Windows、64-bit Python 3.12、此独立 Source Repository。

- 使用新的 `.venv` 实际安装 `requirements.txt`，没有复用原程序的 site-packages。`pip check` 通过。
- 36 项 Tests：31 passed、5 skipped，0 failure / error。跳过项明确依赖不随仓库分发的游戏素材或完整第三方 Catalog。
- `start.cmd --help` 与 `app.py --version` 正常退出；版本为 0.1.0。PowerShell Installer Syntax 检查通过。
- 在新 Environment 实际加载 RapidOCR Model，识别生成的 `TURN 1/30` 文本成功。此 Smoke Test 不代表任意游戏画面的准确率。
- GitHub Workflow 与 Issue Templates 的 YAML Parse 通过；尚未在远程 GitHub Actions 执行。
- Source Archive 的结构、文件白名单、个人目录路径检查、Python Syntax、逐文件 SHA256 和整体 SHA256 检查通过。
- 将 Archive 解压到临时目录，使用新 Environment 启动 `app.py --no-browser`。空数据目录正常 Bootstrap，History 为空、Catalog 未缓存，HTML / JavaScript / CSS 均可读取。验证没有依赖原电脑的私有素材或运行目录。

本次安装的主要依赖为 RapidOCR 1.4.4、OpenCV 5.0.0.93、NumPy 2.5.4、ONNX Runtime 1.31.0、Pillow 12.3.0、openpyxl 3.1.5。Python 3.11 属于允许安装范围，本次未另建 3.11 Environment 验证。

此次为 Source Archive；不包含 `.exe`、Python Runtime、依赖 Package、OCR Model、角色图库、用户视频、截图或分析结果。安装后的识别能力和 Capture 实机范围见 README。
