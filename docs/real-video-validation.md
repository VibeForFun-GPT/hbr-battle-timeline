# 2026-10-16 真实 Video 验证

当前结论：程序能生成可编辑的行动轴草稿，但还没有达到任意 Video 无需核对即可直接发布的准确度。尤其是低清晰度、非标准 Layout 和 BONUS 背景较亮的视频，Skill、行动可用状态和 OD 分类仍有错误。

## 素材与方法

从 B 站搜索「绯染天空打分」，匿名下载三条公开视频的公开可用 Video Stream，实际解码后从 0 开始寻找 Bootstrap，并完整运行分析；另用原 EX100 样本做 Regression。没有使用浏览器 Cookie，也没有把标题中的 T 数当作 Ground Truth。Video 和 Screenshot 均不随 Repository 分发。

人工核对提取出的全部选招 Frame，并在部分样本查看确认后及 OD 过渡 Frame。以下是已提取记录的内容核对，**不是独立逐帧标注的完整 Action Recall**。空白 Skill、灰色 Avatar、UI 截断、不可读名称分别记录；不会把未知项计入正确率。

| 样本 | 实际媒体 | 自动起点 | 已提取记录与核对结果 |
| --- | --- | --- | --- |
| [84 期光可怜，P1](https://www.bilibili.com/video/BV1mis3z9ELf/) | 854 × 388，479.165s | 31.037s，TURN 1 | 23 个确认候选，69/69 个前排 Avatar ID 对齐；Counter 后处理后 21/23 个 TURN 正确，另 2 个保留 unknown。18 个可见 BONUS 仅 5 个读对；至少 2 处命名 Skill 漏读成 A。整体不通过。 |
| [70 期冰队天使，P1](https://www.bilibili.com/video/BV1YX92YaEoi/) | 1600 × 896，393.533s | 17.5s，TURN 1 | 18 条记录，54/54 个前排位置对齐；13 个直接可见 TURN 正确，另 5 行保留 unknown，仅记录最近 TURN 9 为 context。5/5 个可见 BONUS 正确。30 个完整可见 Skill 名称中 21 个正确（允许已核实的繁简差异）；另 3 个 UI 截断不算完整名称。整体仍需核对。 |
| [95 期雷吹雪，P1](https://www.bilibili.com/video/BV176ozBmEak/) | 1920 × 1080，315.4s，含左右黑边 | 37s，TURN 2 | 15 个确认候选，45/45 个保存 Frame 中的前排 Avatar ID 对齐。TURN 1 的 AUTO 过程未恢复；12 张可见 BONUS 中仅 5 张读对，OD 首次分类存在错误；部分 Avatar Ring 被误判为不可行动，候选中有短暂空选招过渡。整体不通过。 |
| 原 EX100 Regression | 本地原始样本 | 73.5s，TURN 1 | 24 条记录，前排身份结合相邻 Frame 核对；个别 Avatar 本身有遮挡。56 个完整可见命名 Skill 中 49 个正确，另有 3 个截断标签及 13 个 A/不行动位置，不混入该分母。1 个 BONUS 保留 unknown；已修复它导致后续 OD Session 被错误拆分的问题。仍有 Skill 错字。 |

低清样本的 23 个候选包含 1 次全灰 Avatar 的空确认；其确认后仍播放排队中的状态，不把它武断计为真实 Skill 行动或 False Positive。雷吹雪的空选招候选也不能仅凭 Screenshot 推断输入点击。

## 本次修复

- Bootstrap 增加六个 Avatar Ring 与 Confirm 按钮的几何约束，避免把 EX 动画或按钮边界当作队员；非标准 Layout 可生成候选 ROI。歧义 Frame 会继续扫描。
- 支持 BONUS 显示在 TURN 区域的 UI，并对分离的 Counter 文本做 Detection 分组。
- 不可读 TURN 保留 unknown，最近已知编号只放入 `turn_context`。Unknown 行不再被误作普通追加并合并；不能确定的 OD 时机保持待核对。
- 同视频出现明确且一致的 Counter 总数时，对 `330 → 3/30` 等缺失分隔符给出带来源的修复，并保留原值和 Review 标记；没有此 Evidence 时不猜补。
- 不再用多数投票改写 BONUS 分母，避免制造非法 `3/2`；不可读 Counter 不回填，但可保留已知 OD Session context。
- 保留 OD 的负号；增加 Avatar Radii Validation，阻止非法配置产生 Exception 或 NaN。
- Skill 保留 Detection / Recognition 差异及相邻 Frame Evidence；异常单字、截断、读数冲突和短暂选招画面增加 Review 提示。

Counter 修复与最后的 OD State Machine 修复在完整 OCR 证据上重新计算并核对，原始自动结果和 Screenshot 不覆盖。没有把后处理描述为重新完成一次 OCR。

## 仍未解决

Skill OCR 仍可能漏字、错字或完全漏检后默认 A；Confidence 高也不能证明正确。非标准 Layout 的 Ring 半径/位置误差可能影响行动可用状态，数字 OD 也可能漏读首位。BONUS 漏读仍会影响 OD 内、追加及前置/后置分类。

AUTO 隐藏选招、剪辑省略或从中段开始的 Video，不能靠连续编号证明完整性。使用者应核对原视频和原始 OCR，再导出最终轴。实时 Capture 本次没有进行真实游戏现场验证。

## 可重复的检查

默认 Tests 不下载素材。除 Unit / Synthetic Tests 外，可在持有本地测试素材时额外启用几何与 Counter 回归：

```powershell
$env:HBR_LAYOUT_TEST_MEDIA = 'D:\ValidationMedia'
$env:HBR_COUNTER_TEST_MEDIA = 'D:\ValidationMedia'
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

Layout 素材位置为 `video-a/video-a.mp4` 和 `video-c/video.mp4`；Counter 证据位置见 `tests/test_counter_reconciliation_media.py`。这些可选检查验证已知失败分支，不能代替新的真实样本验收。
