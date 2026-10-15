# Local runtime data

程序会在这里创建 `jobs/`、`server.json`、`character-catalog/` 等本地内容。

这些文件不属于源码，不应提交到 GitHub。目录可能包含 Video 路径、窗口 Metadata、游戏 Screenshot、Capture Video、导出文件、运行时访问 Token 和从 HBR Quest 下载的第三方图片。

此目录默认被 `.gitignore` 排除；Source Archive 只保留本说明和 `.gitkeep`。角色图库可在界面点击「下载角色图库」，或运行 `python tools/sync_character_catalog.py` 生成。
