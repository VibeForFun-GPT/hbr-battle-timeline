# Changelog

## Unreleased

- 检测 TURN 编号跳跃，显示未提取回合、时间范围与原视频回看入口。
- PNG、HTML、Excel、CSV 与 JSON 保留缺口说明，缺口内的未知行动不推定为 A。

- Video 校准支持 Timeline Slider、逐 Frame 微调与直接设定战斗起点。
- 自动寻找可用初始 Frame，支持后台进度、取消与继续搜索。
- 兼容 `TURN 1` 与 `TURN 1/30` 格式，修复前者无法开始分析的问题。
- 预览使用实际显示 Frame 的时间，并安全处理首尾位置和过期请求。

## 0.1.0

- 实时 Window Capture 与本地 Video 导入。
- Avatar Tracking、任意 Skill OCR、Turn / OD 行动轴提取。
- 从本地 HBR Quest Catalog 自动匹配角色姓名，不确定时使用 Placeholder。
- 逐行回看、姓名与 Skill 简写编辑、Undo、PNG / Excel / CSV / JSON / HTML / ZIP Export。
- 独立 Source Repository、Windows Launcher、便携安装脚本、Tests 与 GitHub Actions 配置。
