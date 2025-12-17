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
    'LAF': ('bsc', '0x541b525b69210bc349c7d94ea6f10e202a6f90fa'),
    'RAIL': ('ethereum', '0xe76c6c83af64e4c60245d8c7de953df673a7a33d'),
    'SOSD': ('solana', '9BJWrL5cP3AXSq42d2QxB71ywmadyTgYJFJoWFbaDp6Z'),
}

TOKEN = os.environ.get("TG_BOT_TOKEN")
CHAT_ID = os.environ.get("TG_CHAT_ID")
HISTORY_FILE = 'history.json'
ALERT_THRESHOLD = 5.0  # 波动达到 5% 时才触发特别提醒

# ================== 核心数据逻辑 ==================

def get_token_data(token_address, chain_id=None):
    url = f"https://api.dexscreener.com/latest/dex/tokens/{token_address}"
    try:
        res = requests.get(url, timeout=15).json()
        pairs = res.get('pairs')
        if not pairs: return None
        # 如果指定了链则过滤，否则自动找全球流动性最大的池子
        valid_pairs = [p for p in pairs if p.get('chainId') == chain_id.lower()] if chain_id else pairs
        if not valid_pairs: return None
        return max(valid_pairs, key=lambda x: float(x.get('liquidity', {}).get('usd', 0)))
    except: return None

def format_msg(pair, title_prefix="查询结果", is_alert=False):
    price = float(pair.get('priceUsd', 0))
    mcap = pair.get('marketCap') or pair.get('fdv', 0)
    change = pair.get('priceChange', {}).get('h24', 0)
    liquidity = float(pair.get('liquidity', {}).get('usd', 0)) / 2
    lp_link = f"https://dexscreener.com/{pair.get('chainId')}/{pair.get('pairAddress')}"
    emoji = "🔔" if not is_alert else "🚨"
    return (
        f"{emoji} <b>{title_prefix} | {pair.get('baseToken', {}).get('symbol')}</b>\n"
        f"网络: {pair.get('chainId').upper()} ({pair.get('dexId').upper()})\n\n"
        f"💰 价格: <code>${price:.10f}</code>\n"
        f"📊 市值: <code>${mcap:,.0f}</code>\n"
        f"📈 24H: <b>{'+' if change>=0 else ''}{change}%</b>\n"
        f"💧 底池: <code>${liquidity:,.0f}</code> (单边)\n"
        f"------------------------------------\n"
        f"🔗 <a href='{lp_link}'>点击实时看盘</a>"
    )

# ================== 交互模式 (手动查询) ==================

async def handle_query(update: Update, context: ContextTypes.DEFAULT_TYPE):
    addr = update.message.text.strip()
    if len(addr) < 30: return
    msg_status = await update.message.reply_text("⚡ 正在从链上检索数据...")
    pair = get_token_data(addr)
    if pair:
        await msg_status.edit_text(format_msg(pair, "手动查询"), parse_mode='HTML', disable_web_page_preview=True)
    else:
        await msg_status.edit_text("❌ 未找到有效池子。")

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
