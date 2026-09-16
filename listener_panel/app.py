"""监听面板 Flask 应用：侧边栏 Web UI + REST API（同步视图）。"""
from __future__ import annotations

import logging
from pathlib import Path

import yaml
from flask import Flask, jsonify, render_template, request

from .db import MonitorDB

BASE_DIR = Path(__file__).resolve().parent.parent
PANEL_DIR = Path(__file__).resolve().parent

logger = logging.getLogger("listener_panel")


def load_config() -> dict:
    return yaml.safe_load((PANEL_DIR / "config.yaml").read_text(encoding="utf-8")) or {}


CFG = load_config()
DB_PATH = (BASE_DIR / CFG.get("database", {}).get("path", "./data/monitor.db")).resolve()
db = MonitorDB(str(DB_PATH))

app = Flask(
    __name__,
    template_folder=str(PANEL_DIR / "templates"),
    static_folder=str(PANEL_DIR / "static"),
)


@app.before_request
def _guard():
    pwd = CFG.get("web", {}).get("panel_password", "")
    if pwd and request.path.startswith("/api") and request.headers.get("X-Panel-Key") != pwd:
        return jsonify({"ok": False, "error": "unauthorized"}), 401


@app.route("/")
def index():
    return render_template("index.html")


# ---------- 关键词 API ----------

@app.route("/api/keywords")
def api_list_keywords():
    return jsonify({"ok": True, "items": db.list_keywords()})


@app.route("/api/keywords", methods=["POST"])
def api_add_keyword():
    data = request.get_json(silent=True) or {}
    kw = (data.get("keyword") or "").strip()
    if not kw:
        return jsonify({"ok": False, "error": "关键词不能为空"}), 400
    ok = db.add_keyword(kw)
    if not ok:
        return jsonify({"ok": False, "error": "关键词已存在"}), 409
    return jsonify({"ok": True})


@app.route("/api/keywords/<keyword>", methods=["DELETE"])
def api_del_keyword(keyword: str):
    ok = db.remove_keyword(keyword)
    return jsonify({"ok": ok})


@app.route("/api/keywords/<keyword>/toggle", methods=["POST"])
def api_toggle_keyword(keyword: str):
    data = request.get_json(silent=True) or {}
    ok = db.set_enabled(keyword, bool(data.get("enabled", True)))
    return jsonify({"ok": ok})


# ---------- 命中记录 API ----------

@app.route("/api/hits")
def api_list_hits():
    limit = min(int(request.args.get("limit", 100)), 500)
    return jsonify({"ok": True, "items": db.list_hits(limit)})


@app.route("/api/stats")
def api_stats():
    return jsonify({"ok": True, **db.stats()})


# ---------- 配置 API ----------

@app.route("/api/config")
def api_get_config():
    cfg = {"invite_link": CFG.get("actions", {}).get("invite_link", "")}
    return jsonify({"ok": True, "config": cfg})


@app.route("/api/config", methods=["POST"])
def api_set_config():
    data = request.get_json(silent=True) or {}
    if "invite_link" in data:
        CFG.setdefault("actions", {})["invite_link"] = (data.get("invite_link") or "").strip()
        (PANEL_DIR / "config.yaml").write_text(
            yaml.safe_dump(CFG, allow_unicode=True, sort_keys=False), encoding="utf-8"
        )
    return jsonify({"ok": True})


@app.route("/api/health")
def api_health():
    from . import MONITOR_RUNNING
    return jsonify({"ok": True, "listening": bool(MONITOR_RUNNING)})


# 监听器运行状态标记（start.py 里写入）
MONITOR_RUNNING = False


def create_app() -> Flask:
    return app
