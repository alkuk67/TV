# IPTV 直播源检测配置说明

> 本说明依据当前 `config/config.py`、`check.py`、`main.py` 的实际逻辑整理。

## 一、整体流程

```
HTTP 快筛 → FFprobe 元数据探测 → 中度探测（仅 m3u8）→ 过滤 → 排序
  第一层          第二层                第三层
```

## 二、各层说明

### 第一层：HTTP 快筛
- 功能：检测 URL 是否可访问，获取响应时间
- 超时：check_timeout = 3.5s
- 并发：check_max_conn = 10
- 特殊流：包含 /rtp/、/udp/ 的地址改用字节检查，至少读取 50KB 才通过
- 结果：status 为 ok / ok_no_ts 时进入下一层

### 第二层：FFprobe 元数据探测
- 开关：enable_ffprobe = True；ffprobe_path 为空时使用系统 PATH 中的 ffprobe
- 功能：获取分辨率、码率、编解码器、speed_x 等元数据
- 超时：ffprobe_timeout = 8s；最多探测 ffprobe_max_streams = 3 个流
- 采样：bitrate_sample_sec = 3，用 packet 大小计算真实码率；0 = 不采样
- 过滤条件：
  - 分辨率：min_resolution = "1080"，宽度 < 1080 的源被淘汰；"0"/空 = 不限制
  - 码率：min_bitrate = 0，不限制；设为正数（如 2500000 = 2.5 Mbps）后低于阈值的源被淘汰，ffprobe 读不到码率的源不受影响
- 失败：FFprobe 读不到元数据时 layer 记为 ffprobe_fail，不直接淘汰，排序时扣分
- 测速：enable_speed_test = True 时下载流数据实测速率（上限 speed_test_max_bytes = 512KB），得到 speed_kbps、首帧延迟和抖动，只用于排序，不过滤

### 第三层：中度探测（仅 m3u8 流）
- 开关：enable_moderate_probe = True
- 功能：解析 m3u8 清单，下载前 3 个 TS 分片计算综合速度（speed_kbps）
- 超时：deep_probe_timeout = 6.5s
- 结果：成功 → layer = "deep"；失败 → 回落为 layer = "ffprobe"
- 复测：stability_test_count = 1 表示只测一次；调大后按 stability_test_interval 间隔重复探测，取中位速度

## 三、过滤逻辑（保留条件）

main.py 调用：
```
filter_dead_urls(channels, check_results, accept_layers=("fast", "ffprobe", "deep", "ffprobe_fail"))
```

同时满足以下条件才保留：
- layer 属于 ("fast", "ffprobe", "deep", "ffprobe_fail")
- status 属于 ("ok", "ok_no_ts")
- 分辨率符合 min_resolution（"1080" 表示宽 >= 1080；"0"/空/非数字 = 不限制）
- 码率符合 min_bitrate（0 = 不限制；读不到码率的源不受影响）
- 速度符合 min_speed_kbps（0 = 不限制；无测速结果的源保留）

即：FFprobe 失败的源不会被淘汰，速度不参与过滤，只影响排序。

## 四、排序逻辑（main.py 的 _url_sort_key）

排序键依次为：
1. IP 优先级：按 ip_version_priority（"ipv6" = IPv6 排前）
2. 来源优先级：按 source_priority 顺序（multicast > hotel > subscription）
3. 响应时间：越短越靠前
4. 最终得分：越高越靠前

打分项：
- 速度分 speed_score：按「实测速度 / 视频码率」的余量比打分（-1000 ~ 1000），码率未知时按 2500 kbps 估算；余量 >= 4 倍为满分，< 0.8 倍为负分
- 画质分 quality_score = 码率分 × 0.45 + 分辨率分 × 0.55（码率以 10 Mbps 为满分，分辨率以宽 1920 为满分）
- 深度层加分：layer = "deep" 时综合分 +500
- ffprobe speed_x：>= 1.0 加分（上限 600），< 1.0 扣分
- 流质量：首帧延迟 <200ms 加 400、<500ms 加 200、>2s 扣分；抖动 <100ms 加 200、<300ms 加 100；丢包按比例扣分
- 元数据缺失：layer = ffprobe_fail 时固定扣 300 分
- 综合分 = 速度分 × 0.48 + 画质分 × quality_weight + ...；quality_weight 在 sort_mode = "quality" 时为 0.65，否则 0.52
- 延迟分 = max(0, (1 - 响应时间/2000)) × 1000，无响应时间数据时给 500
- 最终分 = 延迟分 × 0.3 + 综合分 × 0.7

## 五、配置项对照表

| 配置项 | 当前值 | 说明 |
|--------|--------|------|
| ip_version_priority | "ipv6" | IPv6 地址优先排序 |
| source_priority | ["multicast","hotel","subscription"] | 来源优先级，高的排前 |
| max_lines_per_channel | 8 | 每频道最大线路数，0 = 不限制 |
| sort_mode | "balanced" | 排序模式：speed / quality / balanced |
| enable_isp_split | False | True = 生成运营商分类文件 |
| fetch_timeout | 10 | 订阅源抓取超时（秒）|
| enable_quality_check | True | False = 跳过检测直接输出 |
| check_timeout | 3.5s | HTTP 快筛超时 |
| check_max_conn | 10 | 并发检测数 |
| enable_ffprobe | True | 是否启用 FFprobe |
| ffprobe_path | "" | FFprobe 可执行文件路径；空 = 用系统 PATH |
| ffprobe_max_streams | 3 | FFprobe 最多探测流数 |
| ffprobe_timeout | 8s | FFprobe 超时 |
| bitrate_sample_sec | 3 | 码率采样秒数；0 = 不采样 |
| min_resolution | "1080" | 最低分辨率宽度；"0"/"" = 不限制 |
| min_bitrate | 0 | 最低码率（bps）；0 = 不限制 |
| enable_moderate_probe | True | 启用中度探测（仅 m3u8）|
| deep_probe_timeout | 6.5s | 中度探测超时 |
| stability_test_count | 1 | 额外复测次数；1 = 只测一次 |
| stability_test_interval | 1.0s | 复测间隔 |
| enable_speed_test | True | 启用下载测速（实测速率）|
| speed_test_timeout | 5s | 测速下载超时 |
| speed_test_segments | 3 | 取平均的分片数（当前实现按字节上限下载，此参数暂未生效）|
| speed_test_max_bytes | 512KB | 测速下载字节上限 |
| min_speed_kbps | 0 | 最低测速阈值；0 = 只排序不过滤 |

## 六、输出说明

- layer="fast"：仅通过 HTTP 快筛（FFprobe 未启用时出现）
- layer="ffprobe"：通过第二层，未做或未通过中度探测
- layer="deep"：通过第三层，有速度数据
- layer="ffprobe_fail"：HTTP 通过但 FFprobe 未读到元数据（保留，排序扣分）
- speed_kbps：实测下载速度（kbps）
