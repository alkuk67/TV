# 更新 index.html - 添加日志自动刷新
with open('web/index.html', 'r', encoding='utf-8') as f:
    content = f.read()

# 替换日志区域，添加刷新按钮
old_log = '''      <div id="log-preview" style="font-size:12px;color:#64748b;max-height:300px;overflow-y:auto"></div>'''
new_log = '''      <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px">
          <span>最近日志</span>
          <button class="btn btn-secondary" style="padding:4px 12px;font-size:12px" onclick="refreshLogs()">刷新</button>
        </div>
        <div id="log-preview" style="font-size:12px;color:#64748b;max-height:300px;overflow-y:auto;background:#f8fafc;padding:12px;border-radius:8px"></div>'''
content = content.replace(old_log, new_log)

# 添加日志刷新函数
old_init = '''// Init
loadOverview();
setInterval(loadOverview, 30000);'''
new_init = '''let logRefreshInterval = null;

function startLogRefresh() {
  refreshLogs();
  logRefreshInterval = setInterval(refreshLogs, 3000);
}

function stopLogRefresh() {
  if (logRefreshInterval) {
    clearInterval(logRefreshInterval);
    logRefreshInterval = null;
  }
}

async function refreshLogs() {
  try {
    const res = await fetch('/api/logs?tail=50');
    const data = await res.json();
    const logEl = document.getElementById('log-preview');
    if (logEl) {
      logEl.textContent = data.lines.map(l => l.trim()).join('\n');
      logEl.scrollTop = logEl.scrollHeight;
    }
  } catch(e) {}
}

// Init
loadOverview();
setInterval(loadOverview, 30000);'''
content = content.replace(old_init, new_init)

# 在 runIPTV 中启动日志刷新
old_run = '''  document.getElementById('progress-area').classList.remove('hidden');
  pollStatus();'''
new_run = '''  document.getElementById('progress-area').classList.remove('hidden');
  startLogRefresh();
  pollStatus();'''
content = content.replace(old_run, new_run)

# 在 pollStatus 完成时停止刷新
old_poll = '''    if (run.status === 'done' || run.status === 'idle') {
      loadOverview();
      return;
    }'''
new_poll = '''    if (run.status === 'done' || run.status === 'idle') {
      loadOverview();
      stopLogRefresh();
      return;
    }'''
content = content.replace(old_poll, new_poll)

with open('web/index.html', 'w', encoding='utf-8') as f:
    f.write(content)

print('Updated index.html with auto-refresh logs')
