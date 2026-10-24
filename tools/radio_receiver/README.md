# Radio receiver tools

本目录将已实机验证的接收、中转和 Windows 控制程序整理为可移植工具，运行仅依赖 Python 3.10+ 标准库。核心数据库 schema 不变；机载 DJI SDK 工程不包含在本目录中。

完整设置步骤、包格式及接收/导入区别见 [接收与中转说明](../../docs/radio-receiver.md)。

| 文件 | 用途 |
| --- | --- |
| `server.py` | 服务器/本机 HTTP 接收、长期存储、只读同步导出、条件满足后的标准库导入 |
| `relay_server.py` | 公网 HTTPS 中转、离线队列、回执删除及待传统计 |
| `relay_client.py` | 电脑主动同步并保留服务器数据，或显式选择旧中转回执模式 |
| `runtime_config.py` | 私有配置读取、相对路径和配置模板检查 |
| `relay_status.py` / `receiver_status.py` | 中转队列与本机数据库状态 |
| `RadioRelay-Control.ps1` | Windows 桌面控制、队列计数、关闭窗口清理 |
| `Start-RadioRelay.ps1` / `Stop-RadioRelay.ps1` | 启停本实例的接收/中转进程 |
| `Install-Desktop.ps1` | 创建桌面入口，不设置后台自启动 |
| `examples/` | 不含凭据的配置模板 |

默认配置和运行状态目录为仓库下的 `local/radio_receiver`，可用 `UAV_RADIO_CONFIG_DIR` 覆盖。相对路径字段以配置文件所在目录为基准。Windows Python 可用 `UAV_RADIO_PYTHON` 指定，否则优先仓库 `.venv`，再查找 PATH 的 `python.exe`。

```powershell
New-Item -ItemType Directory -Force local/radio_receiver
Copy-Item tools/radio_receiver/examples/receiver.example.json local/radio_receiver/receiver.private.json
Copy-Item tools/radio_receiver/examples/sync-client.example.json local/radio_receiver/relay-client.private.json
# 编辑私有配置，替换 token、relay_url 和证书路径。
powershell.exe -NoProfile -ExecutionPolicy Bypass -File tools/radio_receiver/Install-Desktop.ps1
```

占位 token 不能直接运行。接口认证、真实配置、数据库、证书私钥、原始日志和 DPK 均不进入 Git。
