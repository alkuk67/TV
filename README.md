# IPTV 直播源聚合工具

自动抓取多来源直播源，经三层质量检测过滤、打分排序后，输出标准 m3u / txt 文件。
附带 Web 管理面板，可通过 Docker 或 GitHub Actions 自动运行。

## 功能

- **多来源聚合**：订阅源（source_urls）、酒店源、组播源，按优先级合并
- **三层质量检测**：HTTP 快筛 → FFprobe 元数据（分辨率/码率）→ m3u8 中度探测（速度）
- **智能排序**：速度余量、画质、延迟、IP/来源优先级综合打分
- **运营商分类**：`enable_isp_split = True` 时按运营商生成独立输出文件
- **公告 / EPG / 频道图标**：输出文件头部带公告，支持 EPG 节目单和频道图标
- **Web 面板**：手动启停、日志、配置编辑、输出文件下载（端口 5077）
- **自动更新**：GitHub Actions 每天 06:00 / 18:00 运行并提交结果

## 快速开始

### 本地运行
```bash
pip install -r requirements.txt
python main.py            # 运行聚合 + 检测 + 输出
python web/server.py      # 启动 Web 面板 http://localhost:5077
```
需要系统安装 `ffprobe`（ffmpeg 自带）。

### Docker
```bash
docker compose up -d --build
```
挂载 `config/`、`cache/`、`output/`，端口默认 5077（通过 `IPTV_PORT` 环境变量修改）。

### GitHub Actions
`.github/workflows/main.yml` 每天 06:00 / 18:00（北京时间）运行，结果自动提交到 `output/` 目录。

## 目录结构

```
├── main.py              # 主程序入口
├── check.py             # 质量检测（三层探测）
├── fetch_hotel.py       # 酒店源抓取
├── fetch_multicast.py   # 组播源抓取
├── isp_checker.py       # ISP 运营商识别
├── config/
│   ├── config.py        # 核心配置（检测阈值、来源、排序模式等）
│   ├── settings.json    # Web 面板设置（密码、定时任务）
│   ├── alias.txt        # 频道别名映射
│   └── demo.txt         # 频道模板（频道名 + 分类）
├── web/
│   ├── server.py        # Flask Web 面板
│   └── index.html       # 前端页面
├── output/              # 输出目录（live.m3u / live.txt 等）
├── cache/               # 缓存目录
├── Dockerfile
├── docker-compose.yml
└── requirements.txt
```

## 输出文件

| 文件 | 说明 |
|------|------|
| `output/live.m3u` | 全局直播源（M3U 格式） |
| `output/live.txt` | 全局直播源（TXT 格式） |
| `output/{运营商缩写}_live.m3u` | 运营商分类源（`enable_isp_split = True` 时生成） |

## 配置

检测参数、来源优先级、排序模式等详见 `config/CONFIG_GUIDE.md`。

配置项集中在 `config/config.py`，主要包含：

- `source_urls` — 订阅源地址列表
- `hotel_config` / `multicast_config` — 酒店源、组播源开关与 API 地址
- `sort_mode` — 排序模式：`speed` / `quality` / `balanced`
- `enable_isp_split` — 是否生成运营商分类文件
- `enable_quality_check` — 是否启用质量检测（关闭则直接输出）

## Web 面板

```bash
python web/server.py
```

访问 http://localhost:5077，首次使用需在设置中创建密码。

| 接口 | 方法 | 说明 |
|------|------|------|
| `/api/run` | POST | 启动聚合任务 |
| `/api/stop` | POST | 停止任务 |
| `/api/status` | GET | 查询运行状态 |
| `/api/channels` | GET | 获取频道列表 |
| `/api/output` | GET | 获取输出文件列表 |
| `/api/logs` | GET | 获取日志 |
| `/api/config` | GET | 获取配置 |
| `/api/config/save` | POST | 保存配置 |
| `/api/settings` | GET | 获取面板设置 |
| `/api/settings/password` | POST | 修改密码 |
| `/api/settings/schedule` | POST | 设置定时任务 |
| `/api/file/<filename>` | GET/POST | 查看/保存文件 |
| `/output/<filename>` | GET | 下载输出文件 |
