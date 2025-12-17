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
    """
    双重检索逻辑：
    1. 先尝试当做【代币地址】检索 (tokens 接口)
    2. 如果失败，尝试当做【池子地址】检索 (pairs 接口)
    """
    headers = {'User-Agent': 'Mozilla/5.0'}
    
    # --- 第一步：尝试作为“代币合约”查询 ---
    token_url = f"https://api.dexscreener.com/latest/dex/tokens/{input_address}"
    try:
        res = requests.get(token_url, timeout=15).json()
        pairs = res.get('pairs')
        if pairs:
            # 如果指定了链则过滤，否则取全球流动性最大池
            valid_pairs = [p for p in pairs if p.get('chainId') == chain_id.lower()] if chain_id else pairs
            if valid_pairs:
                return max(valid_pairs, key=lambda x: float(x.get('liquidity', {}).get('usd', 0)))
    except:
        pass

    # --- 第二步：如果上面没搜到，尝试作为“池子地址”查询 ---
    # 这步是关键！很多搜不到的情况是因为地址其实是 Pair 地址
    # 如果手动指定了链，构造特定的 pairs 接口 URL
    if chain_id:
        pair_url = f"https://api.dexscreener.com/latest/dex/pairs/{chain_id}/{input_address}"
        try:
            res = requests.get(pair_url, timeout=15).json()
            if res.get('pairs'):
                return res['pairs'][0]
        except:
            pass
            
    return None

# ================== 交互模式 (手动查询) ==================

async def handle_query(update: Update, context: ContextTypes.DEFAULT_TYPE):
    input_text = update.message.text.strip().split()
    
    # 逻辑：如果只发地址，全网搜；如果发 "bsc 地址"，指定搜
    if len(input_text) == 1:
        addr = input_text[0]
        chain = None
    elif len(input_text) == 2:
        chain = input_text[0].lower()
        addr = input_text[1]
    else:
        return

    if len(addr) < 30: return
    
    msg_status = await update.message.reply_text(f"⚡ 正在检索 {'全局' if not chain else chain} 数据...")
    
    # 调用我们之前的函数
    pair = get_token_data(addr, chain)
    
    if pair:
        await msg_status.edit_text(format_msg(pair, "手动查询"), parse_mode='HTML', disable_web_page_preview=True)
    else:
        await msg_status.edit_text(f"❌ 未找到池子。\n提示：如果是新币，请尝试发送：\n<code>bsc {addr}</code>")

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
