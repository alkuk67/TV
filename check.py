"""
IPTV 质量检测模块
双引擎：HTTP 快筛 + FFprobe 中度探测
"""
import re
import asyncio
import json
import logging
import os
import subprocess
import aiohttp
import config.config as config

_ffprobe_executor = None
_deep_probe_executor = None
_stop_flag = False
# 跨进程停止标志文件：web/server.py 写入，main.py/check.py 轮询，
# 使外部启动或子进程方式运行的 main.py 也能收到停止信号
STOP_FLAG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "stop.flag")


def set_stop_flag(value):
    global _stop_flag
    _stop_flag = value


def check_stop_flag():
    if _stop_flag:
        return True
    try:
        return os.path.exists(STOP_FLAG_FILE)
    except OSError:
        return False

logger = logging.getLogger(__name__)



def clear_stop_signal():
    global _stop_flag
    _stop_flag = False
    try:
        if os.path.exists(STOP_FLAG_FILE):
            os.remove(STOP_FLAG_FILE)
    except OSError:
        pass


def request_stop_via_signal():
    global _stop_flag
    _stop_flag = True
    try:
        with open(STOP_FLAG_FILE, "w", encoding="utf-8") as f:
            f.write("stop")
    except OSError:
        pass

def _strip_suffix(url: str) -> str:
    """去掉 $LR... 等播放器自定义后缀"""
    if "$" in url:
        return url.split("$", 1)[0]
    return url


def _get_domain(url: str) -> str:
    """从 URL 中提取基础域名（不含路径和端口）"""
    if not url:
        return ""
    stripped = url.split("$", 1)[0] if "$" in url else url
    m = re.match(r"(https?://(?:\[?[^\[/\]]+\]?)?)", stripped)
    return m.group(1) if m else ""


def _is_m3u8_url(url: str) -> bool:
    """判断是否为 m3u8 地址"""
    return ".m3u8" in _strip_suffix(url) or "index.m3u8" in _strip_suffix(url)


def _get_base_url(url: str) -> str:
    """获取 URL 的基地址（用于拼接相对路径的 TS 片段）"""
    stripped = _strip_suffix(url)
    idx = stripped.rfind("/")
    return stripped[:idx + 1] if idx != -1 else stripped


async def _http_fast_check(session, url, timeout):
    import time
    result = {'url': url, 'status': 'unknown', 'detail': '', 'layer': 'fast'}
    req_start = time.time()
    try:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=timeout)) as resp:
            if resp.status != 200:
                result['status'] = 'failed'
                result['detail'] = f'http_status={resp.status}'
                result['response_time_ms'] = int((time.time() - req_start) * 1000)
                return result
            # 限量读取响应体（64KB）：直播 TS 流是无限流，读完整 body 必然
            # 撞上总超时而被误判为 timeout 失效
            data = bytearray()
            while len(data) < 65536:
                chunk = await resp.content.read(65536 - len(data))
                if not chunk:
                    break
                data.extend(chunk)
            if len(data) < 10:
                result['status'] = 'empty'
                result['detail'] = 'playlist is empty'
                result['response_time_ms'] = int((time.time() - req_start) * 1000)
                return result
            if '.m3u8' in url or 'index.m3u8' in url:
                text = bytes(data).decode('utf-8', errors='replace')
                ts_lines = [l.strip() for l in text.splitlines()
                            if l.strip() and not l.strip().startswith('#')]
                result['ts_count'] = len(ts_lines)
                if not ts_lines:
                    result['status'] = 'ok_no_ts'
                    result['detail'] = 'live m3u8 (no ENDLIST)'
                else:
                    result['status'] = 'ok'
                    result['detail'] = f'playlist_ok ts_entries={len(ts_lines)}'
            else:
                result['status'] = 'ok'
                result['detail'] = 'http_ok'
            result['response_time_ms'] = int((time.time() - req_start) * 1000)
    except asyncio.TimeoutError:
        result['status'] = 'timeout'
        result['detail'] = f'timeout >{timeout}s'
        result['response_time_ms'] = 9999
    except Exception as e:
        result['status'] = 'error'
        result['detail'] = str(e)
        result['response_time_ms'] = 9999
    return result

