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
# Remplace par ton Token Telegram (celui de BotFather)
TELEGRAM_TOKEN = "7948324469:AAFzydmSMfMy3_Y6C71apcsGZHFzX_FLMmo"

# Noeuds Solana (Utilise Helius ou Quicknode en prod pour ne pas être bloqué)
SOLANA_WSS = "wss://api.mainnet-beta.solana.com"
SOLANA_RPC = "https://api.mainnet-beta.solana.com"
RAYDIUM_LP_V4 = "675k1q2AYp7saSygv22Ebxnux1qMxt2Uum9NiUJp3nAY"

# --- 🗄️ VARIABLES D'ÉTAT ---
bot_state = {
    "is_scanning": False,
    "anti_rug": True,
    "chat_id": None,
    "scanner_task": None
}

logging.basicConfig(level=logging.INFO)
bot = Bot(token=TELEGRAM_TOKEN)
dp = Dispatcher()

# ==========================================
# 📱 INTERFACE TELEGRAM (UI PRO)
# ==========================================

def get_main_menu():
    status = "🟢 ACTIF" if bot_state["is_scanning"] else "🔴 INACTIF"
    rug = "✅ ON" if bot_state["anti_rug"] else "❌ OFF (Degen)"
    
    builder = InlineKeyboardBuilder()
    if not bot_state["is_scanning"]:
        builder.row(types.InlineKeyboardButton(text="🚀 Lancer le Scan", callback_data="start_scan"))
    else:
        builder.row(types.InlineKeyboardButton(text="🛑 Stopper le Scan", callback_data="stop_scan"))
        
    builder.row(types.InlineKeyboardButton(text=f"🛡️ Anti-Rug : {rug}", callback_data="toggle_rug"))
    builder.row(types.InlineKeyboardButton(text="📈 Stats & Portefeuille", callback_data="show_stats"))
    return builder.as_markup(), status

@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    bot_state["chat_id"] = message.chat.id # Enregistre l'utilisateur
    markup, status = get_main_menu()
    await message.answer(
        "🤖 **RAYDIUM SNIPER BOT - V1.0**\n\n"
        f"**Statut du Scanner :** {status}\n\n"
        "Que veux-tu faire ?",
        reply_markup=markup,
        parse_mode=ParseMode.MARKDOWN
    )

@dp.callback_query(F.data == "toggle_rug")
async def toggle_rug(callback: types.CallbackQuery):
    bot_state["anti_rug"] = not bot_state["anti_rug"]
    markup, status = get_main_menu()
    await callback.message.edit_text(
        "🤖 **RAYDIUM SNIPER BOT - V1.0**\n\n"
        f"**Statut du Scanner :** {status}\n"
        "*(Paramètres mis à jour !)*",
        reply_markup=markup,
        parse_mode=ParseMode.MARKDOWN
    )

@dp.callback_query(F.data == "start_scan")
async def start_scan(callback: types.CallbackQuery):
    if bot_state["is_scanning"]:
        return
    bot_state["is_scanning"] = True
    bot_state["chat_id"] = callback.message.chat.id
    bot_state["scanner_task"] = asyncio.create_task(solana_scanner_loop())
    
    markup, status = get_main_menu()
    await callback.message.edit_text(
        "⚡ **SCAN DÉMARRÉ !**\n\nJe surveille la blockchain en temps réel...",
        reply_markup=markup,
        parse_mode=ParseMode.MARKDOWN
    )

@dp.callback_query(F.data == "stop_scan")
async def stop_scan(callback: types.CallbackQuery):
    bot_state["is_scanning"] = False
    if bot_state["scanner_task"]:
        bot_state["scanner_task"].cancel()
        
    markup, status = get_main_menu()
    await callback.message.edit_text(
        "🛑 **SCAN ARRÊTÉ.**\n\nÀ bientôt pour chasser de nouveaux memecoins.",
        reply_markup=markup,
        parse_mode=ParseMode.MARKDOWN
    )

@dp.callback_query(F.data == "show_stats")
async def show_stats(callback: types.CallbackQuery):
    await callback.answer("Fonctionnalité en cours de développement 🚧", show_alert=True)

