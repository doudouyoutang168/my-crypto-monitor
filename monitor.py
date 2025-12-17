import os
import requests
import json
import time
from datetime import datetime, timezone, timedelta
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

# ================== 核心配置区域 ==================
POOLS = {
    'LAF': ('bsc', '0x3bec20ca77e100c50ef0d0066f4c2b348e615f48'),
    'RAIL': ('ethereum', '0xe76c6c83af64e4c60245d8c7de953df673a7a33d'),
    'SOSD': ('solana', '9BJWrL5cP3AXSq42d2QxB71ywmadyTgYJFJoWFbaDp6Z'),
}

TOKEN = os.environ.get("TG_BOT_TOKEN")
CHAT_ID = os.environ.get("TG_CHAT_ID")
HISTORY_FILE = 'history.json'
ALERT_THRESHOLD = 5.0  

# ================== 核心数据逻辑 ==================

def format_msg(pair, title_prefix="数据报告", is_alert=False):
    try:
        price = float(pair.get('priceUsd', 0))
        mcap = pair.get('marketCap') or pair.get('fdv', 0)
        change = pair.get('priceChange', {}).get('h24', 0)
        liquidity = float(pair.get('liquidity', {}).get('usd', 0)) / 2
        lp_link = f"https://dexscreener.com/{pair.get('chainId')}/{pair.get('pairAddress')}"
        symbol = pair.get('baseToken', {}).get('symbol', '未知')
        
        emoji = "🚨" if is_alert else "🔔"
        return (
            f"{emoji} <b>{title_prefix} | {symbol}</b>\n"
            f"网络: {pair.get('chainId').upper()}\n\n"
            f"💰 价格: <code>${price:.10f}</code>\n"
            f"📊 市值: <code>${mcap:,.0f}</code>\n"
            f"📈 24H: <b>{'+' if change>=0 else ''}{change}%</b>\n"
            f"💧 底池: <code>${liquidity:,.0f}</code> (单边)\n"
            f"------------------------------------\n"
            f"🔗 <a href='{lp_link}'>点击实时看盘</a>"
        )
    except Exception as e:
        return f"⚠️ 格式化消息失败: {e}"

def get_token_data(input_address, chain_id=None):
    headers = {'User-Agent': 'Mozilla/5.0'}
    input_address = input_address.strip()
    
    if chain_id:
        pair_url = f"https://api.dexscreener.com/latest/dex/pairs/{chain_id}/{input_address}"
        try:
            res = requests.get(pair_url, timeout=10).json()
            if res.get('pairs'): return res['pairs'][0]
        except: pass

    token_url = f"https://api.dexscreener.com/latest/dex/tokens/{input_address}"
    try:
        res = requests.get(token_url, timeout=10).json()
        pairs = res.get('pairs')
        if pairs:
            valid = [p for p in pairs if p.get('chainId') == (chain_id.lower() if chain_id else p.get('chainId'))]
            if valid:
                return max(valid, key=lambda x: float(x.get('liquidity', {}).get('usd', 0)))
    except: pass

    return None

async def handle_query(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text: return
    input_text = update.message.text.strip().split()
    print(f"📩 收到消息: {update.message.text}") 

    if len(input_text) == 1:
        addr = input_text[0]
        chain = "solana"  
    elif len(input_text) == 2:
        chain = input_text[0].lower()
        addr = input_text[1]
    else: return

    if len(addr) < 30: return
    
    msg_status = await update.message.reply_text(f"🔍 正在检索 {chain.upper()} 链数据...")
    pair = get_token_data(addr, chain)
    
    if pair:
        await msg_status.edit_text(format_msg(pair, "手动查询"), parse_mode='HTML', disable_web_page_preview=True)
    else:
        await msg_status.edit_text("❌ 检索失败。请检查地址。")

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

        try:
            if abs(diff_pct) >= ALERT_THRESHOLD:
                requests.post(f"https://api.telegram.org/bot{TOKEN}/sendMessage", 
                             data={"chat_id": CHAT_ID, "text": format_msg(pair, f"波动提醒({diff_pct:.1f}%)", True), "parse_mode": "HTML"})
            requests.post(f"https://api.telegram.org/bot{TOKEN}/sendMessage", 
                         data={"chat_id": CHAT_ID, "text": format_msg(pair, "定时监控"), "parse_mode": "HTML"})
        except: pass
        
        new_history[name] = {"last_alert_price": last_alert_price, "last_price": curr_price}
    with open(HISTORY_FILE, 'w') as f: json.dump(new_history, f)

if __name__ == "__main__":
    import sys
    # 💡 端口请根据你的 Clash 设置进行微调
    CLASH_PROXY = "http://127.0.0.1:56423" 

    if len(sys.argv) > 1 and sys.argv[1] == "--cron":
        run_cron_job()
    else:
        if not TOKEN:
            print("❌ 错误: 未设置环境变量")
        else:
            print(f"🤖 机器人启动中... (使用代理: {CLASH_PROXY})")
            
            # ✅ 适配 V20+ 版本的 API
            app = Application.builder() \
                .token(TOKEN) \
                .proxy(CLASH_PROXY) \
                .get_updates_proxy(CLASH_PROXY) \
                .build()
            
            app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_query))
            print("🚀 机器人已连接！请在 Telegram 发合约查询")
            app.run_polling()
