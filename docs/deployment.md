# 部署与接口

## 运行服务

需要 Python 3、Pillow、fontTools 和支持中文的 TrueType 字体。配置以下环境变量后运行 `python3 service.py`。通过 `/open/<访问码>` 打开控制页。

| 环境变量 | 用途 |
| --- | --- |
| `GATE_ORIGIN` | 控制页地址，例如 `http://192.168.1.10:19001` |
| `GATE_PORT`、`GATE_LISTEN` | TCP 端口和监听地址，默认为 `19001` 和 `127.0.0.1` |
| `GATE_ASSET_ORIGIN` | 设备拉取 GIF 使用的服务地址，默认为 `GATE_ORIGIN` |
| `GATE_ACCESS_KEY` | 控制页访问码，32 至 128 位十六进制字符 |
| `GATE_API_KEY` | 桌面自动化访问码，32 至 128 位十六进制字符 |
| `GATE_ENDPOINT`、`GATE_TOKEN` | 设备的 `/divoom_api` 地址和本地令牌 |
| `GATE_FONT`、`GATE_FONT_INDEX` | 字体路径和集合索引，Noto Sans CJK SC 的索引默认为 `2` |
| `GATE_JOURNAL` | 记录原显示布局和恢复时间的文件路径 |

用户级 systemd 配置见 [timegate-notice.service](../deployment/timegate-notice.service)。将应用文件放到 `~/.local/share/timegate-notice/current`，环境变量写入 `~/.config/timegate-notice/service.env`，服务文件放到 `~/.config/systemd/user/`。运行以下命令启用服务。

```sh
systemctl --user daemon-reload
systemctl --user enable --now timegate-notice.service
```

## 自动化 API

自动化请求在 URL 添加 `?key=<GATE_API_KEY>`。控制页通过会话访问接口。

| 方法与路径 | 请求体 |
| --- | --- |
| `POST /api/send` | JSON，例如 `{"text":"会议开始了","color":"#5ce7c5"}` |
| `POST /api/sticker` | GIF、PNG、JPEG 或 WebP 文件，最大 4 MB。响应中的 `id` 可作为发送请求的 `sticker` 字段 |
| `POST /api/restore` | 空请求体恢复当前通知，也可用 JSON `{"id":"…"}` 指定通知 |
| `GET /api/status` | 空请求体 |

发送请求可以加 `"buzzer":true`。设备拉取五份 GIF 后响两声。省略字段时默认为 `false`。

发送返回 `202` 和通知 `id`。手动恢复完成后，状态的 `phase` 为 `read`。自然到期恢复后，状态为 `restored`。
