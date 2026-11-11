import os
import logging
import asyncio
import aiohttp
from urllib.parse import urlparse
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)
from apify_client import ApifyClientAsync

# تنظیمات لاگ و مخفی کردن توکن تلگرام از لاگ‌های httpx
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.getenv("BOT_TOKEN")
APIFY_TOKEN = os.getenv("APIFY_TOKEN")

MAX_FILE_SIZE = 50 * 1024 * 1024  # 50 MB

# ۵ ابزار قدرتمند Apify
APIFY_TOOLS = {
    "tool_1": {
        "name": "1️⃣ Playwright Downloader (مرورگر کامل + دانلودر)",
        "actor_id": "apify/playwright-scraper",
    },
    "tool_2": {
        "name": "2️⃣ Puppeteer Downloader (عبور از کلودفلر + دانلود)",
        "actor_id": "apify/puppeteer-scraper",
    },
    "tool_3": {
        "name": "3️⃣ Website Content Crawler (مخصوص صفحات وب)",
        "actor_id": "apify/website-content-crawler",
    },
    "tool_4": {
        "name": "4️⃣ Cheerio Fast Fetcher (پروکسی چرخشی سریع)",
        "actor_id": "apify/cheerio-scraper",
    },
    "tool_5": {
        "name": "5️⃣ Web Scraper (اسکرپر عمومی کروم)",
        "actor_id": "apify/web-scraper",
    },
}


def get_browser_headers(url: str) -> dict:
    """ساخت هدرهای دقیق مرورگر به همراه Referer برای دور زدن خطای 403 معمولی و هات‌لینک"""
    parsed = urlparse(url)
    origin = f"{parsed.scheme}://{parsed.netloc}"
    return {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,video/mp4,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": origin + "/",
        "Origin": origin,
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "same-origin",
        "Upgrade-Insecure-Requests": "1",
    }


def get_actor_input(actor_id: str, target_url: str) -> dict:
    """تنظیم اسکریپت داخلی Apify برای دانلود مستقیم فایل داخل Key-Value Store در صورت برخورد با کلودفلر"""
    base_input = {
        "startUrls": [{"url": target_url}],
        "maxRequestsPerCrawl": 1,
        "maxRequestRetries": 3,
        "proxyConfiguration": {"useApifyProxy": True},
    }

    if actor_id == "apify/website-content-crawler":
        return base_input

    if actor_id in ["apify/playwright-scraper", "apify/puppeteer-scraper"]:
        # این اسکریپت با مرورگر واقعی وارد شده، کوکی کلودفلر را می‌گیرد، فایل را دانلود و در سرور Apify ذخیره می‌کند
        base_input["pageFunction"] = """
        async function pageFunction(context) {
            const { request, page, Apify } = context;
            const response = await page.goto(request.url, { waitUntil: 'load', timeout: 60000 });
            const buffer = await response.body();
            const contentType = response.headers()['content-type'] || 'application/octet-stream';
            
            if (Apify && Apify.setValue) {
                await Apify.setValue('DOWNLOADED_FILE', buffer, { contentType });
            }
            return {
                url: request.url,
                loadedUrl: response.url(),
                status: response.status(),
                contentType: contentType,
                size: buffer.length
            };
        }
        """
        return base_input

    if actor_id == "apify/cheerio-scraper":
        base_input["pageFunction"] = """
        async function pageFunction(context) {
            const { request, body, contentType, Apify } = context;
            if (Apify && Apify.setValue && body) {
                await Apify.setValue('DOWNLOADED_FILE', body, { contentType: contentType.type || 'application/octet-stream' });
            }
            return {
                url: request.url,
                loadedUrl: request.loadedUrl || request.url,
                title: 'Fetched via Cheerio'
            };
        }
        """
        return base_input

    # برای apify/web-scraper
    base_input["pageFunction"] = """
    async function pageFunction(context) {
        const { request } = context;
        return {
            url: request.url,
            loadedUrl: request.loadedUrl || request.url,
            title: document.title || 'Direct File'
        };
    }
    """
    return base_input


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    welcome_text = (
        "سلام! 👋\n"
        "من ربات دانلودر پیشرفته هستم.\n\n"
        "🔗 **قابلیت‌ها:**\n"
        "1. دانلود مستقیم فایل‌ها و ویدیوها (با شبیه‌ساز هدر مرورگر برای عبور از 403).\n"
        "2. تشخیص فایروال و آنتی‌بات **Cloudflare**.\n"
        "3. دانلود فایل از طریق ۵ ابزار قدرتمند **Apify** و ارسال مستقیم به تلگرام.\n\n"
        "لینک فایل یا ویدیو را ارسال کنید!"
    )
    await update.message.reply_text(welcome_text, parse_mode="Markdown")


def is_cloudflare_blocked(status: int, headers: dict, body_sample: str) -> bool:
    server = headers.get("Server", "").lower()
    cf_ray = headers.get("CF-RAY")
    cf_mitigated = headers.get("cf-mitigated")

    if status in [403, 503, 429]:
        if "cloudflare" in server or cf_ray or cf_mitigated:
            return True
        if (
            "just a moment..." in body_sample.lower()
            or "cf-browser-verification" in body_sample.lower()
            or "attention required! | cloudflare" in body_sample.lower()
        ):
            return True
    return False


