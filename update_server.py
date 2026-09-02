import re

# 更新 server.py - 添加更多进度阶段
with open('web/server.py', 'r', encoding='utf-8') as f:
    content = f.read()

old_pattern = '''                    if "质量检测" in line:
                        _run_phase = "质量检测中..."
                        _run_progress = 30
                    elif "过滤" in line or "保留" in line:
                        _run_progress = 70
                        _run_phase = "过滤失效源..."
                    elif "已生成" in line:
                        _run_progress = 90
                        _run_phase = "生成输出文件..."""

new_pattern = '''                    if "别名映射" in line and "加载" in line:
                        _run_progress = 10
                        _run_phase = "加载别名映射..."
                    elif "EPG映射" in line and "加载" in line:
                        _run_progress = 15
                        _run_phase = "加载 EPG 节目单..."
                    elif "获取成功" in line or "抓取成功" in line:
                        _run_progress = 20
                        _run_phase = "抓取订阅源..."
                    elif "酒店源" in line and "开始" in line:
                        _run_progress = 25
                        _run_phase = "抓取酒店源..."
                    elif "酒店源" in line and "节点" in line:
                        _run_progress = 28
                        _run_phase = "解析酒店源节点..."
                    elif "酒店源" in line and "解析完成" in line:
                        _run_progress = 35
                        _run_phase = "合并频道数据..."
                    elif "质量检测" in line and "开始" in line:
                        _run_progress = 40
                        _run_phase = "开始质量检测..."
                    elif "开始质量检测" in line:
                        _run_progress = 45
                        _run_phase = "检测 URL 中..."
                    elif "通过=" in line:
                        _run_progress = 75
                        _run_phase = "质量检测完成..."
                    elif "过滤" in line and "移除" in line:
                        _run_progress = 80
                        _run_phase = "过滤失效源..."
                    elif "已生成" in line:
                        _run_progress = 90
                        _run_phase = "生成输出文件..."
                    elif "完成" in line and ("运营商" in line or "ISP" in line):
                        _run_progress = 95
                        _run_phase = "分类运营商...'

content = content.replace(old_pattern, new_pattern)

with open('web/server.py', 'w', encoding='utf-8') as f:
    f.write(content)

print('Updated server.py with more progress stages')
