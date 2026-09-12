# ═══════════════════════════════════════════════════════════════════
# IPTV 直播源自动聚合工具 — 配置文件
# 所有开关和阈值均可在此调整
# ═══════════════════════════════════════════════════════════════════

# ── IP 优先级 ───────────────────────────────────────────────────────
# "ipv6" = IPv6 地址优先排在前面；"ipv4" = IPv4 优先
ip_version_priority = "ipv6"

# ── 源优先级 ────────────────────────────────────────────────────────
# 优先级从高到低；可选值："hotel"、"multicast"、"subscription"
source_priority = ["multicast","hotel","subscription"]

# 每频道最大线路数，0 = 不限制
max_lines_per_channel = 8

# ── 排序模式 ──────────────────────────────────────────────────────
# "speed" = 速度优先（60%）画质40%
# "quality" = 画质优先（65%）速度稳定35%
# "balanced" = 综合平衡（分辨率55%+速度码率45%）
sort_mode = "balanced"

# ── ISP 运营商分类 ────────────────────────────────────────────────
# enable_isp_split: True=生成运营商分类文件，False=仅输出全局文件
enable_isp_split = False


# ── 订阅源 ───────────────────────────────────────────────────────
# 每个 URL 都是一个 IPTV 直播源文件（支持 m3u 或 txt 格式）
# main.py 会依次请求这些地址，提取频道名和播放地址
# 注：被注释掉的源暂时停用，可取消注释启用
source_urls = [
    "http://45.192.97.170:6001/txt",
]

# 订阅源抓取超时（秒）
fetch_timeout = 10

hotel_config = {
    "hotel_api": "https://iptvs.pes.im",
    "enabled": True,
    "allowed_orgs": [],
}


multicast_config = {
    "multicast_api": "https://github.776512.xyz/https://raw.githubusercontent.com/alkuk67/iptv-scrape/refs/heads/main/data/channels_all.json",
    "enabled": True,
    "enabled_location": "",
    "enabled_operator": "",
}

# ── URL 黑名单 ───────────────────────────────────────────────────────
# 播放地址包含以下任意子串时会被自动过滤掉
# 用途：屏蔽已知失效、广告插播、低质量或不稳定的源
url_blacklist = [
    "epg.pw/stream/",
    "45.192.97.170:8880",
    "ali-m-l.cztv.com",
    "173.208.212.130:8181",
    "61.221.215.25:8800",
    "38.75.136.137:98"
]

# ── 公告条目 ────────────────────────────────────────────────────────
# 这些条目会写在直播源文件的最前面，位于所有频道之前
# 用途：展示公告信息，如主播链接、更新时间等
#
# name 的三种写法：
#   None                  → 自动替换为当天日期（如 "2026-08-22"）
#   "__TIME__"             → 同上，也替换为当天日期
#   "更新时间：__TIME__"   → 替换为 "更新时间：2026-08-22"
#
# channel   → 该公告在 live.txt 中的分类名（#genre# 分组）
# url       → 播放地址
# logo      → 频道图标 URL（m3u 中 tvg-logo 属性）
announcements = [
    {
        "channel": "公告-yuanzl77",
        "entries": [
            {"name": "www.776512.xyz", "url": "https://liuliuliu.tv/api/channels/233/stream", "logo": "https://ts2.tc.mm.bing.net/th/id/OIP-C.2CL9t6gI2-c5n5DI9Sl_0QAAAA?rs=1&pid=ImgDetMain&o=7&rm=3"},
            {"name": "更新时间：__TIME__", "url": "https://gitlab.com/lr77/IPTV/-/raw/main/%E4%B8%BB%E8%A7%92.mp4", "logo": "https://ts2.tc.mm.bing.net/th/id/OIP-C.2CL9t6gI2-c5n5DI9Sl_0QAAAA?rs=1&pid=ImgDetMain&o=7&rm=3"},
            {"name": "请勿传播", "url": "https://liuliuliu.tv/api/channels/1997/stream", "logo": "https://ts2.tc.mm.bing.net/th/id/OIP-C.2CL9t6gI2-c5n5DI9Sl_0QAAAA?rs=1&pid=ImgDetMain&o=7&rm=3"},
        ]
    }
]