def get_filename_from_url(url: str, headers: dict = None) -> str:
    if headers:
        cd = headers.get("Content-Disposition")
        if cd and "filename=" in cd:
            filename = cd.split("filename=")[-1].strip(' "')
            if filename:
                return filename
    parsed = urlparse(url)
    name = os.path.basename(parsed.path)
    # حذف کاراکترهای URL Encode مثل %20
    from urllib.parse import unquote
    name = unquote(name)
    return name if name and "." in name else "downloaded_video.mp4"


async def download_and_send_to_telegram(
    url: str,
    update: Update,
    status_msg,
    custom_headers: dict = None,
    filename_override: str = None,
) -> bool:
    """تابع کمکی برای دانلود یک لینک و ارسال مستقیم آن به تلگرام"""
    headers = custom_headers or get_browser_headers(url)
    timeout = aiohttp.ClientTimeout(total=120)

    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.get(url, headers=headers, allow_redirects=True) as response:
            if response.status != 200:
                return False

            content_length = response.headers.get("Content-Length")
            if content_length and int(content_length) > MAX_FILE_SIZE:
                size_mb = int(content_length) / (1024 * 1024)
                await status_msg.edit_text(
                    f"⚠️ حجم فایل ({size_mb:.1f} مگابایت) بیشتر از محدودیت ۵۰ مگابایتی تلگرام است.\n\n"
                    f"🔗 لینک مستقیم آماده شده:\n{url}"
                )
                return True

            filename = filename_override or get_filename_from_url(url, response.headers)
            await status_msg.edit_text(f"📥 در حال دانلود `{filename}` ...", parse_mode="Markdown")

            file_path = f"/tmp/{update.effective_user.id}_{filename}"
            downloaded_size = 0

            with open(file_path, "wb") as f:
                async for chunk in response.content.iter_chunked(1024 * 64):
                    downloaded_size += len(chunk)
                    if downloaded_size > MAX_FILE_SIZE:
                        os.remove(file_path)
                        await status_msg.edit_text("⚠️ حجم فایل از ۵۰ مگابایت عبور کرد و متوقف شد.")
                        return True
                    f.write(chunk)

            await status_msg.edit_text("📤 دانلود تمام شد! در حال آپلود به تلگرام...")

            chat_id = update.effective_chat.id
            with open(file_path, "rb") as f:
                if filename.lower().endswith((".mp4", ".mkv", ".mov", ".avi", ".webm")):
                    await update.get_bot().send_video(
                        chat_id=chat_id, video=f, caption=f"✅ دانلود شده: `{filename}`", parse_mode="Markdown"
                    )
                else:
                    await update.get_bot().send_document(
                        chat_id=chat_id, document=f, caption=f"✅ دانلود شده: `{filename}`", parse_mode="Markdown"
                    )

            await status_msg.delete()
            if os.path.exists(file_path):
                os.remove(file_path)
            return True


async def handle_url(update: Update, context: ContextTypes.DEFAULT_TYPE):
    url = update.message.text.strip()
    if not url.startswith(("http://", "https://")):
        await update.message.reply_text("❌ لطفاً یک لینک معتبر (شروع با http یا https) ارسال کنید.")
        return

    status_msg = await update.message.reply_text("🔍 در حال بررسی لینک و مذاکره با سرور مقصد...")
    headers = get_browser_headers(url)

    try:
        timeout = aiohttp.ClientTimeout(total=45)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(url, headers=headers, allow_redirects=True) as response:
                status = response.status
                resp_headers = response.headers

                body_sample = ""
                if status in [403, 503, 429]:
                    chunk = await response.content.read(2048)
                    body_sample = chunk.decode("utf-8", errors="ignore")

                if is_cloudflare_blocked(status, resp_headers, body_sample) or status == 403:
                    context.user_data["target_url"] = url
                    keyboard = [
                        [InlineKeyboardButton(info["name"], callback_data=key)]
                        for key, info in APIFY_TOOLS.items()
                    ]
                    reply_markup = InlineKeyboardMarkup(keyboard)
                    await status_msg.edit_text(
                        "🛡 **محافظت آنتی‌بات Cloudflare / فایروال شناسایی شد!**\n\n"
                        "دسترسی مستقیم مسدود است. لطفاً یکی از ۵ ابزار **Apify** زیر را انتخاب کنید تا فایل را دور زده و برایتان دانلود کند:",
                        reply_markup=reply_markup,
                        parse_mode="Markdown",
                    )
                    return

                if status != 200:
                    await status_msg.edit_text(f"❌ خطا در دریافت فایل! کد وضعیت سرور: `{status}`", parse_mode="Markdown")
                    return

        # اگر وضعیت 200 بود، دانلود و ارسال را انجام بده
        await download_and_send_to_telegram(url, update, status_msg, custom_headers=headers)

    except asyncio.TimeoutError:
        await status_msg.edit_text("⏳ زمان اتصال به سرور به پایان رسید (Timeout).")
    except Exception as e:
        logger.error(f"Error handling URL: {e}")
        await status_msg.edit_text(f"❌ خطایی رخ داد:\n`{str(e)}`", parse_mode="Markdown")