async def _http_byte_check(session, url, timeout, min_bytes=100000):
    """For rtp/udp proxy URLs: download and check byte count.
    Downloads until min_bytes reached or timeout, then decides ok/failed."""
    import time
    result = {"url": url, "status": "unknown", "detail": "", "layer": "fast", "bytes": 0}
    req_start = time.time()
    try:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=timeout)) as resp:
            if resp.status != 200:
                result["status"] = "failed"
                result["detail"] = f"http_status={resp.status}"
                return result
            total_bytes = 0
            # Keep reading until we have enough bytes or timeout
            while total_bytes < min_bytes:
                remaining = timeout - (time.time() - req_start)
                if remaining <= 0:
                    break
                try:
                    data = await resp.content.read(min(65536, min_bytes - total_bytes + 1024))
                    if not data:
                        break
                    total_bytes += len(data)
                except Exception:
                    break
            result["bytes"] = total_bytes
            elapsed = time.time() - req_start
            if total_bytes >= min_bytes:
                result["status"] = "ok"
                kbps = total_bytes * 8 / elapsed / 1000 if elapsed > 0 else 0
                result["detail"] = f"bytes={total_bytes} kbps={kbps:.0f}"
            else:
                result["status"] = "failed"
                result["detail"] = f"bytes={total_bytes} < {min_bytes}"
            result["response_time_ms"] = int(elapsed * 1000)
    except asyncio.TimeoutError:
        result["status"] = "timeout"
        result["detail"] = f"timeout >{timeout}s"
        result["response_time_ms"] = 9999
    except Exception as e:
        result["status"] = "error"
        result["detail"] = str(e)[:60]
        result["response_time_ms"] = 9999
    return result

def _m3u8_speed_test_sync(url, timeout):
    """深度探测 m3u8 直播流：解析 playlist、下载 TS 分片计算速度和流质量

    使用原始 urllib 方案（同步阻塞），由 _m3u8_speed_test 放进线程池执行。
    """
    import time
    import urllib.request
    result = {
        "status": "ok",
        "detail": "",
        "target_duration": 0,
        "segment_count": 0,
        "is_live": False,
        "quality_score": 0,
        "speed_kbps": 0,
        "bandwidth_score": 0,
        "stream_quality": {"first_frame_delay_ms": 0, "jitter_ms": 0, "packet_loss": 0.0},
    }

    # 下载 m3u8 playlist
    m3u8_start = time.time()
    try:
        with urllib.request.urlopen(url, timeout=3) as resp:
            m3u8_content = resp.read().decode("utf-8", errors="replace")
        m3u8_elapsed = time.time() - m3u8_start
    except Exception:
        result["status"] = "failed"
        result["detail"] += " m3u8_fail"
        result["speed_kbps"] = 0
        return result

    # 解析 m3u8，收集 TS 分片 URL（最多 5 个）
    # Detect SCTE-35 ad insertion markers in m3u8 playlist
    ad_markers = sum(1 for line in m3u8_content.splitlines()
        if any(marker in line for marker in ("SCTE35", "CUE-OUT", "CUE-IN", "DATERANGE")))
    result["ad_detected"] = ad_markers > 0
    result["ad_marker_count"] = ad_markers
    if ad_markers:
        result["detail"] += f" ad_markers={ad_markers}"

    base_url = _get_base_url(url)
    ts_urls = []
    for line in m3u8_content.splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            if line.endswith(".ts"):
                ts_urls.append(line)
            elif "/" not in line and base_url and len(ts_urls) < 5:
                ts_urls.append(base_url + line)
        if len(ts_urls) >= 5:
            break

    if not ts_urls:
        result["status"] = "failed"
        result["detail"] += " no_ts"
        result["speed_kbps"] = 0
        return result

    # 下载前 3 个 TS 分片，综合计算速度
    total_bytes = len(m3u8_content.encode())
    total_time = m3u8_elapsed
    ts_downloaded = 0
    seg_ms = []

    for ts_rel in ts_urls[:3]:
        ts_url = ts_rel if ts_rel.startswith("http") else base_url + ts_rel
        try:
            ts_start = time.time()
            with urllib.request.urlopen(ts_url, timeout=3) as ts_resp:
                ts_data = ts_resp.read()
            ts_elapsed = time.time() - ts_start
            total_bytes += len(ts_data)
            total_time += ts_elapsed
            ts_downloaded += 1
            seg_ms.append(ts_elapsed * 1000)
        except Exception:
            continue

    # 计算综合速度
    if ts_downloaded == 0:
        result["status"] = "failed"
        result["detail"] += " all_ts_fail"
        result["speed_kbps"] = 0
    elif total_time > 0:
        speed_kbps = total_bytes * 8 / total_time / 1024
        result["speed_kbps"] = int(speed_kbps)
        result["detail"] += f" {ts_downloaded}ts_avg"
    else:
        result["speed_kbps"] = 0

    # 带宽评分
    speed = result["speed_kbps"]
    if speed >= 3000:
        result["bandwidth_score"] = 90
    elif speed >= 2000:
        result["bandwidth_score"] = 70
    elif speed >= 1000:
        result["bandwidth_score"] = 50
    else:
        result["bandwidth_score"] = 30

    # 流质量
    result["stream_quality"] = {
        "first_frame_delay_ms": int(m3u8_elapsed * 1000),
        "jitter_ms": 0,
        "packet_loss": 0.0,
    }
    if len(seg_ms) >= 2:
        mean = sum(seg_ms) / len(seg_ms)
        variance = sum((x - mean) ** 2 for x in seg_ms) / len(seg_ms)
        result["stream_quality"]["jitter_ms"] = int(variance ** 0.5)

    if result["speed_kbps"] > 0:
        result["detail"] += f" speed={result['speed_kbps']}kbps"
    else:
        result["detail"] += " speed=unknown"

    return result