# ==========================================
# 🔍 MOTEUR WEB3 (SCANNER SOLANA)
# ==========================================

async def fetch_transaction_details(signature):
    """Interroge le noeud RPC pour lire le contenu de la transaction et extraire le Mint."""
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "getTransaction",
        "params": [
            signature,
            {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 0}
        ]
    }
    async with aiohttp.ClientSession() as session:
        try:
            async with session.post(SOLANA_RPC, json=payload) as response:
                result = await response.json()
                if "result" in result and result["result"]:
                    # Logique simplifiée pour extraire le Token
                    # (Dans une tx Raydium, le nouveau token est souvent dans postTokenBalances)
                    meta = result["result"].get("meta", {})
                    balances = meta.get("postTokenBalances", [])
                    for balance in balances:
                        mint = balance.get("mint")
                        # On ignore le SOL (So1111...)
                        if mint and mint != "So11111111111111111111111111111111111111112":
                            return mint
        except Exception as e:
            logging.error(f"Erreur RPC: {e}")
    return None

async def solana_scanner_loop():
    """Se connecte au WSS de Solana et écoute les nouvelles pools."""
    while bot_state["is_scanning"]:
        try:
            async with websockets.connect(SOLANA_WSS) as websocket:
                subscribe_msg = {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "logsSubscribe",
                    "params": [
                        {"mentions": [RAYDIUM_LP_V4]},
                        {"commitment": "processed"} # Plus rapide que "finalized"
                    ]
                }
                await websocket.send(json.dumps(subscribe_msg))
                logging.info("🔌 Connecté au WebSocket Solana.")

                while bot_state["is_scanning"]:
                    response = await websocket.recv()
                    data = json.loads(response)
                    
                    if "method" in data and data["method"] == "logsNotification":
                        logs = data["params"]["result"]["value"]["logs"]
                        
                        # "initialize2" est l'instruction Raydium pour créer une LP
                        if any("initialize2" in log for log in logs):
                            signature = data["params"]["result"]["value"]["signature"]
                            logging.info(f"✨ Nouvelle Pool ! Signature: {signature}")
                            
                            # On attend 2 secondes pour que le RPC indexe la transaction
                            await asyncio.sleep(2) 
                            mint_address = await fetch_transaction_details(signature)
                            
                            if mint_address and bot_state["chat_id"]:
                                await send_alert(mint_address, signature)

        except Exception as e:
            logging.error(f"Erreur WSS: {e}. Reconnexion dans 5s...")
            await asyncio.sleep(5)

async def send_alert(mint_address, signature):
    """Génère le message et les boutons, puis l'envoie sur Telegram."""
    dex_link = f"https://dexscreener.com/solana/{mint_address}"
    axiom_link = f"https://axiom.xyz/token/{mint_address}"
    rugcheck_link = f"https://rugcheck.xyz/tokens/{mint_address}"
    
    msg = (
        "🚨 **NOUVELLE POOL RAYDIUM DÉTECTÉE** 🚨\n\n"
        f"📝 **Token Mint :** `{mint_address}`\n"
        f"🛡️ **Filtre Anti-Rug :** {'✅ ACTIF' if bot_state['anti_rug'] else '❌ INACTIF'}\n\n"
        "⚡ *Analyse rapide recommandée avant d'Ape in !*"
    )
    
    builder = InlineKeyboardBuilder()
    builder.row(types.InlineKeyboardButton(text="📈 Dexscreener", url=dex_link))
    builder.row(types.InlineKeyboardButton(text="🔬 Axiom", url=axiom_link))
    builder.row(types.InlineKeyboardButton(text="🛡️ RugCheck", url=rugcheck_link))
    
    try:
        await bot.send_message(bot_state["chat_id"], msg, reply_markup=builder.as_markup(), parse_mode=ParseMode.MARKDOWN)
    except Exception as e:
        logging.error(f"Erreur Telegram: {e}")

# ==========================================
# 🚀 DÉMARRAGE DU BOT
# ==========================================
async def main():
    print("🤖 Bot en cours de démarrage...")
    # Efface les messages en attente (webhook/polling conflicts)
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("Bot arrêté.")