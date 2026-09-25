# IPTV 直播源检测配置说明

> 本说明依据当前 `config/config.py`、`check.py`、`main.py` 的实际逻辑整理。

## 一、整体流程

```
频道抓取与模板匹配 → HTTP 快筛 → FFprobe 元数据 → 流测速
                    → 过滤 → 排序、去重 → 输出
```

普通订阅、酒店源和组播源先统一匹配 `config/demo.txt`；订阅白名单在质量检测之后获取，不参与检测，输出时排在普通线路之后。

## 二、频道输入格式

### M3U

- 根据前 15 行是否包含 `#EXTINF` 判断格式。
- 从 `#EXTINF` 提取 `group-title` 和频道名，下一条非注释行作为 URL。

### TXT

- 有分组时识别：

```text
分类,#genre#
频道名,URL
```

- 没有 `分类,#genre#` 时，也支持直接输入：

```text
频道名,URL
```

- 直接输入的频道暂存到“未分类”，后续仍由 `config/demo.txt` 决定最终分类、频道和输出顺序。
- 未进入任何分组前，`#` 开头的注释行不会作为频道解析。
- 同步抓取和主流程使用的异步抓取都支持上述 TXT 格式。

## 三、检测各层说明

### 第一层：HTTP 快筛

- 功能：请求 URL，记录响应状态和响应时间。
- 超时：`check_timeout = 3.5s`。
- 并发：`check_max_conn = 10`。
- 最多读取 64KB 响应体，避免无限直播流一直读取到超时。
- M3u8 检查播放列表条目；空播放列表返回 `ok_no_ts`。
- 当前主流程统一调用 `_http_fast_check`。`_http_byte_check` 虽然存在，但没有接入 `_check_single`，因此 `/rtp/`、`/udp/` 地址也走相同的 HTTP 快筛。

### 第二层：FFprobe 元数据

- 开关：`enable_ffprobe = True`。
- `ffprobe_path` 为空时使用系统 `PATH` 中的 `ffprobe`。
- 超时：`ffprobe_timeout = 6s`。
- `ffprobe_max_streams = 3` 表示解析 FFprobe 输出时最多保留前 3 个流；它不限制 ffprobe 内部读取，`0` 表示不限制。
- `bitrate_sample_sec = 3` 时可通过 packet 大小估算真实码率，且只统计视频流。
- 获取分辨率、码率、编码格式等元数据。
- 当前质量检测流程依赖 FFprobe 结果；读取失败或没有宽度元数据时，配置 `min_resolution = "1080"` 会导致该 URL 被过滤。

### 第三层：流测速

#### M3u8

- FFprobe 成功后解析播放列表。
- 下载播放列表及前 3 个 TS 分片，计算 `speed_kbps`、首帧延迟和抖动。
- 测速成功时 `layer = "deep"`；失败时保留 FFprobe 结果，`layer` 回落为 `"ffprobe"`。
- `deep_probe_timeout` 作为播放列表和 TS 分片的剩余时间预算；每次请求使用截至当前的剩余时间，配置会直接影响 M3u8 测速超时。

#### 非 M3u8

- 调用 `_download_speed_test`，按块下载并计算速度、首帧延迟和抖动。
- 下载超时由 `speed_test_timeout = 5s` 控制。
- 下载上限由 `speed_test_max_bytes = 512KB` 控制；修改配置即可调整字节数。
- `speed_test_max_bytes` 不限制 M3u8，M3u8 当前固定下载播放列表和前 3 个 TS 分片。
- 根据 `bursty_ratio` 和 `bursty_max_gap_ms` 判断脉冲流；命中后标记为 `ok_unstable`，过滤阶段会移除。

## 四、过滤逻辑

`main.py` 当前调用：

```python
filter_dead_urls(
    channels,
    check_results,
    accept_layers=("fast", "ffprobe", "deep"),
)
```

URL 必须同时满足：

- `layer` 属于 `fast`、`ffprobe`、`deep`；`ffprobe_fail` 不在接收范围内。
- `status` 属于 `ok`、`ok_no_ts`。
- 深度测速状态不能是 `ok_unstable`。
- `min_resolution = "1080"` 时，FFprobe 宽度必须大于等于 1080；`0`、空字符串或非数字表示不限制。
- `min_bitrate` 为正数时，已读取的码率必须达到阈值；读取不到码率时不受该阈值限制。
- `min_speed_kbps` 为正数时，必须有达到阈值的测速结果；当前值为 0，因此测速不参与过滤。