async def _m3u8_speed_test(url, timeout):
    """异步包装：把同步 urllib 测速放进线程池执行，避免阻塞事件循环"""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _m3u8_speed_test_sync, url, timeout)


async def _download_speed_test(session, url, timeout):
    """非 m3u8 流（/udp/、/rtp/ 等 HTTP 代理流）的测速：边下边统计字节数。"""
    import time
    result = {
        "status": "skip",
        "detail": "",
        "speed_kbps": 0,
        "stream_quality": {"first_frame_delay_ms": 0, "jitter_ms": 0, "packet_loss": 0.0},
    }
    req_start = time.time()
    total_bytes = 0
    first_chunk_ms = 0
    chunk_intervals = []
    last_read_at = None
    try:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=timeout)) as resp:
            if resp.status != 200:
                result["detail"] = f"http_status={resp.status}"
                return result
            chunk_size = 65536
            max_bytes = getattr(config, "speed_test_max_bytes", 512 * 1024)
            while total_bytes < max_bytes and time.time() - req_start < timeout:
                read_start = time.time()
                data = await resp.content.read(chunk_size)
                if not data:
                    break
                read_at = time.time()
                if total_bytes == 0:
                    first_chunk_ms = int((read_start - req_start) * 1000)
                if last_read_at is not None:
                    chunk_intervals.append((read_at - last_read_at) * 1000)
                last_read_at = read_at
                total_bytes += len(data)
        elapsed = time.time() - req_start
        if total_bytes and elapsed > 0:
            result["speed_kbps"] = int(total_bytes * 8 / elapsed / 1024)
            result["status"] = "ok"
            result["detail"] = f"download={total_bytes}B {result['speed_kbps']}kbps"
            result["stream_quality"]["first_frame_delay_ms"] = first_chunk_ms
            if len(chunk_intervals) >= 2:
                mean = sum(chunk_intervals) / len(chunk_intervals)
                variance = sum((x - mean) ** 2 for x in chunk_intervals) / len(chunk_intervals)
                result["stream_quality"]["jitter_ms"] = int(variance ** 0.5)
        else:
            result["detail"] = "no_data"
    except asyncio.TimeoutError:
        if total_bytes:
            elapsed = time.time() - req_start
            result["speed_kbps"] = int(total_bytes * 8 / elapsed / 1024)
            result["status"] = "ok"
            result["detail"] = f"download={total_bytes}B {result['speed_kbps']}kbps"
            result["stream_quality"]["first_frame_delay_ms"] = first_chunk_ms
            if len(chunk_intervals) >= 2:
                mean = sum(chunk_intervals) / len(chunk_intervals)
                variance = sum((x - mean) ** 2 for x in chunk_intervals) / len(chunk_intervals)
                result["stream_quality"]["jitter_ms"] = int(variance ** 0.5)
        else:
            result["status"] = "timeout"
            result["detail"] = f"timeout >{timeout}s"
    except Exception as e:
        result["detail"] = str(e)[:60]

    # Streaming continuity analysis: detect bursty data delivery
    bursty_gap = getattr(config, "bursty_max_gap_ms", 0)
    if bursty_gap > 0 and result["status"] == "ok" and len(chunk_intervals) >= 3:
        avg_interval = sum(chunk_intervals) / len(chunk_intervals)
        max_interval = max(chunk_intervals)
        ratio = getattr(config, "bursty_ratio", 4.0)
        if max_interval > avg_interval * ratio and max_interval > bursty_gap:
            result["status"] = "ok_unstable"
            result["detail"] += f" bursty(max_gap={int(max_interval)}ms)"

    return result

