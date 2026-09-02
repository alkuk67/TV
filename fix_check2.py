import re

with open(r'D:\yuanl\Documents\GitHub\testtt\check.py', 'r', encoding='utf-8') as f:
    content = f.read()

# 修复1: 播放测试应该受配置控制
old_playback = '''            # 深度探测成功后才做播放测试
                pb = await _playback_test_async(clean_url, getattr(config, "playback_test_timeout", 5))
                fast["playback"] = pb  # 无论成功失败都记录'''
new_playback = '''            # 深度探测成功后才做播放测试（受配置控制）
                if getattr(config, "enable_playback_test", False):
                    pb = await _playback_test_async(clean_url, getattr(config, "playback_test_timeout", 5))
                    fast["playback"] = pb  # 无论成功失败都记录'''
content = content.replace(old_playback, new_playback)

# 修复2: 添加整体超时保护
old_completed = '    completed = 0'
new_completed = '''    completed = 0
    overall_start = time.time()
    OVERALL_TIMEOUT = 1800  # 30分钟总超时'''
content = content.replace(old_completed, new_completed)

# 修复3: 在 progress_worker 中添加超时检查
old_progress = '            if total_tasks > 0 and (completed % 100 == 0 or completed == total_tasks):'
new_progress = '''            # 超时检查
            if time.time() - overall_start > OVERALL_TIMEOUT:
                logger.error(f"[质量检测] 整体超时 {OVERALL_TIMEOUT}s，强制停止")
                set_stop_flag(True)
                break
            if total_tasks > 0 and (completed % 100 == 0 or completed == total_tasks):'''
content = content.replace(old_progress, new_progress)

with open(r'D:\yuanl\Documents\GitHub\testtt\check.py', 'w', encoding='utf-8') as f:
    f.write(content)

print('修复完成')
