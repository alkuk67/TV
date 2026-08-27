import re
import asyncio
import requests
import logging
from collections import OrderedDict
from datetime import datetime
import config
import check as quality_checker
import fetch_hotel
import os
import isp_checker


logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s", handlers=[logging.FileHandler("function.log", "w", encoding="utf-8"), logging.StreamHandler()])


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
            resp = requests.get(epg_url, timeout=15)
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
        response = requests.get(url)
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
        if channels:
            categories = ", ".join(channels.keys())
            logging.info(f"url: {url} 抓取成功，包含频道分类: {categories}")
    except requests.RequestException as e:
        logging.error(f"url: {url} 抓取失败。 Error: {e}")

    return channels


def _normalize(name: str) -> str:
    s = name.strip()
    # 去掉括号内容
    s = re.sub(r'[（\[(（\[).?[）\]\)]', '', s)
    # 去掉末尾常见后缀
    suffixes = '高清版|超高清版|频道|卫视频|高清|超高清|HD|台|综艺|纪录|纪实|体育|电影|戏曲|科教|新闻|少儿|音乐|综合|法治|生活|军事|农业|农村|戏剧|文化|经济|社会|百科|世界|地理|历史|探索|发现|天文|游戏|汽车|旅游|时尚|女性|儿童|财经|老年|电视|公映|赛事|中文国际|国防军事|社会与法|奥林匹克|体育赛事|农业农村|电视剧'
    s = re.sub(r'(' + suffixes + ')$', '', s)
    while re.search(r'(' + suffixes + ')$', s):
        s = re.sub(r'(' + suffixes + ')$', '', s)
    # 去掉连字符和空格
    s = s.replace('-', '').replace(' ', '')
    return s


def match_channels(template_channels, all_channels):
    matched_channels = OrderedDict()

    for category, channel_list in template_channels.items():
        matched_channels[category] = OrderedDict()
        for channel_name in channel_list:
            norm_target = _normalize(channel_name)
            for online_category, online_channel_list in all_channels.items():
                for online_channel_name, online_channel_url in online_channel_list:
                    if _normalize(online_channel_name) == norm_target:
                        matched_channels[category].setdefault(channel_name, []).append(online_channel_url)

    return matched_channels


def filter_source_urls(template_file):
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

    matched_channels = match_channels(template_channels, all_channels)

    return matched_channels, template_channels


def is_ipv6(url):
    """判断是否为 IPv6 地址"""
    clean_url = url.rstrip("$")
    return re.match(r"^https?://\[.+\]", clean_url) is not None


def _get_source_type(url: str, category: str = "") -> str:
    """识别 URL 来源类型：hotel（酒店源）或 subscription（订阅源）
    酒店源的 category 为 matchType（txiptv/zhgxtv/jsmpeg），与订阅源分类名不冲突。"""
    hotel_cats = {"txiptv", "zhgxtv", "jsmpeg"}
    if category in hotel_cats:
        return "hotel"
    return "subscription"




