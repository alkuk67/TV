"""
IPTV 质量检测模块
双引擎：HTTP 快筛 + FFprobe 中度探测
"""
import re
import asyncio
import json
import logging
import subprocess
import aiohttp
import config.config as config

_ffprobe_executor = None
_deep_probe_executor = None
_stop_flag = False


def set_stop_flag(value):
    global _stop_flag
    _stop_flag = value


def check_stop_flag():
    return _stop_flag

logger = logging.getLogger(__name__)



def clear_stop_signal():
    global _stop_flag
    _stop_flag = False


def request_stop_via_signal():
    global _stop_flag
    _stop_flag = True

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
            text = await resp.text()
            if not text or len(text) < 10:
                result['status'] = 'empty'
                result['detail'] = 'playlist is empty'
                result['response_time_ms'] = int((time.time() - req_start) * 1000)
                return result
            if '.m3u8' in url or 'index.m3u8' in url:
                ts_lines = [l.strip() for l in text.splitlines()
                            if l.strip() and not l.strip().startswith('#') and l.strip()]
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

async def _download_speed_test(session, url, timeout, segments=3):
    """Download a bounded amount of stream data to estimate throughput."""
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
    return result


async def _check_http_ffprobe(session, url, http_timeout, ffprobe_timeout):
    """HTTP 快筛 + FFprobe 元数据检测，供所有 HTTP 流统一复用。"""
    if any(x in url for x in ("/rtp/", "/udp/")):
        fast = await _http_byte_check(session, url, http_timeout, min_bytes=50000)
    else:
        fast = await _http_fast_check(session, url, http_timeout)
    if fast["status"] not in ("ok", "ok_no_ts"):
        fast["layer"] = "fast_fail"
        return fast
    if config.enable_ffprobe:
        probe = await _ffprobe_thread_async(url, ffprobe_timeout, config.ffprobe_max_streams)
        if probe["status"] != "ok":
            fast["ffprobe"] = probe
            fast["layer"] = "ffprobe_fail"
            return fast
        fast["ffprobe"] = probe
        if "speed_x" in probe:
            fast["ffprobe_speed"] = {"speed_x": probe["speed_x"]}
        if getattr(config, "enable_speed_test", False):
            fast["deep"] = await _download_speed_test(session, url, getattr(config, "speed_test_timeout", 5), getattr(config, "speed_test_segments", 3))
            if isinstance(fast.get("deep"), dict):
                fast["stream_quality"] = fast["deep"].get("stream_quality", {})
        fast["layer"] = "ffprobe"
        return fast
    fast["layer"] = "fast"
    return fast


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
    """运行 ffprobe 探流，返回元数据字典"""
    ffprobe = _find_ffprobe()
    sample_sec = getattr(config, "bitrate_sample_sec", 0)
    cmd = [
        ffprobe,
        "-v", "quiet",
        "-probesize", "1M",
        "-analyzeduration", "1500000",
        "-print_format", "json",
        "-show_streams",
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
        result = {
            "status": "ok",
            "detail": "",
            "video_codec": video_stream.get("codec_name", "") if video_stream else "",
            "width": video_stream.get("width", 0) if video_stream else 0,
            "height": video_stream.get("height", 0) if video_stream else 0,
            "bitrate": 0,
        }
        if video_stream and video_stream.get("bit_rate"):
            result["bitrate"] = int(video_stream["bit_rate"])
        elif audio_stream and audio_stream.get("bit_rate"):
            result["bitrate"] = int(audio_stream["bit_rate"])
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
        return result
    except subprocess.TimeoutExpired:
        return {"status": "timeout", "detail": f"ffprobe timeout >{timeout}s"}
    except Exception as e:
        return {"status": "error", "detail": str(e)}




def _deep_probe_m3u8(url, timeout):
    """深度探测 m3u8 直播流：检查分片稳定性、播放连续性和下载速度"""
    ffprobe = _find_ffprobe()
    result = {
        "status": "ok",
        "detail": "",
        "target_duration": 0,
        "segment_count": 0,
        "is_live": False,
        "quality_score": 0,
        "speed_kbps": 0,  # 新增：下载速度（kbps）
        "bandwidth_score": 0,  # 新增：带宽评分
    }
    
    # ===== 第一部分：ffprobe 基础探测 =====
    cmd = [
        ffprobe,
        "-v", "quiet",
        "-print_format", "json",
        "-show_format",
        "-show_entries", "format=format_name,duration",
        "-i", url,
    ]
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=False,
            timeout=timeout + 2,
        )
        if proc.returncode != 0:
            result["status"] = "failed"
            result["detail"] = f"ffprobe exit={proc.returncode}"
            return result
        
        data = json.loads(proc.stdout.decode("utf-8", errors="replace"))
        fmt = data.get("format", {})
        format_name = fmt.get("format_name", "")
        if "hls" not in format_name and "mpegts" not in format_name:
            result["status"] = "skip"
            result["detail"] = "not_hls_stream"
            return result
        
        cmd2 = [
            ffprobe,
            "-v", "quiet",
            "-print_format", "json",
            "-show_entries", "format=target_duration,duration",
            "-i", url,
        ]
        proc2 = subprocess.run(
            cmd2, capture_output=True, text=False,
            timeout=timeout,
        )
        if proc2.returncode == 0:
            data2 = json.loads(proc2.stdout.decode("utf-8", errors="replace"))
            target_dur = data2.get("format", {}).get("target_duration", 0)
            duration = data2.get("format", {}).get("duration", 0)
            result["target_duration"] = int(target_dur) if target_dur else 0
            result["duration"] = float(duration) if duration else 0
            result["is_live"] = True
            if target_dur > 0 and duration > 0:
                result["segment_count"] = int(duration / target_dur)
        
        if result["target_duration"] > 0:
            if 2 <= result["target_duration"] <= 10:
                result["quality_score"] = 80
            elif result["target_duration"] < 2:
                result["quality_score"] = 60
            else:
                result["quality_score"] = 50
        
        if result["segment_count"] > 0:
            result["detail"] = f"hls segments~{result['segment_count']} target_dur={result['target_duration']}s"
        else:
            result["detail"] = "hls_stream detected"
            
    except subprocess.TimeoutExpired:
        result["status"] = "timeout"
        result["detail"] = f"deep_probe timeout >{timeout}s"
    except Exception as e:
        result["status"] = "error"
        result["detail"] = str(e)
    
    # ===== 第二部分：速度测试（下载多个 TS 分片） =====
    if result["status"] in ("ok", "ok_no_ts") and _is_m3u8_url(url):
        import time
        import urllib.request
        
        # 下载 m3u8 playlist
        m3u8_start = time.time()
        try:
            with urllib.request.urlopen(url, timeout=3) as resp:
                m3u8_content = resp.read().decode("utf-8", errors="replace")
            m3u8_elapsed = time.time() - m3u8_start
        except:
            result["detail"] += " m3u8_fail"
            result["speed_kbps"] = 0
            return result
        
        # 解析 m3u8，收集 TS 分片 URL（最多 5 个）
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
            result["detail"] += " no_ts"
            result["speed_kbps"] = 0
            return result
        
        # 下载前 3 个 TS 分片，综合计算速度
        total_bytes = len(m3u8_content.encode())
        total_time = m3u8_elapsed
        ts_downloaded = 0
        
        for ts_rel in ts_urls[:3]:
            # 处理相对路径和绝对路径
            ts_url = ts_rel if ts_rel.startswith("http") else base_url + ts_rel
            
            try:
                ts_start = time.time()
                with urllib.request.urlopen(ts_url, timeout=3) as ts_resp:
                    ts_data = ts_resp.read()
                ts_elapsed = time.time() - ts_start
                
                total_bytes += len(ts_data)
                total_time += ts_elapsed
                ts_downloaded += 1
            except:
                continue
        
        # 计算综合速度
        if total_time > 0:
            speed_kbps = total_bytes * 8 / total_time / 1024
            result["speed_kbps"] = int(speed_kbps)
            result["detail"] += f" {ts_downloaded}ts_avg"
        else:
            result["speed_kbps"] = 0
        
        # 根据速度评分
        speed = result["speed_kbps"]
        if speed >= 3000:  # >= 3 Mbps
            result["bandwidth_score"] = 90
        elif speed >= 2000:  # >= 2 Mbps
            result["bandwidth_score"] = 70
        elif speed >= 1000:  # >= 1 Mbps
            result["bandwidth_score"] = 50
        else:
            result["bandwidth_score"] = 30
        
        # 更新 detail 信息
        if result["speed_kbps"] > 0:
            result["detail"] += f" speed={result['speed_kbps']}kbps"
        else:
            result["detail"] += " speed=unknown"
    
    return result


