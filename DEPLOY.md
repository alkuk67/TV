# IPTV Web Dashboard 部署说明

## 快速启动

`powershell
# 进入项目目录
cd D:\yuanl\Documents\GitHub\testtt

# 安装依赖
pip install -r requirements.txt

# 启动 Web 服务
python web/server.py
`

访问 http://localhost:5077

## 配置

编辑 config/config.py 修改参数

## 定时任务（可选）

使用 GitHub Actions 自动更新（已配置）

## API 接口

| 接口 | 方法 | 说明 |
|------|------|------|
| /api/run | POST | 启动任务 |
| /api/stop | POST | 停止任务 |
| /api/status | GET | 查询状态 |
| /api/logs | GET | 获取日志 |
| /api/logs/clear | POST | 清空日志 |
| /api/config | GET | 获取配置 |
| /api/config/save | POST | 保存配置 |
| /api/channels | GET | 获取频道列表 |
| /api/output | GET | 获取输出文件列表 |
| /api/output/download/<file> | GET | 下载文件 |
| /api/output/delete/<file> | DELETE | 删除文件 |

## 端口

默认端口：5077（在 web/server.py 中修改）
