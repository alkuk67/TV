# -*- coding: utf-8 -*-
"""IPTV Web Dashboard - Flask版"""
import os, sys, json, time, threading, re, secrets
from flask import Flask, request, jsonify, send_file, send_from_directory
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.middleware.proxy_fix import ProxyFix
import subprocess

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import check as _check

app = Flask(__name__, static_folder=os.path.join(ROOT, "web"), static_url_path="")
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

# 需要登录才能访问的静态文件（含频道元数据或旧版页面备份）
_SENSITIVE_STATIC = {
    "/channel_data.js",
    "/index.html.backup",
    "/index_original.html",
    "/index_new.html",
    "/fix_test.txt",
}

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
_schedule_lock = threading.Lock()
_schedule_thread = None
_next_run_time = None

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

def _compute_next_run(settings):
    """Compute next run datetime from schedule settings (supports multiple times)."""
    global _next_run_time
    sched = settings.get("schedule", {})
    if not sched.get("enabled", False):
        with _schedule_lock:
            _next_run_time = None
        return None
    from datetime import datetime, timedelta
    import calendar
    now = datetime.now()
    interval = sched.get("interval", "daily")
    # Support both "times" (array) and legacy "time" (string)
    time_list = sched.get("times", [])
    if not time_list:
        t = sched.get("time", "06:00")
        if t:
            time_list = [t] if isinstance(t, str) else list(t)
    if not time_list:
        time_list = ["06:00"]
    candidates = []
    for time_str in time_list:
        h, m = map(int, time_str.split(":"))
        run_at = now.replace(hour=h, minute=m, second=0, microsecond=0)
        if run_at <= now:
            if interval == "daily":
                run_at += timedelta(days=1)
            elif interval == "weekly":
                days_ahead = (7 - now.weekday()) % 7 or 7
                run_at += timedelta(days=days_ahead)
            elif interval == "monthly":
                if now.month == 12:
                    run_at = now.replace(year=now.year + 1, month=1, day=1)
                else:
                    max_day = calendar.monthrange(now.year, now.month + 1)[1]
                    run_at = now.replace(month=now.month + 1, day=min(now.day, max_day))
        candidates.append(run_at)
    with _schedule_lock:
        _next_run_time = min(candidates)
    return _next_run_time



def _scheduler_loop():
    """Background loop that checks and triggers scheduled runs."""
    global _schedule_thread
    while True:
        try:
            sched_enabled = _next_run_time is not None
            next_run = _next_run_time
            if sched_enabled and next_run:
                from datetime import datetime
                now = datetime.now()
                diff = (next_run - now).total_seconds()
                if 0 < diff <= 30:
                    with _run_lock:
                        if _run_process is None or _run_process.poll() is not None:
                            threading.Thread(target=run_main, daemon=True).start()
                            _compute_next_run(_load_settings())
            time.sleep(15)
        except Exception:
            time.sleep(30)


def _start_scheduler():
    global _schedule_thread
    settings = _load_settings()
    _compute_next_run(settings)
    if _schedule_thread is None or not _schedule_thread.is_alive():
        _schedule_thread = threading.Thread(target=_scheduler_loop, daemon=True)
        _schedule_thread.start()
        print("[Scheduler] 定时任务已启动")


def get_next_run():
    with _schedule_lock:
        nr = _next_run_time
    if nr:
        return nr.strftime("%Y-%m-%d %H:%M")
    return None


def api_schedule():
    settings = _load_settings()
    sched = settings.get("schedule", {})
    times = sched.get("times", [])
    if not times:
        t = sched.get("time", "06:00")
        times = [t] if isinstance(t, str) else ["06:00"]
    return jsonify({
        "enabled": sched.get("enabled", False),
        "times": times,
        "time": times[0],
        "interval": sched.get("interval", "daily"),
        "next_run": get_next_run(),
    })


