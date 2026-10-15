# GitHub 发布说明

本目录是独立 Repository Root。首次提交可在这里使用 GitHub Desktop「Add local repository」后创建 Repository，或在 Git 中执行：

```text
git init
git add .
git status --short
git commit -m "Initial release: HBR Battle Timeline"
git branch -M main
```

随后在自己的 GitHub 账号创建空仓库，按 GitHub 展示的实际 Remote URL 连接并 Push。仓库名可使用 `hbr-battle-timeline`。上传网页文件时同样只选择本目录内容，并包含 `.github`、`.gitignore` 等隐藏项。

## 每次发布

1. 更新 `axis/__init__.py` 中的 `__version__` 与 `CHANGELOG.md`。
2. 在干净 Environment 运行 Tests 与 `python app.py --help`。
3. 运行 `python tools/make_source_archive.py --output dist`。
4. 运行 `python tools/check_release.py --archive dist/hbr-battle-timeline-source.zip`。
5. 将生成的 ZIP 和相邻 SHA256 文件作为 GitHub Release Assets 上传；GitHub 的自动 Source ZIP 也可供 Clone 使用。

`make_source_archive.py` 包含检查步骤，采用文件白名单。`.venv`、`data` 运行数据、私有测试图片、下载的数据库与 Avatar、Video、OCR Model、日志、访问 Token、源码目录外的文件均不应出现在发布包中。

MIT License 覆盖本项目自有源码。游戏内容、HBR Quest 数据、字体、模型和依赖的授权范围见 `THIRD_PARTY_NOTICES.md`；当前 Source Archive 不携带它们。

## 验证与限制

Source Archive 解压后的第一层是 `hbr-battle-timeline/`，其中 `app.py`、启动器、`axis/` 和 `web/` 位于相对位置。使用者首次启动时仍需 Python 与网络安装依赖；自动姓名需要另外下载 Catalog。

`check_release.py` 校验结构、禁止的路径 / 文件类型、个人目录路径、敏感配置文件以及 Python Syntax。它是发布检查，不替代代码审查或通用 Secret Scanner。请在实际 Push 前查看 `git status`，不要使用 `git add -f` 强制提交本地数据。
