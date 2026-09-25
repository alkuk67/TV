"""
酒店源抓取模块 — 从速度测试站获取 IPTV 节点并按类型解析
"""
import asyncio
import json
import time
import logging
import aiohttp
import config.config as config
from urllib.parse import urlparse, urlunparse

logger = logging.getLogger(__name__)
_logger_fmt = logging.Formatter("%(asctime)s - %(levelname)s - %(name)s:%(filename)s:%(lineno)d - %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
for _h in logger.handlers:
    _h.setFormatter(_logger_fmt)


def _as_timeout(timeout):
    """Accept seconds or ClientTimeout; always return ClientTimeout."""
    if isinstance(timeout, aiohttp.ClientTimeout):
        return timeout
    try:
        return aiohttp.ClientTimeout(total=float(timeout))
    except (TypeError, ValueError):
        return aiohttp.ClientTimeout(total=15)


async def _fetch_json(session, url, timeout):
    """通用 JSON 请求"""
    try:
        async with session.get(url, timeout=_as_timeout(timeout)) as resp:
            resp.raise_for_status()
            text = await resp.text()
            return json.loads(text)
    except Exception:
        return None


async def _fetch_text(session, url, timeout):
    """通用文本请求（自动识别 UTF-8/GBK 编码）"""
    try:
        async with session.get(url, timeout=_as_timeout(timeout)) as resp:
            resp.raise_for_status()
            raw = await resp.read()
            # 优先 UTF-8（大多数 ZHGXTV 接口是 UTF-8）
            try:
                text = raw.decode("utf-8")
                return text
            except UnicodeDecodeError:
                pass
            # GBK 兜底
            return raw.decode("gbk", errors="replace")
    except Exception:
        return None


async def fetch_nodes():
    """从速度测试站拉取节点列表，按 allowed_orgs 过滤。"""
    hotel_api = config.hotel_config.get("hotel_api")
    allowed_orgs = config.hotel_config.get("allowed_orgs", [])
    timeout = aiohttp.ClientTimeout(total=15)
    connector = aiohttp.TCPConnector(limit=5, ssl=False)
    async with aiohttp.ClientSession(connector=connector, timeout=timeout) as session:
        data = await _fetch_json(session, hotel_api, timeout)
    if not data or "results" not in data:
        logger.warning("[酒店源] 未获取到节点数据")
        return []
    all_nodes = data["results"]
    logger.info(f"[酒店源] 共获取 {len(all_nodes)} 个节点")
    if allowed_orgs:
        filtered = [n for n in all_nodes if n.get("org", "") in allowed_orgs]
        logger.info(f"[酒店源] 按运营商过滤后剩余 {len(filtered)} 个节点")
    else:
        filtered = all_nodes
    type_count = {}
    for n in filtered:
        t = n.get("matchType", "unknown")
        type_count[t] = type_count.get(t, 0) + 1
    logger.info(f"[酒店源] 类型分布: {type_count}")
    return filtered


async def parse_txiptv(session, host, timeout):
    """TXIPTV: 返回 {channel_name: url}"""
    result = {}
    url = f"{host}/iptv/live/1000.json?key=txiptv"
    data = await _fetch_json(session, url, timeout)
    if not data or data.get("code") != 0:
        return result
    for ch in data.get("data", []):
        name = ch.get("name", "").strip()
        path = ch.get("url", "").strip()
        if name and path:
            full_url = path if path.startswith("http") else f"{host}{path}"
            result[name] = full_url
    return result


async def parse_zhgxtrv(session, host, timeout):
    """ZHGXTV: 返回 {channel_name: url}，过滤乱码行"""
    result = {}
    url = f"{host}/ZHGXTV/Public/json/live_interface.txt"
    text = await _fetch_text(session, url, timeout)
    if not text:
        return result
    for line in text.splitlines():
        line = line.strip()
        if not line or "," not in line:
            continue
        parts = line.split(",", 1)
        if len(parts) != 2:
            continue
        name, channel_url = parts[0].strip(), parts[1].strip()
        if not name or not channel_url:
            continue
        # live_interface.txt returns placeholder IP (e.g. http://2.2.2.2/hls/4/index.m3u8);
        # rewrite to the real host so the URL is playable
        parsed = urlparse(channel_url)
        if parsed.scheme in ("http", "https"):
            host_parsed = urlparse(host)
            if parsed.hostname in (None, "", "2.2.2.2") or parsed.netloc != host_parsed.netloc:
                channel_url = urlunparse((host_parsed.scheme or "http", host_parsed.netloc, parsed.path or "/", parsed.params, parsed.query, parsed.fragment))
            result[name] = channel_url
        elif channel_url.startswith("/"):
            result[name] = f"{host}{channel_url}"
    return result


async def parse_jsmpeg(session, host, timeout):
    """JSMPEG: 返回 {channel_name: http_url}"""
    result = {}
    url = f"{host}/streamer/list"
    data = await _fetch_json(session, url, timeout)
    if not data or not isinstance(data, list):
        return result
    for item in data:
        name = item.get("name", "").strip()
        key = item.get("key", "").strip()
        source = item.get("source", "").strip()
        if not name:
            continue
        # streamer/list source is an rtsp:// direct link (not playable);
        # build the hls URL from key whenever present
        if key:
            result[name] = f"{host}/hls/{key}/index.m3u8"
        elif source.startswith("http"):
            result[name] = source
    return result


# hsmdtv panel has no channel-list API; sampled hosts all expose the same
# fixed 40 channels at /newlive/live/hls/<N>/live.m3u8 (N=1..40).
# Names verified identical across 7 hosts from iptvs.pes.im.
HSMDTV_CHANNEL_NAMES = {
    1: "CCTV1", 2: "CCTV2", 3: "CCTV3", 4: "CCTV4", 5: "CCTV5",
    6: "CCTV5+", 7: "CCTV6", 8: "CCTV7", 9: "CCTV8", 10: "CCTV9",
    11: "CCTV10", 12: "CCTV11", 13: "CCTV12", 14: "CCTV13",
    15: "CCTV14", 16: "CCTV15", 17: "CCTV16", 18: "CGTN",
    19: "\u798f\u5efa\u7efc\u5408", 20: "\u4e1c\u5357\u536b\u89c6",
    21: "\u798f\u5efa\u4e61\u6751\u632f\u5174", 22: "\u798f\u5efa\u65b0\u95fb",
    23: "\u798f\u5efa\u7535\u89c6\u5267", 24: "\u798f\u5efa\u65c5\u6e38",
    25: "\u798f\u5efa\u7ecf\u6d4e\u751f\u6d3b", 26: "\u798f\u5efa\u5c11\u513f",
    27: "\u6d77\u5ce1\u536b\u89c6", 28: "\u53a6\u95e8\u536b\u89c6",
    29: "\u4e1c\u65b9\u536b\u89c6", 30: "\u6e56\u5357\u536b\u89c6",
    31: "\u6c5f\u82cf\u536b\u89c6", 32: "\u6d59\u6c5f\u536b\u89c6",
    33: "\u6cb3\u5317\u536b\u89c6", 34: "\u5e7f\u4e1c\u536b\u89c6",
    35: "\u5c71\u4e1c\u536b\u89c6", 36: "\u6e56\u5317\u536b\u89c6",
    37: "\u6df1\u5733\u536b\u89c6", 38: "\u9ed1\u9f99\u6c5f\u536b\u89c6",
    39: "\u8d35\u5dde\u536b\u89c6", 40: "\u5b89\u5fbd\u536b\u89c6",
}


HSMDTV_CANARY_IDS = (1, 21, 40)


async def parse_hsmdtv(session, host, timeout):
    """HSMDTV: no list API; canary-probe host liveness, then emit the fixed lineup."""
    result = {}

    # Probe a small canary set first; a dead host fails fast instead of
    # burning 40 requests. Alive hosts emit the full fixed lineup and let
    # the downstream quality check prune any dead id.
    canary = await asyncio.gather(
        *(_probe_hsm_id(session, host, n, timeout) for n in HSMDTV_CANARY_IDS)
    )
    if not any(canary):
        return result
    for n in HSMDTV_CHANNEL_NAMES:
        result[HSMDTV_CHANNEL_NAMES[n]] = f"{host}/newlive/live/hls/{n}/live.m3u8"
    return result


async def _probe_hsm_id(session, host, n, timeout):
    """Probe one hsmdtv id; return n if 200 + #EXTM3U else None."""
    url = f"{host}/newlive/live/hls/{n}/live.m3u8"
    try:
        async with session.get(url, timeout=_as_timeout(timeout)) as resp:
            if resp.status != 200:
                return None
            body = (await resp.read()).decode("utf-8", errors="ignore")
            if "#EXTM3U" in body:
                return n
    except Exception:
        pass
    return None


async def fetch_all_from_hotel():
    """
    主入口：获取所有节点，按类型解析，返回 channels 格式。
    返回: {category: {channel_name: [urls]}}
    """
    nodes = await fetch_nodes()
    if not nodes:
        return {}

    by_type = {}
    seen_hosts = set()
    for node in nodes:
        mt = node.get("matchType", "unknown")
        # Skip repeated hosts from the upstream list to avoid re-probing them.
        host = (node.get("link") or "").rstrip("/")
        if (mt, host) in seen_hosts:
            continue
        seen_hosts.add((mt, host))
        by_type.setdefault(mt, []).append(node)

    # 节点 API（频道列表 JSON/文本）响应体可能很大，用独立超时（默认 15s，
    # 可在 hotel_config 里加 "timeout" 调整）。不能复用 check_timeout
    # （3.5s，那是单流检测超时），否则大部分节点会解析超时
    # Short connect timeout: unreachable hosts fail in ~5s instead of the full 15s.
    timeout = aiohttp.ClientTimeout(total=config.hotel_config.get("timeout", 15), connect=5)
    # Hotel parsing is lightweight JSON/text fetching; a higher concurrency
    # than check_max_conn is safe and cuts wall time on large node lists.
    concurrency = int(config.hotel_config.get("concurrency", 50))
    connector = aiohttp.TCPConnector(limit=concurrency, ssl=False)
    channels = {}
    per_node_timeout = float(config.hotel_config.get("timeout", 15)) + 10
    sem = asyncio.Semaphore(concurrency)
    max_hosts = int(config.hotel_config.get("max_hosts", 0))

    async def _parse_host(mt, host, session):
        if mt == "txiptv":
            return await parse_txiptv(session, host, timeout)
        if mt == "zhgxtv":
            return await parse_zhgxtrv(session, host, timeout)
        if mt == "jsmpeg":
            return await parse_jsmpeg(session, host, timeout)
        if mt == "hsmdtv":
            return await parse_hsmdtv(session, host, timeout)
        return {}


    async def _select_best_hosts(all_nodes, session):
        """Probe host latency, keep the fastest max_hosts alive ones."""
        probe_timeout = aiohttp.ClientTimeout(total=3, connect=2)

        async def _probe(node):
            host = (node.get("link") or "").rstrip("/")
            t0 = time.monotonic()
            try:
                async with sem:
                    async with session.get(f"{host}/", timeout=probe_timeout):
                        pass
                return (time.monotonic() - t0, node)
            except Exception:
                return (None, node)

        results = await asyncio.gather(*(_probe(n) for n in all_nodes))
        alive = sorted((r for r in results if r[0] is not None), key=lambda x: x[0])
        selected = [n for _, n in alive[:max_hosts]]
        logger.info(f"[酒店源] 延迟探测 {len(all_nodes)} 个 host，存活 {len(alive)}，取最快 {len(selected)} 个")
        picked = {}
        for n in selected:
            mt = n.get("matchType", "unknown")
            picked.setdefault(mt, []).append(n)
        return picked


    async def _parse_node(mt, node, session):
        host = node.get("link", "").rstrip("/")
        try:
            async with sem:
                result = await asyncio.wait_for(
                    _parse_host(mt, host, session), timeout=per_node_timeout
                )
                for name, url in result.items():
                    channels.setdefault(mt, {}).setdefault(name, []).append(url)
        except Exception as e:
            logger.warning(f"[酒店源] {node.get('link')} 解析异常: {e}")

    async with aiohttp.ClientSession(connector=connector, timeout=timeout) as session:
        if max_hosts > 0:
            all_nodes = [node for nl in by_type.values() for node in nl]
            if len(all_nodes) > max_hosts:
                by_type = await _select_best_hosts(all_nodes, session)
        tasks = [_parse_node(mt, node, session) for mt, nl in by_type.items() for node in nl]
        await asyncio.gather(*tasks, return_exceptions=True)

    # 去重
    for cat in channels:
        for name in channels[cat]:
            channels[cat][name] = list(dict.fromkeys(channels[cat][name]))

    total = sum(len(urls) for ch in channels.values() for urls in ch.values())
    logger.info(f"[酒店源] 解析完成，共 {total} 个频道-URL 组合")
    return channels