async def _unified_probe(session, clean_url, ffprobe_timeout):
    """统一探测接口：一次 ffprobe 调用获取全部元数据 + m3u8 速度测试"""
    if not config.enable_ffprobe:
        return {"status": "ok", "detail": "ffprobe disabled", "layer": "fast"}
    # 1. ffprobe 获取流元数据和格式信息（一次调用）
    probe = await _ffprobe_thread_async(clean_url, ffprobe_timeout, config.ffprobe_max_streams)
    if probe["status"] != "ok":
        return {"status": probe["status"], "detail": probe["detail"], "layer": "ffprobe_fail"}

    result = {
        "status": "ok",
        "detail": probe["detail"],
        "layer": "ffprobe",
        "response_time_ms": probe.get("response_time_ms", 0),
        "ffprobe": probe,
    }

    # 2. 如果是 m3u8，额外获取 segment info 和速度测试
    if _is_m3u8_url(clean_url):
        deep = await _m3u8_speed_test(clean_url, config.deep_probe_timeout)
        # 合并 ffprobe 的格式信息到 deep 字典
        fmt_name = probe.get("format_name", "")
        fmt_target_dur = probe.get("target_duration", 0)
        fmt_duration = probe.get("duration", 0)
        if not deep.get("target_duration") and fmt_target_dur:
            deep["target_duration"] = int(fmt_target_dur)
        if not deep.get("duration") and fmt_duration:
            deep["duration"] = fmt_duration
        if not deep.get("is_live") and fmt_name and ("hls" in fmt_name or "mpegts" in fmt_name):
            deep["is_live"] = True
        if deep["target_duration"] > 0 and deep.get("duration", 0) > 0 and not deep.get("segment_count"):
            deep["segment_count"] = int(deep["duration"] / deep["target_duration"])
        if deep["target_duration"] > 0 and not deep.get("quality_score"):
            td = deep["target_duration"]
            deep["quality_score"] = 80 if 2 <= td <= 10 else (60 if td < 2 else 50)
        if deep.get("segment_count", 0) > 0:
            deep["detail"] = f"hls segments~{deep['segment_count']} target_dur={deep['target_duration']}s"

        if deep.get("status") == "ok":
            result["deep"] = deep
            if deep.get("stream_quality"):
                result["stream_quality"] = deep["stream_quality"]
            result["speed_kbps"] = deep.get("speed_kbps", 0)
    else:
        # 非 m3u8 流（/udp/、/rtp/ 代理流、普通 HTTP 流）用原来的下载测速方案
        deep = await _download_speed_test(
            session, clean_url, getattr(config, "speed_test_timeout", 5)
        )
        if deep.get("status") in ("ok", "ok_unstable"):
            result["deep"] = deep
            if deep.get("stream_quality"):
                result["stream_quality"] = deep["stream_quality"]
            result["speed_kbps"] = deep.get("speed_kbps", 0)

    return result

async def _ffprobe_thread_async(url, timeout, max_streams):
    """用线程池异步运行 ffprobe（避开 Windows ProcessPoolExecutor 问题）"""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _run_ffprobe, url, timeout, max_streams)



def _find_ffprobe():
    """查找 ffprobe 可执行文件"""
    import shutil
    if config.ffprobe_path:
        return config.ffprobe_path
    path = shutil.which("ffprobe")
    return path or "ffprobe"


