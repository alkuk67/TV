with open(r'D:\yuanl\Documents\GitHub\testtt\check.py', 'r', encoding='utf-8') as f:
    lines = f.readlines()

# 找到并移除 break 所在的行及上下文
new_lines = []
skip_until = -1
for i, line in enumerate(lines):
    if i < skip_until:
        continue
    if 'break' in line and i > 800:  # 只处理第800行后的 break
        # 找到这个 break 所在块的开始（检查前面的注释行）
        # 向上查找 "# 超时检查" 注释
        j = i - 1
        while j >= 0 and lines[j].strip().startswith('#'):
            j -= 1
        # j 现在是 "# 超时检查" 行的上一行
        # 需要删除从 j 到 i 的行
        skip_until = i
        # 跳过前面的空行和注释
        k = i - 1
        while k >= 0 and (lines[k].strip().startswith('#') or lines[k].strip() == ''):
            k -= 1
        # k+1 是删除的起始位置
        new_lines.extend(lines[:k+1])
        lines = lines[i+1:]
        break
    else:
        new_lines.append(line)

with open(r'D:\yuanl\Documents\GitHub\testtt\check.py', 'w', encoding='utf-8') as f:
    f.writelines(new_lines + list(lines))

print('修复完成')
