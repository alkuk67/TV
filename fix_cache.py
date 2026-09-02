import sys
sys.path.insert(0, r'D:\yuanl\Documents\GitHub\testtt')

with open(r'D:\yuanl\Documents\GitHub\testtt\main.py', 'r', encoding='utf-8') as f:
    content = f.read()

if 'import cache_manager' not in content:
    content = content.replace('import isp_checker\n', 'import isp_checker\nimport cache_manager\n')
    
    old_code = '''        channels = quality_checker.filter_dead_urls(channels, check_results, accept_layers=("fast", "ffprobe"))'''
    new_code = '''        # 写入检测结果到缓存
        try:
            cache_manager.batch_upsert(check_results)
            logging.info("[缓存] 检测结果已写入缓存")
        except Exception as e:
            logging.warning(f"[缓存] 写入失败: {e}")
        
        channels = quality_checker.filter_dead_urls(channels, check_results, accept_layers=("fast", "ffprobe"))'''
    
    content = content.replace(old_code, new_code)
    
    with open(r'D:\yuanl\Documents\GitHub\testtt\main.py', 'w', encoding='utf-8') as f:
        f.write(content)
    print('main.py已更新，添加了缓存写入功能')
else:
    print('main.py已包含cache_manager导入')
