import os
import requests
import json
import time
from datetime import datetime, timezone, timedelta
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

# ================== 核心配置区域 ==================
# 这里填入你想每天收日报的币种。格式：'显示名字': ('链ID', '代币合约')
POOLS = {
    'LAF': ('bsc', '0x3bec20ca77e100c50ef0d0066f4c2b348e615f48'),
    'RAIL': ('ethereum', '0xe76c6c83af64e4c60245d8c7de953df673a7a33d'),
    'SOSD': ('solana', '9BJWrL5cP3AXSq42d2QxB71ywmadyTgYJFJoWFbaDp6Z'),
}

TOKEN = os.environ.get("TG_BOT_TOKEN")
CHAT_ID = os.environ.get("TG_CHAT_ID")
HISTORY_FILE = 'history.json'
ALERT_THRESHOLD = 5.0  # 波动达到 5% 时才触发特别提醒

# ================== 核心数据逻辑 ==================

def get_token_data(input_address, chain_id=None):
    headers = {'User-Agent': 'Mozilla/5.0'}
    input_address = input_address.strip()
    
    # 路径 A：如果你手动指定了链（例如发送：bsc 0x...）
    if chain_id:
        # 尝试 Pairs 接口（最精准）
        pair_url = f"https://api.dexscreener.com/latest/dex/pairs/{chain_id}/{input_address}"
        try:
            res = requests.get(pair_url, timeout=10).json()
            if res.get('pairs'): return res['pairs'][0]
        except: pass

    # 路径 B：尝试 Token 接口（全网搜索）
    token_url = f"https://api.dexscreener.com/latest/dex/tokens/{input_address}"
    try:
        res = requests.get(token_url, timeout=10).json()
        pairs = res.get('pairs')
        if pairs:
            # 如果指定了链则过滤，否则取流动性最大的
            valid = [p for p in pairs if p.get('chainId') == chain_id.lower()] if chain_id else pairs
            if valid:
                return max(valid, key=lambda x: float(x.get('liquidity', {}).get('usd', 0)))
    except: pass

    # 路径 C：万能搜索（不指定链搜索 Pair 接口）
    # 有些地址其实是池子地址，通过这个接口能强制搜出来
    search_url = f"https://api.dexscreener.com/latest/dex/search/?q={input_address}"
    try:
        res = requests.get(search_url, timeout=10).json()
        pairs = res.get('pairs')
        if pairs:
            valid = [p for p in pairs if p.get('chainId') == chain_id.lower()] if chain_id else pairs
            if valid:
                return max(valid, key=lambda x: float(x.get('liquidity', {}).get('usd', 0)))
    except: pass

    return None

# ================== 交互模式 (手动查询) ==================

async def handle_query(update: Update, context: ContextTypes.DEFAULT_TYPE):
    input_text = update.message.text.strip().split()
    
    # 支持两种格式：
    # 1. 直接发地址：6vrUSDsW...
    # 2. 链+地址：solana 6vrUSDsW...
    if len(input_text) == 1:
        addr = input_text[0]
        chain = "solana" # 如果你大部分查的是索拉纳，可以默认设为 solana
    elif len(input_text) == 2:
        chain = input_text[0].lower()
        addr = input_text[1]
    else:
        return

    if len(addr) < 30: return
    
    msg_status = await update.message.reply_text(f"🔍 正在精准穿透检索 {chain} 链数据...")
    
    # 核心变动：直接拼凑 Pairs 接口 URL，跳过 Tokens 接口
    url = f"https://api.dexscreener.com/latest/dex/pairs/{chain}/{addr}"
    
    try:
        headers = {'User-Agent': 'Mozilla/5.0'}
        res = requests.get(url, headers=headers, timeout=10).json()
        pair = None
        
        # 如果直接查到了（说明你发的是池子地址）
        if res.get('pairs'):
            pair = res['pairs'][0]
        else:
            # 如果查不到，再降级去搜一次 Tokens 接口（说明你发的是代币地址）
            token_url = f"https://api.dexscreener.com/latest/dex/tokens/{addr}"
            res_token = requests.get(token_url, headers=headers, timeout=10).json()
            if res_token.get('pairs'):
                # 过滤出对应链并取流动性最高的
                v_pairs = [p for p in res_token['pairs'] if p.get('chainId') == chain]
                if v_pairs:
                    pair = max(v_pairs, key=lambda x: float(x.get('liquidity', {}).get('usd', 0)))

        if pair:
            await msg_status.edit_text(format_msg(pair, "精准查询"), parse_mode='HTML', disable_web_page_preview=True)
        else:
            await msg_status.edit_text("❌ 检索失败。请检查地址是否正确，或者尝试加上链名发送（如：solana 地址）。")
            
    except Exception as e:
        await msg_status.edit_text(f"⚠️ 系统错误: {str(e)}")
# ================== 定时模式 (自动化任务) ==================

def run_cron_job():
    if not TOKEN or not CHAT_ID: return
    history = {}
    if os.path.exists(HISTORY_FILE):
        try:
            with open(HISTORY_FILE, 'r') as f: history = json.load(f)
        except: history = {}
    
    new_history = {}
    for name, (chain, addr) in POOLS.items():
        pair = get_token_data(addr, chain)
        if not pair: continue
        curr_price = float(pair.get('priceUsd', 0))
        last_record = history.get(name, {})
        last_alert_price = last_record.get('last_alert_price', curr_price)
        diff_pct = ((curr_price - last_alert_price) / last_alert_price * 100) if last_alert_price > 0 else 0

        # 1. 波动警报
        if abs(diff_pct) >= ALERT_THRESHOLD:
            requests.post(f"https://api.telegram.org/bot{TOKEN}/sendMessage", 
                         data={"chat_id": CHAT_ID, "text": format_msg(pair, f"波动提醒({diff_pct:.1f}%)", True), "parse_mode": "HTML"})
            last_alert_price = curr_price
        
        # 2. 定时简报
        requests.post(f"https://api.telegram.org/bot{TOKEN}/sendMessage", 
                     data={"chat_id": CHAT_ID, "text": format_msg(pair, "定时监控"), "parse_mode": "HTML"})
        
        new_history[name] = {"value": curr_price, "last_alert_price": last_alert_price}
        time.sleep(1)
    with open(HISTORY_FILE, 'w') as f: json.dump(new_history, f)

if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "--cron":
        run_cron_job()
    else:
        if not TOKEN:
            print("请先在环境变量设置 TG_BOT_TOKEN")
        else:
            app = Application.builder().token(TOKEN).build()
            app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_query))
            print("机器人运行中... 请在 Telegram 发送合约地址")
            app.run_polling()
