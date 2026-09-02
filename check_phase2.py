"""
两阶段质量检测模块
阶段1: HTTP快筛 + ffprobe (快速过滤)
阶段2: 深度探测 (仅对通过的URL)
"""
import asyncio
import logging
import aiohttp
import config.config as config
import check as quality_checker

logger = logging.getLogger(__name__)


async def check_all_phase1(channels):
    """
    阶段1: 快速检测 (不启用深度探测)
    返回初步通过的URL和结果
    """
    # 临时关闭深度探测
    original_deep_probe = config.enable_deep_probe
    config.enable_deep_probe = False
    
    try:
        results, fail_domains = await quality_checker.check_all(channels)
    finally:
        config.enable_deep_probe = original_deep_probe
    
    return results, fail_domains


def get_phase1_passed_urls(channels, check_results):
    """
    从阶段1结果中提取通过的URL
    """
    passed_urls = []
    for cat, ch_dict in channels.items():
        for ch_name, url_list in ch_dict.items():
            for url in url_list:
                r = check_results.get(cat, {}).get(ch_name, {}).get(url, {})
                # 通过ffprobe或deep层检测的URL
                if r.get("layer") in ("ffprobe", "deep") and r.get("status") in ("ok", "ok_no_ts"):
                    passed_urls.append((cat, ch_name, url, r))
    return passed_urls


async def check_all_phase2(channels, phase1_results, phase1_passed_urls):
    """
    阶段2: 对通过的URL进行深度探测
    """
    if not config.enable_deep_probe:
        logger.info("[阶段2] 深度探测已禁用，跳过")
        return phase1_results, {}
    
    logger.info(f"[阶段2] 开始深度探测，共 {len(phase1_passed_urls)} 个URL")
    
    # 重建channels结构，只包含阶段1通过的URL
    phase2_channels = {}
    for cat, ch_name, url, result in phase1_passed_urls:
        phase2_channels.setdefault(cat, {}).setdefault(ch_name, []).append(url)
    
    # 重新运行check_all，这次会启用深度探测
    final_results, fail_domains = await quality_checker.check_all(phase2_channels)
    
    # 合并结果
    for cat, ch_dict in final_results.items():
        for ch_name, url_results in ch_dict.items():
            for url, result in url_results.items():
                phase1_results.setdefault(cat, {}).setdefault(ch_name, {})[url] = result
    
    logger.info(f"[阶段2] 深度探测完成")
    return phase1_results, fail_domains


async def check_all_two_phase(channels):
    """
    两阶段质量检测主函数
    """
    logger.info("[两阶段检测] 开始阶段1...")
    
    # 阶段1: 快速检测
    phase1_results, fail_domains = await check_all_phase1(channels)
    
    # 提取通过的URL
    passed_urls = get_phase1_passed_urls(channels, phase1_results)
    logger.info(f"[两阶段检测] 阶段1完成: {len(passed_urls)} 个URL通过")
    
    # 阶段2: 深度探测 (可选)
    final_results, final_fail_domains = await check_all_phase2(
        channels, phase1_results, passed_urls
    )
    
    return final_results, final_fail_domains

