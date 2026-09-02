with open('D:/yuanl/Documents/GitHub/testtt/web/server.py', 'r', encoding='utf-8') as f:
    content = f.read()

import re
pattern = r'def get_latest_log\(tail=500, offset=None\):.*?(?=\n\ndef |\Z)'
match = re.search(pattern, content, re.DOTALL)

if match:
    new_func = 'def get_latest_log(tail=500, offset=None):\n'
    new_func += '    """读取最新日志，支持增量更新"""\n'
    new_func += '    global _log_tail\n'
    new_func += '    log_path = os.path.join(ROOT, "function.log")\n'
    new_func += '    if not os.path.isfile(log_path):\n'
    new_func += '        return jsonify({"lines": [], "offset": 0, "new_lines": 0})\n'
    new_func += '    try:\n'
    new_func += '        with _log_lock:\n'
    new_func += '            with open(log_path, "r", encoding="utf-8", errors="ignore") as f:\n'
    new_func += '                all_lines = f.readlines()\n'
    new_func += '        \n'
    new_func += '        current_total = len(all_lines)\n'
    new_func += '        start_offset = offset if offset is not None and offset < current_total else max(0, current_total - tail)\n'
    new_func += '        \n'
    new_func += '        if current_total <= start_offset:\n'
    new_func += '            _log_tail = current_total\n'
    new_func += '            return jsonify({"lines": [], "offset": current_total, "new_lines": 0})\n'
    new_func += '        \n'
    new_func += '        result_lines = all_lines[start_offset:current_total]\n'
    new_func += '        _log_tail = current_total\n'
    new_func += '        return jsonify({\n'
    new_func += '            "lines": [l.rstrip("\\n") for l in result_lines],\n'
    new_func += '            "offset": current_total,\n'
    new_func += '            "new_lines": len(result_lines)\n'
    new_func += '        })\n'
    new_func += '    except Exception as e:\n'
    new_func += '        return jsonify({"error": str(e), "lines": [], "offset": 0, "new_lines": 0})\n'
    
    content = content[:match.start()] + new_func + content[match.end():]
    with open('D:/yuanl/Documents/GitHub/testtt/web/server.py', 'w', encoding='utf-8') as f:
        f.write(content)
    print('Fixed')
else:
    print('Not found')
