content = open(r'D:\yuanl\Documents\GitHub\testtt\main.py', 'r', encoding='utf-8').read()

if 'async def fetch_channels_async' not in content:
    print('Adding async functions...')
    
    async_funcs = '''

async def fetch_channels_async(session, url, timeout):
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
                categories = ', '.join(channels.keys())
                logging.info(f'[抓取] {url} 抓取成功，包含频道分类: {categories}')
            else:
                logging.warning(f'[抓取] {url} 未抓到任何频道数据')
    except asyncio.TimeoutError:
        logging.error(f'[抓取] {url} 超时 (>{timeout.total:.0f}s)')
    except aiohttp.ClientError as e:
        logging.error(f'[抓取] {url} 网络错误: {type(e).__name__}: {e}')
    except Exception as e:
        logging.error(f'[抓取] {url} 抓取失败。 Error: {type(e).__name__}: {e}')
    return channels


async def filter_source_urls_async(template_file, alias_map=None):
    template_channels = parse_template(template_file)
    source_urls = config.source_urls
    fetch_timeout = aiohttp.ClientTimeout(total=getattr(config, 'fetch_timeout', 10))
    connector = aiohttp.TCPConnector(limit=5, ssl=False)
    
    all_channels = OrderedDict()
    async with aiohttp.ClientSession(connector=connector, timeout=fetch_timeout) as session:
        tasks = [fetch_channels_async(session, url, fetch_timeout) for url in source_urls]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        for url, fetched_channels in zip(source_urls, results):
            if isinstance(fetched_channels, Exception):
                logging.error(f'[抓取] {url} 异步抓取异常: {fetched_channels}')
                continue
            for category, channel_list in fetched_channels.items():
                if category in all_channels:
                    all_channels[category].extend(channel_list)
                else:
                    all_channels[category] = channel_list

    matched_channels = match_channels(template_channels, all_channels, alias_map)
    return matched_channels, template_channels

'''
    
    content = content.replace('def is_ipv6(url):', async_funcs + 'def is_ipv6(url):')
    content = content.replace(
        'channels, template_channels = filter_source_urls(template_file, alias_map)',
        'channels, template_channels = await filter_source_urls_async(template_file, alias_map)'
    )
    
    with open(r'D:\yuanl\Documents\GitHub\testtt\main.py', 'w', encoding='utf-8') as f:
        f.write(content)
    print('Done')
else:
    print('Already exists')
