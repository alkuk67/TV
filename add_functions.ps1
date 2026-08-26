$content = Get-Content "D:\yuanl\Documents\GitHub\testtt2\main.py" -Raw -Encoding UTF8

$newFuncs = @"

def _get_domain(url: str) -> str:
    if not url:
        return ''
    stripped = url.split('$', 1)[0] if '$' in url else url
    m = re.match(r'(https?://(?:\\[?[^[\\]/\\]]+\\]?)?)', stripped)
    return m.group(1) if m else ''


def _classify_by_isp(channels: dict) -> dict:
    checker = isp_checker.get_isp_checker()
    isp_channels = {}
    cdn_count = 0
    for category, ch_dict in channels.items():
        for ch_name, url_list in ch_dict.items():
            for url in url_list:
                domain = _get_domain(url)
                if not domain:
                    continue
                ip_match = re.search(r'\\[?([\\d.]+)\\]?', domain)
                if not ip_match:
                    import socket
                    try:
                        ip_str = socket.gethostbyname(domain.replace('http://', '').replace('https://', ''))
                    except:
                        ip_str = None
                else:
                    ip_str = ip_match.group(1)
                if ip_str:
                    isp = checker.get_isp(ip_str)
                    isp_name = isp if isp else 'CDN'
                else:
                    isp_name = 'CDN'
                isp_channels.setdefault(isp_name, {}).setdefault(category, {}).setdefault(ch_name, []).append(url)
                if isp_name == 'CDN':
                    cdn_count += 1
    logging.info(f'[ISP分类] 完成，识别到 {len(isp_channels)} 个运营商组，CDN源 {cdn_count} 个')
    return isp_channels


def _write_channel_file(filepath_txt, filepath_m3u, channels, template_channels, epg_id_map, check_results):
    written_urls = set()
    epg_id_map = epg_id_map or {}
    current_date = datetime.now().strftime('%Y-%m-%d')
    for group in config.announcements:
        for announcement in group['entries']:
            name = announcement.get('name')
            if name is None or name == '__TIME__':
                name = current_date
            elif isinstance(name, str) and '__TIME__' in name:
                name = name.replace('__TIME__', current_date)
            announcement['name'] = name
    with open(filepath_m3u, 'w', encoding='utf-8') as f_m3u:
        epg_attr = ','.join(chr(34)+epg_url+chr(34) for epg_url in config.epg_urls)
        f_m3u.write(f'#EXTM3U x-tvg-url={epg_attr}\n')
        with open(filepath_txt, 'w', encoding='utf-8') as f_txt:
            for group in config.announcements:
                f_txt.write(f"{group['channel']},#genre#\n")
                for announcement in group['entries']:
                    f_m3u.write(f"""#EXTINF:-1 tvg-id="{announcement['name']}" tvg-name="{announcement['name']}" tvg-logo="{announcement['logo']}" group-title="{group['channel']}",{announcement['name']}\n""")
                    f_m3u.write(f"{announcement['url']}\n")
                    f_txt.write(f"{announcement['name']},{announcement['url']}\n")
            for category, channel_list in template_channels.items():
                f_txt.write(f"{category},#genre#\n")
                if category in channels:
                    for channel_name in channel_list:
                        if channel_name in channels[category]:
                            sorted_urls = sorted(channels[category][channel_name], key=lambda url: _url_sort_key(url, check_results, category))
                            filtered_urls = []
                            for url in sorted_urls:
                                if url and url not in written_urls and not any(blacklist in url for blacklist in config.url_blacklist):
                                    filtered_urls.append(url)
                                    written_urls.add(url)
                            if config.max_lines_per_channel > 0 and len(filtered_urls) > config.max_lines_per_channel:
                                old_count = len(filtered_urls)
                                filtered_urls = filtered_urls[:config.max_lines_per_channel]
                                logging.info('[频道] %s 线路从 %d 截断至 %d', channel_name, old_count, config.max_lines_per_channel)
                            total_urls = len(filtered_urls)
                            for index, url in enumerate(filtered_urls, start=1):
                                if is_ipv6(url):
                                    extra = _get_meta_suffix(url, check_results)
                                    url_suffix = f'$LR—IPV6{extra}' if total_urls == 1 else f'$LR—IPV6【线路{index}】{extra}'
                                else:
                                    extra = _get_meta_suffix(url, check_results)
                                    url_suffix = f'$LR—IPV4{extra}' if total_urls == 1 else f'$LR—IPV4【线路{index}】{extra}'
                                if '$' in url:
                                    base_url = url.split('$', 1)[0]
                                else:
                                    base_url = url
                                new_url = f"{base_url}{url_suffix}"
                                tvg_id = epg_id_map.get(channel_name, channel_name)
                                f_m3u.write(f'#EXTINF:-1 tvg-id="{tvg_id}" tvg-name="{channel_name}" tvg-logo="https://gcore.jsdelivr.net/gh/yuanzl77/TVlogo@master/png/{channel_name}.png" group-title="{category}",{channel_name}\n')
                                f_m3u.write(new_url + '\n')
                                f_txt.write(f'{channel_name},{new_url}\n')
            f_txt.write('\n')


def _output_isp_files(channels, template_channels, epg_id_map, check_results):
    isp_channels = _classify_by_isp(channels)
    isp_abbr = {'China Mobile': 'cmcc', 'China Telecom': 'ct', 'China Unicom': 'cu',
                'China Education & Research Network': 'cernet', 'China Science & Technology Network': 'cstnet',
                'Dr.Peng': 'dp', 'CDN': 'cdn'}
    updateChannelUrlsM3U(channels, template_channels, epg_id_map, check_results)
    logging.info('[输出] 已生成 live.txt / live.m3u')
    for isp_name, isp_data in isp_channels.items():
        abbr = isp_abbr.get(isp_name, isp_name.lower())
        prefix = f'{abbr}_live'
        _write_channel_file(f'{prefix}.txt', f'{prefix}.m3u', isp_data, template_channels, epg_id_map, check_results)
        logging.info('[输出] 已生成 %s.txt / %s.m3u (%s)', prefix, prefix, isp_name)

"@

$oldPattern = 'if __name__ == "__main__":'
if ($content -match [regex]::Escape($oldPattern)) {
    $content = $content.Replace($oldPattern, $newFuncs + $oldPattern)
    Set-Content "D:\yuanl\Documents\GitHub\testtt2\main.py" -Value $content -Encoding UTF8
    Write-Host "Done"
} else {
    Write-Host "Pattern not found"
}