def _run_ffprobe(url, timeout, max_streams):
    """运行 ffprobe 探流，返回元数据字典（一次调用同时获取流和格式信息）"""
    ffprobe = _find_ffprobe()
    sample_sec = getattr(config, "bitrate_sample_sec", 0)
    cmd = [
        ffprobe,
        "-v", "quiet",
        "-probesize", "1M",
        "-analyzeduration", "1500000",
        "-print_format", "json",
        "-show_streams",
        "-show_format",
    ]
    if sample_sec > 0:
        cmd += ["-show_entries", "packet=size,pts_time", "-read_intervals", f"%+{sample_sec}"]
    cmd += ["-rw_timeout", str(int(timeout * 1000000)), "-i", url]
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=False,
            timeout=timeout + 2,
        )
        if proc.returncode != 0:
            return {"status": "failed", "detail": f"ffprobe exit={proc.returncode}"}
        data = json.loads(proc.stdout.decode("utf-8", errors="replace"))
        streams = data.get("streams", [])
        video_stream = next((s for s in streams if s.get("codec_type") == "video"), None)
        audio_stream = next((s for s in streams if s.get("codec_type") == "audio"), None)
        fmt = data.get("format", {})

        result = {
            "status": "ok",
            "detail": "",
            "video_codec": video_stream.get("codec_name", "") if video_stream else "",
            "width": video_stream.get("width", 0) if video_stream else 0,
            "height": video_stream.get("height", 0) if video_stream else 0,
            "bitrate": 0,
            "format_name": fmt.get("format_name", ""),
            "target_duration": int(fmt.get("target_duration", 0)) if fmt.get("target_duration") else 0,
            "duration": float(fmt.get("duration", 0)) if fmt.get("duration") else 0,
        }
        if video_stream and video_stream.get("bit_rate"):
            result["bitrate"] = int(video_stream["bit_rate"])
        # Do NOT fall back to audio bitrate: audio (128-256kbps) is a different
        # scale from video (2-8Mbps). Let packet sampling calculate the real value.
        # packet 采样计算真实码率（TS 无 bit_rate 字段时 fallback）
        if result["bitrate"] == 0 and sample_sec > 0:
            pkts = data.get("packets", [])
            if len(pkts) >= 2:
                total_bytes = sum(int(p.get("size", 0) or 0) for p in pkts)
                try:
                    pts_vals = [float(p["pts_time"]) for p in pkts if p.get("pts_time") is not None]
                    if len(pts_vals) >= 2:
                        duration = max(pts_vals) - min(pts_vals)
                        if duration > 0:
                            result["bitrate"] = int(total_bytes * 8 / duration)
                except (ValueError, TypeError):
                    pass
        detail_parts = []
        if video_stream:
            res = f"{result['width']}x{result['height']}"
            detail_parts.append(f"v:{video_stream['codec_name']}@{res}")
        if audio_stream:
            detail_parts.append(f"a:{audio_stream['codec_name']}")
        if result["bitrate"] > 0:
            detail_parts.append(f"br:{result['bitrate']//1000}kbps")
        result["detail"] = " ".join(detail_parts)

        # 格式信息计算 quality_score / segment_count / is_live
        target_dur = result["target_duration"]
        if target_dur > 0 and fmt.get("format_name") and ("hls" in fmt["format_name"] or "mpegts" in fmt["format_name"]):
            result["is_live"] = True
            if result["duration"] > 0:
                result["segment_count"] = int(result["duration"] / target_dur)
            if 2 <= target_dur <= 10:
                result["quality_score"] = 80
            elif target_dur < 2:
                result["quality_score"] = 60
            else:
                result["quality_score"] = 50
            if result.get("segment_count", 0) > 0:
                result["detail"] = f"hls segments~{result['segment_count']} target_dur={target_dur}s"

        return result
    except subprocess.TimeoutExpired:
        return {"status": "timeout", "detail": f"ffprobe timeout >{timeout}s"}
    except Exception as e:
        return {"status": "error", "detail": str(e)}


async def _check_single(session, url, http_timeout, ffprobe_timeout, ffprobe_semaphore=None):
    """统一检测：HTTP 快筛 + 一次 ffprobe 拿全部信息"""
    clean_url = _strip_suffix(url)
    fast = await _http_fast_check(session, clean_url, http_timeout)
    if fast["status"] not in ("ok", "ok_no_ts"):
        return fast
    # 统一探测：一次 ffprobe + m3u8 速度测试
    probe = await _unified_probe(session, clean_url, ffprobe_timeout)
    if probe["status"] != "ok":
        fast.update(probe)
        return fast
    fast["ffprobe"] = probe["ffprobe"]
    fast["layer"] = probe["layer"]
    if "deep" in probe:
        fast["deep"] = probe["deep"]
    if "stream_quality" in probe:
        fast["stream_quality"] = probe["stream_quality"]
    if "speed_kbps" in probe:
        fast["speed_kbps"] = probe["speed_kbps"]
    return fast



