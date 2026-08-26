import main
import config

check_results = {
    '央视频道': {
        'CCTV1': {
            'http://a.com/1.m3u8': {'layer': 'deep', 'ffprobe': {'bitrate': 256000, 'width': 1920, 'height': 1080}, 'deep': {'speed_kbps': 9000}},
            'http://b.com/2.m3u8': {'layer': 'deep', 'ffprobe': {'bitrate': 256000, 'width': 1920, 'height': 1080}, 'deep': {'speed_kbps': 3000}},
            'http://c.com/3.m3u8': {'layer': 'deep', 'ffprobe': {'bitrate': 0, 'width': 720, 'height': 576}, 'deep': {'speed_kbps': 12000}},
        }
    }
}

urls = ['http://a.com/1.m3u8', 'http://b.com/2.m3u8', 'http://c.com/3.m3u8']
for i, url in enumerate(urls, 1):
    key = main._url_sort_key(url, check_results, '央视频道')
    r = check_results['央视频道']['CCTV1'][url]
    info = '%dx%d, %dMbps' % (r['ffprobe']['width'], r['ffprobe']['height'], r['deep']['speed_kbps']//1000)
    print('Line %d: key=%s | %s' % (i, key, info))
