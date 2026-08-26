def _playback_test(url, timeout, test_duration=3):
    import time
    result = {
        "status": "ok",
        "detail": "",
        "decoded_frames": 0,
        "decode_errors": 0,
        "first_frame_time_ms": 0,
        "avg_frame_time_ms": 0,
    }
    cmd = ["ffmpeg", "-y", "-v", "error", "-i", url, "-t", str(test_duration), "-f", "null", "-"]
    try:
        proc_start = time.time()
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout + 5)
        total_time_ms = int((time.time() - proc_start) * 1000)
        stderr = proc.stderr or ""
        import re as re_module
        frame_matches = re_module.findall(r"frame=\s*(\d+)", stderr)
        if frame_matches:
            result["decoded_frames"] = int(frame_matches[-1])
        ts_matches = re_module.findall(r"pts_time=([\d.]+)", stderr)
        if ts_matches:
            try:
                result["first_frame_time_ms"] = int(float(ts_matches[0]) * 1000)
            except:
                pass
        if result["decoded_frames"] > 0 and total_time_ms > 0:
            result["avg_frame_time_ms"] = total_time_ms // result["decoded_frames"]
        error_match = re_module.findall(r"error|Error", stderr)
        result["decode_errors"] = len(error_match)
        if proc.returncode != 0 and result["decoded_frames"] == 0:
            result["status"] = "failed"
            result["detail"] = "decode_failed"
        elif result["decode_errors"] > 10:
            result["status"] = "degraded"
            result["detail"] = "high_error_rate"
        else:
            result["status"] = "ok"
            parts = []
            if result["first_frame_time_ms"] > 0:
                parts.append("first=" + str(result["first_frame_time_ms"]) + "ms")
            if result["avg_frame_time_ms"] > 0:
                parts.append("avg=" + str(result["avg_frame_time_ms"]) + "ms")
            parts.append(str(result["decoded_frames"]) + "f")
            result["detail"] = " ".join(parts)
    except subprocess.TimeoutExpired:
        result["status"] = "timeout"
        result["detail"] = "timeout"
    except Exception as e:
        result["status"] = "error"
        result["detail"] = str(e)
    return result
