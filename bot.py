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

# تنظیمات لاگ برای دیباگ راحت در Railway
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

# دریافت متغیرهای محیطی
BOT_TOKEN = os.getenv("BOT_TOKEN")
APIFY_TOKEN = os.getenv("APIFY_TOKEN")

# محدودیت حجم آپلود مستقیم بات تلگرام (50 مگابایت)
MAX_FILE_SIZE = 50 * 1024 * 1024

# ۵ ابزار (Actor) قدرتمند Apify
APIFY_TOOLS = {
    "tool_1": {
        "name": "1️⃣ Playwright Scraper (پیشنهادی - مرورگر واقعی)",
        "actor_id": "apify/playwright-scraper",
    },
    "tool_2": {
        "name": "2️⃣ Puppeteer Scraper (عبور از چالش‌های JS)",
        "actor_id": "apify/puppeteer-scraper",
    },
    "tool_3": {
        "name": "3️⃣ Website Content Crawler (خزنده هوشمند)",
        "actor_id": "apify/website-content-crawler",
    },
    "tool_4": {
        "name": "4️⃣ Cheerio Scraper (سریع با پروکسی چرخشی)",
        "actor_id": "apify/cheerio-scraper",
    },
    "tool_5": {
        "name": "5️⃣ Web Scraper (اسکرپر عمومی مرورگر)",
        "actor_id": "apify/web-scraper",
    },
}


def get_actor_input(actor_id: str, target_url: str) -> dict:
    """ساخت ورودی دقیق و استاندارد متناسب با هر ابزار Apify"""
    base_input = {
        "startUrls": [{"url": target_url}],
        "maxRequestsPerCrawl": 1,
        "proxyConfiguration": {"useApifyProxy": True},
    }

    # ابزار شماره 3 نیازی به pageFunction ندارد
    if actor_id == "apify/website-content-crawler":
        return base_input

    # ابزار شماره 4 (Cheerio)
    if actor_id == "apify/cheerio-scraper":
        base_input["pageFunction"] = """
        async function pageFunction(context) {
            const { request, $ } = context;
            return {
                url: request.url,
                loadedUrl: request.loadedUrl,
                title: $('title').text()
            };
        }
        """
        return base_input

    # ابزارهای مرورگر محور (Playwright, Puppeteer, Web Scraper)
    base_input["pageFunction"] = """
    async function pageFunction(context) {
        const { request, page } = context;
        const title = page ? await page.title() : 'Processed';
        return {
            url: request.url,
            loadedUrl: request.loadedUrl || request.url,
            title: title
        };
    }
    """
    return base_input


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """دستور شروع ربات"""
    welcome_text = (
        "سلام! 👋\n"
        "من ربات دانلودر پیشرفته هستم.\n\n"
        "🔗 **قابلیت‌ها:**\n"
        "1. دانلود مستقیم فایل‌ها و ویدیوها با سرعت بالا.\n"
        "2. تشخیص هوشمند فایروال و آنتی‌بات **Cloudflare**.\n"
        "3. اتصال به ۵ ابزار قدرتمند **Apify** برای پردازش لینک به انتخاب شما.\n\n"
        "کافیست لینک مستقیم فایل یا ویدیو را برای من ارسال کنید!"
    )
    await update.message.reply_text(welcome_text, parse_mode="Markdown")


def is_cloudflare_blocked(status: int, headers: dict, body_sample: str) -> bool:
    """تشخیص دقیق وجود آنتی‌بات کلودفلر"""
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


def get_filename_from_url(url: str, headers: dict) -> str:
    """استخراج نام فایل از هدر یا لینک"""
    cd = headers.get("Content-Disposition")
    if cd and "filename=" in cd:
        filename = cd.split("filename=")[-1].strip(' "')
        if filename:
            return filename
    parsed = urlparse(url)
    name = os.path.basename(parsed.path)
    return name if name and "." in name else "downloaded_file.mp4"


