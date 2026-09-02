# -*- coding: utf-8 -*-
"""IPTV Web Dashboard - Flask版"""
import os, sys, json, time, threading, re
from flask import Flask, request, jsonify, send_file, send_from_directory
import subprocess

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

app = Flask(__name__, static_folder=os.path.join(ROOT, "web"), static_url_path="")

_run_process = None
_run_start_time = None
_run_progress = 0
_run_phase = ""
_stop_requested = False
_config_cache = None
_config_cache_time = 0

_run_lock = threading.Lock()
_log_lock = threading.Lock()
_config_lock = threading.Lock()

def request_stop():
    with _run_lock:
        global _stop_requested
        _stop_requested = True

def _load_config():
    global _config_cache, _config_cache_time
    now = time.time()
    with _config_lock:
        if _config_cache is None or now - _config_cache_time > 60:
            config_path = os.path.join(ROOT, "config", "config.py")
            try:
                with open(config_path, "r", encoding="utf-8", errors="ignore") as f:
                    _config_cache = f.read()
                _config_cache_time = now
            except Exception:
                pass
    return _config_cache or ""

def run_main():
    global _run_process, _run_start_time, _run_progress, _run_phase
    with _run_lock:
        if _run_process is not None and _run_process.poll() is None:
            return jsonify({"error": "正在运行中"}), 409
        _run_progress = 5
        _run_phase = "初始化中..."
        global _stop_requested
        _stop_requested = False
        _run_start_time = time.time()
        log_path = os.path.join(ROOT, "function.log")
        proc = subprocess.Popen(
            [sys.executable, os.path.join(ROOT, "main.py")],
            cwd=ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        _run_process = proc
        def _read_log():
            global _run_progress, _run_phase
            patterns = [
                (r"别名映射.*加载.*条别名规则", "加载别名规则...", 5),
                (r"EPG映射.*加载成功", "加载EPG映射...", 10),
                (r"EPG映射.*共合并加载.*个频道", "合并频道数据...", 15),
                (r"酒店源.*开始抓取", "抓取酒店源...", 30),
                (r"抓取成功.*包含频道分类", "抓取订阅源...", 45),
                (r"开始质量检测.*共.*个 URL", "质量检测中...", 55),
                (r"ISP.*预过滤移除.*个", "ISP预过滤中...", 60),
                (r"ISP分类.*完成", "运营商分类完成...", 75),
                (r"过滤后.*保留\s+(\d+)\s+个", "过滤失效源...", 85),
                (r"质量检测完成", "质量检测完成...", 90),
                (r"已生成.*live\.m3u", "生成输出文件...", 99),
            ]
            with open(log_path, "a", encoding="utf-8") as log_file:
                for line in proc.stdout:
                    line = line.strip()
                    if line:
                        with _log_lock:
                            log_file.write(line + "\n")
                            log_file.flush()
                        for pattern, phase, prog in patterns:
                            m = re.search(pattern, line)
                            if m:
                                with _run_lock:
                                    _run_phase = phase
                                    _run_progress = prog
                                break
                    elif proc.poll() is not None:
                        with _run_lock:
                            _run_progress = 100
                            _run_phase = "完成"
                        break
            with _run_lock:
                _run_process = None
        threading.Thread(target=_read_log, daemon=True).start()
    return jsonify({"status": "running", "pid": proc.pid, "progress": 5, "phase": "初始化中..."})

def stop_run():
    global _run_process, _run_progress, _run_phase, _stop_requested
    with _run_lock:
        _stop_requested = True
        if _run_process is not None and _run_process.poll() is None:
            pid = _run_process.pid
            _run_process.terminate()
            for _ in range(30):
                time.sleep(0.1)
                if _run_process.poll() is not None:
                    _run_process = None
                    _run_progress = 0
                    _run_phase = ""
                    return jsonify({"status": "stopped", "progress": 0, "phase": ""})
            _run_process.kill()
            _run_process = None
            _run_progress = 0
            _run_phase = ""
            return jsonify({"status": "stopped", "progress": 0, "phase": ""})
        else:
            _run_process = None
            _run_progress = 0
            _run_phase = ""
            return jsonify({"status": "stopped", "progress": 0, "phase": ""})
    return jsonify({"error": "无法停止"}), 500

def get_run_status():
    global _run_process, _run_start_time, _run_progress, _run_phase
    stats = {"channels": 0, "urls": 0, "files": 0, "last_update": ""}
    txt_path = os.path.join(ROOT, "output", "live.txt")
    if os.path.isfile(txt_path):
        try:
            channels = set()
            url_count = 0
            with open(txt_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#"):
                        if "#genre#" not in line:
                            parts = line.split(",")
                            if len(parts) >= 2:
                                channels.add(parts[0].strip())
                                url_count += 1
            stats["channels"] = len(channels)
            stats["urls"] = url_count
            stats["last_update"] = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(os.path.getmtime(txt_path)))
        except Exception:
            pass
    output_dir = os.path.join(ROOT, "output")
    if os.path.isdir(output_dir):
        try:
            files = [f for f in os.listdir(output_dir) if f.endswith(('.m3u', '.txt'))]
            stats["files"] = len(files)
        except Exception:
            pass
    with _run_lock:
        result = {"stopped": _stop_requested}
        if _run_process is None:
            return jsonify({"status": "idle", "progress": 0, "phase": "", **result, **stats})
        ret = _run_process.poll()
        if ret is not None:
            _run_process = None
            _run_progress = 100
            _run_phase = "完成"
            return jsonify({"status": "completed", "progress": 100, "phase": "完成", **result, **stats})
        elapsed = int(time.time() - _run_start_time) if _run_start_time else 0
        return jsonify({
            "status": "running",
            "progress": _run_progress,
            "phase": _run_phase,
            "pid": _run_process.pid,
            "elapsed": elapsed,
            **result,
            **stats
        })

def get_latest_log(tail=500):
    log_path = os.path.join(ROOT, "function.log")
    if not os.path.isfile(log_path):
        return jsonify({"lines": []})
    try:
        with _log_lock:
            with open(log_path, "r", encoding="utf-8", errors="ignore") as f:
                all_lines = f.readlines()
        lines = all_lines[-tail:]
        return jsonify({"lines": [l.rstrip("\n") for l in lines]})
    except Exception as e:
        return jsonify({"error": str(e), "lines": []})

def get_output_files():
    output_dir = os.path.join(ROOT, "output")
    if not os.path.isdir(output_dir):
        return jsonify({"files": [], "total": 0})
    try:
        files = []
        for f in os.listdir(output_dir):
            if f.endswith(('.m3u', '.txt')):
                stat = os.stat(os.path.join(output_dir, f))
                files.append({"name": f, "size": stat.st_size, "modified": stat.st_mtime})
        return jsonify({"files": files, "total": len(files)})
    except Exception as e:
        return jsonify({"error": str(e), "files": [], "total": 0})

def get_channels():
    txt_path = os.path.join(ROOT, "output", "live.txt")
    if not os.path.isfile(txt_path):
        return jsonify({"groups": [], "total": 0})
    try:
        groups = []
        current_group = None
        with open(txt_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if "#genre#" in line:
                    current_group = line.split(",")[0].strip()
                    groups.append({"name": current_group, "channels": []})
                elif current_group:
                    parts = line.split(",")
                    if len(parts) >= 2:
                        name = parts[0].strip()
                        url = parts[1].strip()
                        resolution = ""
                        match = re.search(r'【(\d+)x(\d+)\s*', line)
                        if match:
                            resolution = match.group(1) + "x" + match.group(2)
                        speed = ""
                        match2 = re.search(r'(\d+)kbps', line)
                        if match2:
                            speed = match2.group(1)
                        groups[-1]["channels"].append({"name": name, "url": url, "resolution": resolution, "speed": speed})
        total = sum(len(g["channels"]) for g in groups)
        return jsonify({"groups": groups, "total": total})
    except Exception as e:
        return jsonify({"error": str(e), "groups": [], "total": 0})

@app.route("/")
def index():
    return send_from_directory(os.path.join(ROOT, "web"), "index.html")

@app.route("/api/run", methods=["POST"])
def api_run():
    return run_main()

@app.route("/api/stop", methods=["POST"])
def api_stop():
    return stop_run()

@app.route("/api/status")
def api_status():
    return get_run_status()

@app.route("/api/channels")
def api_channels():
    return get_channels()

@app.route("/api/output")
def api_output():
    return get_output_files()

@app.route("/api/logs")
def api_logs():
    tail = request.args.get("tail", 500, type=int)
    return get_latest_log(tail)
    return get_latest_log(tail, offset)

@app.route("/api/logs/clear", methods=["POST"])
def api_logs_clear():
    log_path = os.path.join(ROOT, "function.log")
    with _log_lock:
        with open(log_path, "w", encoding="utf-8", errors="ignore") as f:
            f.write("")
    return jsonify({"ok": True})

@app.route("/api/file/<path:filename>")
def api_file(filename):
    if filename in ["demo.txt", "alias.txt"]:
        fp = os.path.join(ROOT, "config", filename)
        if os.path.isfile(fp):
            with open(fp, "r", encoding="utf-8") as f:
                return jsonify({"content": f.read()})
        return jsonify({"error": "File not found"}), 404
    return jsonify({"error": "Not found"}), 404

@app.route("/api/file/save/<path:filename>", methods=["POST"])
def api_file_save(filename):
    if filename not in ["demo.txt", "alias.txt"]:
        return jsonify({"error": "Not found"}), 404
    data = request.get_json(silent=True) or {}
    content = data.get("content", "")
    if len(content) > 500000:
        return jsonify({"error": "Content too large"}), 413
    fp = os.path.join(ROOT, "config", filename)
    with open(fp, "w", encoding="utf-8") as f:
        f.write(content)
    return jsonify({"ok": True})

@app.route("/api/config")
def api_config():
    return jsonify({"content": _load_config()})

@app.route("/api/config/save", methods=["POST"])
def api_config_save():
    global _config_cache
    data = request.get_json(silent=True) or {}
    content = data.get("content", "")
    if len(content) > 500000:
        return jsonify({"error": "Content too large"}), 413
    config_path = os.path.join(ROOT, "config", "config.py")
    with _config_lock:
        with open(config_path, "w", encoding="utf-8", errors="ignore") as f:
            f.write(content)
        _config_cache = None
    with _run_lock:
        if _run_process is not None and _run_process.poll() is None:
            try:
                _run_process.send_signal(subprocess.signal.SIGUSR1)
            except Exception:
                pass
    return jsonify({"ok": True, "message": "配置已保存"})

@app.route("/channel_data.js")
def channel_data_js():
    js_path = os.path.join(ROOT, "web", "channel_data.js")
    if os.path.isfile(js_path):
        return send_file(js_path, mimetype="application/javascript")
    return jsonify({"error": "Not found"}), 404

if __name__ == "__main__":
    PORT = 5077
    print(f"[Web] IPTV dashboard started: http://localhost:{PORT}")
    app.run(host="0.0.0.0", port=PORT, threaded=True, debug=False)
