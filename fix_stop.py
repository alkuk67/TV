with open('D:/yuanl/Documents/GitHub/testtt/web/server.py', 'r', encoding='utf-8') as f:
    content = f.read()

old_func = '''def stop_run():
    global _run_process, _run_progress, _run_phase
    with _run_lock:
        if _run_process and _run_process.poll() is None:
            _run_process.terminate()
            _run_process = None
            _run_progress = 0
            _run_phase = ""
            return {"status": "stopped"}
        return {"error": "没有正在运行的任务"}'''

new_func = '''def stop_run():
    import subprocess
    import time
    global _run_process, _run_progress, _run_phase
    with _run_lock:
        if _run_process and _run_process.poll() is None:
            pid = _run_process.pid
            # 终止主进程
            _run_process.terminate()
            
            # 等待子进程结束（最多3秒）
            for _ in range(30):
                time.sleep(0.1)
                if _run_process.poll() is not None:
                    break
            
            # 如果还在运行，强制杀死
            if _run_process.poll() is None:
                try:
                    _run_process.kill()
                except:
                    pass
            
            # 杀死所有子进程
            try:
                subprocess.run(['taskkill', '/F', '/T', '/PID', str(pid)], 
                              capture_output=True, timeout=5)
            except:
                pass
            
            _run_process = None
            _run_progress = 0
            _run_phase = ""
            return {"status": "stopped", "message": "已终止主进程和所有子进程"}
        return {"error": "没有正在运行的任务"}'''

if old_func in content:
    content = content.replace(old_func, new_func)
    with open('D:/yuanl/Documents/GitHub/testtt/web/server.py', 'w', encoding='utf-8') as f:
        f.write(content)
    print('Fixed stop function - now kills child processes too')
else:
    print('Pattern not found')
    # Debug
    idx = content.find('def stop_run():')
    if idx > 0:
        print('Found at:', idx)
        print(content[idx:idx+400])