async def handle_url(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """بررسی لینک ارسالی توسط کاربر"""
    url = update.message.text.strip()
    if not url.startswith(("http://", "https://")):
        await update.message.reply_text("❌ لطفاً یک لینک معتبر (شروع با http یا https) ارسال کنید.")
        return

    status_msg = await update.message.reply_text("🔍 در حال بررسی لینک و وضعیت سرور مقصد...")

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
    }

    try:
        timeout = aiohttp.ClientTimeout(total=30)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(url, headers=headers, allow_redirects=True) as response:
                status = response.status
                resp_headers = response.headers

                body_sample = ""
                if status in [403, 503, 429]:
                    chunk = await response.content.read(2048)
                    body_sample = chunk.decode("utf-8", errors="ignore")

                # بررسی وجود کلودفلر
                if is_cloudflare_blocked(status, resp_headers, body_sample):
                    context.user_data["target_url"] = url
                    keyboard = [
                        [InlineKeyboardButton(info["name"], callback_data=key)]
                        for key, info in APIFY_TOOLS.items()
                    ]
                    reply_markup = InlineKeyboardMarkup(keyboard)
                    await status_msg.edit_text(
                        "🛡 **محافظت آنتی‌بات Cloudflare شناسایی شد!**\n\n"
                        "دسترسی مستقیم به این لینک مسدود است. لطفاً یکی از ۵ ابزار قدرتمند **Apify** زیر را برای پردازش لینک انتخاب کنید:",
                        reply_markup=reply_markup,
                        parse_mode="Markdown",
                    )
                    return

                if status != 200:
                    await status_msg.edit_text(f"❌ خطا در دریافت فایل! کد وضعیت سرور: `{status}`", parse_mode="Markdown")
                    return

                # بررسی حجم فایل
                content_length = resp_headers.get("Content-Length")
                if content_length and int(content_length) > MAX_FILE_SIZE:
                    size_mb = int(content_length) / (1024 * 1024)
                    await status_msg.edit_text(
                        f"⚠️ حجم فایل ({size_mb:.1f} مگابایت) بیشتر از محدودیت ۵۰ مگابایتی ربات‌های تلگرام است."
                    )
                    return

                # شروع دانلود فایل
                filename = get_filename_from_url(url, resp_headers)
                await status_msg.edit_text(f"📥 در حال دانلود `{filename}` ...", parse_mode="Markdown")

                file_path = f"/tmp/{update.effective_user.id}_{filename}"
                downloaded_size = 0

                with open(file_path, "wb") as f:
                    async for chunk in response.content.iter_chunked(1024 * 64):
                        downloaded_size += len(chunk)
                        if downloaded_size > MAX_FILE_SIZE:
                            os.remove(file_path)
                            await status_msg.edit_text("⚠️ حجم فایل در حین دانلود از ۵۰ مگابایت عبور کرد و متوقف شد.")
                            return
                        f.write(chunk)

                await status_msg.edit_text("📤 دانلود تمام شد! در حال آپلود به تلگرام...")

                with open(file_path, "rb") as f:
                    if filename.lower().endswith((".mp4", ".mkv", ".mov", ".avi", ".webm")):
                        await update.message.reply_video(video=f, caption=f"✅ دانلود شده: `{filename}`", parse_mode="Markdown")
                    else:
                        await update.message.reply_document(document=f, caption=f"✅ دانلود شده: `{filename}`", parse_mode="Markdown")

                await status_msg.delete()
                if os.path.exists(file_path):
                    os.remove(file_path)

    except asyncio.TimeoutError:
        await status_msg.edit_text("⏳ زمان اتصال به سرور به پایان رسید (Timeout).")
    except Exception as e:
        logger.error(f"Error handling URL: {e}")
        await status_msg.edit_text(f"❌ خطایی رخ داد:\n`{str(e)}`", parse_mode="Markdown")


async def apify_button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """مدیریت انتخاب ابزار Apify توسط کاربر"""
    query = update.callback_query
    await query.answer()

    tool_key = query.data
    target_url = context.user_data.get("target_url")

    if not target_url:
        await query.edit_message_text("❌ لینک مورد نظر منقضی شده است. لطفاً دوباره لینک را ارسال کنید.")
        return

    if not APIFY_TOKEN:
        await query.edit_message_text("❌ توکن `APIFY_TOKEN` در تنظیمات سرور (Railway) وارد نشده است!", parse_mode="Markdown")
        return

    tool_info = APIFY_TOOLS.get(tool_key)
    actor_id = tool_info["actor_id"]
    tool_name = tool_info["name"]

    await query.edit_message_text(
        f"⚙️ در حال اجرای ابزار **{tool_name}** روی سرورهای Apify...\n"
        "⏳ این عملیات ممکن است بین ۳۰ تا ۹۰ ثانیه زمان ببرد. لطفاً صبر کنید...",
        parse_mode="Markdown",
    )

    try:
        apify_client = ApifyClientAsync(APIFY_TOKEN)

        # دریافت تنظیمات ورودی متناسب با ابزار انتخاب شده
        run_input = get_actor_input(actor_id, target_url)

        # اجرای Actor بدون پارامتر قدیمی timeout_secs
        actor_client = apify_client.actor(actor_id)
        call_result = await actor_client.call(run_input=run_input)

        if not call_result or call_result.get("status") != "SUCCEEDED":
            status_text = call_result.get("status") if call_result else "UNKNOWN"
            await query.edit_message_text(
                f"❌ اجرای ابزار `{actor_id}` با وضعیت `{status_text}` متوقف شد. لطفاً ابزار دیگری را امتحان کنید.",
                parse_mode="Markdown",
            )
            return

        # استخراج نتایج از Dataset تولید شده توسط Apify
        dataset_id = call_result.get("defaultDatasetId")
        dataset_client = apify_client.dataset(dataset_id)
        items_result = await dataset_client.list_items()
        items = items_result.items

        extracted_info = f"✅ **پردازش با `{actor_id}` انجام شد!**\n\n"
        if items:
            first_item = items[0]
            resolved_url = first_item.get("loadedUrl") or first_item.get("url") or target_url
            title = first_item.get("title") or first_item.get("metadata", {}).get("title") or "بدون عنوان"
            extracted_info += f"📌 **عنوان صفحه/فایل:** `{title}`\n"
            extracted_info += f"🔗 **لینک نهایی پس از پردازش:**\n{resolved_url}\n\n"
            extracted_info += f"🗂 **شناسه دیتاست در پنل Apify شما:** `{dataset_id}`"
        else:
            extracted_info += f"عملیات اجرا شد. شناسه دیتاست در پنل Apify:\n`{dataset_id}`"

        await query.edit_message_text(extracted_info, parse_mode="Markdown")

    except Exception as e:
        logger.error(f"Apify execution error: {e}")
        await query.edit_message_text(
            f"❌ خطا هنگام ارتباط با Apify:\n`{str(e)}`", parse_mode="Markdown"
        )


def main():
    """تابع اصلی اجرای ربات"""
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
