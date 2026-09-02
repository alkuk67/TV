with open(r'D:\yuanl\Documents\GitHub\testtt\check.py', 'r', encoding='utf-8') as f:
    content = f.read()

# 1. 添加 _deep_probe_executor 变量
if '_deep_probe_executor = None' not in content:
    content = content.replace('_ffprobe_executor = None\n\nlogger', '_ffprobe_executor = None\n_deep_probe_executor = None\n\nlogger')

# 2. 添加 _get_deep_probe_executor 函数
if '_get_deep_probe_executor' not in content:
    old = '''async def _get_ffprobe_executor():
    global _ffprobe_executor
    if _ffprobe_executor is None:
        import concurrent.futures
        _ffprobe_executor = concurrent.futures.ProcessPoolExecutor(max_workers=min(config.check_max_conn, 16))
    return _ffprobe_executor'''

    new = '''async def _get_ffprobe_executor():
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
    return _deep_probe_executor'''

    content = content.replace(old, new)

# 3. 添加 _shutdown_deep_probe_executor 函数
if '_shutdown_deep_probe_executor' not in content:
    old = '''def _shutdown_ffprobe_executor():
    global _ffprobe_executor
    if _ffprobe_executor is not None:
        try:
            _ffprobe_executor.shutdown(wait=False)
        except Exception:
            pass
        _ffprobe_executor = None'''

    new = '''def _shutdown_ffprobe_executor():
    global _ffprobe_executor
    if _ffprobe_executor is not None:
        try:
            _ffprobe_executor.shutdown(wait=False)
        except Exception:
            pass
        _ffprobe_executor = None


def _shutdown_deep_probe_executor():
    """关闭深度探测进程池"""
    global _deep_probe_executor
    if _deep_probe_executor is not None:
        try:
            _deep_probe_executor.shutdown(wait=False)
        except Exception:
            pass
        _deep_probe_executor = None'''

    content = content.replace(old, new)

# 4. 修改 _deep_probe_async 使用独立 executor
content = content.replace(
    'executor = await _get_ffprobe_executor()\n    return await loop.run_in_executor(executor, _deep_probe_m3u8',
    'executor = await _get_deep_probe_executor()\n    return await loop.run_in_executor(executor, _deep_probe_m3u8)'
)

# 5. 播放测试受配置控制
content = content.replace(
    '                # 深度探测成功后才做播放测试\n                pb = await _playback_test_async(clean_url, getattr(config, "playback_test_timeout", 5))\n                fast["playback"] = pb  # 无论成功失败都记录',
    '                # 深度探测成功后才做播放测试（受配置控制）\n                if getattr(config, "enable_playback_test", False):\n                    pb = await _playback_test_async(clean_url, getattr(config, "playback_test_timeout", 5))\n                    fast["playback"] = pb'
)

with open(r'D:\yuanl\Documents\GitHub\testtt\check.py', 'w', encoding='utf-8') as f:
    f.write(content)

print('修复完成')
