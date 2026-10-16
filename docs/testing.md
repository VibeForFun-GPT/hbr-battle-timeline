# Tests

真实 B 站样本的逐项结果及尚未通过的项目见 [真实 Video 验证](real-video-validation.md)。Unit Tests 通过不代表真实 OCR 已达到可直接发布的准确度。

在 Repository Root 安装 `requirements.txt` 后运行：

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

默认测试覆盖 Turn / OD、Skill 保留、Avatar Matcher 的合成图片、Unknown Fallback、HTTP API、姓名保存与换位、Capture 的 Mocked Window Source、Video Decode / Crop / Seeking、Export 及 Source Archive 检查。不会抓取桌面或请求网站。

以下 Integration Tests 需要本地素材，干净 Clone 会明确 `skip`：

- 真实 Video Frame 的 OCR / 换位与完整 Capture Replay：需要 `tests/fixtures/` 的 `bootstrap.jpg`、`select-{1,2,3}.jpg`、`confirm-{1,2,3}.jpg`；部分还需要匹配此素材的 Profile 与本地 Catalog。
- 独立网站图对真实 Avatar 的匹配：还需要 `data/sample-final/avatars/{1..6}.jpg`、上述 Bootstrap 和已下载的完整 Catalog。
- 完整网站 Catalog 的负例测试：需要本地有效 Catalog；不会自动下载。

这些是原开发样本的可选验收 Tests，不是任意截图的通用导入接口。仓库不分发这些素材；请勿把私人 Screenshot、录像或第三方图库提交进 Git。

Windows 的中文 Font 用于 PNG Export。其他系统如运行 Video / Export Tests，需安装 Noto Sans CJK；实时 Window Capture 仅支持 Windows。GitHub Actions 使用 Windows Runner。
