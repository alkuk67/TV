const fs = require('fs');
const lines = fs.readFileSync('D:\\yuanl\\Documents\\GitHub\\testtt\\check.py', 'utf8').split('\n');
let s = -1, e = -1;
for (let i = 0; i < lines.length; i++) {
  if (lines[i].includes('# FFprobe 并发限制')) s = i;
  if (s >= 0 && lines[i].trim() === 'total_tasks = len(all_tasks)') { e = i; break; }
}
console.log('s=' + s + ' e=' + e);
const before = lines.slice(0, s).join('\n');
const after = lines.slice(e + 1).join('\n');
const rep = [
  '    connector = aiohttp.TCPConnector(limit=config.check_max_conn, ssl=False)',
  '    timeout = aiohttp.ClientTimeout(total=config.check_timeout)',
  '',
  '    fail_domains = {}',
  '    total_tasks = 0',
  '    for cat, ch_dict in channels.items():',
  '        for ch_name, url_list in ch_dict.items():',
  '            total_tasks += len(url_list)',
  '',
  '    logger.info(',
  "        \"开始质量检测，共 %d 个 URL，并发数 %d，HTTP超时 %.1fs, FFprobe超时 %.1fs, 深度探测=%s, min_speed=%d\",",
  '        total_tasks, config.check_max_conn, config.check_timeout, config.ffprobe_timeout,',
  "        config.enable_deep_probe, getattr(config, 'min_speed_kbps', 0)",
  ')',
].join('\n');
let result = before + '\n' + rep + '\n' + after;
result = result.replace(
  'if completed % 100 == 0 or completed == total_tasks:',
  'if total_tasks > 0 and (completed % 100 == 0 or completed == total_tasks):'
);
fs.writeFileSync('D:\\yuanl\\Documents\\GitHub\\testtt\\check.py', result, 'utf8');
console.log('Done');