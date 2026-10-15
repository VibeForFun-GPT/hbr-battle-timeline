# Third-party notices

项目自有源码使用 [MIT License](LICENSE)。此 License 不涵盖游戏内容、第三方数据库、图片、字体、OCR Model 或所安装的依赖。

## 游戏与角色数据

- 《Heaven Burns Red / 绯染天空 / ヘブンバーンズレッド》的名称、角色与美术等内容属于各自权利人。本工具为独立社区工具，与游戏官方无隶属关系。
- 自动命名使用 [HBR Quest / Seraph Database](https://hbr.quest/) 公开提供的数据与图片。同步工具保留来源 URL；用户通过界面或 CLI 下载到本地 `data/character-catalog/`。
- Source Archive 不包含角色图片、数据库 Cache、游戏录像、游戏 Screenshot 或用户分析结果。项目源码 License 不为这些资源授予再分发许可。
- 使用系统已安装的 Microsoft YaHei 或 Noto Sans CJK Font，不在仓库中分发字体文件。

## Python dependencies

依赖通过 Package Registry 安装；其代码、模型和第三方组件遵循各自 Package 附带的 License / Notices。以下链接便于查看上游说明，不代替依赖所附 License：

| Dependency | Upstream |
| --- | --- |
| RapidOCR / rapidocr-onnxruntime | https://github.com/RapidAI/RapidOCR |
| ONNX Runtime | https://github.com/microsoft/onnxruntime |
| OpenCV / opencv-python | https://github.com/opencv/opencv-python |
| NumPy | https://github.com/numpy/numpy |
| Pillow | https://github.com/python-pillow/Pillow |
| openpyxl | https://openpyxl.readthedocs.io/ |

OCR Model 随所安装的 RapidOCR Package 提供，本仓库不单独打包 Model。安装环境中其他间接依赖的 License 请以相应 Package Metadata 和 License 文件为准。
