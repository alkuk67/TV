import json, re

# 从 live.m3u 提取频道数据
data = []
current_group = None
name = None

with open('output/live.m3u', 'r', encoding='utf-8') as f:
    for line in f:
        line = line.strip()
        if line.startswith('#EXTINF'):
            g = re.search(r'group-title="([^"]+)"', line)
            if g:
                current_group = g.group(1)
                if not any(d['name'] == current_group for d in data):
                    data.append({'name': current_group, 'channels': []})
            ch = re.search(r',([^,"]+)$', line)
            if ch:
                name = ch.group(1).strip()
        elif line.startswith('#') or not line:
            continue
        else:
            url = line
            if current_group and name:
                res = '1920x1080'
                speed = ''
                if '【' in url:
                    meta = re.search(r'【([^】]+)】', url)
                    if meta:
                        meta_str = meta.group(1)
                        res_match = re.search(r'(\d+x\d+)', meta_str)
                        if res_match:
                            res = res_match.group(1)
                        speed_match = re.search(r'(\d+(?:\.\d+)?(?:Mbps|kbps))', meta_str)
                        if speed_match:
                            speed = speed_match.group(1)
                for d in data:
                    if d['name'] == current_group:
                        d['channels'].append({'name': name, 'resolution': res, 'speed': speed})
                        break

print(f'Loaded {len(data)} groups')
total = sum(len(g['channels']) for g in data)
print(f'Total channels: {total}')

js_content = 'const CHANNEL_DATA = ' + json.dumps(data, ensure_ascii=False) + ';\n'
with open('web/channel_data.js', 'w', encoding='utf-8') as f:
    f.write(js_content)
print(f'Written {len(js_content)} bytes')