async def apify_button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    tool_key = query.data
    target_url = context.user_data.get("target_url")

    if not target_url:
        await query.edit_message_text("❌ لینک منقضی شده است. لطفاً دوباره لینک را ارسال کنید.")
        return

    if not APIFY_TOKEN:
        await query.edit_message_text("❌ توکن `APIFY_TOKEN` تنظیم نشده است!", parse_mode="Markdown")
        return

    tool_info = APIFY_TOOLS.get(tool_key)
    actor_id = tool_info["actor_id"]
    tool_name = tool_info["name"]

    await query.edit_message_text(
        f"⚙️ در حال اجرای **{tool_name}** روی سرورهای Apify...\n"
        "⏳ در حال دور زدن Cloudflare و استخراج فایل (۳۰ تا ۹۰ ثانیه صبر کنید)...",
        parse_mode="Markdown",
    )

    try:
        apify_client = ApifyClientAsync(APIFY_TOKEN)
        run_input = get_actor_input(actor_id, target_url)

        actor_client = apify_client.actor(actor_id)
        call_result = await actor_client.call(run_input=run_input)

        if not call_result or call_result.get("status") != "SUCCEEDED":
            status_text = call_result.get("status") if call_result else "UNKNOWN"
            await query.edit_message_text(
                f"❌ اجرای ابزار `{actor_id}` با وضعیت `{status_text}` متوقف شد. لطفاً ابزار دیگری (مثل گزینه ۱ یا ۲) را امتحان کنید.",
                parse_mode="Markdown",
            )
            return

        # بررسی اینکه آیا فایل مستقیماً در Key-Value Store سرور Apify ذخیره شده است یا خیر
        kvs_id = call_result.get("defaultKeyValueStoreId")
        kvs_client = apify_client.key_value_store(kvs_id)
        record = await kvs_client.get_record("DOWNLOADED_FILE")

        if record and record.get("value"):
            # لینک مستقیم بدون کلودفلر از سرور Apify
            apify_direct_file_url = f"https://api.apify.com/v2/key-value-stores/{kvs_id}/records/DOWNLOADED_FILE?disableRedirect=true"
            orig_filename = get_filename_from_url(target_url)
            await query.edit_message_text("✅ کلودفلر دور زده شد! در حال انتقال فایل از سرور Apify به تلگرام...")
            success = await download_and_send_to_telegram(
                apify_direct_file_url,
                update,
                query.message,
                custom_headers={},
                filename_override=orig_filename,
            )
            if success:
                return

        # اگر فایل باینری نبود، نتایج Dataset را نشان بده
        dataset_id = call_result.get("defaultDatasetId")
        dataset_client = apify_client.dataset(dataset_id)
        items_result = await dataset_client.list_items()
        items = items_result.items

        extracted_info = f"✅ **پردازش با `{actor_id}` انجام شد!**\n\n"
        if items:
            first_item = items[0]
            resolved_url = first_item.get("loadedUrl") or first_item.get("url") or target_url
            extracted_info += f"🔗 **لینک پردازش شده:**\n{resolved_url}\n\n"
            extracted_info += f"📦 **لینک مخزن فایل در Apify:**\n`https://api.apify.com/v2/datasets/{dataset_id}/items`"
        else:
            extracted_info += (
                "⚠️ سایت مقصد ارتباط پروکسی دیتاسنتر Apify را مسدود کرد (خطای 403 روی فایل مستقیم).\n"
                "💡 برای چنین فایل‌هایی گزینه **1 (Playwright)** یا **2 (Puppeteer)** بهترین عملکرد را دارند."
            )

        await query.edit_message_text(extracted_info, parse_mode="Markdown")

    except Exception as e:
        error_str = str(e)
        logger.error(f"Apify execution error: {error_str}")
        if "approvePermissions=true" in error_str:
            await query.edit_message_text(
                "⚠️ **نیاز به تایید یک‌باره در سایت Apify:**\n\n"
                "شما هنوز مجوز اجرای این ابزار را در اکانت Apify خود تایید نکرده‌اید. "
                "لطفاً روی لینک زیر کلیک کنید و دکمه **Approve** را بزنید، سپس دوباره در ربات تست کنید:\n\n"
                f"{error_str.split('running it: ')[-1]}"
            )
        else:
            await query.edit_message_text(
                f"❌ خطا هنگام ارتباط با Apify:\n`{error_str}`", parse_mode="Markdown"
            )


def main():
    if not BOT_TOKEN:
        raise ValueError("خطا: متغیر محیطی BOT_TOKEN تنظیم نشده است!")

    application = Application.builder().token(BOT_TOKEN).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_url))
    application.add_handler(CallbackQueryHandler(apify_button_handler))

    logger.info("Bot started successfully...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