# ── EPG 电子节目单 ────────────────────────────────────────────────────
# 同时用于两件事：
#   1. M3U 头部的 x-tvg-url 属性，供播放器读取节目指南
#   2. 频道 ID 映射（{频道名: tvg-id}），让播放器正确显示节目单
# 建议将最全面的源放在最后，作为保底
epg_urls = [
    "http://e.erw.cc/e.xml.gz",
    "https://gitee.com/taksssss/tv/raw/main/epg/112114.xml.gz",
    "http://epg.51zmt.top:8000/e.xml.gz"
]

# 频道图标模板：用 channel_name 变量替换，空字符串则不输出 tvg-logo
channel_logo_template = "https://tb.zbds.top/logo/{channel_name}.png"

# ── 质量检测 — # ── 质量检测 — HTTP 检测（第一层）──...──
# enable_quality_check : True=启用质量检测（HTTP + FFprobe），False=直接输出不过滤
# check_timeout        : 单个 URL HTTP 请求超时（秒），超时视为失效
# check_max_conn       : 最大并发检测数，调高可加速但更占带宽
enable_quality_check = True
check_timeout    = 3.5
check_max_conn   = 10

# ── 质量检测 — FFprobe 检测（第二层，参考 iptv-checker-rs）──...
# enable_ffprobe     : True=启用 FFprobe 探流，False=仅 HTTP 快筛
enable_moderate_probe = True
deep_probe_timeout    = 6.5
stability_test_count    = 1
stability_test_interval = 1.0
#                      推荐 True，能拿到分辨率/码率/视频编解码器等元数据
# ffprobe_path       : FFprobe 可执行文件路径（用于第二层元数据探测）
#                      空字符串 = 使用系统 PATH 里的 ffprobe
#                      Windows 如不在 PATH 中，填绝对路径即可
# ffprobe_timeout    : 单个 URL FFprobe 超时（秒）
#                      IPTV 流通常 1~3 秒即可探完，设为 5 秒以容忍慢源
# min_bitrate        : 最低码率阈值（bps）
#                      仅当 ffprobe 能读到码率字段且 > 0 时才走过滤
#                      0 = 不限制（IPTV 流常读不到码率，常不会被过滤）
# min_resolution     : 最低分辨率宽度（字符串，如 "720" 表示宽 >= 720px）
#                      空字符串 "" = 不限制分辨率
ffprobe_path       = ""        # 空 = 使用系统 PATH 里的 ffprobe
enable_ffprobe     = True
ffprobe_max_streams = 3
ffprobe_timeout    = 8
# bitrate_sample_sec : 每次 ffprobe 采样的秒数，用 packet 大小计算真实码率
#                      0 = 不采样（仅依赖容器声明的 bit_rate，TS 流通常无此字段）
#                      建议 2~5 秒，增加探测时间但获得准确码率数据
bitrate_sample_sec = 3
min_bitrate        = 0
min_resolution     = "1080"


# ── 质量检测 — 速度测试（第三层，可选，默认关闭）──...
# 仅针对 m3u8 流。通过下载前 N 个 TS 分片并取平均速度，作为排序依据
# 较慢的源会被排到后面，但默认不过滤（只排序不淘汰）
# enable_speed_test   : True=启用下载测速，False=不启用
# speed_test_timeout  : 单个 TS 分片下载超时（秒）
# speed_test_segments : 取平均的分片数量（推荐 3，太大拖慢整体检测）
# min_speed_kbps      : 最小下载速度阈值（kbps）
#                      0 = 不过滤，只参与排序
#                      > 0 = 低于此值的源会被过滤掉（推荐 2500 = 2.5 Mbps；
#                      无测速结果的源会被保留，避免误杀）
enable_speed_test   = True
speed_test_timeout  = 5
speed_test_segments = 3
min_speed_kbps      = 0
speed_test_max_bytes = 512 * 1024