async def _get_ffprobe_executor():
    global _ffprobe_executor
    if _ffprobe_executor is None:
        import concurrent.futures
        _ffprobe_executor = concurrent.futures.ProcessPoolExecutor(max_workers=min(config.check_max_conn, 16))
    return _ffprobe_executor


async def _get_deep_probe_executor():
    """获取深度探测专用进程池（与 ffprobe 分离，避免死锁）"""
    global _deep_probe_executor
    if _deep_probe_executor is None:
        import concurrent.futures
        _deep_probe_executor = concurrent.futures.ProcessPoolExecutor(max_workers=min(config.check_max_conn, 8))
    return _deep_probe_executor


def _shutdown_ffprobe_executor():
    global _ffprobe_executor
    if _ffprobe_executor is not None:
        try:
            _ffprobe_executor.shutdown(wait=False)
        except Exception:
            pass
        _ffprobe_executor = None


async def _ffprobe_async(url, timeout, max_streams):
    """在独立进程池中异步运行 ffprobe"""
    loop = asyncio.get_event_loop()
    executor = await _get_ffprobe_executor()
    return await loop.run_in_executor(executor, _run_ffprobe, url, timeout, max_streams)


async def _deep_probe_async(url, timeout):
    """异步运行深度探测"""
    loop = asyncio.get_event_loop()
    executor = await _get_deep_probe_executor()
    return await loop.run_in_executor(executor, _deep_probe_m3u8, url, timeout)