def _url_sort_key(url: str, check_results: dict, category: str = ""):
    clean = url.split(chr(36), 1)[0] if chr(36) in url else url
    is_v6 = is_ipv6(url)
    ipv6_first = config.ip_version_priority == "ipv6"
    ipv6_rank = 0 if (is_v6 and ipv6_first) or (not is_v6 and not ipv6_first) else 1
    source_priority = config.source_priority
    source_rank = 0 if _get_source_type(url, category) == source_priority else 1
    layer = "fast"
    bitrate = 0
    width = 0
    speed_kbps = 0
    response_time_ms = 0
    if check_results:
        for cat_ch in check_results.values():
            for ch_urls in cat_ch.values():
                r = ch_urls.get(clean, {})
                if r:
                    layer = r.get("layer", "fast")
                    fp = r.get("ffprobe", {})
                    if fp:
                        bitrate = fp.get("bitrate", 0)
                        width = fp.get("width", 0)
                    deep = r.get("deep", {})
                    if deep:
                        speed_kbps = deep.get("speed_kbps", 0)
                    response_time_ms = r.get("response_time_ms", 0)
                    break
    layer_rank = 0 if layer in ("ffprobe", "deep") else 1
    sort_mode = getattr(config, "sort_mode", "balanced")
    if sort_mode == "speed":
        speed_weight = 0.6
        quality_weight = 0.4
    elif sort_mode == "quality":
        speed_weight = 0.35
        quality_weight = 0.65
    else:
        speed_weight = 0.48
        quality_weight = 0.52
    max_speed = max(speed_kbps, 1)
    max_bitrate = max(bitrate, 1)
    max_width = max(width, 1)
    speed_score = (speed_kbps / max_speed) * 1000 if max_speed > 0 else 0
    bitrate_score = (bitrate / max_bitrate) * 1000 if max_bitrate > 0 else 0
    quality_score = (speed_score * 0.5 + bitrate_score * 0.5) * 0.45 + (width / max_width) * 1000 * 0.55
    combined_score = speed_score * speed_weight + quality_score * quality_weight + layer_rank * 500
    max_latency = 2000
    if response_time_ms > 0:
        latency_score = max(0, (1 - response_time_ms / max_latency)) * 1000
    else:
        latency_score = 500
    final_score = latency_score * 0.3 + combined_score * 0.7
    return (ipv6_rank, source_rank, response_time_ms, -final_score)


def _get_meta_suffix(url: str, check_results: dict) -> str:
    """从 check_results 提取 ffprobe 元数据和速度信息，生成后缀如 【1920x1080@256kbps 1.7Mbps】"""
    clean = url.split(chr(36), 1)[0] if chr(36) in url else url
    if not check_results:
        return ""
    for cat_ch in check_results.values():
        for ch_urls in cat_ch.values():
            r = ch_urls.get(clean, {})
            fp = r.get("ffprobe", {})
            # 获取深度探测的速度信息
            deep = r.get("deep", {})
            speed_kbps = deep.get("speed_kbps", 0) if deep else 0
            
            if fp and fp.get("status") == "ok":
                w, h = fp.get("width", 0), fp.get("height", 0)
                br = fp.get("bitrate", 0)
                parts = []
                if w and h:
                    parts.append(f"{w}x{h}")
                if br > 0:
                    parts.append(f"{br//1000}kbps")
                # 添加速度信息
                if speed_kbps > 0:
                    if speed_kbps >= 1000:
                        parts.append(f"{speed_kbps//1000}Mbps")
                    else:
                        parts.append(f"{speed_kbps}kbps")
                if parts:
                    # 分辨率和码率用 @ 连接，速度单独放后面
                    if len(parts) >= 2 and "x" in parts[0]:
                        # 有分辨率，格式：【1920x1080@256kbps 4Mbps】
                        res_br = "@".join(parts[:-1])
                        speed = parts[-1]
                        return f" 【{res_br} {speed}】"
                    else:
                        return " 【" + " ".join(parts) + "】"
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
    epg_id_map = fetch_epg_id_map()
    template_file = "demo.txt"
    channels, template_channels = filter_source_urls(template_file)

    # 酒店源抓取
    if config.hotel_config.get("enabled", False):
        logging.info("[酒店源] 开始抓取...")
        hotel_channels = await fetch_hotel.fetch_all_from_hotel()
        # 酒店源数据是 {cat: {name: [urls]}}，转成 {cat: [(name, url), ...]} 格式
        st_flat = {}
        for cat, ch_dict in hotel_channels.items():
            st_flat[cat] = []
            for name, urls in ch_dict.items():
                for url in urls:
                    st_flat[cat].append((name, url))
        # 用 match_channels 做模糊匹配，只保留模板中有的频道
        st_matched = match_channels(template_channels, st_flat)
        # 合并到 channels
        merged = 0
        for cat, ch_dict in st_matched.items():
            for name, urls in ch_dict.items():
                channels.setdefault(cat, {}).setdefault(name, []).extend(urls)
                merged += len(urls)
        logging.info(f"[酒店源] 合并完成，共添加 {merged} 个 URL")

    if config.enable_quality_check:
        logging.info("[质量检测] 开始...")
        # ISP 运营商预过滤（在检测前剔除不符合要求的 URL，节省探测开销）
        channels, isp_removed = await quality_checker._isp_filter_urls(channels)
        if isp_removed > 0:
            logging.info(f"[ISP] 预过滤移除 {isp_removed} 个 URL")
        check_results, fail_domains = await quality_checker.check_all(channels)
        channels = quality_checker.filter_dead_urls(channels, check_results)
        _print_domain_suggestions(fail_domains)
        logging.info("[质量检测] 完成")

    # ISP 分类输出
    if config.enable_isp_split:
        _output_isp_files(channels, template_channels, epg_id_map, check_results)
    else:
        updateChannelUrlsM3U(channels, template_channels, epg_id_map, check_results)


