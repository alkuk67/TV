"""
组播源抓取模块 — 从 API 获取频道列表，按 alive 状态过滤
"""
import json
import logging
import aiohttp
import config.config as config

logger = logging.getLogger(__name__)
_logger_fmt = logging.Formatter(
    "%(asctime)s - %(levelname)s - %(name)s:%(filename)s:%(lineno)d - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
for _h in logger.handlers:
    _h.setFormatter(_logger_fmt)


async def _fetch_json(session, url, timeout):
    """通用 JSON 请求"""
    try:
        async with session.get(url, timeout=timeout) as resp:
            resp.raise_for_status()
            text = await resp.text()
            return json.loads(text)
    except Exception:
        return None


async def fetch_multicast_channels():
    """
    从组播源 API 获取频道列表。
    每条数据格式：
        {"channel_name": "...", "stream_url": "...", [alive: "..."]}
    过滤规则：alive == "dead" 的直接丢弃，其他状态（包括无 alive 字段）全部保留。
    返回：{"multicast": {channel_name: [stream_url, ...]}}
    """
    mc_api = config.multicast_config.get("multicast_api")
    if not mc_api:
        logger.warning("[组播源] multicast_api 未配置，跳过")
        return {}

    timeout = aiohttp.ClientTimeout(total=15)
    connector = aiohttp.TCPConnector(limit=5, ssl=False)

    async with aiohttp.ClientSession(connector=connector, timeout=timeout) as session:
        data = await _fetch_json(session, mc_api, 15)

    if not data:
        logger.warning("[组播源] 未获取到数据")
        return {}

    # API 可能直接返回列表，也可能嵌套在某个 key 下
    if isinstance(data, list):
        items = data
    elif isinstance(data, dict):
        for key in ("results", "data", "channels", "list"):
            if key in data and isinstance(data[key], list):
                items = data[key]
                break
        else:
            logger.warning("[组播源] 未知响应结构: %s", list(data.keys())[:5])
            return {}
    else:
        logger.warning("[组播源] 响应类型非 list/dict，跳过")
        return {}

    filter_location = config.multicast_config.get("enabled_location", "")
    filter_operator = config.multicast_config.get("enabled_operator", "")

    total = len(items)
    alive_count = 0
    dead_count = 0
    no_alive_count = 0
    with_alive_passed = 0
    loc_skip = 0
    op_skip = 0
    channels = {}

    for item in items:
        channel_name = item.get("channel_name", "").strip()
        stream_url = item.get("stream_url", "").strip()
        if not channel_name or not stream_url:
            continue

        alive = item.get("alive")
        if alive == "dead":
            dead_count += 1
            continue
        if alive is None:
            no_alive_count += 1
        else:
            with_alive_passed += 1

        # 省份/运营商过滤
        if filter_location and item.get("location", "") != filter_location:
            loc_skip += 1
            continue
        if filter_operator and item.get("operator", "") != filter_operator:
            op_skip += 1
            continue

        channels.setdefault(channel_name, []).append(stream_url)
        alive_count += 1

    logger.info(
        "[组播源] 共 %d 条，保留 %d 条（dead=%d，省份过滤=%d，运营商过滤=%d）",
        total, alive_count, dead_count, loc_skip, op_skip,
    )
    return {"multicast": channels}