因此，当前配置下 FFprobe 失败或分辨率元数据不足的源会被过滤，而不是保留后仅做排序扣分。

## 五、排序逻辑

`main.py` 的排序键依次比较：

1. IP 版本：`ip_version_priority = "ipv6"` 时 IPv6 排在 IPv4 前。
2. 来源优先级：`["multicast", "hotel", "subscription"]`。
3. 最终得分：得分越高越靠前；响应时间已经计入最终得分，不是单独的第三层排序键。

得分组成：

- `speed_score`：按“实测速度 / 视频码率”的余量比计算，范围约为 -1000 到 1000；码率未知时按 2500 kbps 估算。
- `quality_score = 码率分 × 0.45 + 分辨率分 × 0.55`；码率以 10 Mbps 为满分，分辨率以宽 1920 为满分。
- `layer = "deep"` 时综合分增加 500。
- 根据首帧延迟、抖动和丢包进行加减分。
- 元数据缺失扣分公式仍然存在，但 `ffprobe_fail` 通常已在过滤阶段被移除。
- `quality_weight`：`sort_mode = "quality"` 时为 0.65，`"speed"` 时为 0.40，其他模式为 0.52。
- 综合分：

```text
speed_score × 0.48
+ quality_score × quality_weight
+ deep 层加分
+ 流质量加减分
+ 元数据惩罚
```

- 延迟分：响应时间越短越高；没有响应时间数据时为 500。
- 最终分：

```text
延迟分 × 0.16 + 综合分 × 0.84
```

## 六、主要配置项

| 配置项 | 当前值 | 说明 |
|--------|--------|------|
| `ip_version_priority` | `"ipv6"` | IPv6 地址优先排序 |
| `source_priority` | `["multicast","hotel","subscription"]` | 来源优先级，靠前优先 |
| `max_lines_per_channel` | 8 | 每频道最大普通线路数；0 = 不限制 |
| `sort_mode` | `"balanced"` | `speed` / `quality` / `balanced` |
| `enable_isp_split` | `False` | 是否生成运营商分类文件 |
| `enable_announcements` | `True` | 是否把 `announcements` 写入全局和运营商输出 |
| `fetch_timeout` | 10 | 普通订阅源抓取超时（秒） |
| `enable_quality_check` | `True` | 是否执行质量检测 |
| `check_timeout` | 3.5 | HTTP 快筛超时（秒） |
| `check_max_conn` | 10 | 质量检测并发数 |
| `enable_ffprobe` | `True` | 是否执行 FFprobe 元数据探测 |
| `ffprobe_path` | `""` | 空字符串 = 使用系统 `PATH` |
| `ffprobe_max_streams` | 3 | 解析结果最多保留的前 N 个流；0 = 不限制 |
| `ffprobe_timeout` | 6 | FFprobe 超时（秒） |
| `bitrate_sample_sec` | 3 | packet 码率采样秒数；0 = 不采样 |
| `min_resolution` | `"1080"` | 最低宽度；`"0"` / 空字符串 = 不限制 |
| `min_bitrate` | 0 | 最低码率（bps）；0 = 不限制 |
| `deep_probe_timeout` | 6 | M3u8 播放列表和分片测速的总时间预算（秒） |
| `min_speed_kbps` | 0 | 最低测速阈值；0 = 只参与排序 |
| `speed_test_timeout` | 5 | 非 M3u8 下载测速超时（秒） |
| `speed_test_max_bytes` | 512KB | 非 M3u8 测速下载字节上限 |
| `bursty_ratio` | 4.0 | 最大间隔达到平均间隔的倍数时判定脉冲 |
| `bursty_max_gap_ms` | 800 | 判定脉冲所需的最大间隔（毫秒） |

## 七、公告开关

```python
enable_announcements = True
```

- `True`：把 `config.announcements` 写入 `live.m3u` 和 `live.txt`。
- `False`：全局输出和运营商分类输出都完全跳过公告分组及公告条目。
- 普通频道、检测、排序和线路数量限制不受该开关影响。

## 八、输出层含义

- `fast`：只取得 HTTP 快筛结果。
- `ffprobe`：通过 FFprobe，但没有成功进入深度测速。
- `deep`：完成真实流下载测速，带 `speed_kbps` 等数据。
- `ffprobe_fail`：FFprobe 未读到有效元数据；当前主流程不接收该层。
- `speed_kbps`：实测下载速度，单位 kbps。
