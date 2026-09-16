#!/usr/bin/env bash
# TG 监控 Bot 启动脚本
# 用法: ./run.sh [start|stop|restart|status|logs|reload]
set -euo pipefail

cd "$(dirname "$0")"
PY=.venv/bin/python
PIDFILE=.bot.pid
LOGFILE=logs/bot.log

mkdir -p logs

start() {
    if [ -f "$PIDFILE" ] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null; then
        echo "Bot 已在运行 (PID $(cat "$PIDFILE"))"
        exit 0
    fi
    nohup "$PY" -m src.main >> "$LOGFILE" 2>&1 &
    echo $! > "$PIDFILE"
    echo "Bot 已启动 (PID $(cat "$PIDFILE"))，日志: $LOGFILE"
}

stop() {
    if [ -f "$PIDFILE" ] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null; then
        kill "$(cat "$PIDFILE")"
        rm -f "$PIDFILE"
        echo "Bot 已停止"
    else
        echo "Bot 未在运行"
    fi
}

case "${1:-start}" in
    start)   start ;;
    stop)    stop ;;
    restart) stop; sleep 1; start ;;
    status)  if [ -f "$PIDFILE" ] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null; then
                 echo "运行中 (PID $(cat "$PIDFILE"))"
             else
                 echo "未运行"
             fi ;;
    logs)    tail -f "$LOGFILE" ;;
    reload)  kill -HUP "$(cat "$PIDFILE")" 2>/dev/null && echo "已触发热重载" || echo "Bot 未运行" ;;
    *)       echo "用法: $0 [start|stop|restart|status|logs|reload]" ;;
esac
