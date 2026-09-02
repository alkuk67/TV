import asyncio, sys, aiohttp
sys.path.insert(0, '.')
import fetch_hotel
class MC: pass
mc = MC()
mc.hotel_config = {"hotel_api": "https://iptvs-speed.humorously.cn", "enabled": True, "allowed_orgs": []}
mc.check_timeout = 5
mc.check_max_conn = 20
import config.config as cfg
cfg.__dict__.update(vars(mc))
async def run():
    t = aiohttp.ClientTimeout(total=5)
    c = aiohttp.TCPConnector(limit=5, ssl=False)
    async with aiohttp.ClientSession(connector=c, timeout=t) as s:
        r = await fetch_hotel.parse_jsmpeg(s, "http://123.14.94.224:9003", t)
        lines = [k+"|"+v for k,v in list(r.items())[:30]]
        open("urls.txt", "w", encoding="utf-8").write("\n".join(lines))
        print("OK")
asyncio.run(run())
