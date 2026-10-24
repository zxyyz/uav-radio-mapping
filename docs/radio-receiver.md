# 无线测量接收与服务器中转

接收链路是原几何数据库之外的工具层：机载 JSON 上传 → 服务器长期保存 → 电脑主动同步 → 本机接收库 → 条件满足后导入标准观测表。也支持空间有限服务器的回执删除中转模式。Python 服务均使用标准库；Windows 桌面控制额外使用系统自带的 PowerShell 和 WinForms。

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

从 `tools/radio_receiver/examples/` 复制 `receiver.example.json` 和 `sync-client.example.json` 到 `local/radio_receiver/`，分别改名为 `receiver.private.json` 和 `relay-client.private.json`。用以下命令生成两个不同的随机 token，分别用于本机接收与服务器：

```bash
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

本机 token 同时填入本机 receiver 的 `token` 和 client 的 `local_token`；服务器 token 同时填入服务器 receiver 的 `token`、client 的 `relay_token` 和机载私有上传配置。主机地址、证书和密钥不随源码发布。

配置路径相对配置文件所在目录解析，模板以 `local/radio_receiver` 为放置位置。自定义目录通过 `UAV_RADIO_CONFIG_DIR` 设置；直接启动服务时 `--config` 可指定配置文件。

```bash
# 单独启动本机接口，默认只监听 loopback
python tools/radio_receiver/server.py --config local/radio_receiver/receiver.private.json

# 另一个终端只处理一轮待传队列
python tools/radio_receiver/relay_client.py --config local/radio_receiver/relay-client.private.json --once

python tools/radio_receiver/receiver_status.py
python tools/radio_receiver/relay_status.py
```

## 服务器长期保存与电脑同步

服务器运行 `server.py`，配置数据持久化目录、仓库路径和独立 token。内网监听端点由反向代理提供公网 HTTPS；例如公网代理通过覆盖网络访问校内服务器，应用和数据库均放在校内。代理需转发 Authorization 请求头和完整 JSON 正文，正文上限至少 2 MiB。备份运行中的数据库使用 SQLite backup API。

电脑配置 `mode: "sync"`，`relay_url` 填公网 HTTPS 根地址。机载仍向同一地址的 `POST /api/v1/radio/packets` 上传。使用公共 CA 证书时省略 `ca_file`，保留默认的证书及主机名校验。

以下接口除 `/health` 外均要求 Bearer token：

| 方法和路径 | 用途 |
| --- | --- |
| `POST /api/v1/radio/packets` | 校验并持久化上传包，重复内容幂等接收 |
| `GET /api/v1/sync/status?after=N` | 服务器总包数及当前电脑游标之后待同步的包数、观测数、大小 |
| `GET /api/v1/sync/packets?after=N&limit=5&server_id=UUID` | 按递增同步 ID 只读导出最多 5 包，数据库身份不匹配返回 409 |
| `GET /status` | 接收库及标准表导入状态 |

电脑先将完整包提交到本机 SQLite，确认 `accepted=true` 后才原子保存本机游标。断线或重启后继续同步，重放由接收端去重；每台电脑保留独立游标。切换到不同身份的服务器数据库会从头同步。恢复同一身份的较旧数据库备份时，管理员应一并重置电脑游标。游标损坏会报错，不能静默跳过数据。

同步读取和游标更新均不会删除服务器数据，也不调用中转回执接口。窗口的“待同步”仅代表该电脑尚未取回的记录，0 包不代表服务器没有存档。真实定位不齐的记录在服务器和电脑均保留为待定位；合成测试记录单独入测试库。

## 可选：空间有限的公网中转

仅在需要电脑收取后删除服务器载荷时使用 `relay_server.py` 和 `relay-client.example.json`。客户端省略 `mode` 或指定 `"relay"`，按以下旧中转协议工作；不要将其指向长期保存的 `server.py`。

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

窗口显示待同步包数、采样数、大小和更新时间；永久模式另显示服务器存档包数。查询在子进程中执行，失败显示未知，不能按 0 处理。启动后持续补收；最后一次收到新包后，成功查询为空累计至少 10 秒，自动停止本机接收与同步进程。新包、网络/写库错误会重置计时；实际退出还包括网络与轮询耗时。自动停止后的新数据需再次点击启动收取。

关闭窗口会终止本实例的接收、中转、状态查询和起停辅助进程，不留后台；安装器不配置无窗口自启动。PowerShell 仅管理脚本和配置路径匹配的进程；自动停止请求绑定当前 client PID，旧请求不能结束新一轮接收。

Linux/macOS 可直接使用 Python 命令，空队列确认后 client 自行退出，本机 HTTP 接口由终端或进程管理器独立管理；Windows 才会联动 PowerShell 停止本机服务。

## 验证及范围

```bash
python -m unittest discover -s tests -v
```

接收测试覆盖事务接收、同包/观测去重、非法批次拒绝、合成标准表导入、永久同步保留载荷、游标恢复、数据库身份切换、本机失败不推进游标，以及旧中转的离线保留、回执删除、磁盘回收、有限摘要和空队列判断。测试仅使用明确标记的合成包，所有数据库放在临时目录。

部署原型曾在 M400 + 妙算 3 + 外接 USB LTE 模块上验证真实收发、断开电脑接收后补收、重复发送、回执删除、桌面队列计数和窗口关闭退出。实测数据库和原始日志未发布，硬件实测不能代替对当前设备、运营商网络和部署配置的复测。机载 DJI SDK 工程及凭据不包含在这次接收工具提交中。
