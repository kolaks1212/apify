import os, io, json, time, logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, InputFile
from telegram.ext import (Application, CommandHandler, MessageHandler,
                          CallbackQueryHandler, ContextTypes, filters)
from apify_client import ApifyClientAsync

logging.basicConfig(level=logging.INFO)
TG_TOKEN = os.environ["TELEGRAM_TOKEN"]
APIFY_TOKEN = os.environ["APIFY_TOKEN"]
ALLOWED = os.environ.get("ALLOWED_USER_ID")      # ???????: ??? ??????
TIMEOUT = int(os.environ.get("ACTOR_TIMEOUT", "120"))  # ?????

# ???????? ????????? ?? ???? ??? ?? ?? Input ?? ????? ????? ????
ACTORS = [
    ("pamberton/cloudflare-bypass",       lambda u: {"url": u}),
    ("ecomscrape/cloudflare-web-scraper", lambda u: {"urls": [u]}),
    ("dtrungtin/cloudflare-web-scraper",  lambda u: {"startUrls": [{"url": u}]}),
    ("xtech/cloudflare-scraper-pro",      lambda u: {"startUrls": [{"url": u}]}),
    ("get_anything/web-unblocker",        lambda u: {"url": u}),
]
BLOCK_SIGNS = ["just a moment", "cf-chl", "attention required", "challenge-platform"]

apify = ApifyClientAsync(APIFY_TOKEN)

def allowed(update: Update) -> bool:
    return not ALLOWED or str(update.effective_user.id) == ALLOWED

async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if allowed(update):
        await update.message.reply_text("???? ?? ??????? ?? ?? ???????? Apify ??? ???.")

async def on_url(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not allowed(update):
        return
    url = update.message.text.strip()
    if not url.startswith(("http://", "https://")):
        await update.message.reply_text("???? ???? ?? http ?? https ???? ???.")
        return
    ctx.user_data["url"] = url
    kb = [[InlineKeyboardButton(f"{i+1}. {a[0]}", callback_data=f"run:{i}")]
          for i, a in enumerate(ACTORS)]
    kb.append([InlineKeyboardButton("?? ??? ??? (??????????)", callback_data="run:all")])
    await update.message.reply_text("???? ??????", reply_markup=InlineKeyboardMarkup(kb))

async def run_actor(idx: int, url: str, chat_id: int, ctx: ContextTypes.DEFAULT_TYPE):
    actor_id, build = ACTORS[idx]
    await ctx.bot.send_message(chat_id, f"? ????? {actor_id} ...")
    t0 = time.time()
    try:
        run = await apify.actor(actor_id).call(run_input=build(url), timeout_secs=TIMEOUT)
        if not run:
            raise RuntimeError("???? ???????? ?????????")
        items = (await apify.dataset(run["defaultDatasetId"]).list_items(limit=5)).items
        output = items
        if not items:  # ???? ??????? ????? ?? ?? key-value store ?????????
            rec = await apify.key_value_store(run["defaultKeyValueStoreId"]).get_record("OUTPUT")
            output = rec["value"] if rec else None
        text = json.dumps(output, ensure_ascii=False, indent=2, default=str)
        blocked = any(s in text.lower() for s in BLOCK_SIGNS)
        cost = run.get("usageTotalUsd")
        msg = (f"?? {actor_id}\n"
               f"?????: {run.get('status')}\n"
               f"????: {time.time()-t0:.0f}s\n"
               f"?????: {'$%.4f' % cost if cost is not None else '??????'}\n"
               f"????? ????: {len(items)}\n"
               f"?????: {'? ????? ????' if not output else '?? ???????? ???? ???' if blocked else '? ????? ????'}")
        await ctx.bot.send_message(chat_id, msg)
        if output:
            f = io.BytesIO(text.encode("utf-8"))
            await ctx.bot.send_document(chat_id, InputFile(f, filename=f"result_{idx+1}.json"))
    except Exception as e:
        await ctx.bot.send_message(chat_id, f"? {actor_id}\n???: {str(e)[:1500]}")

async def on_button(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    if not allowed(update):
        return
    url = ctx.user_data.get("url")
    if not url:
        await q.edit_message_text("??? ???? ???????.")
        return
    choice = q.data.split(":")[1]
    targets = range(len(ACTORS)) if choice == "all" else [int(choice)]
    for i in targets:
        await run_actor(i, url, q.message.chat_id, ctx)

def main():
    app = Application.builder().token(TG_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_url))
    app.add_handler(CallbackQueryHandler(on_button, pattern=r"^run:"))
    app.run_polling()

if __name__ == "__main__":
    main()
