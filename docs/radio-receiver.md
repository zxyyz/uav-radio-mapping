# 无线测量接收与服务器中转

接收链路是原几何数据库之外的工具层：机载 JSON 上传 → 公网中转暂存 → 电脑主动取回 → 本机接收库 → 条件满足后导入标准观测表。Python 服务均使用标准库；Windows 桌面控制额外使用系统自带的 PowerShell 和 WinForms。

## 接收和导入是两个阶段

`server.py` 首先事务保存包、观测和实际小区到 `data/radio_receiver/inbox.sqlite3`。包 ID 和 `(device_id, sequence)` 用于去重，同一个身份改变内容会被拒绝。`accepted=true` 只表示本机已可靠接收，不能作为标准表导入成功的证据。

包内观测必须包含真实有效的三维定位、固定实验区域原点、明确的 WGS84 椭球高及 ENU 坐标，才可设置 `ready_for_database=true`。合格记录由 `import_ready()` 写入原 schema 的 sites、flights、poses、radio_observations、cell_measurements。不齐备时保留 `pending_pose` 原始包；导入错误保留为 `import_error`。现有工具不自动补齐历史定位，也不将无效定位写成零点。

默认标准库仍是 `data/geometry/maps.sqlite3`。明确标记 `provenance.synthetic=true` 的可导入合成包写到独立 `network-test.synthetic.sqlite3`，避免混入实际定位。

## 线路格式

```text
format: "uav-radio-mapping.radio.v1"
database_schema_version: 1
packet_id / created_at_ns
site: 固定实验原点；未配置为 null
flight: flight_id / site_id / device_id / started_at_ns / ended_at_ns / metadata_json
observations[]:
  ready_for_database / missing_requirements[]
  pose: 标准 poses 字段；不可用时为 null
  radio_observation: 标准观测字段，含 device_id / sequence / 查询时间 / 原始响应
  cell_measurements[]: 最多 1 serving + 4 neighbor
```

`*_ns` 在 JSON 中使用 UTC 纳秒十进制字符串，接收后转 SQLite INTEGER；单位不代表采样的硬件时间精度。`*_json` 在线路上为对象，入库序列化为 TEXT。MCC/MNC/Cell ID/TAC 保留字符串及前导零，邻区身份未知时保持 null，不能复制主小区字段。RSRP/RSSI 为 dBm，RSRQ/SINR 为 dB，频点字段为 `arfcn`。缺少邻区时只保存实际测到的小区。

一次请求 1～200 次观测，正文上限 2 MiB；常用机载策略每 5 次采样一包，停止时发送剩余记录。HTTP 头带 Bearer token，可带 `Idempotency-Key: packet_id`。采样序号应跨重启持久化，允许间隙，不允许身份复用。

## 本机配置

从 `tools/radio_receiver/examples/` 复制 `receiver.example.json` 和 `relay-client.example.json` 到 `local/radio_receiver/`，改名为对应的 `*.private.json`。用以下命令生成两个不同的随机 token，分别用于本机接收与公网中转：

```bash
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

本机 token 同时填入 receiver 的 `token` 和 client 的 `local_token`；公网 token 同时填入 relay-server 的 `token`、client 的 `relay_token` 和机载私有上传配置。主机地址、证书和密钥不随源码发布。

配置路径相对配置文件所在目录解析，模板以 `local/radio_receiver` 为放置位置。自定义目录通过 `UAV_RADIO_CONFIG_DIR` 设置；直接启动服务时 `--config` 可指定配置文件。

```bash
# 单独启动本机接口，默认只监听 loopback
python tools/radio_receiver/server.py --config local/radio_receiver/receiver.private.json

# 另一个终端只处理一轮待传队列
python tools/radio_receiver/relay_client.py --config local/radio_receiver/relay-client.private.json --once

python tools/radio_receiver/receiver_status.py
python tools/radio_receiver/relay_status.py
```

## 公网中转部署

准备 relay-server 私有配置、可验证的 TLS 证书和私钥。证书必须包含机载实际访问的域名或 IP SAN；私有 CA/自签证书的公开证书应同步到电脑与机载信任配置，不关闭证书或主机名验证。系统信任的证书可省略 client 的 `ca_file`。占位 token 会被拒绝。

```bash
python tools/radio_receiver/relay_server.py --config /path/to/relay-server.private.json
```

对外使用配置的端口（模板为 TCP 8878）。使用 Docker 或其他进程管理器时，将配置、证书和 queue.sqlite3 所在数据目录持久化挂载，采用非 root 用户，并在部署环境配置重启和日志保留策略。中转只使用标准库，不要求访问标准几何库。

接口如下，除 `/health` 外都需要认证：

| 方法和路径 | 用途 |
| --- | --- |
| `POST /api/v1/radio/packets` | 持久化并校验上传包 |
| `GET /api/v1/relay/pending` | 取最多 5 个待交付包 |
| `POST /api/v1/relay/ack` | 电脑提交成功后回执，删除完整载荷 |
| `GET /status` | 待传包数、采样数、字节数与有限回执数 |

电脑不需要公网地址或入站端口。电脑离线时包留在中转，接收恢复后补收。电脑只在本机 SQLite 已提交并返回 accepted 后发回执；本机入库失败不回执。重试由服务器哈希与本机身份共同去重。

收到回执后删除服务器完整包，只保留最多 10000 条、7 天内的去重摘要；过期摘要在后续请求时清理，SQLite FULL auto_vacuum 回收已删页面。超期重传仍由电脑最终去重。不要把服务器当作已经收到的数据归档。备份运行中的 WAL 数据库时使用 SQLite backup API，不单独复制主文件。

## Windows 控制窗口

运行 `tools/radio_receiver/Install-Desktop.ps1` 创建桌面入口。可选 `-ConfigDirectory`、`-PythonExecutable`、`-ShortcutPath`；这些路径写进本机快捷方式，不进入源码。Python 需 3.10+。

窗口显示服务器待传包数、采样数、大小和更新时间。查询在子进程中执行，失败显示未知，不能按 0 处理。启动后持续补收；最后一次收到新包后，成功查询为空累计至少 10 秒，自动停止接收与中转。新包、网络/写库/回执错误会重置计时；实际退出还包括网络与轮询耗时。自动停止后的新数据需再次点击启动收取。

关闭窗口会终止本实例的接收、中转、状态查询和起停辅助进程，不留后台；安装器不配置无窗口自启动。PowerShell 仅管理脚本和配置路径匹配的进程；自动停止请求绑定当前 client PID，旧请求不能结束新一轮接收。

Linux/macOS 可直接使用 Python 命令，空队列确认后 client 自行退出，本机 HTTP 接口由终端或进程管理器独立管理；Windows 才会联动 PowerShell 停止本机服务。

## 验证及范围

```bash
python -m unittest discover -s tests -v
```

接收测试覆盖事务接收、同包/观测去重、非法批次拒绝、合成标准表导入、离线保留、回执删除、磁盘回收、有限摘要、HTTP 本机写入失败不回执、队列统计和空队列判断。测试仅使用明确标记的合成包，所有数据库放在临时目录。

部署原型曾在 M400 + 妙算 3 + 外接 USB LTE 模块上验证真实收发、断开电脑接收后补收、重复发送、回执删除、桌面队列计数和窗口关闭退出。实测数据库和原始日志未发布，硬件实测不能代替对当前设备、运营商网络和部署配置的复测。机载 DJI SDK 工程及凭据不包含在这次接收工具提交中。
