"""listener_panel 包：TG 监听面板（监听 + Web 侧边栏控制）。"""
from __future__ import annotations

import threading

# 监听器运行状态（由 start.py 启动后置 True）
MONITOR_RUNNING = False
MONITOR_LOCK = threading.Lock()