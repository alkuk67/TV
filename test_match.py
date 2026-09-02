import sys
sys.path.insert(0, 'D:\yuanl\Documents\GitHub\testtt')

from main import parse_template, resolve_alias, _normalize, load_alias_map

template = parse_template('config/demo.txt')
alias_map = load_alias_map('config/alias.txt')

print(f"demo.txt 模板频道数: {sum(len(channels) for channels in template.values())}")
print(f"alias.txt 别名规则数: {len(alias_map)}")
print()

print("=" * 60)
print("广东频道匹配测试:")
print("=" * 60)

guangdong_channels = [
    "广东珠江", "广东体育", "广东民生", "广东少儿", 
    "广东综艺", "广东影视", "经济科教", "岭南戏曲", "现代教育",
    "大湾区卫视", "广州综合", "广州影视", "广州竞赛",
    "江门综合", "江门侨乡生活", "佛山综合", "深圳卫视",
    "汕尾公共", "汕头综合", "汕头经济", "汕头文旅",
    "茂名综合", "茂名公共"
]

for ch in guangdong_channels:
    norm = _normalize(ch)
    resolved = resolve_alias(ch, alias_map)
    print(f"{ch:12s} -> 归一化: {norm:15s} -> 别名: {resolved or '无'}")

print()
print("=" * 60)
print("CCTV频道匹配测试:")
print("=" * 60)

cctv_channels = ["CCTV1", "CCTV2", "CCTV3", "CCTV4", "CCTV5", "CCTV5+", "CCTV6"]
for ch in cctv_channels:
    norm = _normalize(ch)
    resolved = resolve_alias(ch, alias_map)
    print(f"{ch:10s} -> 归一化: {norm:15s} -> 别名: {resolved or '无'}")