def api_schedule_set():
    data = request.get_json() or {}
    settings = _load_settings()
    if "schedule" not in settings:
        settings["schedule"] = {}
    times = data.get("times", [])
    if isinstance(times, str):
        times = [times]
    if not times:
        times = ["06:00"]
    settings["schedule"].update({
        "enabled": data.get("enabled", False),
        "times": times,
        "time": times[0],
        "interval": data.get("interval", "daily"),
    })
    _save_settings(settings)
    _compute_next_run(settings)
    print("[Scheduler] schedule updated: enabled=" + str(settings["schedule"]["enabled"]) + " times=" + str(times) + " interval=" + str(settings["schedule"]["interval"]))
    return jsonify({"ok": True, "next_run": get_next_run(), "schedule_enabled": _load_settings().get("schedule", {}).get("enabled", False)})


_PROGRESS_PATTERNS = [
    (r"\u522b\u540d\u6620\u5c04.*\u52a0\u8f7d.*\u6761\u4ef6\u89c4\u5219", "\u52a0\u8f7d\u522b\u540d\u89c4\u5219...", 5),
    (r"EPG\u6620\u5c04.*\u52a0\u8f7d\u6210\u529f", "\u52a0\u8f7dEPG\u6620\u5c04...", 10),
    (r"EPG\u6620\u5c04.*\u5408\u5e76.*\u9891\u9053", "\u5408\u5e76\u9891\u9053\u6570\u636e...", 15),
    (r"\u9152\u5e97\u6e90.*\u5f00\u59cb\u6293\u53d6", "\u6293\u53d6\u9152\u5e97\u6e90...", 30),
    (r"\u6293\u53d6\u6210\u529f.*\u5305\u542b\u9891\u9053\u5206\u7c7b", "\u6293\u53d6\u8ba2\u9605\u6e90...", 45),
    (r"\u5f00\u59cb\u8d28\u91cf\u68c0\u6d4b", "\u8d28\u91cf\u68c0\u6d4b\u4e2d...", None),
    (r"ISP.*\u9884\u8fc7\u6ee9.*\u79fb\u9664.*\u4e2a", "ISP\u9884\u8fc7\u6ee9...", 60),
    (r"ISP\u5206\u7c7b.*\u5b8c\u6210", "\u8fd0\u8425\u5546\u5206\u7c7b\u5b8c\u6210...", 70),
    (r"\u8fc7\u6ee9\u540e.*\u4fdd\u7559\s+(\d+)\s+\u4e2a", "\u8fc7\u6ee9\u5931\u6548\u6e90...", 85),
    (r"\u8d28\u91cf\u68c0\u6d4b\u5b8c\u6210", "\u8d28\u91cf\u68c0\u6d4b\u5b8c\u6210...", 90),
    (r"\u5df2\u751f\u6210.*live\.m3u", "\u751f\u6210\u8f93\u51fa\u6587\u4ef6...", 95),
]


