# 时光之门文字通知调研

工作区是空仓库。`mi.xclz.tech` 的 `b3n1gnx` 账户可以公钥登录，服务器运行 Manjaro，已有 Python 3.14、Pillow 和简体中文 Noto Sans CJK Black 字体。用户级 systemd 可以接收命令，用户已启用 linger。调研时 TCP 19001 空闲。

服务器经 `mir.xclz.tech:19000/divoom_api` 发出只读命令并收到成功响应。目标设备为时光之门 II Hardware 402。此前同一个 GIF 可经 `Device/PlayGif` 下载一次并在五屏同步显示，五个不同的短 GIF 也曾由 `Device/PlayGifLCDs` 触发下载。设备是否能从跨网段的服务器拉取素材需要实测。

`Channel/SetRGBInfo` 支持亮度、固定颜色和灯效编号。社区实测中，`SelectLightIndex: 0` 与 `SelectEffect: 4` 对应全区单色呼吸。公开参数没有周期字段，参考资料未确定 402 上的实际周期。 [官方参数](https://docin.divoom-gz.com/web/#/5/353) · [社区灯效记录](https://github.com/averhaegen/hacs-divoom-times-gate-dev/blob/e8475a1485e00340646bcd52f39971ad34429990/docs/RGB_LIGHTS.md)

Keeper Studio 可以预先切分五屏全景，但会跨边界裁切画面，且桌面客户端使用旧版 `/post`。Divoom 的 MCP 验证过 402 的局部更新，适合参考协议行为。Pentadeck 在另一版 ESP32-S3 硬件上本地渲染。 [Keeper Studio](https://github.com/raishack/divoom-times-gate-controller) · [Divoom MCP](https://github.com/DivoomDevelop/mcp-divoom-lan/releases) · [Pentadeck](https://github.com/lesyhau/pentadeck)
