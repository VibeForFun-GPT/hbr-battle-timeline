# HBR Battle Timeline · 绯染天空战斗行动轴

把《Heaven Burns Red / 绯染天空》的战斗画面整理成可核对的队伍行动轴。支持实时 Capture 和本地 Video，记录前排 3 人的 Skill、换位、Turn、追加回合与前置 / 后置 OD。

程序在本机运行，无需 API Key。识别结果可编辑，并导出 PNG、Excel、CSV、JSON、HTML 或带 Screenshot 的 ZIP。

## 快速开始

当前支持 **Windows 10 / 11、64-bit Python 3.11 或 3.12**，推荐 Python 3.12。RapidOCR 1.4.4 不支持 Python 3.13+。

1. 从 [python.org](https://www.python.org/downloads/) 安装 Python 3.12，并安装 Python Launcher 或将 Python 加入 PATH。
2. 下载仓库 ZIP 或 Clone 到可写目录，解压后双击 `启动行动轴.cmd`（也可用 `start.cmd`）。首次启动会创建 `.venv` 并联网安装依赖。
3. 浏览器会打开本地界面。首次需要自动识别姓名时，点击「下载角色图库」；下载后可离线匹配 Avatar。没有图库时仍能生成使用 Placeholder 的行动轴。

若需要指定 Python 的位置：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\install.ps1 -Python 'D:\Python312\python.exe'
.\start.cmd
```

也可手动安装和运行：

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -X utf8 app.py
```

关闭启动器窗口可停止服务。默认使用空闲端口，只监听 `127.0.0.1`，每次启动生成新的本地访问 URL。

## 使用方法

### 实时 Capture

选择游戏窗口，点击「开始捕获」后切回游戏，在第一回合选招前开始。结束时按住 **Ctrl + Alt + E**，或返回页面点击「结束并生成行动轴」。

保持游戏在前台且完整可见。切到其他应用、游戏最小化或移出屏幕时暂停取新画面，恢复后继续；暂停期间的行动可能遗漏，需要核对。捕获不会点击或操作游戏，素材保存在本地 `capture.webm`，不含 Audio。

首次使用先点「预览与画面校准」。预览会留出 3 秒切回游戏，取到画面后返回页面框选完整游戏区域。捕获期间修改校准，会用于当前这场战斗的分析。

### Video 导入

选择「导入录像」并选择本地视频，程序会自动寻找含有 Avatar、TURN 和行动开始按钮的可用选招画面，并填入战斗开始时间。手动粘贴路径后可点击「自动寻找战斗起点」。搜索支持取消，较长视频可继续搜索剩余部分。

在「预览与画面校准」中拖动进度条查看整段视频，使用「上一帧 / 下一帧」或方向键微调；点击「从此画面开始并应用校准」即可使用当前显示的画面作为起点。也可在侧栏直接输入 `MM:SS`、`HH:MM:SS` 或秒数。有黑边、直播布局或模拟器工具栏时，先框选完整游戏区域，再重新寻找起点。支持 OpenCV 能解码的 MP4、MKV、MOV、AVI、WebM 和 M4V。

### 姓名、Skill 与核对

如果回合编号出现 `11 → 22` 等跳跃，界面会列出未提取的回合和对应时间，并提供原视频回看；导出文件也保留缺口说明。AUTO ON 隐藏选招、视频剪辑或遮挡时，当前流程可能无法读取中间的角色与 Skill，结果需要核对。缺口中的未知行动不计为普通攻击。

- 队伍不固定。程序从首次选招画面学习 6 个 Avatar，并跟踪其换位；名字不参与位置判断。
- 默认用 HBR Quest 本地 Catalog 自动填入角色姓名，保留全名和图表简称。不确定的匹配保留 `角色 1–6` Placeholder，并提供候选供核对。
- 手动姓名和显式 Profile 优先。已有行动轴可在「队伍与 Skill 简写」点击「自动识别队伍名字」补全；操作可 Undo。
- 任意 Skill 保留原始 OCR、完整名称和简写。可以自定义简称，并同步修改同一角色的相同 Skill。
- 点击待核对行，回看确认前 Screenshot 或 Video，修正姓名、Skill、Turn、时间或 OD，再保存并导出。

前置 OD 在该 Normal Turn 确认前发动；后置 OD 在确认后发动。Skill 产生的额外行动标记为「追加」。OD Gauge 使用画面实际显示的读数，不能恢复没有显示的小数。

## 支持范围

界面按常见横屏 6 人战斗 UI、1920 × 1080 基准处理，可通过 Crop 校准边框。默认 OCR Model 面向中文和 English。头像严重遮挡、低分辨率、UI 改版、日文 Skill、录像剪辑省略选招等情况需要人工核对或调整 Layout / Model。

这是可编辑的自动草稿工具。Capture 的自动流程经过模拟 Source 和窗口范围 Tests；实际游戏、服别和录制方式仍需自行验证预览与结果。

## 角色图库

姓名和图片由 [HBR Quest / Seraph Database](https://hbr.quest/) 提供，下载至 `data/character-catalog/`。分析不上传 Video 或 Screenshot；图库更新只下载公开数据。同步失败时保留上次有效 Cache。

也可从 CLI 下载：

```powershell
.\.venv\Scripts\python.exe -X utf8 tools\sync_character_catalog.py
```

Avatar Matching 同时检查 Raw Similarity 与不同角色之间的 Margin。部分 Style 复用同一张图片，匹配来源的 Style ID 不保证唯一确定装备的 Style。图库数据、游戏图片与模型不随此源码包分发，具体范围见 [Third-party notices](THIRD_PARTY_NOTICES.md)。

## CLI

```powershell
.\.venv\Scripts\python.exe -X utf8 app.py `
  --video 'D:\Videos\battle.mp4' --start 01:13 `
  --output data\my-battle
```

可用 `--end 05:00` 限制结束时间，`--sample-rate 8` 提高扫描频率，`--profile examples\placeholder-profile.json` 明确使用手动命名。`examples/sample-profile.json` 演示姓名、Skill 简写和 Layout 配置；使用前按自己的队伍修改。

`--no-browser` 可只启动服务，`--port 11079` 可指定本地端口，`--data` 可指定分析结果目录。角色 Catalog 当前固定在项目内的 `data/character-catalog/`，独立于 `--data`。

## 项目结构

```text
hbr-battle-timeline/
├── app.py                  # 本地 GUI / CLI Server
├── axis/                   # OCR、Avatar、Capture、Turn / OD、Export
├── web/                    # 浏览器界面，无需 Node.js Build
├── tools/                  # Catalog 同步、Source Archive 构建与检查
├── scripts/                # 可复用的发布检查规则
├── tests/                  # Unit / Synthetic Tests；真实素材 Tests 可选
├── examples/               # 可编辑 Profile
├── docs/                   # 开发与 GitHub 发布说明
├── data/                   # 本地数据，Git 默认忽略
├── .github/                # CI 与 Issue / PR Templates
├── requirements.txt
├── install.ps1
├── start.cmd
├── 启动行动轴.cmd
├── LICENSE
└── THIRD_PARTY_NOTICES.md
```

## 开发和测试

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe app.py --help
```

默认 Tests 不需要联网或私人视频。依赖真实游戏素材或第三方 Catalog 的 Integration Tests 会明确 `skip`，需要素材时参见 [测试说明](docs/testing.md)。GitHub Actions 会在 Windows + Python 3.12 下运行 Tests、CLI 和 Source Archive 检查；配置工作流不代表已经在远程执行。

## 发布到 GitHub

这个目录本身就是 Repository Root，可直接上传其内容；不要上传上一级工作目录。常规 Git 提交会自动排除 `.venv`、视频、分析结果、截图、Token 和下载的 Catalog。

生成用于 GitHub Release 的干净 Source Archive：

```powershell
.\.venv\Scripts\python.exe tools\make_source_archive.py --output dist
.\.venv\Scripts\python.exe tools\check_release.py --archive dist\hbr-battle-timeline-source.zip
```

构建采用明确的文件白名单，运行产生的数据即使留在项目目录也不会进入 Source Archive。发布步骤和检查范围见 [发布说明](docs/github-release.md)。该 Archive 是源码版，使用者仍需 Python；不包含预装 Environment 或 `.exe`。

项目源码采用 [MIT License](LICENSE)，第三方内容遵循各自 License。贡献指南见 [CONTRIBUTING.md](CONTRIBUTING.md)。
