# Contributing

欢迎提交可复现的 Bug report 或范围明确的 Pull request。较大的识别策略改动请先用 Issue 描述输入、预期行为和验证方法。

## 本地运行

使用 Windows 与 Python 3.12。在项目根目录打开 PowerShell：

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe app.py
```

## 验证修改

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe app.py --help
.\.venv\Scripts\python.exe tools/make_source_archive.py --output dist
.\.venv\Scripts\python.exe tools/check_release.py --archive dist/hbr-battle-timeline-source.zip
```

CI 使用 Windows 与 Python 3.12，不携带私人游戏录像或本机 Fixtures。依赖这些素材的 optional Integration Tests 会 skip；请使用可公开的 Synthetic Fixtures 增加 Regression Tests，并区分自动检查、测试画面和实际游戏验证的范围。

修改角色匹配时，覆盖未知角色、歧义候选、Avatar ID 与换位，以及手动名字优先的行为。修改 Turn / OD State Machine 时，覆盖行动确认、前置 OD、后置 OD、OD 内回合、追加行动和重复 Frame，避免重复或遗漏记录。不要把某个队伍、Skill 或游戏 UI 的识别结果推广为所有场景都能自动识别。

## 数据与提交范围

不要提交私人录像、Screenshot、帐号信息、local path、window title、日志、生成的行动记录、Capture 素材或角色图库缓存。Bug report 和 PR 中的示例也应先脱敏。外部图库应由下载工具获取；不要把第三方图片或数据直接加入源码。

保持变更范围集中，并在 PR 中写明行为变化、验证结果及尚未验证的场景。更新用户可见行为时同步修改说明文档。
