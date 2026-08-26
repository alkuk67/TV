with open('main.py', 'r', encoding='utf-8') as f:
    content = f.read()

old = r\"r'(高清版|超清版|频道|卫视|高清|超清|HD|台)$'\"
new = r\"r'(高清版|超清版|频道|卫视|高清|超清|HD|台|财经|体育|综艺|新闻|综合|少儿|戏曲|生活|农业|记录|世界|纪实|人文|电影|电视剧|动画|游戏|音乐|汽车)$'\"

content = content.replace(old, new)
# 替换两次（因为有两处）
content = content.replace(old, new)

with open('main.py', 'w', encoding='utf-8') as f:
    f.write(content)
print('done')
