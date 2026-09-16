#!/usr/bin/env python3
"""后台脚本：等待 FloodWaitError 过期后加入剩余群组，完成后重启面板。

用法: nohup python3 scripts/join_remaining.py > logs/join_remaining.log 2>&1 &
"""
import asyncio, os, sys, time, json
from pathlib import Path
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")

import yaml
from telethon import TelegramClient
from telethon.tl.functions.channels import JoinChannelRequest

api_id  = int(os.getenv("TG_API_ID"))
api_hash = os.getenv("TG_API_HASH")
PANEL_DIR = BASE_DIR / "listener_panel"
CONFIG_FILE = PANEL_DIR / "config.yaml"

# 还没加入的群（去掉了已成功的 7 个）
REMAINING = """@SLLK01 @SLLK899 @YMNY4 @anhuiji @answertalk
@anwang019 @au0775 @bc009168 @beihai005 @cbiprr
@changzhou118 @chigua_187 @daili6f @dailitge @dny66666u
@dny_xinxijiaoliu @dubxdub @dwqdqwd232dcc @dxxb6 @dyzbdysh8888
@eb1gc @ecup78_LuLu156 @euejdjs @feiji_chat @feijituiguangyinliu
@guagua1120 @bdsm618 @RHFGchuhai @FLB003""".split()

WAIT_SECONDS = 25 * 60  # 等待 25 分钟 FloodWaitError 过期

def stop_panel():
    os.system("pkill -f 'python start.py' 2>/dev/null")
    time.sleep(2)

def start_panel():
    os.system(f"cd {BASE_DIR} && source .venv/bin/activate && nohup python start.py > /tmp/listener_panel.log 2>&1 &")

def update_config(new_chats):
    cfg = yaml.safe_load(CONFIG_FILE.read_text(encoding="utf-8"))
    existing = {(c["id"], c["name"]) for c in cfg["listen"]["target_chats"]}
    for gid, name in new_chats:
        if gid not in {c["id"] for c in cfg["listen"]["target_chats"]}:
            cfg["listen"]["target_chats"].append({"id": gid, "name": name})
    CONFIG_FILE.write_text(yaml.safe_dump(cfg, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return len(cfg["listen"]["target_chats"])

async def join_groups():
    client = TelegramClient(str(BASE_DIR / ".tg_user"), api_id, api_hash)
    await client.start()
    me = await client.get_me()
    print(f"[{time.strftime('%H:%M:%S')}] 账号: {me.first_name} id={me.id}")

    joined = []
    failed = []
    BATCH = 2
    for i in range(0, len(REMAINING), BATCH):
        batch = REMAINING[i:i+BATCH]
        for g in batch:
            try:
                ent = await client.get_entity(g)
                await client(JoinChannelRequest(ent.id))
                joined.append((ent.id, getattr(ent, "title", "") or g.lstrip("@")))
                print(f"[{time.strftime('%H:%M:%S')}] ✅ {g:24s} {getattr(ent,'title','')[:30]}")
            except Exception as e:
                ename = type(e).__name__
                failed.append((g, ename))
                wait = getattr(e, "seconds", None)
                print(f"[{time.strftime('%H:%M:%S')}] ❌ {g:24s} {ename}" + (f' (需等{wait}s)' if wait else ''))
            await asyncio.sleep(3)
        print(f"[{time.strftime('%H:%M:%S')}] batch done (成功{len(joined)} 失败{len(failed)})")
        await asyncio.sleep(8)
    return joined, failed

def main():
    print(f"[{time.strftime('%H:%M:%S')}] 等待 {WAIT_SECONDS//60} 分钟 FloodWaitError 过期...")
    time.sleep(WAIT_SECONDS)

    print(f"[{time.strftime('%H:%M:%S')}] 开始加入剩余群组...")
    stop_panel()

    joined, failed = asyncio.run(join_groups())

    if joined:
        total = update_config(joined)
        print(f"[{time.strftime('%H:%M:%S')}] config 已更新，共 {total} 个群")

    start_panel()
    print(f"[{time.strftime('%H:%M:%S')}] 面板已重启，监听 {7 + len(joined)} 个群")
    return 0

if __name__ == "__main__":
    sys.exit(main())