async def _isp_filter_urls(channels):
    """
    ISP 运营商预过滤：在检测前剔除不符合 allowed_isps 的 URL。
    策略：IP 直连优先，域名才 DNS 解析。
    """
    import asyncio
    import ipaddress
    import socket
    from urllib.parse import urlparse
    from isp_checker import get_isp_checker

    allowed = getattr(config, "allowed_isps", [])
    if not allowed:
        return channels, 0

    checker = get_isp_checker()

    # 第一阶段：收集所有唯一 hostname
    host_to_urls: dict[str, set[str]] = {}
    for cat, ch_dict in channels.items():
        for ch_name, url_list in ch_dict.items():
            for url in url_list:
                clean = _strip_suffix(url)
                parsed = urlparse(clean)
                hostname = parsed.hostname or parsed.netloc.split("@")[-1].split(":")[0]
                if hostname:
                    host_to_urls.setdefault(hostname, set()).add(clean)

    # 第二阶段：获取 IP 地址（IP 直连优先，域名才 DNS 解析）
    ip_map: dict[str, str | None] = {}
    hostnames_to_resolve = []
    for hostname in host_to_urls:
        try:
            ipaddress.ip_address(hostname)
            ip_map[hostname] = hostname
        except ValueError:
            hostnames_to_resolve.append(hostname)

    if hostnames_to_resolve:
        ipv6_first = config.ip_version_priority == "ipv6"

        async def _resolve_one(hostname: str) -> list:
            try:
                addr_info = await asyncio.get_event_loop().run_in_executor(
                    None, socket.getaddrinfo, hostname, None, socket.AF_UNSPEC, socket.SOCK_STREAM
                )
                ips = [info[4][0] for info in addr_info]
                v4 = [ip for ip in ips if ipaddress.ip_address(ip).version == 4]
                v6 = [ip for ip in ips if ipaddress.ip_address(ip).version == 6]
                ordered = (v6 + v4) if ipv6_first else (v4 + v6)
                return ordered
            except Exception:
                return []

        tasks = [_resolve_one(h) for h in hostnames_to_resolve]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        for hostname, result in zip(hostnames_to_resolve, results):
            if isinstance(result, Exception):
                ip_map[hostname] = []
            else:
                ip_map[hostname] = result  # list of IPs


    # 第三阶段：批量 ISP 过滤（支持多 IP，任一匹配即保留）
    filtered_channels = {}
    removed = 0
    for cat, ch_dict in channels.items():
        filtered_ch = {}
        for ch_name, url_list in ch_dict.items():
            valid_urls = []
            for url in url_list:
                clean = _strip_suffix(url)
                parsed = urlparse(clean)
                hostname = parsed.hostname or parsed.netloc.split("@")[-1].split(":")[0]
                ip_list = ip_map.get(hostname) if hostname else None
                if ip_list:
                    if isinstance(ip_list, str):
                        ip_list = [ip_list]
                    # Keep URL only if at least one resolved IP is from an allowed ISP
                    if not any(checker.is_allowed(ip, allowed) for ip in ip_list):
                        removed += 1
                        continue
                valid_urls.append(url)
            if valid_urls:
                filtered_ch[ch_name] = valid_urls
        if filtered_ch:
            filtered_channels[cat] = filtered_ch

    if removed > 0:
        logger.info(f"[ISP] 预过滤移除 {removed} 个 URL，allowed={allowed}")
    return filtered_channels, removed


