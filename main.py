import re
import asyncio
import aiohttp
import requests as sync_requests
import logging
from collections import OrderedDict
from datetime import datetime
import os
import sys
sys.path.insert(0, os.path.dirname(__file__))
import config.config as config
import check as quality_checker
import fetch_hotel
import fetch_multicast
import isp_checker


# 自定义格式：时间 - 级别 - 模块.函数:行号 - 消息
_FILE_FORMAT = "%(asctime)s - %(levelname)s - %(name)s:%(filename)s:%(lineno)d - %(message)s"
_CONSOLE_FORMAT = "%(asctime)s - %(levelname)s - %(message)s"
_file_fmt = logging.Formatter(_FILE_FORMAT, datefmt="%Y-%m-%d %H:%M:%S")
_console_fmt = logging.Formatter(_CONSOLE_FORMAT, datefmt="%H:%M:%S")
# server.py 以子进程方式启动 main.py 时设 IPTV_SUPERVISED=1；
# 此时只注册 StreamHandler（stdout 由 server.py 统一捕获写 function.log），
# 避免 FileHandler + stdout 中继导致 function.log 中每条日志出现两遍。
_supervised = os.environ.get("IPTV_SUPERVISED") == "1"
_handlers = []
if not _supervised:
    try:
        file_handler = logging.FileHandler("function.log", "a", encoding="utf-8")
        file_handler.setFormatter(_file_fmt)
        _handlers.append(file_handler)
    except OSError:
        # function.log 缺失或挂载异常时只输出控制台，避免主程序启动即崩溃
        pass
stream_handler = logging.StreamHandler()
stream_handler.setFormatter(_console_fmt)
_handlers.append(stream_handler)
# 防止 server.py 以子进程/import 方式重复注册 handler（否则每条日志打印两遍）
if not logging.getLogger().handlers:
    logging.basicConfig(level=logging.INFO, handlers=_handlers)
logging.getLogger().setLevel(logging.INFO)


