with open(r'D:\yuanl\Documents\GitHub\testtt\check.py', 'r', encoding='utf-8') as f:
    content = f.read()

# 添加 _deep_probe_executor 变量
if '_deep_probe_executor' not in content:
    content = content.replace('_ffprobe_executor = None\n\nlogger', '_ffprobe_executor = None\n_deep_probe_executor = None\n\nlogger')

# 添加 _get_deep_probe_executor 函数
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

# 修改 _deep_probe_async 使用独立 executor
content = content.replace(
    'executor = await _get_ffprobe_executor()\n    return await loop.run_in_executor(executor, _deep_probe_m3u8)',
    'executor = await _get_deep_probe_executor()\n    return await loop.run_in_executor(executor, _deep_probe_m3u8)'
)

with open(r'D:\yuanl\Documents\GitHub\testtt\check.py', 'w', encoding='utf-8') as f:
    f.write(content)

print('修复完成')
