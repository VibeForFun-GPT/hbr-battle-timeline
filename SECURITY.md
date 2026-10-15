# Security

程序默认只监听 `127.0.0.1`，每次运行生成随机访问 Token。请使用启动器打开的本地 URL。Video、Capture 和 OCR 在本机处理，角色图库更新只下载公开资源。

请勿将本地服务通过 Port Forwarding、公开 Tunnel 或代理暴露到 Internet；API 能访问本机选择的视频和分析目录。分享行动轴时，先检查 JSON 中的视频绝对路径、窗口标题、截图中的账号信息和个人备注。

发现安全问题时，请优先使用仓库启用的 GitHub Private Vulnerability Reporting。若该功能尚未启用，可提交不包含复现细节或个人数据的 Issue，请维护者提供私下联系渠道。不要在公开 Issue 上传本地访问 Token、完整分析目录或敏感 Screenshot。
