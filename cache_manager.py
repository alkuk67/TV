"""
缓存管理器：跨 GitHub Actions Run 持久化源数据和检测结果。

持久化两份数据：
  - cache/sources.json   ：订阅源 + 酒店节点 + EPG映射（TTL 24h）
  - cache/results.db     ：SQLite，每条结果带 checked_at 时间戳

Run 1（首次 / sources 过期）：完整抓取 + 全量检测 + 写入缓存
Run 2（sources 未过期）：跳过网络请求，仅对 >12h 的旧结果增量重测
"""
import json
import os
import time
import sqlite3
import logging

logger = logging.getLogger(__name__)

CACHE_DIR = "cache"
SOURCES_FILE = os.path.join(CACHE_DIR, "sources.json")
DB_FILE = os.path.join(CACHE_DIR, "results.db")

SOURCE_CACHE_TTL = 24 * 3600      # 源数据 TTL：24 小时
RESULT_STALE_THRESHOLD = 12 * 3600  # 检测结果超过 12h 视为过期，需要重测
HOTEL_CACHE_TTL = 12 * 3600      # 酒店节点 TTL：12 小时


def ensure_cache_dir():
    os.makedirs(CACHE_DIR, exist_ok=True)


# ── sources.json ────────────────────────────────────────────────────────────

def save_sources(sources, epg_map):
    """写入源数据和 EPG 映射，附带时间戳。"""
    ensure_cache_dir()
    data = {
        "fetched_at": time.time(),
        "sources": sources,       # {url: {category: [(name, url), ...]}}
        "epg": epg_map,           # {channel_name: epg_id}
    }
    with open(SOURCES_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    logger.info(f"[缓存] sources.json 已写入，含 {len(epg_map)} 条 EPG 映射")


def load_sources():
    """读取缓存的源数据，若不存在或已过期返回 None。"""
    if not os.path.exists(SOURCES_FILE):
        return None
    try:
        with open(SOURCES_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        age = time.time() - data.get("fetched_at", 0)
        if age > SOURCE_CACHE_TTL:
            logger.info(f"[缓存] sources.json 已过期（{age / 3600:.1f}h），将重新抓取")
            return None
        logger.info(f"[缓存] 命中 sources.json（{age / 3600:.1f}h 前）")
        return data
    except Exception as e:
        logger.warning(f"[缓存] 读取 sources.json 失败: {e}")
        return None



# ── hotel.json ──────────────────────────────────────────────────────────────

HOTEL_FILE = os.path.join(CACHE_DIR, "hotel.json")


def save_hotel(hotel_data):
    """写入酒店节点数据，附带时间戳。
    hotel_data: {category: {channel_name: [urls]}}
    """
    ensure_cache_dir()
    data = {"fetched_at": time.time(), "nodes": hotel_data}
    with open(HOTEL_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    total = sum(len(urls) for ch in hotel_data.values() for urls in ch.values())
    logger.info(f"[缓存] hotel.json 已写入，共 {total} 个酒店 URL")


def load_hotel():
    """读取缓存的酒店节点，若不存在或已过期返回 None。"""
    if not os.path.exists(HOTEL_FILE):
        return None
    try:
        with open(HOTEL_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        age = time.time() - data.get("fetched_at", 0)
        if age > HOTEL_CACHE_TTL:
            logger.info(f"[缓存] hotel.json 已过期（{age / 3600:.1f}h），将重新抓取")
            return None
        logger.info(f"[缓存] 命中 hotel.json（{age / 3600:.1f}h 前）")
        return data.get("nodes", {})
    except Exception as e:
        logger.warning(f"[缓存] 读取 hotel.json 失败: {e}")
        return None


# ── results.db ──────────────────────────────────────────────────────────────

def _get_conn():
    ensure_cache_dir()
    conn = sqlite3.connect(DB_FILE)
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db():
    """建表（若不存在）。"""
    conn = _get_conn()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS results (
            category   TEXT,
            channel    TEXT,
            url        TEXT,
            check_data TEXT,
            checked_at REAL,
            PRIMARY KEY (category, channel, url)
        )
    """)
    conn.commit()
    conn.close()


def get_stale_keys(threshold_seconds=RESULT_STALE_THRESHOLD):
    """返回需要重测的 (category, channel, url) 列表——checked_at 超过阈值的记录。"""
    conn = _get_conn()
    cutoff = time.time() - threshold_seconds
    rows = conn.execute(
        "SELECT category, channel, url FROM results WHERE checked_at < ?",
        (cutoff,),
    ).fetchall()
    conn.close()
    return rows


def batch_upsert(results_by_cat_ch):
    """
    批量写入检测结果。
    results_by_cat_ch: {category: {channel: {url: check_data}}}
    """
    conn = _get_conn()
    now = time.time()
    for cat, ch_dict in results_by_cat_ch.items():
        for ch, url_dict in ch_dict.items():
            for url, data in url_dict.items():
                conn.execute(
                    """
                    INSERT INTO results (category, channel, url, check_data, checked_at)
                    VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(category, channel, url)
                    DO UPDATE SET check_data = excluded.check_data, checked_at = excluded.checked_at
                    """,
                    (cat, ch, url, json.dumps(data, ensure_ascii=False), now),
                )
    conn.commit()
    conn.close()
    total = sum(len(urls) for ch in results_by_cat_ch.values() for urls in ch.values())
    logger.info(f"[缓存] results.db 写入 {total} 条结果")


def get_cached_results():
    """读取 DB 中所有结果，返回与 check_all 相同格式的结构：{cat: {ch: {url: data}}}."""
    conn = _get_conn()
    rows = conn.execute(
        "SELECT category, channel, url, check_data FROM results"
    ).fetchall()
    conn.close()
    results = {}
    for cat, ch, url, data_str in rows:
        try:
            data = json.loads(data_str)
        except Exception:
            continue
        results.setdefault(cat, {}).setdefault(ch, {})[url] = data
    return results


def restore_channels_from_db():
    """从 DB 读取所有缓存结果，返回 {cat: {ch: [url]}}。
    仅保留 status 为 ok/ok_no_ts 且 layer 为 ffprobe/deep 的记录（即有效源）。
    """
    cached = get_cached_results()
    valid = {}
    for cat, ch_dict in cached.items():
        for ch, url_dict in ch_dict.items():
            urls = [url for url, data in url_dict.items()
                    if data.get('status') in ('ok', 'ok_no_ts')
                    and data.get('layer') in ('ffprobe', 'deep')]
            if urls:
                valid.setdefault(cat, {})[ch] = urls
    return valid