def _infer_external_progress():
    """外部 main.py 没有子进程读取器，改为从 function.log 末尾推断阶段与进度。"""
    log_path = os.path.join(ROOT, "function.log")
    if not os.path.isfile(log_path):
        return 5, "\u521d\u59cb\u5316\u4e2d..."
    try:
        with _log_lock:
            with open(log_path, "r", encoding="utf-8", errors="ignore") as f:
                lines = f.readlines()
    except OSError:
        return 5, "\u521d\u59cb\u5316\u4e2d..."
    for line in reversed(lines[-200:]):
        m = re.search(r"\d+/\d+\s*\((\d+)%\)", line)
        if m:
            return int(m.group(1)), "\u8d28\u91cf\u68c0\u6d4b\u4e2d..."
        for pattern, phase, prog in _PROGRESS_PATTERNS:
            if re.search(pattern, line):
                return (prog if prog is not None else 55), phase
    return 5, "\u521d\u59cb\u5316\u4e2d..."


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
            with open(log_path, "a", encoding="utf-8") as log_file:
                for line in proc.stdout:
                    line = line.strip()
                    if line:
                        with _log_lock:
                            log_file.write(line + "\n")
                            log_file.flush()
                        # Real progress from check module
                        prog_match = re.search(r'\d+/\d+\s*\((\d+)%\)', line)
                        if prog_match:
                            with _run_lock:
                                _run_progress = int(prog_match.group(1))
                            continue
                        for pattern, phase, prog in _PROGRESS_PATTERNS:
                            if prog is None:
                                # Quality check start: match log message instead of line number
                                if '[质量检测] 开始' in line:
                                    m = True
                                else:
                                    m = re.search(pattern, line)
                                if m:
                                    import time as _time
                                    global _quality_start_time
                                    if "_quality_start_time" not in globals():
                                        _quality_start_time = _time.time()
                                    with _run_lock:
                                        _run_phase = phase
                                        elapsed = _time.time() - _quality_start_time
                                        _run_progress = min(55 + int(elapsed / 60.0 * 30), 85)
                                    break
                            else:
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

def _external_main_pids():
    """查找由外部启动的 main.py 进程 PID（如 Docker 容器 CMD 里自动运行的那个），
    排除本 web 服务自身及其 run_main 启动的子进程。"""
    own_pid = os.getpid()
    child_pid = _run_process.pid if (_run_process is not None and _run_process.poll() is None) else None
    pids = []
    try:
        for entry in os.listdir("/proc"):
            if not entry.isdigit():
                continue
            pid = int(entry)
            if pid == own_pid or pid == child_pid:
                continue
            try:
                with open(f"/proc/{pid}/cmdline", "rb") as f:
                    raw = f.read()
            except OSError:
                continue
            args = [a for a in raw.split(b"\x00") if a]
            if any(os.path.basename(a.decode(errors="ignore")) == "main.py" for a in args):
                pids.append(pid)
    except FileNotFoundError:
        # 非 Linux 环境（本地开发）没有 /proc，无需外部进程检测
        pass
    return pids


