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
source_urls = [
    "https://cdn.qd.je/live.m3u",
    "http://rihou.cc:567/gggg.nzk",
    "http://193.123.86.190:14888/TV/iptv.php",
    "https://gh.927223.xyz/https://raw.githubusercontent.com/Kimentanm/aptv/master/m3u/iptv.m3u",
    #"https://github.776512.xyz/https://raw.githubusercontent.com/yuanzl77/zf/refs/heads/main/testtg.txt",
]

# ── 订阅白名单（保底源）──────────────────────────────────────────────
subscription_whitelist = [
  "http://192.168.1.31:2134",
  "http://192.168.1.31:18808/hoy-proxy.txt",
  "http://192.168.1.31:18080/channels.txt",
]

# 订阅源抓取超时（秒）
fetch_timeout = 10

hotel_config = {
    "hotel_api": "https://iptvs.pes.im",
    "enabled": True,
    "allowed_orgs": [],
    "concurrency": 60,
    "max_hosts": 50,
}

multicast_config = {
    "multicast_api": "https://raw.githubusercontent.com/alkuk67/iptv-scrape/refs/heads/main/data/channels_all.json",
    "enabled": True,
    "enabled_location": "",
    "enabled_operator": "",
}

# ── URL 黑名单 ───────────────────────────────────────────────────────
url_blacklist = [
    "epg.pw/stream/",
    "45.192.97.170:8880",
    "ali-m-l.cztv.com",
    "173.208.212.130:8181",
    "61.221.215.25:8800",
    "38.75.136.137:98"
]

# ── 公告条目 ────────────────────────────────────────────────────────
# True = 输出 announcements，False = 全局和运营商输出均跳过公告
enable_announcements = True

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
epg_urls = [
    "http://e.erw.cc/e.xml.gz",
    "https://gitee.com/taksssss/tv/raw/main/epg/112114.xml.gz",
    "http://epg.51zmt.top:8000/e.xml.gz"
]

# 频道图标模板
channel_logo_template = "https://tb.zbds.top/logo/{channel_name}.png"

# ── 质量检测 — HTTP 检测（第一层）──...──
# enable_quality_check : True=启用质量检测（HTTP + FFprobe），False=直接输出不过滤
# check_timeout        : 单个 URL HTTP 请求超时（秒）
# check_max_conn       : 最大并发检测数
enable_quality_check = True
check_timeout    = 3.5
check_max_conn   = 10

# ── 质量检测 — FFprobe 检测 ──...
# enable_ffprobe     : True=启用 FFprobe 探流，False=仅 HTTP 快筛
# ffprobe_path       : FFprobe 可执行文件路径（空=使用系统 PATH）
# ffprobe_timeout    : 单个 URL FFprobe 超时（秒）
# ffprobe_max_streams: 解析 FFprobe 输出时最多保留前 N 个流，0=不限制
# bitrate_sample_sec : 每次 ffprobe 采样的秒数（0=不采样）
# min_bitrate        : 最低码率阈值（bps），0=不限制
# min_resolution     : 最低分辨率宽度（如 "1080"），空字符串=不限制
ffprobe_path       = ""
enable_ffprobe     = True
ffprobe_max_streams = 3
ffprobe_timeout    = 6
bitrate_sample_sec = 3
min_bitrate        = 0
min_resolution     = "1080"

# ── 质量检测 — 速度测试（针对 m3u8 流的排序依据）──...
# deep_probe_timeout  : 单个 m3u8 速度测试超时（秒）
# min_speed_kbps      : 最小下载速度阈值（kbps），0=不过滤只排序
deep_probe_timeout    = 6
min_speed_kbps      = 0
speed_test_timeout  = 5
speed_test_max_bytes = 512 * 1024

# 脉冲流检测（0 = 关闭）
# bursty_ratio      : 最大间隔超过平均间隔的倍数才算脉冲
# bursty_max_gap_ms : 最大间隔超过此值（ms）才算脉冲
bursty_ratio      = 4.0
bursty_max_gap_ms = 800
