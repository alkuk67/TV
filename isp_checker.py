# -*- coding: utf-8 -*-
import ipaddress
import logging
import os
import tempfile
import urllib.request
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

ORG_MAP = {
    "chinanet": "China Telecom",
    "cmcc": "China Mobile",
    "unicom": "China Unicom",
    "cernet": "China Education & Research Network",
    "cstnet": "China Science & Technology Network",
    "drpeng": "Dr.Peng",
    "chinanet46": "China Telecom",
    "cmcc46": "China Mobile",
    "unicom46": "China Unicom",
    "cernet46": "China Education & Research Network",
    "cstnet46": "China Science & Technology Network",
    "drpeng46": "Dr.Peng",
}

CIDR_FILES = ["chinanet46.txt", "cmcc46.txt", "unicom46.txt", "cernet46.txt", "cstnet46.txt", "drpeng46.txt"]
RAW_URL_BASE = "https://raw.githubusercontent.com/gaoyifan/china-operator-ip/ip-lists/"
CACHE_DIR = os.path.join(tempfile.gettempdir(), "iptv_isp_cache")
CACHE_EXPIRY_HOURS = 12


class ISPChecker:
    def __init__(self, cache_dir=CACHE_DIR, expiry_hours=CACHE_EXPIRY_HOURS):
        self._cache_dir = cache_dir
        self._expiry = timedelta(hours=expiry_hours)
        self._v4 = {}
        self._v6 = {}
        self._last_load = None
        self._load_if_needed()

    def _cache_path(self, filename):
        return os.path.join(self._cache_dir, filename)

    def _need_reload(self):
        if self._last_load is None:
            return True
        return datetime.now() - self._last_load > self._expiry

    def _load_if_needed(self):
        if not self._need_reload():
            return
        self._load_all()
        self._last_load = datetime.now()
        total_v4 = sum(len(v) for v in self._v4.values())
        total_v6 = sum(len(v) for v in self._v6.values())
        logger.info(f"[ISP] CIDR 数据已加载，运营商 {len(self._v4)} 个，IPv4={total_v4} 条，IPv6={total_v6} 条")

    def _load_all(self):
        os.makedirs(self._cache_dir, exist_ok=True)
        self._v4 = {}
        self._v6 = {}
        for fname in CIDR_FILES:
            local = self._cache_path(fname)
            if not os.path.isfile(local):
                self._download(fname, local)
            if os.path.isfile(local):
                self._parse(fname, local)

    def _download(self, filename, local_path):
        url = RAW_URL_BASE + filename
        try:
            urllib.request.urlretrieve(url, local_path)
            logger.debug(f"[ISP] 下载 {filename} 成功 ({os.path.getsize(local_path)} bytes)")
        except Exception as e:
            logger.warning(f"[ISP] 下载 {filename} 失败: {e}")

    def _parse(self, filename, path):
        operator = filename.replace(".txt", "")
        display_name = ORG_MAP.get(operator, operator)
        v4_nets = []
        v6_nets = []
        try:
            with open(path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#"):
                        continue
                    try:
                        net = ipaddress.ip_network(line, strict=False)
                        (v4_nets if net.version == 4 else v6_nets).append(net)
                    except ValueError:
                        continue
        except Exception as e:
            logger.warning(f"[ISP] 解析 {filename} 失败: {e}")
            return
        self._v4.setdefault(operator, []).extend(v4_nets)
        self._v6.setdefault(operator, []).extend(v6_nets)
        logger.debug(f"[ISP] {display_name}: IPv4={len(v4_nets)} 条, IPv6={len(v6_nets)} 条")

    def get_isp(self, ip_str):
        self._load_if_needed()
        try:
            addr = ipaddress.ip_address(ip_str)
        except ValueError:
            return None
        nets = self._v4 if addr.version == 4 else self._v6
        for operator, net_list in nets.items():
            for net in net_list:
                if addr in net:
                    return ORG_MAP.get(operator, operator)
        return None

    def is_allowed(self, ip_str, allowed):
        if not allowed:
            return True
        isp = self.get_isp(ip_str)
        if isp is None:
            return True
        return isp in allowed


_isp_checker = None

def get_isp_checker():
    global _isp_checker
    if _isp_checker is None:
        _isp_checker = ISPChecker()
    return _isp_checker
