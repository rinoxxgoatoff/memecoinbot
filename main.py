import os
import asyncio
import json
import logging
import aiohttp
import websockets
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.enums import ParseMode

# --- ⚙️ CONFIGURATION ---
TELEGRAM_TOKEN = "7948324469:AAFzydmSMfMy3_Y6C71apcsGZHFzX_FLMmo"
HELIUS_API_KEY = "e03f5eb6-c27f-42fe-94e8-d08dbe5a0694"

SOLANA_WSS = f"wss://mainnet.helius-rpc.com/?api-key={HELIUS_API_KEY}"
SOLANA_RPC = f"https://mainnet.helius-rpc.com/?api-key={HELIUS_API_KEY}"
RAYDIUM_LP_V4 = "675k1q2AYp7saSygv22Ebxnux1qMxt2Uum9NiUJp3nAY"

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

bot = Bot(token=TELEGRAM_TOKEN)
dp = Dispatcher()

bot_state = {
    "is_scanning": False,
    "anti_rug": True,
    "chat_id": None,
    "scanner_task": None
}

# --- 📱 INTERFACE ---

def get_main_menu():
    status = "🟢 SCAN EN COURS" if bot_state["is_scanning"] else "🔴 EN PAUSE"
    rug = "✅ ON" if bot_state["anti_rug"] else "❌ OFF"
    builder = InlineKeyboardBuilder()
    btn_text = "🛑 Stopper le Scanner" if bot_state["is_scanning"] else "🚀 Démarrer le Scanner"
    btn_callback = "stop_scan" if bot_state["is_scanning"] else "start_scan"
    
    builder.row(types.InlineKeyboardButton(text=btn_text, callback_data=btn_callback))
    builder.row(types.InlineKeyboardButton(text=f"🛡️ Anti-Rug : {rug}", callback_data="toggle_rug"))
    return builder.as_markup(), status

@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    bot_state["chat_id"] = message.chat.id
    markup, status = get_main_menu()
    await message.answer(f"⚡ **RAYDIUM SNIPER PRO**\n\n**Statut :** {status}", reply_markup=markup, parse_mode=ParseMode.MARKDOWN)

@dp.callback_query(F.data == "toggle_rug")
async def toggle_rug(callback: types.CallbackQuery):
    bot_state["anti_rug"] = not bot_state["anti_rug"]
    markup, status = get_main_menu()
    await callback.message.edit_reply_markup(reply_markup=markup)

@dp.callback_query(F.data == "start_scan")
async def start_scan(callback: types.CallbackQuery):
    if not bot_state["is_scanning"]:
        bot_state["is_scanning"] = True
        bot_state["scanner_task"] = asyncio.create_task(solana_scanner_loop())
        markup, status = get_main_menu()
        await callback.message.edit_text(f"🟢 **SCANNER ACTIVÉ**\nÉcoute de la blockchain...", reply_markup=markup, parse_mode=ParseMode.MARKDOWN)

@dp.callback_query(F.data == "stop_scan")
async def stop_scan(callback: types.CallbackQuery):
    bot_state["is_scanning"] = False
    if bot_state["scanner_task"]:
        bot_state["scanner_task"].cancel()
    markup, status = get_main_menu()
    await callback.message.edit_text(f"🔴 **SCANNER ARRÊTÉ**", reply_markup=markup, parse_mode=ParseMode.MARKDOWN)

# --- 🔍 MOTEUR DE DÉTECTION ---

async def fetch_transaction_details(signature):
    payload = {
        "jsonrpc": "2.0", "id": 1, "method": "getTransaction",
        "params": [signature, {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 0}]
    }
    async with aiohttp.ClientSession() as session:
        try:
            async with session.post(SOLANA_RPC, json=payload) as response:
                res = await response.json()
                if "result" in res and res["result"]:
                    # On cherche le token qui n'est pas du SOL dans les balances finales
                    balances = res["result"].get("meta", {}).get("postTokenBalances", [])
                    for b in balances:
                        mint = b.get("mint")
                        if mint and mint != "So11111111111111111111111111111111111111112":
                            return mint
        except Exception as e:
            logging.error(f"Erreur RPC fetch: {e}")
    return None

async def solana_scanner_loop():
    while bot_state["is_scanning"]:
        try:
            async with websockets.connect(SOLANA_WSS, ping_interval=20) as websocket:
                sub = {
                    "jsonrpc": "2.0", "id": 1, "method": "logsSubscribe",
                    "params": [{"mentions": [RAYDIUM_LP_V4]}, {"commitment": "processed"}]
                }
                await websocket.send(json.dumps(sub))
                logging.info("🔌 Connecté au flux Helius...")

                while bot_state["is_scanning"]:
                    msg = await websocket.recv()
                    data = json.loads(msg)
                    
                    if "params" in data:
                        logs = data["params"]["result"]["value"]["logs"]
                        signature = data["params"]["result"]["value"]["signature"]
                        
                        # Détection plus flexible des nouvelles pools
                        if any("initialize2" in l.lower() or "initinstruction" in l.lower() for l in logs):
                            logging.info(f"✨ Pool potentielle détectée ! Signature: {signature}")
                            
                            # On attend que la transaction soit bien enregistrée
                            await asyncio.sleep(2.5) 
                            mint = await fetch_transaction_details(signature)
                            
                            if mint:
                                await send_alert(mint, signature)
                            else:
                                logging.warning("Impossible d'extraire le Mint de la transaction.")

        except Exception as e:
            logging.error(f"Erreurs WSS: {e}. Reconnexion...")
            await asyncio.sleep(5)

async def send_alert(mint, signature):
    msg = (
        "🚨 **NOUVELLE POOL RAYDIUM** 🚨\n\n"
        f"📝 **Token:** `{mint}`\n"
        f"🛡️ **Filtre:** {'✅ Anti-Rug' if bot_state['anti_rug'] else '❌ Degen'}\n"
    )
    builder = InlineKeyboardBuilder()
    builder.row(types.InlineKeyboardButton(text="📈 Dexscreener", url=f"https://dexscreener.com/solana/{mint}"))
    builder.row(types.InlineKeyboardButton(text="🔬 RugCheck", url=f"https://rugcheck.xyz/tokens/{mint}"))
    
    if bot_state["chat_id"]:
        await bot.send_message(bot_state["chat_id"], msg, reply_markup=builder.as_markup(), parse_mode=ParseMode.MARKDOWN)

async def main():
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