def updateChannelUrlsM3U(channels, template_channels, epg_id_map=None, check_results=None):
    written_urls = set()
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
            for group in config.announcements:
                f_txt.write(f"{group['channel']},#genre#\n")
                for announcement in group["entries"]:
                    f_m3u.write(f"""#EXTINF:-1 tvg-id="{announcement['name']}" tvg-name="{announcement['name']}" tvg-logo="{announcement['logo']}" group-title="{group['channel']}",{announcement['name']}\n""")
                    f_m3u.write(f"{announcement['url']}\n")
                    f_txt.write(f"{announcement['name']},{announcement['url']}\n")

            for category, channel_list in template_channels.items():
                f_txt.write(f"{category},#genre#\n")
                if category in channels:
                    for channel_name in channel_list:
                        if channel_name in channels[category]:
                            sorted_urls = sorted(
                                channels[category][channel_name],
                                key=lambda url: _url_sort_key(url, check_results, category)
                            )
                            filtered_urls = []
                            for url in sorted_urls:
                                if url and url not in written_urls and not any(blacklist in url for blacklist in config.url_blacklist):
                                    filtered_urls.append(url)
                                    written_urls.add(url)

                            # 限制每频道最大线路数
                            if config.max_lines_per_channel > 0 and len(filtered_urls) > config.max_lines_per_channel:
                                old_count = len(filtered_urls)
                                filtered_urls = filtered_urls[:config.max_lines_per_channel]
                                logging.info("[频道] %s 线路从 %d 截断至 %d", channel_name, old_count, config.max_lines_per_channel)
                            total_urls = len(filtered_urls)
                            for index, url in enumerate(filtered_urls, start=1):
                                if is_ipv6(url):
                                    extra = _get_meta_suffix(url, check_results)
                                    url_suffix = f"$LR—IPV6{extra}" if total_urls == 1 else f"$LR—IPV6【线路{index}】{extra}"
                                else:
                                    extra = _get_meta_suffix(url, check_results)
                                    url_suffix = f"$LR—IPV4{extra}" if total_urls == 1 else f"$LR—IPV4【线路{index}】{extra}"
                                if "$" in url:
                                    base_url = url.split("$", 1)[0]
                                else:
                                    base_url = url

                                new_url = f"{base_url}{url_suffix}"

                                tvg_id = epg_id_map.get(channel_name, channel_name)
                                f_m3u.write(f"#EXTINF:-1 tvg-id=\"{tvg_id}\" tvg-name=\"{channel_name}\" tvg-logo=\"https://gcore.jsdelivr.net/gh/yuanzl77/TVlogo@master/png/{channel_name}.png\" group-title=\"{category}\",{channel_name}\n")
                                f_m3u.write(new_url + "\n")
                                f_txt.write(f"{channel_name},{new_url}\n")

            f_txt.write("\n")




def _get_domain(url: str) -> str:
    """从 URL 中提取 hostname（不含端口和路径）"""
    if not url:
        return ''
    stripped = url.split('\$', 1)[0] if '\$' in url else url
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
                try:
                    ip_obj = ipaddress.ip_address(domain)
                    ip_str = str(ip_obj)
                except ValueError:
                    try:
                        ip_str = socket.gethostbyname(domain)
                    except Exception:
                        ip_str = None
                if ip_str:
                    isp = checker.get_isp(ip_str)
                    isp_name = isp if isp else 'CDN'
                else:
                    isp_name = 'CDN'
                target = cdn_channels if isp_name == 'CDN' else isp_channels
                target.setdefault(isp_name, {}).setdefault(category, {}).setdefault(ch_name, []).append(url)
                if isp_name == 'CDN':
                    cdn_count += 1
                else:
                    isp_count += 1
    logging.info(f'[ISP分类] 完成，识别到 {len(isp_channels)} 个运营商组，CDN源 {cdn_count} 个，运营商源 {isp_count} 个')
    return isp_channels, cdn_channels