async def _check_single(session, url, http_timeout, ffprobe_timeout, ffprobe_semaphore=None):
    """单 URL 检测：HTTP 快筛 + FFprobe 基础探测 + 中度探测"""
    clean_url = _strip_suffix(url)
    # 第一层：HTTP 快筛 + FFprobe 元数据检测
    fast = await _check_http_ffprobe(session, clean_url, http_timeout, ffprobe_timeout)
    if fast["status"] not in ("ok", "ok_no_ts"):
        return fast
    # 可选深度探测：只补测速信息，不重复 FFprobe
    if getattr(config, "enable_moderate_probe", False) and _is_m3u8_url(clean_url):
        deep = await _deep_probe_async(clean_url, config.deep_probe_timeout)
        fast["deep"] = deep
        if deep["status"] == "ok":
            fast["layer"] = "deep"
        else:
            fast["layer"] = "ffprobe"
        # 中度探测复测：默认仅 1 次，避免重复拉流
        if fast.get("layer") == "deep" and "deep" in fast:
            import statistics as _stat
            speeds = []
            for _ in range(min(getattr(config, "stability_test_count", 1), 5)):
                import time as _time
                _time.sleep(getattr(config, "stability_test_interval", 1.0))
                deeper = await _deep_probe_async(clean_url, getattr(config, "deep_probe_timeout", 6.5))
                if deeper.get("status") == "ok" and deeper.get("speed_kbps", 0) > 0:
                    speeds.append(deeper["speed_kbps"])
            if speeds:
                median_speed = _stat.median(speeds)
                fast["deep"]["speed_kbps"] = median_speed
                fast["deep"]["stability"] = "stable" if len(speeds) >= 2 else "single"
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
                ip_str = ip_map.get(hostname) if hostname else None
                if ip_str and not checker.is_allowed(ip_str, allowed):
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
        + (f"，中度探测启用，超时 {config.deep_probe_timeout}s" if getattr(config, "enable_moderate_probe", False) else "")
    )

    async with aiohttp.ClientSession(connector=connector, timeout=timeout) as session:
        tasks = [asyncio.create_task(t) for t in all_tasks]
        raw_results = await asyncio.gather(*tasks, return_exceptions=True)

    for item in raw_results:
        if isinstance(item, Exception):
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