async def check_all(channels):
    """
    双引擎并发检测所有频道的所有 URL。

    参数:
        channels: {category: {channel_name: [url1, url2, ...]}}

    返回:
        (check_results, fail_domains)
    """
    semaphore = asyncio.Semaphore(config.check_max_conn)
    results = {}

    # FFprobe 并发限制（避免同时启动过多进程导致系统资源耗尽）
    async def _worker(cat, ch_name, url):
        nonlocal completed
        async with semaphore:
            # 排队任务拿到信号量后先检查停止标志，收到停止信号即跳过剩余检测
            if check_stop_flag():
                return None
            r = await _check_single(session, url, config.check_timeout, config.ffprobe_timeout)
            completed += 1
            if completed % 100 == 0 or completed == total:
                logger.info(f"[质量检测] 进度: {completed}/{total} ({completed*100//total}%)")
            return (cat, ch_name, url, r)

    connector = aiohttp.TCPConnector(limit=config.check_max_conn, ssl=False)
    timeout = aiohttp.ClientTimeout(total=config.check_timeout)
    all_tasks = []
    for cat, ch_dict in channels.items():
        for ch_name, url_list in ch_dict.items():
            for url in url_list:
                all_tasks.append(_worker(cat, ch_name, url))

    completed = 0
    total = len(all_tasks)
    fail_domains = {}

    logger.info(
        f"开始质量检测，共 {len(all_tasks)} 个 URL，并发数 {config.check_max_conn}，"
        f"HTTP 超时 {config.check_timeout}s"
        + (f"，FFprobe 启用，超时 {config.ffprobe_timeout}s" if config.enable_ffprobe else "")
    )

    async with aiohttp.ClientSession(connector=connector, timeout=timeout) as session:
        tasks = [asyncio.create_task(t) for t in all_tasks]
        raw_results = await asyncio.gather(*tasks, return_exceptions=True)

    if total > completed and check_stop_flag():
        logger.info(f"[质量检测] 收到停止信号，跳过剩余 {total - completed} 个 URL")

    for item in raw_results:
        if item is None or isinstance(item, Exception):
            continue
        cat, ch_name, url, r = item
        results.setdefault(cat, {}).setdefault(ch_name, {})[url] = r
        if r["status"] in ("ok", "ok_no_ts"):
            logger.debug(f"  [{ch_name}] OK  {url}  ({r.get('detail', '')})")
        else:
            logger.debug(f"  [{ch_name}] FAIL {url}  ({r.get('detail', '')})")
            domain = _get_domain(url)
            if domain:
                fail_domains.setdefault(domain, []).append(
                    {"url": url, "status": r["status"], "detail": r.get("detail", "")}
                )

    total = failed = timeout_cnt = error_cnt = 0
    for cat_ch in results.values():
        for ch_urls in cat_ch.values():
            for r in ch_urls.values():
                total += 1
                if r["status"] == "failed":
                    failed += 1
                elif r["status"] == "timeout":
                    timeout_cnt += 1
                elif r["status"] in ("error", "empty"):
                    error_cnt += 1
    ok_count = total - failed - timeout_cnt - error_cnt
    logger.info(
        f"质量检测完成: 总计={total} 通过={ok_count} 失败={failed} 超时={timeout_cnt} 错误={error_cnt}"
    )
    return results, fail_domains



def filter_dead_urls(channels, check_results, accept_layers=("ffprobe", "deep")):
    """
    根据检测结果过滤失效源，返回去重后的 channels。
    保留条件: status 为 "ok" 或 "ok_no_ts"，且 layer 为 "ffprobe" 或 "deep"（排除仅快筛无元数据的源）
    """
    filtered = {}
    for cat, ch_dict in channels.items():
        filtered[cat] = {}
        for ch_name, url_list in ch_dict.items():
            valid = []
            for url in url_list:
                r = check_results.get(cat, {}).get(ch_name, {}).get(url, {})
                # 接受 ffprobe 或 deep 层检测通过的源
                fp = r.get("ffprobe", {})
                min_resolution = str(getattr(config, "min_resolution", "0") or "0")
                min_bitrate = getattr(config, "min_bitrate", 0)
                resolution_ok = not min_resolution.isdigit() or int(min_resolution) <= 0 or fp.get("width", 0) >= int(min_resolution)
                bitrate_ok = not min_bitrate or fp.get("bitrate", 0) <= 0 or fp.get("bitrate", 0) >= min_bitrate
                if (
                    r.get("layer") in accept_layers
                    and r.get("status") in ("ok", "ok_no_ts")
                    and r.get("deep", {}).get("status") != "ok_unstable"
                    and resolution_ok
                    and bitrate_ok
                    and (not getattr(config, "min_speed_kbps", 0) or r.get("deep", {}).get("speed_kbps", 0) >= config.min_speed_kbps)
                ):
                    valid.append(url)
            if valid:
                filtered[cat][ch_name] = valid

    removed = 0
    for c in filtered:
        for n in filtered.get(c, {}):
            removed += len(channels[c].get(n, [])) - len(filtered[c][n])
    kept = sum(len(urls) for c in filtered for urls in filtered[c].values())
    logger.info(f"过滤后: 保留 {kept} 个有效源，移除 {removed} 个失效源")
    return filtered