def _write_channel_file(filepath_txt, filepath_m3u, channels, template_channels, epg_id_map, check_results):
    """写入单个运营商的频道文件"""
    written_urls = set()
    epg_id_map = epg_id_map or {}
    current_date = datetime.now().strftime('%Y-%m-%d')
    for group in config.announcements:
        for announcement in group['entries']:
            name = announcement.get('name')
            if name is None or name == '__TIME__':
                name = current_date
            elif isinstance(name, str) and '__TIME__' in name:
                name = name.replace('__TIME__', current_date)
            announcement['name'] = name
    with open(filepath_m3u, 'w', encoding='utf-8') as f_m3u:
        epg_attr = ','.join(chr(34)+epg_url+chr(34) for epg_url in config.epg_urls)
        f_m3u.write(f'#EXTM3U x-tvg-url={epg_attr}\n')
        with open(filepath_txt, 'w', encoding='utf-8') as f_txt:
            for group in config.announcements:
                f_txt.write(f"{group['channel']},#genre#\n")
                for announcement in group['entries']:
                    f_m3u.write(f"""#EXTINF:-1 tvg-id="{announcement['name']}" tvg-name="{announcement['name']}" tvg-logo="{announcement['logo']}" group-title="{group['channel']}",{announcement['name']}\n""")
                    f_m3u.write(f"{announcement['url']}\n")
                    f_txt.write(f"{announcement['name']},{announcement['url']}\n")
            for category, channel_list in template_channels.items():
                f_txt.write(f"{category},#genre#\n")
                if category in channels:
                    for channel_name in channel_list:
                        if channel_name in channels[category]:
                            sorted_urls = sorted(channels[category][channel_name], key=lambda url: _url_sort_key(url, check_results, category))
                            filtered_urls = []
                            for url in sorted_urls:
                                if url and url not in written_urls and not any(blacklist in url for blacklist in config.url_blacklist):
                                    filtered_urls.append(url)
                                    written_urls.add(url)
                            if config.max_lines_per_channel > 0 and len(filtered_urls) > config.max_lines_per_channel:
                                old_count = len(filtered_urls)
                                filtered_urls = filtered_urls[:config.max_lines_per_channel]
                                logging.info('[频道] %s 线路从 %d 截断至 %d', channel_name, old_count, config.max_lines_per_channel)
                            total_urls = len(filtered_urls)
                            for index, url in enumerate(filtered_urls, start=1):
                                if is_ipv6(url):
                                    extra = _get_meta_suffix(url, check_results)
                                    url_suffix = f'$LR—IPV6{extra}' if total_urls == 1 else f'$LR—IPV6【线路{index}】{extra}'
                                else:
                                    extra = _get_meta_suffix(url, check_results)
                                    url_suffix = f'$LR—IPV4{extra}' if total_urls == 1 else f'$LR—IPV4【线路{index}】{extra}'
                                if '\$' in url:
                                    base_url = url.split('\$', 1)[0]
                                else:
                                    base_url = url
                                new_url = f"{base_url}{url_suffix}"
                                tvg_id = epg_id_map.get(channel_name, channel_name)
                                f_m3u.write(f'#EXTINF:-1 tvg-id="{tvg_id}" tvg-name="{channel_name}" tvg-logo="https://gcore.jsdelivr.net/gh/yuanzl77/TVlogo@master/png/{channel_name}.png" group-title="{category}",{channel_name}\n')
                                f_m3u.write(new_url + '\n')
                                f_txt.write(f'{channel_name},{new_url}\n')
            f_txt.write('\n')


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
    try:
        asyncio.run(async_main())
    finally:
        quality_checker._shutdown_ffprobe_executor()



