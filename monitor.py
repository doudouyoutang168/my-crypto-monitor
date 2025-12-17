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

# 💡 小白提示：如果环境变量不生效，可以暂时在这里直接填入字符串，例如 TOKEN = "12345:xxxx"
TOKEN = os.environ.get("TG_BOT_TOKEN")
CHAT_ID = os.environ.get("TG_CHAT_ID")
HISTORY_FILE = 'history.json'
ALERT_THRESHOLD = 5.0  

# ================== 核心数据逻辑 ==================

def format_msg(pair, title_prefix="数据报告", is_alert=False):
    """
    统一的消息格式化工具，修复了你代码中缺失的部分
    """
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
    """
    智能寻池逻辑
    """
    headers = {'User-Agent': 'Mozilla/5.0'}
    input_address = input_address.strip()
    
    # 路径 A：指定链查询
    if chain_id:
        pair_url = f"https://api.dexscreener.com/latest/dex/pairs/{chain_id}/{input_address}"
        try:
            res = requests.get(pair_url, timeout=10).json()
            if res.get('pairs'): return res['pairs'][0]
        except: pass

    # 路径 B：全网代币地址查询
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

# ================== 交互模式 (手动查询) ==================

async def handle_query(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text: return
    
    input_text = update.message.text.strip().split()
    print(f"📩 收到消息: {update.message.text}") # 在黑窗口打印收到的消息

    if len(input_text) == 1:
        addr = input_text[0]
        chain = "solana"  # 默认链
    elif len(input_text) == 2:
        chain = input_text[0].lower()
        addr = input_text[1]
    else: return

    if len(addr) < 30: return
    
    msg_status = await update.message.reply_text(f"🔍 正在检索 {chain.upper()} 链数据...")
    
    # 尝试通过 get_token_data 获取（包含自动识别代币和池子）
    pair = get_token_data(addr, chain)
    
    if pair:
        print(f"✅ 查询成功: {addr}")
        await msg_status.edit_text(format_msg(pair, "手动查询"), parse_mode='HTML', disable_web_page_preview=True)
    else:
        print(f"❌ 查询失败: {addr}")
        await msg_status.edit_text("❌ 检索失败。请检查地址是否正确，或尝试发送: <code>链名 地址</code>")

# ================== 定时模式 (自动化任务) ==================

def run_cron_job():
    if not TOKEN or not CHAT_ID: 
        print("❌ 错误: 缺少 TOKEN 或 CHAT_ID 环境变量")
        return
    
    history = {}
    if os.path.exists(HISTORY_FILE):
        try:
            with open(HISTORY_FILE, 'r') as f: history = json.load(f)
        except: history = {}
    
    new_history = {}
    for name, (chain, addr) in POOLS.items():
        print(f"📊 正在执行日报: {name}")
        pair = get_token_data(addr, chain)
        if not pair: continue
        
        curr_price = float(pair.get('priceUsd', 0))
        last_record = history.get(name, {})
        last_alert_price = last_record.get('last_alert_price', curr_price)
        diff_pct = ((curr_price - last_alert_price) / last_alert_price * 100) if last_alert_price > 0 else 0

        # 发送日报和警报
        try:
            if abs(diff_pct) >= ALERT_THRESHOLD:
                requests.post(f"https://api.telegram.org/bot{TOKEN}/sendMessage", 
                             data={"chat_id": CHAT_ID, "text": format_msg(pair, f"波动提醒({diff_pct:.1f}%)", True), "parse_mode": "HTML"})
            
            requests.post(f"https://api.telegram.org/bot{TOKEN}/sendMessage", 
                         data={"chat_id": CHAT_ID, "text": format_msg(pair, "定时监控"), "parse_mode": "HTML"})
        except Exception as e:
            print(f"⚠️ 发送失败: {e}")
        
        new_history[name] = {"last_alert_price": last_alert_price, "last_price": curr_price}
        time.sleep(1)
        
    with open(HISTORY_FILE, 'w') as f: json.dump(new_history, f)
    print("✅ 日报任务完成")

if __name__ == "__main__":
    import sys
    # 检查命令行参数
    if len(sys.argv) > 1 and sys.argv[1] == "--cron":
        run_cron_job()
    else:
        if not TOKEN:
            print("❌ 错误: 未设置 TG_BOT_TOKEN 环境变量")
        else:
            print("🤖 机器人启动中...")
            app = Application.builder().token(TOKEN).build()
            app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_query))
            print("🚀 机器人运行中... 请在 Telegram 发送合约地址")
            app.run_polling()