def _kill_external_main():
    """终止外部启动的 main.py 进程（容器启动时自动运行的那个）。"""
    for pid in _external_main_pids():
        try:
            os.kill(pid, subprocess.signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            pass


def stop_run():
    global _run_process, _run_progress, _run_phase, _stop_requested
    with _run_lock:
        _stop_requested = True
        _kill_external_main()
        if _run_process is not None and _run_process.poll() is None:
            # Write stop signal file for graceful exit
            _check.request_stop_via_signal()
            for _ in range(60):
                time.sleep(0.1)
                if _run_process.poll() is not None:
                    _run_process = None
                    _run_progress = 0
                    _run_phase = ""
                    return jsonify({"status": "stopped", "progress": 0, "phase": ""})
            # Fallback: force kill if still running after 6s
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
            ext = _external_main_pids()
            if ext:
                progress, phase = _infer_external_progress()
                return jsonify({"status": "running", "progress": progress, "phase": phase, "pid": ext[0], **result, **stats, "next_run": get_next_run(), "schedule_enabled": _load_settings().get("schedule", {}).get("enabled", False)})
            return jsonify({"status": "idle", "progress": 0, "phase": "", **result, **stats, "next_run": get_next_run(), "schedule_enabled": _load_settings().get("schedule", {}).get("enabled", False)})
        ret = _run_process.poll()
        if ret is not None:
            _run_process = None
            _run_progress = 100
            _run_phase = "完成"
            return jsonify({"status": "completed", "progress": 100, "phase": "完成", **result, **stats, "next_run": get_next_run(), "schedule_enabled": _load_settings().get("schedule", {}).get("enabled", False)})
        elapsed = int(time.time() - _run_start_time) if _run_start_time else 0
        return jsonify({
            "status": "running",
            "progress": _run_progress,
            "phase": _run_phase,
            "pid": _run_process.pid,
            "elapsed": elapsed,
            **result,
            **stats,
            "next_run": get_next_run()
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
                files.append({"name": f, "size": stat.st_size, "mtime": int(stat.st_mtime)})
        return jsonify({"files": files, "total": len(files), "share_token": _get_share_token()})
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
                        match2 = re.search(r'\s(\d+)(kbps|Mbps)】', line)
                        if match2:
                            speed = match2.group(1) + " " + match2.group(2)
                        bitrate = ""
                        match3 = re.search(r'@(\d+)(kbps|Mbps)', line)
                        if match3:
                            bitrate = match3.group(1) + " " + match3.group(2)
                        groups[-1]["channels"].append({"name": name, "url": url, "resolution": resolution, "bitrate": bitrate, "speed": speed})
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

# ── 系统设置 API ────────────────────────────────────────────────
_settings_path = os.path.join(ROOT, "config", "settings.json")


_sessions = {}
_auth_lock = threading.Lock()
_SESSION_TTL = 7 * 86400

# 登录失败限速：同一 IP 在窗口内失败达上限则临时锁定
_LOGIN_FAIL_WINDOW = 5 * 60
_LOGIN_MAX_FAILS = 5
_LOGIN_LOCK_SECONDS = 15 * 60
_login_fails = {}
_login_fail_lock = threading.Lock()

# 可选 Host 白名单（逗号分隔，防 DNS rebinding）；未设置时不校验
_ALLOWED_HOSTS = [h.strip().lower() for h in os.environ.get("ALLOWED_HOSTS", "").split(",") if h.strip()]


def _check_auth():
    """会话校验；未设置密码时不做保护。"""
    settings = _load_settings()
    if not settings.get("password", ""):
        return True
    token = request.cookies.get("iptv_token", "")
    if not token:
        return False
    with _auth_lock:
        exp = _sessions.get(token)
    if not exp:
        return False
    if exp < time.time():
        with _auth_lock:
            _sessions.pop(token, None)
        return False
    return True


def _verify_password(pwd, stored):
    """校验密码：兼容旧明文与 pbkdf2 哈希。"""
    if not stored:
        return False
    try:
        if check_password_hash(stored, pwd):
            return True
    except (ValueError, TypeError):
        pass
    return secrets.compare_digest(stored, pwd)


def _login_blocked():
    ip = request.remote_addr or "unknown"
    now = time.time()
    with _login_fail_lock:
        entry = _login_fails.get(ip)
        if not entry:
            return False
        if entry["locked_until"] and now < entry["locked_until"]:
            return True
        if now - entry["window_start"] > _LOGIN_FAIL_WINDOW:
            _login_fails.pop(ip, None)
            return False
        return entry["count"] >= _LOGIN_MAX_FAILS


def _record_login_failure():
    ip = request.remote_addr or "unknown"
    now = time.time()
    with _login_fail_lock:
        entry = _login_fails.get(ip)
        if not entry or now - entry["window_start"] > _LOGIN_FAIL_WINDOW:
            entry = {"count": 0, "window_start": now, "locked_until": 0}
            _login_fails[ip] = entry
        entry["count"] += 1
        if entry["count"] >= _LOGIN_MAX_FAILS:
            entry["locked_until"] = now + _LOGIN_LOCK_SECONDS


def _clear_login_failures():
    ip = request.remote_addr or "unknown"
    with _login_fail_lock:
        _login_fails.pop(ip, None)


def _check_basic_auth():
    """HTTP Basic 认证：供播放器等无法携带 Cookie 的客户端拉取输出文件。"""
    auth = request.authorization
    if not auth or not auth.username or not auth.password:
        return False
    settings = _load_settings()
    stored = settings.get("password", "")
    if not stored:
        return True
    return _verify_password(auth.password, stored)


@app.before_request
def _guard():
    if _ALLOWED_HOSTS and request.host.split(':')[0].strip('[]').lower() not in _ALLOWED_HOSTS:
        return jsonify({"error": "非法 Host"}), 400
    if request.path in ("/api/login", "/api/logout"):
        return None
    if request.path.startswith("/api/"):
        if not _check_auth():
            return jsonify({"error": "需要登录"}), 401
    elif request.path.startswith("/output/"):
        token = request.args.get("token", "")
        if token and secrets.compare_digest(token, _get_share_token()):
            return None
        if not (_check_auth() or _check_basic_auth()):
            return jsonify({"error": "需要登录"}), 401
    elif request.path in _SENSITIVE_STATIC and not _check_auth():
        return jsonify({"error": "需要登录"}), 401
    return None


@app.after_request
def _security_headers(resp):
    resp.headers.setdefault("X-Frame-Options", "DENY")
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("Referrer-Policy", "no-referrer")
    resp.headers.setdefault("Content-Security-Policy",
        "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data: https:; connect-src 'self'; frame-ancestors 'none'")
    return resp


@app.route("/api/login", methods=["POST"])
def api_login():
    if _login_blocked():
        return jsonify({"error": "尝试次数过多，请稍后再试"}), 429
    data = request.get_json(silent=True) or {}
    settings = _load_settings()
    if not settings.get("password", ""):
        return jsonify({"error": "未启用密码保护"}), 403
    if not _verify_password(data.get("password", ""), settings.get("password", "")):
        _record_login_failure()
        return jsonify({"error": "密码错误"}), 401
    # 旧明文密码登录成功后自动升级为哈希存储
    if settings["password"].count("$") < 2:
        settings["password"] = generate_password_hash(settings["password"])
        _save_settings(settings)
    _clear_login_failures()
    token = secrets.token_hex(16)
    with _auth_lock:
        _sessions[token] = time.time() + _SESSION_TTL
    resp = jsonify({"ok": True})
    resp.set_cookie("iptv_token", token, max_age=_SESSION_TTL, httponly=True, samesite="Lax", secure=request.is_secure)
    return resp


@app.route("/api/logout", methods=["POST"])
def api_logout():
    token = request.cookies.get("iptv_token", "")
    with _auth_lock:
        _sessions.pop(token, None)
    resp = jsonify({"ok": True})
    resp.delete_cookie("iptv_token")
    return resp

def _load_settings():
    if os.path.exists(_settings_path):
        with open(_settings_path, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"autostart": False, "schedule": {"enabled": False, "time": "06:00", "interval": "daily"}}

def _save_settings(data):
    with open(_settings_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _get_share_token():
    """获取/生成持久化的播放分享 token（存 settings.json，容器重建后不变）。"""
    settings = _load_settings()
    tok = settings.get("share_token", "")
    if not tok:
        tok = secrets.token_urlsafe(24)
        settings["share_token"] = tok
        _save_settings(settings)
    return tok

@app.route("/api/settings")
def api_settings():
    settings = _load_settings()
    settings.pop("password", None)
    return jsonify(settings)

@app.route("/api/settings/autostart", methods=["POST"])
def api_settings_autostart():
    data = request.get_json() or {}
    settings = _load_settings()
    settings["autostart"] = data.get("enabled", False)
    _save_settings(settings)
    return jsonify({"ok": True})

@app.route("/api/settings/schedule", methods=["POST"])
def api_settings_schedule():
    data = request.get_json() or {}
    settings = _load_settings()
    if "schedule" not in settings:
        settings["schedule"] = {}
    settings["schedule"].update({
        "enabled": data.get("enabled", False),
        "time": data.get("time", "06:00"),
        "interval": data.get("interval", "daily")
    })
    _save_settings(settings)
    return jsonify({"ok": True})





def _cleanup_old_logs():
    """Clear log entries older than 7 days."""
    import time as _time
    log_path = os.path.join(ROOT, "function.log")
    if not os.path.isfile(log_path):
        return 0
    cutoff = _time.time() - 7 * 86400  # 7 days
    kept = 0
    try:
        with open(log_path, 'r', encoding='utf-8', errors='replace') as f:
            lines = f.readlines()
        new_lines = []
        for line in lines:
            try:
                ts_str = line[:19]
                ts = _time.mktime(_time.strptime(ts_str, "%Y-%m-%d %H:%M:%S"))
                if ts >= cutoff:
                    new_lines.append(line)
                    kept += 1
            except (ValueError, IndexError):
                new_lines.append(line)
                kept += 1
        if len(new_lines) < len(lines):
            with open(log_path, 'w', encoding='utf-8') as f:
                f.writelines(new_lines)
            print(f"[Log] 自动清理: 保留 {kept} 条，删除 {len(lines) - kept} 条旧日志")
        else:
            print(f"[Log] 无需清理，共 {kept} 条")
    except Exception as e:
        print(f"[Log] 清理失败: {e}")
    return kept


@app.route('/api/logs/cleanup', methods=['POST'])
def api_logs_cleanup():
    """Manually trigger log cleanup."""
    kept = _cleanup_old_logs()
    return jsonify({"ok": True, "kept": kept})


@app.route('/api/settings/password', methods=['POST'])
def api_settings_password():
    data = request.get_json() or {}
    settings = _load_settings()
    pwd = data.get('password', '')
    settings['password'] = generate_password_hash(pwd) if pwd else ''
    _save_settings(settings)
    return jsonify({'ok': True})

@app.route("/output/<path:filename>")
def serve_output_file(filename):
    output_dir = os.path.join(ROOT, "output")
    filepath = os.path.join(output_dir, filename)
    if os.path.isfile(filepath) and os.path.dirname(os.path.abspath(filepath)) == os.path.abspath(output_dir):
        return send_file(filepath)
    return jsonify({"error": "Not found"}), 404


@app.route("/api/output/download/<path:filename>", methods=["GET"])
def api_output_download(filename):
    output_dir = os.path.join(ROOT, "output")
    filepath = os.path.join(output_dir, filename)
    if os.path.isfile(filepath) and os.path.dirname(os.path.abspath(filepath)) == os.path.abspath(output_dir):
        return send_file(filepath, as_attachment=True, download_name=filename)
    return jsonify({"error": "Not found"}), 404

@app.route("/api/output/delete/<path:filename>", methods=["DELETE"])
def api_output_delete(filename):
    output_dir = os.path.join(ROOT, "output")
    filepath = os.path.join(output_dir, filename)
    if os.path.isfile(filepath) and os.path.dirname(os.path.abspath(filepath)) == os.path.abspath(output_dir):
        os.remove(filepath)
        return jsonify({"ok": True})
    return jsonify({"error": "Not found"}), 404


@app.route("/api/schedule", methods=["GET"])
def api_schedule_get():
    return api_schedule()

@app.route("/api/schedule", methods=["POST"])
def api_schedule_post():
    result = api_schedule_set()
    _start_scheduler()
    return result

@app.route("/api/schedule/start", methods=["POST"])
def api_schedule_start_now():
    with _run_lock:
        if _run_process is not None and _run_process.poll() is None:
            return jsonify({"error": "正在运行中"}), 409
    threading.Thread(target=run_main, daemon=True).start()
    return jsonify({"ok": True})

if __name__ == "__main__":
    PORT = int(os.environ.get("PORT", "5077"))
    print(f"[Web] IPTV dashboard started: http://localhost:{PORT}")
    _start_scheduler()
    _cleanup_old_logs()
    try:
        from waitress import serve
        serve(app, host="0.0.0.0", port=PORT, threads=16)
    except ImportError:
        app.run(host="0.0.0.0", port=PORT, threaded=True, debug=False)
