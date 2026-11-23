# 时光之门文字服务

通过浏览器给 Divoom Times Gate II 的五块屏发送闪烁的文字。预览和 GIF 共用排版，每个字符完整显示在一块 LCD 上。设备拉取五份动画后显示 30 秒，然后重新选择通知前的整机表盘或独立屏组。可设置氛围灯颜色，灯效峰值设置为 100。

## 运行

需要 Python 3、Pillow、fontTools 和支持中文的 TrueType 字体。入口为 `python3 service.py`。要持续后台运行，可将 `deployment/timegate-notice.service` 安装为用户级 systemd 服务。把应用文件放到 `~/.local/share/timegate-notice/current`，配置放到 `~/.config/timegate-notice/service.env`，然后运行 `systemctl --user daemon-reload` 和 `systemctl --user enable --now timegate-notice.service`。

| 环境变量 | 用途 |
| --- | --- |
| `GATE_ORIGIN` | 浏览器入口，如 `http://host:19001` |
| `GATE_PORT`、`GATE_LISTEN` | TCP 端口和本机监听地址，默认为 `19001` 和 `127.0.0.1` |
| `GATE_ASSET_ORIGIN` | 设备可访问的同一服务地址，默认和浏览器入口相同 |
| `GATE_ACCESS_KEY` | 32 至 128 位十六进制访问码，打开 `/open/<访问码>` 进入页面 |
| `GATE_ENDPOINT`、`GATE_TOKEN` | 设备的 `/divoom_api` URL 和本地令牌 |
| `GATE_FONT`、`GATE_FONT_INDEX` | 字体文件和 TTC 字体索引，索引默认 `2` 对应 Noto CJK 简体中文 |
| `GATE_JOURNAL` | 恢复记录的文件路径，应放在服务用户可写的持久目录 |

灯光采用设备原生单色呼吸档位，公开接口未提供周期字段。恢复对应 Divoom 记录的整机表盘或屏组，临时推送内容没有可读取的原始素材。服务需访问 Divoom 通道信息接口才能保存恢复信息。
