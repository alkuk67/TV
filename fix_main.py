import re

content = open(r'D:\yuanl\Documents\GitHub\testtt\main.py', 'r', encoding='utf-8').read()

# 找到并替换整个 fetch_channels_async 函数
pattern = r'async def fetch_channels_async\(session, url, timeout\):.*?(?=\nasync def|\ndef is_ipv6)'
match = re.search(pattern, content, re.DOTALL)

if match:
    new_func = '''async def fetch_channels_async(session, url, timeout):
    channels = OrderedDict()
    logging.info(f'[抓取] 开始: {url}')
    try:
        async with session.get(url, timeout=timeout) as response:
            response.raise_for_status()
            text = await response.text()
            lines = text.split(chr(10))
            current_category = None
            is_m3u = any('#EXTINF' in line for line in lines[:15])
            source_type = 'm3u' if is_m3u else 'txt'
            logging.info(f'[抓取] {url} 获取成功，判断为{source_type}格式')

            if is_m3u:
                for line in lines:
                    line = line.strip()
                    if line.startswith('#EXTINF'):
                        match = re.search(r'group-title="(.*?)",(.*)', line)
                        if match:
                            current_category = match.group(1).strip()
                            channel_name = match.group(2).strip()
                            if current_category not in channels:
                                channels[current_category] = []
                    elif line and not line.startswith('#'):
                        channel_url = line.strip()
                        if current_category and channel_name:
                            channels[current_category].append((channel_name, channel_url))
            else:
                for line in lines:
                    line = line.strip()
                    if '#genre#' in line:
                        current_category = line.split(',')[0].strip()
                        channels[current_category] = []
                    elif current_category:
                        match = re.match(r'^(.*?),(.*?)$', line)
                        if match:
                            channel_name = match.group(1).strip()
                            channel_url = match.group(2).strip()
                            channels[current_category].append((channel_name, channel_url))
                        elif line:
                            channels[current_category].append((line, ''))
            if channels:
                total_urls = sum(len(urls) for urls in channels.values())
                categories = ', '.join(channels.keys())
                logging.info(f'[抓取] {url} 抓取成功，包含频道分类: {categories}')
                logging.info(f'[抓取] {url} 共获取 {total_urls} 个 URL')
            else:
                logging.warning(f'[抓取] {url} 未抓到任何频道数据')
    except asyncio.TimeoutError:
        logging.error(f'[抓取] {url} 超时 (>{timeout.total:.0f}s)')
    except aiohttp.ClientError as e:
        logging.error(f'[抓取] {url} 网络错误: {type(e).__name__}: {e}')
    except Exception as e:
        logging.error(f'[抓取] {url} 抓取失败。 Error: {type(e).__name__}: {e}')
    return channels
'''
    content = content[:match.start()] + new_func + content[match.end():]
    with open(r'D:\yuanl\Documents\GitHub\testtt\main.py', 'w', encoding='utf-8') as f:
        f.write(content)
    print('Function replaced successfully')
else:
    print('Function not found')
