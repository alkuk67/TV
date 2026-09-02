import json
import os
import threading
import time
from datetime import datetime, time as dt_time

# Schedule config path
SCHEDULE_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath("server.py"))), "config", "schedule.json")

# Global schedule state
_schedule = {
    "enabled": False,
    "days": [],  # 0=Monday, 6=Sunday
    "times": [],  # ["06:00", "12:00", "18:00", "22:00"]
    "last_run": None,
    "next_run": None
}
_schedule_lock = threading.Lock()
_timer_thread = None

def load_schedule():
    global _schedule
    schedule_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config", "schedule.json")
    if os.path.exists(schedule_path):
        try:
            with open(schedule_path, "r", encoding="utf-8") as f:
                _schedule = json.load(f)
        except Exception:
            pass
    return _schedule.copy()

def save_schedule(data):
    global _schedule
    with _schedule_lock:
        _schedule.update(data)
    schedule_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config", "schedule.json")
    try:
        with open(schedule_path, "w", encoding="utf-8") as f:
            json.dump(_schedule, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"Save schedule error: {e}")

def get_next_run_time():
    """Calculate next run time based on schedule"""
    now = datetime.now()
    weekdays = now.weekday()  # 0=Monday
    
    # Find next matching day
    sorted_days = sorted(_schedule.get("days", []))
    if not sorted_days:
        return None
    
    # Find next day
    next_day = None
    for day in sorted_days:
        if day > weekdays:
            next_day = day
            break
    if next_day is None:
        next_day = sorted_days[0]  # Wrap to first day of next week
    
    days_ahead = (next_day - weekdays) % 7
    if days_ahead == 0:
        days_ahead = 7  # Next week if same day
    
    # Find next time for that day
    sorted_times = sorted(_schedule.get("times", []))
    if not sorted_times:
        return None
    
    next_time_str = None
    for t in sorted_times:
        h, m = map(int, t.split(":"))
        run_time = dt_time(h, m)
        if run_time > now.time():
            next_time_str = t
            break
    
    if next_time_str is None:
        # All times passed today, use first time tomorrow
        h, m = map(int, sorted_times[0].split(":"))
        next_time_str = sorted_times[0]
        days_ahead += 7
    
    h, m = map(int, next_time_str.split(":"))
    next_run = now.replace(hour=h, minute=m, second=0, microsecond=0)
    from datetime import timedelta
    next_run += timedelta(days=days_ahead)
    
    return next_run.isoformat()

def check_and_run():
    """Check if task should run and execute if needed"""
    global _timer_thread
    with _schedule_lock:
        if not _schedule.get("enabled", False):
            _timer_thread = None
            return
        if _timer_thread and _timer_thread.is_alive():
            return
    
    now = datetime.now()
    weekdays = now.weekday()
    
    if weekdays in _schedule.get("days", []):
        current_time = now.strftime("%H:%M")
        if current_time in _schedule.get("times", []):
            # Check if we already ran in the last 5 minutes
            last_run = _schedule.get("last_run")
            if last_run:
                try:
                    last_dt = datetime.fromisoformat(last_run)
                    if (now - last_dt).total_seconds() < 300:
                        return  # Already ran recently
                except Exception:
                    pass
            
            # Run the task
            print(f"[Schedule] Auto running at {now.strftime('%Y-%m-%d %H:%M:%S')}")
            from server import run_main
            result = run_main()
            if "error" not in result:
                with _schedule_lock:
                    _schedule["last_run"] = now.isoformat()
                    schedule_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config", "schedule.json")
                    try:
                        with open(schedule_path, "w", encoding="utf-8") as f:
                            json.dump(_schedule, f, ensure_ascii=False, indent=2)
                    except Exception:
                        pass

def schedule_timer():
    """Timer thread that checks every minute"""
    global _timer_thread
    while True:
        time.sleep(60)
        try:
            check_and_run()
        except Exception as e:
            print(f"Schedule error: {e}")

def start_schedule_timer():
    """Start the schedule checker thread"""
    global _timer_thread
    load_schedule()
    if _timer_thread is None or not _timer_thread.is_alive():
        _timer_thread = threading.Thread(target=schedule_timer, daemon=True)
        _timer_thread.start()
        print("[Schedule] Timer started")

if __name__ == "__main__":
    start_schedule_timer()
    print("Schedule module loaded")