def parse_template(template_file):
    template_channels = OrderedDict()
    current_category = None

    with open(template_file, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#"):
                if "#genre#" in line:
                    current_category = line.split(",")[0].strip()
                    template_channels[current_category] = []
                elif current_category:
                    channel_name = line.split(",")[0].strip()
                    template_channels[current_category].append(channel_name)

    return template_channels


def fetch_epg_id_map():
    """从 config.epg_urls 依次加载 EPG，按频道名合并映射。保底源放末尾。"""
    import gzip
    import xml.etree.ElementTree as ET
    epg_id_map = {}
    for epg_url in config.epg_urls:
        try:
            resp = sync_requests.get(epg_url, timeout=15)
            resp.raise_for_status()
            # .gz 链接需要解压，.xml 链接直接用
            if epg_url.endswith(".gz"):
                xml_data = gzip.decompress(resp.content)
            else:
                xml_data = resp.content
            tree = ET.fromstring(xml_data)
            for ch in tree.findall("channel"):
                dn = ch.find("display-name")
                if dn is not None and dn.text:
                    eid = ch.get("id", "").strip()
                    name = dn.text.strip()
                    if eid and name:
                        epg_id_map.setdefault(name, eid)
            logging.info(f"[EPG映射] 从 {epg_url} 加载成功")
        except Exception as e:
            logging.warning(f"[EPG映射] 从 {epg_url} 加载失败，尝试下一个源: {e}")
    if epg_id_map:
        logging.info(f"[EPG映射] 共合并加载 {len(epg_id_map)} 个频道 ID")
    else:
        logging.warning("[EPG映射] 所有 EPG 源均加载失败，将使用默认数字 ID")
    return epg_id_map




def fetch_channels(url):
    channels = OrderedDict()

    try:
        response = sync_requests.get(url, timeout=10)
        response.raise_for_status()
        response.encoding = "utf-8"
        lines = response.text.split("\n")
        current_category = None
        is_m3u = any("#EXTINF" in line for line in lines[:15])
        source_type = "m3u" if is_m3u else "txt"
        logging.info(f"url: {url} 获取成功，判断为{source_type}格式")

        if is_m3u:
            for line in lines:
                line = line.strip()
                if line.startswith("#EXTINF"):
                    match = re.search(r'group-title="(.*?)",(.*)', line)
                    if match:
                        current_category = match.group(1).strip()
                        channel_name = match.group(2).strip()
                        if current_category not in channels:
                            channels[current_category] = []
                    else:
                        # 无 group-title 的 EXTINF：只更新频道名、不动当前分组，
                        # 避免后续 URL 被记到上一个频道名下
                        m2 = re.match(r'#EXTINF[^,]*,(.*)', line)
                        channel_name = m2.group(1).strip() if m2 else None
                elif line and not line.startswith("#"):
                    channel_url = line.strip()
                    if current_category and channel_name:
                        channels[current_category].append((channel_name, channel_url))
        else:
            for line in lines:
                line = line.strip()
                if "#genre#" in line:
                    current_category = line.split(",")[0].strip()
                    channels[current_category] = []
                elif current_category:
                    match = re.match(r"^(.*?),(.*?)$", line)
                    if match:
                        channel_name = match.group(1).strip()
                        channel_url = match.group(2).strip()
                        channels[current_category].append((channel_name, channel_url))
                    elif line:
                        channels[current_category].append((line, ""))
                elif not line.startswith("#"):
                    match = re.match(r"^(.*?),(.*?)$", line)
                    if match:
                        channel_name = match.group(1).strip()
                        channel_url = match.group(2).strip()
                        channels.setdefault("未分类", []).append((channel_name, channel_url))
        if channels:
            categories = ", ".join(channels.keys())
            logging.info(f"url: {url} 抓取成功，包含频道分类: {categories}")
    except (sync_requests.RequestException, Exception) as e:
        logging.error(f"url: {url} 抓取失败。 Error: {e}")

    return channels


def _normalize(name: str) -> str:
    s = name.strip()
    # 去掉括号及其内容，支持中英文圆括号/方括号/实心方头括号
    s = re.sub(r'[（(【\[][^（）()【\]】]*[）)】\]]', '', s)
    # 清理残余的孤立括号字符，以及名字中的 . 和 ?（旧版行为，保留避免已有匹配回归）
    s = re.sub(r'[（）()【\]】.?]', '', s)
    # 去掉末尾常见后缀
    suffixes = '高清版|超高清版|频道|卫视频|高清|超高清|SD|sd|HD|hd'
    s = re.sub(r'(' + suffixes + ')$', '', s)
    while re.search(r'(' + suffixes + ')$', s):
        s = re.sub(r'(' + suffixes + ')$', '', s)
    # 去掉连字符和空格
    s = s.replace('-', '').replace(' ', '')
    # 全部被剥离时回退到原名，避免不同频道归一化成同一个空串而互相误匹配
    return s or name.strip()


def load_alias_map(alias_file='config/alias.txt'):
    alias_map = {}
    try:
        with open(alias_file, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith('#'):
                    continue
                parts = [p.strip() for p in line.split(',')]
                if len(parts) < 2:
                    continue
                standard_name = parts[0]
                # 添加主名到自己
                alias_map[standard_name] = standard_name
                for alias in parts[1:]:
                    alias = alias.strip()
                    if alias:
                        alias_map[alias] = standard_name
        logging.info('[别名映射] 从 %s 加载 %d 条别名规则', alias_file, len(alias_map))
    except FileNotFoundError:
        logging.warning('[别名映射] 未找到别名文件 %s，跳过别名匹配', alias_file)
    except Exception as e:
        logging.warning('[别名映射] 加载别名文件失败: %s', e)
    return alias_map


def resolve_alias(name, alias_map):
    n = name.strip()
    # 先精确匹配
    if n in alias_map:
        return alias_map[n]
    # normalize 后匹配
    norm = _normalize(n)
    if norm in alias_map:
        return alias_map[norm]
    # 正则匹配（re: 前缀）
    import re as _re
    for alias_pattern, std in alias_map.items():
        if alias_pattern.startswith('re:'):
            try:
                if _re.match(alias_pattern[3:], n):
                    return std
            except Exception:
                pass
    return None



def _build_reverse_index(all_channels, alias_map):
    """预建反向索引：normalize名称 -> [(online_name, url), ...]，大幅加速匹配"""
    import re as _re
    index = {}
    # 预编译别名中的正则模式
    regex_patterns = []
    if alias_map:
        for k, v in alias_map.items():
            if k.startswith("re:"):
                try:
                    regex_patterns.append((_re.compile(k[3:]), v))
                except Exception:
                    pass
    # 遍历所有在线URL，建立索引
    for cat, entries in all_channels.items():
        for name, url in entries:
            # 1. 精确匹配别名
            if alias_map and name in alias_map:
                std = alias_map[name]
                norm_std = _normalize(std)
                index.setdefault(norm_std, []).append((name, url))
                continue
            # 2. normalize 后匹配
            norm = _normalize(name)
            index.setdefault(norm, []).append((name, url))
            # 3. 正则别名匹配
            if alias_map and regex_patterns:
                for pat, std in regex_patterns:
                    try:
                        if pat.match(name):
                            norm_std = _normalize(std)
                            index.setdefault(norm_std, []).append((name, url))
                    except Exception:
                        pass
    return index


def match_channels(template_channels, all_channels, alias_map=None):
    matched_channels = OrderedDict()
    total_matched_urls = 0
    # 预建反向索引，O(N+M) 替代 O(N*M*K)
    reverse_index = _build_reverse_index(all_channels, alias_map)
    for category, channel_list in template_channels.items():
        matched_channels[category] = OrderedDict()
        for channel_name in channel_list:
            norm_target = _normalize(channel_name)
            matched_urls = reverse_index.get(norm_target, [])
            if matched_urls:
                matched_channels[category][channel_name] = [url for _, url in matched_urls]
                total_matched_urls += len(matched_urls)
    total_template = sum(len(ch) for ch in template_channels.values())
    # all_channels[category] 是 (频道名, url) 元组列表，直接取列表长度即 URL 数
    total_online = sum(len(entries) for entries in all_channels.values())
    logging.info(f"[匹配统计] 模板 {total_template} 频道，在线 {total_online} URL，匹配 {total_matched_urls} URL")
    return matched_channels



def filter_source_urls(template_file, alias_map=None):
    template_channels = parse_template(template_file)
    source_urls = config.source_urls

    all_channels = OrderedDict()
    for url in source_urls:
        fetched_channels = fetch_channels(url)
        for category, channel_list in fetched_channels.items():
            if category in all_channels:
                all_channels[category].extend(channel_list)
            else:
                all_channels[category] = channel_list

    matched_channels = match_channels(template_channels, all_channels, alias_map)

    return matched_channels, template_channels




async def fetch_channels_async(session, url, timeout):
    channels = OrderedDict()
    logging.info(f'[抓取] 开始: {url}')
    try:
        async with session.get(url, timeout=timeout) as response:
            response.raise_for_status()
            text = await response.text()
            lines = text.split(chr(10))
            current_category = None
            is_m3u = any('#EXTINF' in line for line in lines[:15])
            source_type = 'm3u' if is_m3u else 'txt'
            logging.info(f'[抓取] {url} 获取成功，判断为{source_type}格式')

            if is_m3u:
                for line in lines:
                    line = line.strip()
                    if line.startswith('#EXTINF'):
                        match = re.search(r'group-title="(.*?)",(.*)', line)
                        if match:
                            current_category = match.group(1).strip()
                            channel_name = match.group(2).strip()
                            if current_category not in channels:
                                channels[current_category] = []
                        else:
                            # 无 group-title 的 EXTINF：只更新频道名、不动当前分组，
                            # 避免后续 URL 被记到上一个频道名下
                            m2 = re.match(r'#EXTINF[^,]*,(.*)', line)
                            channel_name = m2.group(1).strip() if m2 else None
                    elif line and not line.startswith('#'):
                        channel_url = line.strip()
                        if current_category and channel_name:
                            channels[current_category].append((channel_name, channel_url))
            else:
                for line in lines:
                    line = line.strip()
                    if '#genre#' in line:
                        current_category = line.split(',')[0].strip()
                        channels[current_category] = []
                    elif current_category:
                        match = re.match(r'^(.*?),(.*?)$', line)
                        if match:
                            channel_name = match.group(1).strip()
                            channel_url = match.group(2).strip()
                            channels[current_category].append((channel_name, channel_url))
                        elif line:
                            channels[current_category].append((line, ''))
                    elif not line.startswith('#'):
                        match = re.match(r'^(.*?),(.*?)$', line)
                        if match:
                            channel_name = match.group(1).strip()
                            channel_url = match.group(2).strip()
                            channels.setdefault('未分类', []).append((channel_name, channel_url))
            if channels:
                total_urls = sum(len(urls) for urls in channels.values())
                categories = ', '.join(channels.keys())
                logging.info(f'[抓取] {url} 抓取成功，包含频道分类: {categories}')
                logging.info(f'[抓取] {url} 共获取 {total_urls} 个 URL')
                # 打印样本频道名用于调试
                sample_channels = []
                for cat in list(channels.values())[:3]:
                    for name, url_sample in list(cat)[:5]:
                        sample_channels.append(name)
                logging.info(f'[抓取] {url} 样本频道: {sample_channels[:10]}')
            else:
                logging.warning(f'[抓取] {url} 未抓到任何频道数据')
    except asyncio.TimeoutError:
        logging.error(f'[抓取] {url} 超时 (>{timeout.total:.0f}s)')
    except aiohttp.ClientError as e:
        logging.error(f'[抓取] {url} 网络错误: {type(e).__name__}: {e}')
    except Exception as e:
        logging.error(f'[抓取] {url} 抓取失败。 Error: {type(e).__name__}: {e}')
    return channels

async def filter_source_urls_async(template_file, alias_map=None):
    template_channels = parse_template(template_file)
    source_urls = config.source_urls
    fetch_timeout = aiohttp.ClientTimeout(total=getattr(config, 'fetch_timeout', 10))
    connector = aiohttp.TCPConnector(limit=5, ssl=False)
    
    all_channels = OrderedDict()
    async with aiohttp.ClientSession(connector=connector, timeout=fetch_timeout) as session:
        tasks = [fetch_channels_async(session, url, fetch_timeout) for url in source_urls]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        logging.info('[抓取] 等待所有源完成...')
        for url, fetched_channels in zip(source_urls, results):
            if isinstance(fetched_channels, Exception):
                logging.error(f'[抓取] {url} 异步抓取异常: {fetched_channels}')
                continue
            for category, channel_list in fetched_channels.items():
                if category in all_channels:
                    all_channels[category].extend(channel_list)
                else:
                    all_channels[category] = channel_list

    matched_channels = match_channels(template_channels, all_channels, alias_map)
    return matched_channels, template_channels


async def fetch_subscription_whitelist(template_channels, alias_map=None):
    """抓取订阅白名单（保底源）并按模板匹配；不参与质量检测。"""
    urls = getattr(config, "subscription_whitelist", []) or []
    if not urls:
        return {}
    fetch_timeout = aiohttp.ClientTimeout(total=getattr(config, "fetch_timeout", 10))
    connector = aiohttp.TCPConnector(limit=5, ssl=False)
    all_channels = OrderedDict()
    async with aiohttp.ClientSession(connector=connector, timeout=fetch_timeout) as session:
        tasks = [fetch_channels_async(session, url, fetch_timeout) for url in urls]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        for url, fetched_channels in zip(urls, results):
            if isinstance(fetched_channels, Exception):
                logging.warning(f"[订阅白名单] {url} 抓取失败: {fetched_channels}")
                continue
            for category, channel_list in fetched_channels.items():
                all_channels.setdefault(category, []).extend(channel_list)
    return match_channels(template_channels, all_channels, alias_map)

def is_ipv6(url):
    """判断是否为 IPv6 地址"""
    clean_url = url.rstrip("$")
    return re.match(r"^https?://\[.+\]", clean_url) is not None


# 匹配阶段会丢失 URL 的来源信息（排序时 category 已是模板分类名），
# 这里按 URL 记录来源类型，供 _get_source_type 排序时使用
_url_source_types = {}


# 订阅白名单匹配结果缓存，key=category/channel -> [url, ...]
_whitelist_matched = {}


def _mark_url_source(url_list, source_type):
    for u in url_list:
        _url_source_types[u.split("$", 1)[0]] = source_type


def _get_source_type(url: str, category: str = "") -> str:
    """识别 URL 来源类型：hotel、multicast 或 subscription。"""
    hotel_cats = {"txiptv", "zhgxtv", "jsmpeg", "hsmdtv"}
    clean_url = url.split("$", 1)[0]
    # 匹配阶段记录的来源优先
    marked = _url_source_types.get(clean_url)
    if marked:
        return marked
    if clean_url.startswith(("rtp://", "udp://")) or "/rtp/" in clean_url or "/udp/" in clean_url:
        return "multicast"
    if category in hotel_cats:
        return "hotel"
    return "subscription"




def _build_url_index(check_results: dict) -> dict:
    """把 {cat:{ch:{url:data}}} 扁平化为 {clean_url: data}，避免每个 URL 排序时全表扫描。"""
    index = {}
    if not check_results:
        return index
    for ch_dict in check_results.values():
        for url_dict in ch_dict.values():
            for url, data in url_dict.items():
                clean = url.split(chr(36), 1)[0] if chr(36) in url else url
                index.setdefault(clean, data)
    return index

# 测速打分基准：按"实测速度 / 视频码率"的余量比打分；
# ffprobe 读不到码率时按此默认码率估算（即标准示例中的 2.5 Mbps）
DEFAULT_STREAM_BITRATE_KBPS = 2500


def _speed_headroom_score(speed_kbps: float, bitrate_bps: int) -> float:
    """按余量比给测速打分（-1000~1000），替代原来的绝对速度打分。

    标准（以 2.5 Mbps 码率为例）：
      实测 >=10 Mbps（4x 余量） → ✅ 1000，余量充足流畅
      实测 5~10 Mbps（2~4x）    → 700~1000
      实测 3~5 Mbps（1.2~2x）   → 500~700，可以播，波动时可能缓冲
      实测 2~3 Mbps（0.8~1.2x） → 0~500，边缘
      实测 <2 Mbps（<0.8x）     → ❌ 负分（-1000~0），带宽不够必卡，
                                  码率画质再高也无法抵消，必排到能流畅的源之后
    """
    if speed_kbps <= 0:
        return 0.0
    bitrate_kbps = (bitrate_bps / 1000) if bitrate_bps and bitrate_bps > 0 else DEFAULT_STREAM_BITRATE_KBPS
    ratio = speed_kbps / max(bitrate_kbps, 1.0)
    if ratio >= 4.0:
        return 1000.0
    if ratio >= 2.0:
        return 700 + (ratio - 2.0) / 2.0 * 300
    if ratio >= 1.2:
        return 500 + (ratio - 1.2) / 0.8 * 200
    if ratio >= 0.8:
        return (ratio - 0.8) / 0.4 * 500
    # <0.8x：带宽不够必卡，随余量越低负分越深
    return -1000 + ratio / 0.8 * 1000


# Temporary per-run cache for _build_url_index, rebuilt on each sort batch
_url_index_cache = {}
_url_index_src_id = None

def _url_sort_key(url: str, check_results: dict, category: str = ""):
    clean = url.split(chr(36), 1)[0] if chr(36) in url else url
    is_v6 = is_ipv6(url)
    ipv6_first = config.ip_version_priority == "ipv6"
    ipv6_rank = 0 if (is_v6 and ipv6_first) or (not is_v6 and not ipv6_first) else 1
    source_type = _get_source_type(url, category)
    source_priorities = config.source_priority
    if isinstance(source_priorities, str):
        source_priorities = [source_priorities]
    source_rank = source_priorities.index(source_type) if source_type in source_priorities else len(source_priorities)
    layer = "http"
    bitrate = 0
    width = 0
    speed_kbps = 0
    response_time_ms = 0
    # check_results 为空（enable_quality_check=False）时下面这些不会被赋值，
    # 必须先给默认值，否则引用时抛 NameError
    first_frame_delay_ms = 0
    jitter_ms = 0
    packet_loss = 0.0
    if check_results:
        global _url_index_src_id
        if not _url_index_cache or _url_index_src_id is not id(check_results):
            _url_index_cache.clear()
            _url_index_cache.update(_build_url_index(check_results))
            _url_index_src_id = id(check_results)
        r = _url_index_cache.get(clean, {})
        if r:
            layer = r.get("layer", "http")
            fp = r.get("ffprobe", {})
            if fp:
                bitrate = fp.get("bitrate", 0)
                width = fp.get("width", 0)
            sp = r.get("deep", {})
            if sp:
                speed_kbps = sp.get("speed_kbps", 0)
            sq = r.get("stream_quality", {})
            first_frame_delay_ms = sq.get("first_frame_delay_ms", 0)
            jitter_ms = sq.get("jitter_ms", 0)
            packet_loss = sq.get("packet_loss", 0.0)
            response_time_ms = r.get("response_time_ms", 0)
    # 深度探测做过真实流下载测速，排序时应优于仅元数据/快筛结果
    layer_rank = 1 if layer == "deep" else 0
    sort_mode = getattr(config, "sort_mode", "balanced")
    if sort_mode == "quality":
        quality_weight = 0.65
    elif sort_mode == "speed":
        quality_weight = 0.40
    else:
        quality_weight = 0.52
    # 归一化基准必须是固定参考值。原来用 max(bitrate,1)/max(width,1) 会导致
    # 每个源都相对自身归一，quality_score 恒为 1000，码率与分辨率完全失去排序作用
    REF_BITRATE = 10_000_000  # 10 Mbps 视为满分码率
    REF_WIDTH = 1920          # 1080p 视为满分宽度
    bitrate_score = min(bitrate / REF_BITRATE, 1.0) * 1000
    # 速度分按"实测速度/视频码率"余量比打分，而非绝对速度：
    # 10 Mbps 线路跑 2.5 Mbps 的流是满分，但只跑到 2 Mbps 则必卡
    speed_score = _speed_headroom_score(speed_kbps, bitrate)
    quality_score = bitrate_score * 0.45 + min(width / REF_WIDTH, 1.0) * 1000 * 0.55
    # Stream quality scores (never filter, only affect ranking)
    # First frame delay: <200ms=+400, <500ms=+200, <1000ms=0, >2000ms=-300
    ff_score = 0
    if first_frame_delay_ms > 0:
        if first_frame_delay_ms < 200:
            ff_score = 400
        elif first_frame_delay_ms < 500:
            ff_score = 200
        elif first_frame_delay_ms < 1000:
            ff_score = 0
        else:
            ff_score = -min((first_frame_delay_ms - 1000) // 500 * 100, 300)
    # Jitter: <100ms=+200, <300ms=+100, >500ms=-200
    jit_score = 0
    if jitter_ms > 0:
        if jitter_ms < 100:
            jit_score = 200
        elif jitter_ms < 300:
            jit_score = 100
        elif jitter_ms < 500:
            jit_score = 0
        else:
            jit_score = -min((jitter_ms - 300) // 200 * 100, 200)
    # Packet loss: >0% penalizes
    pl_score = 0
    if packet_loss > 0:
        pl_score = -int(packet_loss * 500)
    # FFprobe 无元数据：说明分辨率/码率未知，排序时固定扣分
    metadata_penalty = -300 if check_results and layer == "ffprobe_fail" else 0
    combined_score = speed_score * 0.48 + quality_score * quality_weight + layer_rank * 500 + ff_score + jit_score + pl_score + metadata_penalty
    max_latency = 2000
    if response_time_ms > 0:
        latency_score = max(0, (1 - response_time_ms / max_latency)) * 1000
    else:
        latency_score = 500
    final_score = latency_score * 0.16 + combined_score * 0.84
    return (ipv6_rank, source_rank, -final_score)


def _get_meta_suffix(url: str, check_results: dict) -> str:
    """从 check_results 提取 ffprobe 元数据，生成后缀如 【1920x1080@256kbps】"""
    clean = url.split(chr(36), 1)[0] if chr(36) in url else url
    if not check_results:
        return ""
    global _url_index_src_id
    if not _url_index_cache or _url_index_src_id is not id(check_results):
        _url_index_cache.clear()
        _url_index_cache.update(_build_url_index(check_results))
        _url_index_src_id = id(check_results)
    r = _url_index_cache.get(clean, {})
    if not r:
        return ""
    fp = r.get("ffprobe", {})
    sp = r.get("deep", {})
    if fp and fp.get("status") == "ok":
        w, h = fp.get("width", 0), fp.get("height", 0)
        br = fp.get("bitrate", 0)
        speed = sp.get("speed_kbps", 0) if sp else 0
        parts = []
        if w and h:
            parts.append(f"{w}x{h}")
        if br > 0:
            parts.append(f"{br//1000}kbps")
        if speed > 0:
            if speed >= 1000:
                parts.append(f"{speed//1000}Mbps")
            else:
                parts.append(f"{speed}kbps")
        if parts or speed > 0:
            res_str = f"{w}x{h}" if (w and h) else ""
            bit_str = f"{br//1000}kbps" if br > 0 else ""
            speed_str = ""
            if speed > 0:
                speed_str = f"{speed//1000}Mbps" if speed >= 1000 else f"{speed}kbps"
            core = "@".join([s for s in [res_str, bit_str] if s])
            suffix = f" 【{core} {speed_str}】" if speed_str else f" 【{core}】"
            return suffix
    return ""

def _print_domain_suggestions(fail_domains: dict):
    """打印检测失败的域名建议列表，供用户考虑加入黑名单"""
    if not fail_domains:
        return
    summary = {}
    for domain, entries in sorted(fail_domains.items(), key=lambda x: -len(x[1])):
        statuses = {}
        for e in entries:
            s = e["status"]
            statuses[s] = statuses.get(s, 0) + 1
        status_str = ", ".join(f"{k}={v}" for k, v in sorted(statuses.items()))
        summary[domain] = f"失败次数={len(entries)}  ({status_str})"

    logging.info("[黑名单建议] 以下域名检测频繁失败，可考虑加入 url_blacklist：")
    for domain, info in summary.items():
        logging.info(f"  {domain}  {info}")



async def async_main():
    """异步主入口：fetch -> check -> write"""
    quality_checker.clear_stop_signal()
    _url_source_types.clear()
    alias_map = load_alias_map()
    logging.info("[抓取] 执行完整抓取流程，config.source_urls=%d 个, hotel=%s", len(config.source_urls), config.hotel_config.get("enabled"))
    epg_id_map = fetch_epg_id_map()
    template_file = "config/demo.txt"
    channels, template_channels = await filter_source_urls_async(template_file, alias_map)

    hotel_channels = {}
    if config.hotel_config.get("enabled", False):
        logging.info("[酒店源] 开始抓取...")
        hotel_channels = await fetch_hotel.fetch_all_from_hotel()
    # 酒店源按模板分类匹配
    all_hotel = []
    for cat, ch_dict in hotel_channels.items():
        for name, url_list in ch_dict.items():
            for url in url_list:
                all_hotel.append((name, url))
    if all_hotel:
        hotel_matched = match_channels(template_channels, {"hotel": all_hotel}, alias_map)
        for cat, ch_dict in hotel_matched.items():
            for ch_name, url_list in ch_dict.items():
                channels.setdefault(cat, {}).setdefault(ch_name, []).extend(url_list)
                _mark_url_source(url_list, "hotel")
        matched_count = sum(len(v) for v in channels.values())
        logging.info(f"[酒店源] 匹配到 {matched_count} 个频道")

    # 组播源
    multicast_channels = {}
    if config.multicast_config.get("enabled", False):
        logging.info("[组播源] 开始抓取...")
        multicast_channels = await fetch_multicast.fetch_multicast_channels()
    all_multicast = []
    for cat, ch_dict in multicast_channels.items():
        for name, url_list in ch_dict.items():
            for url in url_list:
                all_multicast.append((name, url))
    if all_multicast:
        multicast_matched = match_channels(template_channels, {"multicast": all_multicast}, alias_map)
        for cat, ch_dict in multicast_matched.items():
            for ch_name, url_list in ch_dict.items():
                channels.setdefault(cat, {}).setdefault(ch_name, []).extend(url_list)
                _mark_url_source(url_list, "multicast")
        matched_count = sum(len(v) for v in channels.values())
        logging.info(f"[组播源] 匹配到 {matched_count} 个频道")

    if quality_checker.check_stop_flag():
        logging.info('[主程序] 收到停止信号，退出')
        return
    check_results = None
    if config.enable_quality_check:
        logging.info("[质量检测] 开始全量检测（无缓存模式）... enable_ffprobe=%s, ffprobe_timeout=%ss, min_resolution=%s", config.enable_ffprobe, config.ffprobe_timeout, config.min_resolution)
        # ISP 运营商预过滤
        channels, isp_removed = await quality_checker._isp_filter_urls(channels)
        if isp_removed > 0:
            logging.info(f"[ISP] 预过滤移除 {isp_removed} 个 URL")
        # 直接全量检测
        check_results, fail_domains = await quality_checker.check_all(channels)
        if quality_checker.check_stop_flag():
            logging.info('[主程序] 收到停止信号，退出')
            return
        channels = quality_checker.filter_dead_urls(channels, check_results, accept_layers=("fast", "ffprobe", "deep"))
        _print_domain_suggestions(fail_domains)
        logging.info("[质量检测] 完成")

    # 订阅白名单：普通订阅源，但跳过质量检测；命中模板后垫在该频道线路最后
    if getattr(config, "subscription_whitelist", None):
        logging.info("[订阅白名单] 开始抓取保底源...")
        _whitelist_matched.clear()
        _whitelist_matched.update(await fetch_subscription_whitelist(template_channels, alias_map))
        count = sum(len(v) for v in _whitelist_matched.values())
        logging.info(f"[订阅白名单] 匹配到 {count} 个频道")

    total_channels = sum(len(ch) for ch in channels.values())
    total_urls = sum(sum(len(urls) for urls in ch.values()) for ch in channels.values())
    logging.info("[汇总] 频道分类 %d 个，总频道数 %d，总URL数 %d", len(channels), total_channels, total_urls)
    if config.enable_isp_split:
        _output_isp_files(channels, template_channels, epg_id_map, check_results)
    else:
        updateChannelUrlsM3U(channels, template_channels, epg_id_map, check_results)


def _write_channels_to_files(f_m3u, f_txt, channels, template_channels, epg_id_map, check_results, written_urls):
    """Write sorted, filtered, whitelist-appended channel URLs to m3u and txt files."""
    output_channels = set()
    output_url_count = 0
    for category, channel_list in template_channels.items():
        f_txt.write(f"{category},#genre#\n")
        if category in channels or category in _whitelist_matched:
            for channel_name in channel_list:
                whitelist_urls = _whitelist_matched.get(category, {}).get(channel_name, [])
                if channels.get(category, {}).get(channel_name) or whitelist_urls:
                    sorted_urls = sorted(
                        channels.get(category, {}).get(channel_name, []),
                        key=lambda url: _url_sort_key(url, check_results, category)
                    )
                    filtered_urls = []
                    for url in sorted_urls:
                        if url and url not in written_urls and not any(blacklist in url for blacklist in config.url_blacklist):
                            filtered_urls.append(url)
                            written_urls.add(url)
                    if config.max_lines_per_channel > 0 and len(filtered_urls) > config.max_lines_per_channel:
                        filtered_urls = filtered_urls[:config.max_lines_per_channel]
                    for wl_url in whitelist_urls:
                        if wl_url and wl_url not in written_urls:
                            filtered_urls.append(wl_url)
                            written_urls.add(wl_url)
                    total_urls = len(filtered_urls)
                    for index, url in enumerate(filtered_urls, start=1):
                        if is_ipv6(url):
                            extra = _get_meta_suffix(url, check_results)
                            url_suffix = f"$LR\u2014IPV6{extra}" if total_urls == 1 else f"$LR\u2014IPV6\u3010\u7ebf\u8def{index}\u3011{extra}"
                        else:
                            extra = _get_meta_suffix(url, check_results)
                            url_suffix = f"$LR\u2014IPV4{extra}" if total_urls == 1 else f"$LR\u2014IPV4\u3010\u7ebf\u8def{index}\u3011{extra}"
                        if "$" in url:
                            base_url = url.split("$", 1)[0]
                        else:
                            base_url = url
                        new_url = f"{base_url}{url_suffix}"
                        tvg_id = epg_id_map.get(channel_name, channel_name)
                        logo_url = config.channel_logo_template.format(channel_name=channel_name) if config.channel_logo_template else ""
                        f_m3u.write(f'#EXTINF:-1 tvg-id="{tvg_id}" tvg-name="{channel_name}" tvg-logo="{logo_url}" group-title="{category}",{channel_name}\n')
                        f_m3u.write(new_url + "\n")
                        f_txt.write(f"{channel_name},{new_url}\n")
                        output_channels.add((category, channel_name))
                        output_url_count += 1
    f_txt.write("\n")
    return len(output_channels), output_url_count

def updateChannelUrlsM3U(channels, template_channels, epg_id_map=None, check_results=None):
    written_urls = set()
    output_url_count = 0
    ch_count = 0
    epg_id_map = epg_id_map or {}

    current_date = datetime.now().strftime("%Y-%m-%d")
    for group in config.announcements:
        for announcement in group["entries"]:
            name = announcement.get("name")
            if name is None or name == "__TIME__":
                name = current_date
            elif isinstance(name, str) and "__TIME__" in name:
                name = name.replace("__TIME__", current_date)
            announcement["name"] = name

    output_dir = "output"
    os.makedirs(output_dir, exist_ok=True)
    with open(os.path.join(output_dir, "live.m3u"), "w", encoding="utf-8") as f_m3u:
        epg_attr = ",".join(chr(34)+epg_url+chr(34) for epg_url in config.epg_urls)
        f_m3u.write(f"#EXTM3U x-tvg-url={epg_attr}\n")

        with open(os.path.join(output_dir, "live.txt"), "w", encoding="utf-8") as f_txt:
            announcement_groups = config.announcements if config.enable_announcements else []
            for group in announcement_groups:
                f_txt.write(f"{group['channel']},#genre#\n")
                for announcement in group["entries"]:
                    f_m3u.write(f"""#EXTINF:-1 tvg-id="{announcement['name']}" tvg-name="{announcement['name']}" tvg-logo="{announcement['logo']}" group-title="{group['channel']}",{announcement['name']}\n""")
                    f_m3u.write(f"{announcement['url']}\n")
                    f_txt.write(f"{announcement['name']},{announcement['url']}\n")
                    output_url_count += 1

            ch_count, ch_urls = _write_channels_to_files(f_m3u, f_txt, channels, template_channels, epg_id_map, check_results, written_urls)
            output_url_count += ch_urls

    logging.info("[输出统计] 最终输出 %d 频道 / %d URL", ch_count, output_url_count)
def _get_domain(url: str) -> str:
    """从 URL 中提取 hostname（不含端口和路径）"""
    if not url:
        return ''
    stripped = url.split('$', 1)[0] if '$' in url else url
    from urllib.parse import urlparse
    parsed = urlparse(stripped)
    return parsed.hostname or ''


def _classify_by_isp(channels: dict) -> tuple:
    """按运营商分类频道，返回 (isp_channels, cdn_channels)"""
    checker = isp_checker.get_isp_checker()
    isp_channels = {}
    cdn_channels = {}
    cdn_count = 0
    isp_count = 0
    import ipaddress
    import socket
    for category, ch_dict in channels.items():
        for ch_name, url_list in ch_dict.items():
            for url in url_list:
                domain = _get_domain(url)
                if not domain:
                    continue
                all_ips = []
                try:
                    ip_obj = ipaddress.ip_address(domain)
                    all_ips = [str(ip_obj)]
                except ValueError:
                    try:
                        addr_info = socket.getaddrinfo(domain, None, socket.AF_UNSPEC, socket.SOCK_STREAM)
                        all_ips = [info[4][0] for info in addr_info]
                        ipv6_first = config.ip_version_priority == 'ipv6'
                        _v4 = [ip for ip in all_ips if ipaddress.ip_address(ip).version == 4]
                        _v6 = [ip for ip in all_ips if ipaddress.ip_address(ip).version == 6]
                        all_ips = (_v6 + _v4) if ipv6_first else (_v4 + _v6)
                    except Exception:
                        all_ips = []
                
                isp_matched = False
                for ip_str in all_ips:
                    isp = checker.get_isp(ip_str)
                    if isp:
                        isp_channels.setdefault(isp, {}).setdefault(category, {}).setdefault(ch_name, []).append(url)
                        isp_count += 1
                        isp_matched = True
                        break
                
                if not isp_matched:
                    cdn_channels.setdefault(category, {}).setdefault(ch_name, []).append(url)
                    cdn_count += 1
    logging.info(f'[ISP分类] 完成，识别到 {len(isp_channels)} 个运营商组，CDN源 {cdn_count} 个，运营商源 {isp_count} 个')
    return isp_channels, cdn_channels


def _write_channel_file(filepath_txt, filepath_m3u, channels, template_channels, epg_id_map, check_results):
    """写入单个运营商的频道文件"""
    written_urls = set()
    epg_id_map = epg_id_map or {}
    with open(filepath_m3u, 'w', encoding='utf-8') as f_m3u:
        epg_attr = ','.join(chr(34)+epg_url+chr(34) for epg_url in config.epg_urls)
        f_m3u.write(f'#EXTM3U x-tvg-url={epg_attr}\n')
        with open(filepath_txt, 'w', encoding='utf-8') as f_txt:
            announcement_groups = config.announcements if config.enable_announcements else []
            for group in announcement_groups:
                f_txt.write(f"{group['channel']},#genre#\n")
                for announcement in group['entries']:
                    f_m3u.write(f"""#EXTINF:-1 tvg-id="{announcement['name']}" tvg-name="{announcement['name']}" tvg-logo="{announcement['logo']}" group-title="{group['channel']}",{announcement['name']}\n""")
                    f_m3u.write(f"{announcement['url']}\n")
                    f_txt.write(f"{announcement['name']},{announcement['url']}\n")
            _write_channels_to_files(f_m3u, f_txt, channels, template_channels, epg_id_map, check_results, written_urls)


def _output_isp_files(channels, template_channels, epg_id_map, check_results):
    """按运营商分类输出频道文件"""
    isp_channels, cdn_channels = _classify_by_isp(channels)
    isp_abbr = {'China Mobile': 'cmcc', 'China Telecom': 'ct', 'China Unicom': 'cu',
                'China Education & Research Network': 'cernet', 'China Science & Technology Network': 'cstnet',
                'Dr.Peng': 'dp', 'CDN': 'cdn'}
    output_dir = 'output'
    os.makedirs(output_dir, exist_ok=True)
    updateChannelUrlsM3U(channels, template_channels, epg_id_map, check_results)
    logging.info('[输出] 已生成 %s/live.txt / %s/live.m3u', output_dir, output_dir)
    for isp_name, isp_data in isp_channels.items():
        abbr = isp_abbr.get(isp_name, isp_name.lower())
        prefix = f'{abbr}_live'
        merged = {}
        for cat, ch_dict in isp_data.items():
            merged[cat] = ch_dict
        for cat, ch_dict in cdn_channels.items():
            if cat not in merged:
                merged[cat] = ch_dict
            else:
                for ch_name, urls in ch_dict.items():
                    merged[cat].setdefault(ch_name, []).extend(urls)
        # 补充 CDN-only 分类到运营商文件
        all_isp_cats = set(isp_data.keys())
        cdn_only_cats = set(cdn_channels.keys()) - all_isp_cats
        for cat in cdn_only_cats:
            if cat not in merged:
                merged[cat] = {}
            cat_cdn = cdn_channels.get(cat, {})
            for ch_name, urls in cat_cdn.items():
                if isinstance(urls, str):
                    urls = [urls]
                if ch_name not in merged[cat]:
                    merged[cat][ch_name] = []
                # 确保是列表再 extend
                if isinstance(merged[cat][ch_name], list):
                    merged[cat][ch_name].extend(urls)
                else:
                    merged[cat][ch_name] = urls
        _write_channel_file(os.path.join(output_dir, f'{prefix}.txt'), os.path.join(output_dir, f'{prefix}.m3u'), merged, template_channels, epg_id_map, check_results)
        logging.info('[输出] 已生成 %s/%s.txt / %s/%s.m3u (%s)', output_dir, prefix, output_dir, prefix, isp_name)

if __name__ == "__main__":
    quality_checker.clear_stop_signal()
    asyncio.run(async_main())
