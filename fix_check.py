import re

with open(r'D:\yuanl\Documents\GitHub\testtt\check.py', 'r', encoding='utf-8') as f:
    content = f.read()

# 1. 添加 _deep_probe_executor 变量
content = content.replace('_ffprobe_executor = None\n_stop_flag = False', '_ffprobe_executor = None\n_deep_probe_executor = None\n_stop_flag = False')

# 2. 添加 _get_deep_probe_executor 函数
old_func = '''async def _get_ffprobe_executor():
    global _ffprobe_executor
    if _ffprobe_executor is None:
        import concurrent.futures
        _ffprobe_executor = concurrent.futures.ProcessPoolExecutor(max_workers=min(config.check_max_conn, 16))
    return _ffprobe_executor'''

new_func = '''async def _get_ffprobe_executor():
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
        # 深度探测也有 subprocess.run 调用 ffprobe，需要独立池
        _deep_probe_executor = concurrent.futures.ProcessPoolExecutor(max_workers=min(config.check_max_conn, 8))
    return _deep_probe_executor'''

content = content.replace(old_func, new_func)

# 3. 添加 _shutdown_deep_probe_executor 函数
old_shutdown = '''def _shutdown_ffprobe_executor():
    global _ffprobe_executor
    if _ffprobe_executor is not None:
        try:
            _ffprobe_executor.shutdown(wait=False)
        except Exception:
            pass
        _ffprobe_executor = None'''

new_shutdown = '''def _shutdown_ffprobe_executor():
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

content = content.replace(old_shutdown, new_shutdown)

# 4. 修改 _deep_probe_async 使用独立 executor
content = content.replace(
    'executor = await _get_ffprobe_executor()\n    return await loop.run_in_executor(executor, _deep_probe_m3u8',
    'executor = await _get_deep_probe_executor()\n    return await loop.run_in_executor(executor, _deep_probe_m3u8)'
)

# 5. 在 check_all 函数末尾添加清理
content = content.replace(
    '    # 清理 executor\n    _shutdown_ffprobe_executor()',
    '    # 清理 executor\n    _shutdown_ffprobe_executor()\n    _shutdown_deep_probe_executor()'
)

with open(r'D:\yuanl\Documents\GitHub\testtt\check.py', 'w', encoding='utf-8') as f:
    f.write(content)

print('修改完成')
