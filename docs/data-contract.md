# 数据约定 v1

## 坐标与几何

区域原点使用 WGS84 经度、纬度和椭球高。计算与几何存储统一为该原点的局部 ENU 坐标：`x` 向东、`y` 向北、`z` 向上，单位米。定位、基站和地图必须共享原点。当前导入器只接收已经转换为 ENU 的几何。

从测绘软件输出导入前，记录水平 CRS、垂直基准、原点与转换参数。经纬度转换应经过 WGS84 ECEF → ENU；正高、起飞点相对高度和椭球高需先进行明确的垂直基准转换。商业地图坐标先统一到 WGS84，再转换 ENU。

楼房模型为一个无孔洞、无自相交的闭合二维轮廓，支持凹多边形，统一为逆时针。`base_z_m` 为基底相对 ENU 原点的高程，`height_m` 为基底到屋顶的高度；模型顶部的 ENU 高程为二者之和。当前每栋建筑为单一挤出体，复杂屋顶后续另建表达。

交换格式为自定义 JSON，**不是 RFC 7946 GeoJSON**，坐标字段明确带 `_m`：

```json
{
  "format_version": 1,
  "site": {
    "site_id": "synthetic-site",
    "name": "Synthetic example",
    "origin_lon_deg": 0,
    "origin_lat_deg": 0,
    "origin_alt_m": 0,
    "vertical_datum": "WGS84_ellipsoidal",
    "frame": "ENU"
  },
  "map": {
    "map_id": "prior-v1",
    "role": "prior_2d",
    "provenance": {"source": "synthetic footprint"}
  },
  "buildings": [
    {
      "building_id": "A",
      "footprint_xy_m": [[10,10], [30,10], [30,25], [10,25], [10,10]],
      "base_z_m": 0,
      "height_m": null
    }
  ]
}
```

`map_id` 全库唯一，`building_id` 在地图版本内唯一。不同来源之间的建筑对应关系在评估阶段显式建立；不能依赖轮廓提取顺序。`role` 可取 `prior_2d`、`lidar_truth`、`rf_reconstruction`、`synthetic`；测绘与重构结果使用不同 `map_id`。可选 `source_asset_id` 必须引用同一区域已登记的资产；当前资产登记 API 尚待开发。

## 数据表

| 表 | 语义 |
| --- | --- |
| `schema_version` | 模式版本与创建时间 |
| `sites` | 区域与不可变坐标原点 |
| `assets` | 外部大文件的相对路径、SHA-256、坐标和来源 |
| `map_versions` | 地图角色、来源、版本创建时间 |
| `buildings` / `building_bounds` | 轮廓、基底、楼高、范围及 RTree |
| `flights` | 航次、设备、时间范围及配置 |
| `poses` | 定位时刻、ENU/WGS84 位置及质量信息 |
| `radio_observations` | 一次无线查询，原始响应、采集/接收时间和定位时间偏差 |
| `cell_measurements` | 单次查询中的服务小区和任意数量邻区 |
| `base_stations` | 基站位置、身份、位置不确定度及来源 |
| `reconstruction_runs` | 算法、Git 提交、输入清单、参数、输出地图和指标 |

完整 SQL 为 [schema.sql](../src/uav_radio_mapping/schema.sql)。v1 CLI 仅操作区域与建筑地图；其他表定义为后续开发的数据底座。

## 时间与测量

采集表使用 UTC Unix 纳秒整数。纳秒单位用于统一表达，不代表硬件具备纳秒精度；同时保存同步来源、误差和质量标记。API 的未来 JSON 表达需将这些 64 位整数作为十进制字符串传输，避免 JavaScript Number 精度损失。

分别记录 RF 查询开始/结束时刻、定位时刻、主机接收时刻。时间匹配策略及允许偏差写入实验配置。后续优先以查询时间区间中点匹配或插值定位，保留实际偏差；上传到达时间用于网络监控和补传，不替代观测时间。回放按观测时间排序，同一时刻以稳定记录 ID 排序。

一次 `radio_observation` 对应多条 `cell_measurements`，不固定邻区数量。设备使用跨航次持续递增并持久化的 `sequence`，`(device_id, sequence)` 作为补传去重键。若设备计数器重置，应更换设备记录标识，避免复用旧键。

LTE 指标：RSRP/RSSI 单位 dBm，RSRQ/SINR 单位 dB。不支持或无法解析的字段为 NULL，保留原始 AT 响应。AT 工程指标与原始复数 CSI 分开定义；本项目首期使用模块可取得的蜂窝测量指标。

小区身份优先使用制式、运营商 MCC/MNC 与完整 Cell ID。邻区缺少 Cell ID 时保留 PCI + ARFCN 及观测范围，后续与基站登记表关联。PCI + ARFCN 是区域内的候选标识，不作为全球唯一基站标识。MCC/MNC、Cell ID、TAC 使用字符串保存，保留前导零与原始编码信息。
