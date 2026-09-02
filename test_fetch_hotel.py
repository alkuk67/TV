"""
独立测试 fetch_hotel.py 的脚本
使用缓存数据和已知可用的 host
"""
import asyncio
import sys
import os
import json
import aiohttp
import re

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

class MockConfig:
    hotel_config = {
        "hotel_api": "https://iptvs-speed.humorously.cn",
        "enabled": True,
        "allowed_orgs": [],
    }
    check_timeout = 5
    check_max_conn = 20

import config.config as config_module
config_module.__dict__.update(MockConfig().__dict__)

import fetch_hotel

# 已知可用的 host
KNOWN_HOSTS = {
    "txiptv": ["http://221.232.198.240:7777", "http://101.18.29.114:808"],
    "zhgxtv": ["http://47.104.102.192:80", "http://106.46.117.173:808", "http://60.220.147.37:808"],
    "jsmpeg": ["http://123.14.94.224:9003", "http://116.228.170.214:9003"],
}

async def test_with_cached_data():
    print("=" * 60)
    print("测试 fetch_hotel.py 功能 (使用缓存数据)")
    print("=" * 60)
    
    cache_file = os.path.join(os.path.dirname(__file__), "cache", "hotel.json")
    with open(cache_file, "r", encoding="utf-8") as f:
        cached_data = json.load(f)
    
    nodes = cached_data.get("nodes", {})
    print(f"\n缓存中的节点类型: {list(nodes.keys())}")
    for mt, channels in nodes.items():
        print(f"  {mt}: {len(channels)} 个频道")
    
    print("\n" + "-" * 60)
    print("测试各解析器功能...")
    print("-" * 60)
    
    timeout = aiohttp.ClientTimeout(total=5)
    connector = aiohttp.TCPConnector(limit=5, ssl=False)
    
    async with aiohttp.ClientSession(connector=connector, timeout=timeout) as session:
        for mt in ["txiptv", "zhgxtv", "jsmpeg"]:
            hosts = KNOWN_HOSTS.get(mt, [])
            if not hosts:
                print(f"\n[{mt}] 跳过: 无可用 host")
                continue
            
            print(f"\n[{mt}] 测试 parse_{mt} 函数...")
            
            best_host = None
            best_result = None
            
            for host in hosts:
                print(f"  测试 host: {host}")
                
                if mt == "txiptv":
                    result = await fetch_hotel.parse_txiptv(session, host, timeout)
                elif mt == "zhgxtv":
                    result = await fetch_hotel.parse_zhgxtrv(session, host, timeout)
                elif mt == "jsmpeg":
                    result = await fetch_hotel.parse_jsmpeg(session, host, timeout)
                
                if result:
                    print(f"    解析结果: {len(result)} 个频道 [成功]")
                    best_host = host
                    best_result = result
                    break
                else:
                    print(f"    解析结果: 0 个频道 [失败]")
            
            if best_result:
                print(f"  最佳 host: {best_host}")
                print(f"  前3个频道: {list(best_result.keys())[:3]}")
            else:
                print(f"  所有 host 都不可用")
    
    print("\n" + "=" * 60)
    print("测试完成!")
    print("=" * 60)

if __name__ == "__main__":
    asyncio.run(test_with_cached_data())
