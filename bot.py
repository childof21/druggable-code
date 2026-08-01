#!/usr/bin/env python3
"""
Telegram Bot for Druggable Code Blog
- Receives interesting articles from colleagues
- Uses AI to format them as blog posts
- Publishes to druggable-code.vercel.app
"""

import os
import sys
import json
import time
import asyncio
import logging
import subprocess
import re
from datetime import datetime
from pathlib import Path

# Setup paths
BOT_DIR = Path(__file__).parent.resolve()
POSTS_FILE = BOT_DIR / "posts.json"
INDEX_FILE = BOT_DIR / "index.html"
VERCEL_TOKEN = os.environ.get("VERCEL_TOKEN", "")
OPENROUTER_KEY = os.environ.get("OPENROUTER_API_KEY", "")
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
ALLOWED_USER = os.environ.get("ALLOWED_USER", "")  # Telegram username of the colleague

# Logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(BOT_DIR / "bot.log"),
        logging.StreamHandler()
    ]
)
log = logging.getLogger(__name__)


def load_posts():
    """Load current posts from posts.json"""
    with open(POSTS_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def save_posts(posts):
    """Save posts to posts.json"""
    with open(POSTS_FILE, "w", encoding="utf-8") as f:
        json.dump(posts, f, ensure_ascii=False, indent=2)
    log.info(f"Saved {len(posts)} posts to posts.json")


def get_next_id(posts):
    """Get the next available post ID"""
    ids = [p.get("id", 0) for p in posts]
    return max(ids) + 1 if ids else 1


def get_persian_date():
    """Get current date in Persian calendar format (YYYY/MM/DD)"""
    try:
        import jdatetime
        now = jdatetime.datetime.now()
        return f"{now.year:04d}/{now.month:02d}/{now.day:02d}"
    except ImportError:
        now = datetime.now()
        persian_year = now.year - 621
        return f"{persian_year:04d}/{now.month:02d}/{now.day:02d}"


async def format_with_ai(raw_text, user_info="", pdf_link=""):
    """Use AI to format the content as a blog post. Produces a comprehensive Persian summary."""
    import httpx, json, re
    
    # Build the prompt - AI returns JSON with: title, tags, and enhanced text
    system_prompt = """You are a blog formatting assistant for a Persian medical/biotech blog. Return JSON with:
{
  "title": "Persian title (5-12 words, with emoji)",
  "titleEn": "English title (optional)",
  "tags": ["3-5 Persian tags"],
  "enhanced_text": "a comprehensive Persian summary with highlights, emojis, and tables"
}

RULES:
0. **SUMMARY, NOT FULL TEXT: Write a comprehensive summary of the content in Persian (فارسی).** Do NOT reproduce the entire document verbatim. Instead: capture EVERY key section/heading, every important number, every table (condensed to its essential rows), and every main point. A reader should understand the complete picture without reading the original — nothing important missed.
0b. **LANGUAGE: ALL enhanced_text MUST be in Persian (فارسی).** If the input is English, translate to Persian. Keep technical terms, drug names, company names, and numbers in original Latin form where appropriate.
1. Structure the summary with ## headings matching the document's major sections. Under each heading use - bullets with the key facts.
2. Include ALL important numbers, statistics, percentages, and dates mentioned in the document.
3. Include tables (| separated) for ranking/comparison data — keep the most important rows, no more than 10-12 rows per table.
4. Use <span class="hl">IMPORTANT TERMS</span> (purple highlight) for: drug names, protein names, model names, key numbers, breakthroughs. NOT <mark>.
5. Use <strong>BOLD</strong> for emphasis on key points.
6. Add emojis at section starts: 🔬 💊 🤖 🧬 🧪 📊 🚀 🧫 💉 ⚕️
7. Use ONLY HTML tags. NEVER use markdown (** or * for bold/italic). NEVER use <mark>. NEVER use backticks (`) for code.
8. Keep it comprehensive but readable: aim for roughly 1/4 to 1/3 of the original length. Short paragraphs, blank lines between them.
9. Tags from: ['هوش مصنوعی', 'مدل‌های زبانی', 'مهندسی پروتئین', 'ESM3', 'PLM', 'طراحی دارو', 'سرمایه‌گذاری', 'تولید دارو', 'آنتی‌بادی مونوکلونال', 'بیوتکنولوژی', 'سلول مجازی', 'اوپن‌سورس', 'ایمونولوژی', 'بیوانفورماتیک']

Output ONLY valid JSON."""

    user_prompt = f"""Format this content into a comprehensive Persian summary. Return JSON with title, tags, and enhanced_text:

{raw_text[:60000]}"""

    meta = {"title": "📄 پست جدید", "titleEn": "", "tags": ["عمومی"]}
    enhanced = raw_text
    
    try:
        async with httpx.AsyncClient(timeout=150.0) as client:
            response = await client.post(
                "https://openrouter.ai/api/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {OPENROUTER_KEY}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": "deepseek/deepseek-chat",
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt}
                    ],
                    "temperature": 0.7,
                    "max_tokens": 4000,
                }
            )
            
            result = response.json()
            content = result["choices"][0]["message"]["content"]
            
            # Clean up the response (remove markdown code fences if any)
            content = content.strip()
            if content.startswith("```"):
                content = content.split("\n", 1)[1]
            if content.endswith("```"):
                content = content.rsplit("```", 1)[0]
            content = content.strip()
            
            try:
                meta = json.loads(content)
                enhanced = meta.get("enhanced_text", content)
            except json.JSONDecodeError as e:
                log.error(f"Failed to parse AI response: {e}")
                enhanced = content or raw_text
                meta = {"title": "📄 پست جدید", "titleEn": "", "tags": ["عمومی"]}
            
            log.info(f"✅ AI summary generated ({len(enhanced)} chars)")
    except Exception as e:
        log.error(f"❌ AI formatting failed: {e}", exc_info=True)
        # Fallback: use first 3000 chars of raw text so post still publishes
        enhanced = raw_text[:3000]
    
    # Append PDF link at the end for direct study
    if pdf_link:
        enhanced += f"\n\n---\n\n📄 <strong>مطالعه کامل (PDF اصلی):</strong> <a href=\"{pdf_link}\">دانلود و مطالعه مستقیم</a>"
    
    enhanced = enhanced.strip()
    
    # Parse the enhanced text into content blocks
    content_blocks = []
    current_para = []
    in_list = False
    list_items = []
    in_table = False
    table_headers = []
    table_rows = []
    
    for line in enhanced.strip().split('\n'):
        stripped = line.strip()
        if not stripped:
            _flush_para(content_blocks, current_para, list_items, in_list)
            current_para, list_items, in_list = [], [], False
            continue
        
        # Table row
        if stripped.startswith('|') and stripped.endswith('|') and stripped.count('|') > 2:
            _flush_para(content_blocks, current_para, list_items, in_list)
            current_para, list_items, in_list = [], [], False
            cells = [c.strip() for c in stripped.split('|')[1:-1]]
            if not in_table:
                table_headers = cells
                in_table = True
            elif all(c.strip() in ['---', ':', ':-', '-:', '::'] for c in stripped.split('|')[1:-1]):
                continue
            else:
                table_rows.append(cells)
            continue
        else:
            if in_table:
                _flush_table(content_blocks, table_headers, table_rows)
                table_headers, table_rows, in_table = [], [], False
        
        # Heading
        if stripped.startswith('##') or stripped.startswith('###'):
            _flush_para(content_blocks, current_para, list_items, in_list)
            current_para, list_items, in_list = [], [], False
            content_blocks.append({"type": "h3", "fa": re.sub(r'^#+\s*', '', stripped)})
        
        # List item
        elif stripped.startswith('- ') or stripped.startswith('* ') or stripped.startswith('• '):
            _flush_para_no_list(content_blocks, current_para)
            current_para = []
            in_list = True
            list_items.append(re.sub(r'^[-*•]\s*', '', stripped))
        
        # Regular paragraph
        else:
            if in_list:
                _flush_list(content_blocks, list_items)
                list_items, in_list = [], False
            current_para.append(stripped)
    
    # Flush remaining
    if in_table:
        _flush_table(content_blocks, table_headers, table_rows)
    if in_list and list_items:
        _flush_list(content_blocks, list_items)
    if current_para:
        content_blocks.append({"type": "p", "fa": "\n".join(current_para)})
    
    # If no blocks, fallback to simple text
    if not content_blocks:
        content_blocks.append({"type": "p", "fa": enhanced})
    
    # Build the final post data
    post_data = {
        "title": meta.get("title", "📄 پست جدید"),
        "titleEn": meta.get("titleEn", ""),
        "tags": meta.get("tags", ["عمومی"]),
        "lang": meta.get("lang", "fa"),
        "content": content_blocks
    }
    
    return post_data


def _flush_para(blocks, para, items, in_list):
    """Flush current paragraph and list to blocks"""
    if in_list and items:
        blocks.append({"type": "ul", "fa": items})
    if para:
        blocks.append({"type": "p", "fa": "\n".join(para)})

def _flush_para_no_list(blocks, para):
    """Flush paragraph only"""
    if para:
        blocks.append({"type": "p", "fa": "\n".join(para)})

def _flush_list(blocks, items):
    """Flush list only"""
    if items:
        blocks.append({"type": "ul", "fa": items})

def _flush_table(blocks, headers, rows):
    """Flush table to blocks"""
    if headers and rows:
        blocks.append({"type": "table", "fa": {"headers": headers, "rows": rows}})


def deploy_to_vercel():
    """Deploy the updated site to Vercel"""
    vercel_path = "/home/mohammad/.local/bin/vercel"
    cmd = [
        vercel_path, "--prod", "--token", VERCEL_TOKEN, "--yes",
        "--cwd", str(BOT_DIR)
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    if result.returncode == 0:
        log.info(f"✅ Deployed successfully: {result.stdout.strip()}")
        return True
    else:
        log.error(f"❌ Deploy failed: {result.stderr}")
        return False


async def publish_post(raw_text, user_info="", author="", pdf_link=""):
    """Main function: format content, add to posts.json, deploy"""
    log.info(f"Starting AI formatting... (author: {author})")
    
    # Format with AI
    post_data = await format_with_ai(raw_text, user_info, pdf_link)
    
    # Load current posts
    posts = load_posts()
    
    # Assign ID and date
    post_data["id"] = get_next_id(posts)
    if "date" not in post_data or not post_data.get("date"):
        post_data["date"] = get_persian_date()
    if "lang" not in post_data:
        post_data["lang"] = "fa"
    if author:
        post_data["author"] = author
    
    # Add to posts
    posts.append(post_data)
    
    # Save
    save_posts(posts)
    
    # Deploy
    log.info("Deploying to Vercel...")
    success = deploy_to_vercel()
    
    return post_data, success


# ============== Telegram Bot ==============

from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /start command"""
    await update.message.reply_text(
        "👋 سلام! من بات وبلاگ Druggable Code هستم.\n\n"
        "مطالب جالب‌ات رو برام بفرست تا به صورت یه پست وبلاگی منتشرش کنم.\n\n"
        "دستورات:\n"
        "/start - شروع\n"
        "/help - راهنما\n"
        "/status - وضعیت آخرین پست\n"
        "/about - درباره بات"
    )


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /help command"""
    await update.message.reply_text(
        "📖 **راهنما**\n\n"
        "کافیه یه متن، لینک یا خلاصه از یه مطلب جالب رو برام بفرستی.\n"
        "من با هوش مصنوعی فرمتش می‌کنم و توی وبلاگ منتشر می‌کنم.\n\n"
        "موضوعات مورد علاقه:\n"
        "🔬 ایمونولوژی\n"
        "💊 مهندسی دارو\n"
        "🤖 هوش مصنوعی در پزشکی\n"
        "🧬 بیوتکنولوژی\n\n"
        "⚠️ دقت کن: پست‌ها به صورت خودکار منتشر می‌شن!"
    )


async def about(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /about command"""
    await update.message.reply_text(
        "🤖 **Druggable Code Bot**\n\n"
        "یه بات هوشمند که مطالب جالب رو به پست وبلاگی تبدیل می‌کنه\n"
        "و توی druggable-code.vercel.app منتشر می‌کنه.\n\n"
        "Powered by Hermes AI + OpenRouter"
    )


async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /status command"""
    try:
        posts = load_posts()
        await update.message.reply_text(
            f"📊 **وضعیت وبلاگ**\n\n"
            f"تعداد پست‌ها: {len(posts)}\n"
            f"آخرین پست: {posts[-1].get('title', 'N/A') if posts else 'هیچ'}\n"
            f"آخرین بروزرسانی: {posts[-1].get('date', 'N/A') if posts else 'هیچ'}"
        )
    except Exception as e:
        await update.message.reply_text(f"❌ خطا: {e}")


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle incoming text messages"""
    user = update.effective_user
    text = update.message.text or update.message.caption or ""
    
    if not text.strip():
        await update.message.reply_text("لطفاً متن یا فایل مورد نظرت رو بفرست 🙏")
        return
    
    await process_content(update, context, text, user)


async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle incoming document files (.md, .pdf, .txt, etc.)"""
    user = update.effective_user
    doc = update.message.document
    caption = update.message.caption or ""
    
    # Send typing indicator
    await update.message.chat.send_action("typing")
    
    msg = await update.message.reply_text(
        f"📥 فایل دریافت شد: `{doc.file_name}`\n"
        "در حال استخراج متن... ⏳"
    )
    
    try:
        # Download the file
        file = await doc.get_file()
        file_bytes = await file.download_as_bytearray()
        file_name = doc.file_name.lower()
        
        # Extract text based on file type
        text = ""
        pdf_link = ""
        
        if file_name.endswith('.pdf'):
            # Extract text from PDF
            import fitz  # PyMuPDF
            pdf_doc = fitz.open(stream=file_bytes, filetype="pdf")
            for page in pdf_doc:
                text += page.get_text() + "\n\n"
            pdf_doc.close()
            log.info(f"📄 Extracted {len(text)} chars from PDF: {doc.file_name}")
            
            # Save PDF to site's pdfs/ folder so we can link it in the post
            try:
                pdf_dir = BOT_DIR / "pdfs"
                pdf_dir.mkdir(exist_ok=True)
                # Safe filename: lowercase, keep letters/digits/dashes
                safe_name = re.sub(r'[^a-zA-Z0-9._-]', '-', doc.file_name.lower())
                pdf_path = pdf_dir / safe_name
                with open(pdf_path, 'wb') as f:
                    f.write(bytes(file_bytes))
                pdf_link = f"https://druggable-code.vercel.app/pdfs/{safe_name}"
                log.info(f"📎 PDF saved for direct study: {pdf_path} -> {pdf_link}")
            except Exception as e:
                log.error(f"⚠️ Could not save PDF for linking: {e}")
            
        elif file_name.endswith('.md') or file_name.endswith('.txt') or file_name.endswith('.markdown'):
            # Plain text / markdown
            text = file_bytes.decode('utf-8', errors='replace')
            log.info(f"📄 Read {len(text)} chars from {doc.file_name}")
            
        elif file_name.endswith('.html') or file_name.endswith('.htm'):
            # Extract clean text from HTML (strip tags, remove scripts/styles)
            from bs4 import BeautifulSoup
            raw = file_bytes.decode('utf-8', errors='replace')
            soup = BeautifulSoup(raw, 'html.parser')
            # Remove non-content elements
            for tag in soup(['script', 'style', 'nav', 'footer', 'header', 'aside', 'form', 'button', 'noscript']):
                tag.decompose()
            text = soup.get_text(separator='\n')
            # Clean up blank lines
            lines = [l.strip() for l in text.split('\n') if l.strip()]
            text = '\n'.join(lines)
            log.info(f"📄 Extracted {len(text)} chars from HTML: {doc.file_name}")
            
        else:
            # Try reading as text
            try:
                text = file_bytes.decode('utf-8', errors='replace')
                log.info(f"📄 Read {len(text)} chars from {doc.file_name} (as text)")
            except:
                await msg.edit_text("❌ فرمت فایل پشتیبانی نمی‌شه. لطفاً PDF, MD, TXT یا HTML بفرست.")
                return
        
        if not text.strip():
            await msg.edit_text("❌ فایل خالیه یا متنی ازش استخراج نشد.")
            return
        
        # Add caption if present
        if caption:
            text = f"{caption}\n\n---\n\n{text}"
        
        await msg.edit_text(
            f"✅ متن استخراج شد! ({len(text)} کاراکتر)\n"
            "در حال پردازش با هوش مصنوعی... 🧠"
        )
        
        await process_content(update, context, text, user, pdf_link)
        
    except Exception as e:
        log.error(f"❌ Error processing file: {e}", exc_info=True)
        await msg.edit_text(f"❌ خطا در پردازش فایل: {str(e)[:200]}")


async def process_content(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str, user, pdf_link=""):
    """Process text content (shared by text messages and file uploads)"""
    # Determine author based on who sent the message
    username = (user.username or "").lower()
    full_name = (user.full_name or "").lower()
    user_id = user.id
    
    # Dr. Afshari (the blog owner)
    if any(x in username for x in ["mafshari", "dr_afshari", "afshari"]) or \
       any(x in full_name for x in ["افشاری", "afshari", "mohammad"]):
        author = "دکتر افشاری"
    # Dr. Kazemi (colleague)
    elif any(x in username for x in ["kazemi", "dr_kazemi"]) or \
         any(x in full_name for x in ["کاظمی", "kazemi"]):
        author = "دکتر کاظمی"
    # Unknown user - use their name
    else:
        author = user.full_name or f"کاربر {user_id}"
    
    log.info(f"📩 Processing from {user.username or user.id} (author: {author}): {text[:100]}...")
    
    # Send typing indicator
    await update.message.chat.send_action("typing")
    
    # Try to find the reply message
    try:
        msg = await get_reply_or_send(update, context, 
            "📥 در حال پردازش با هوش مصنوعی...\n⏳ این فرآیند چند دقیقه طول می‌کشه...")
    except:
        msg = await update.message.reply_text(
            "📥 در حال پردازش با هوش مصنوعی...\n⏳ این فرآیند چند دقیقه طول می‌کشه...")
    
    try:
        user_info = f"User: {user.full_name} (@{user.username})" if user.username else f"User: {user.full_name}"
        post_data, success = await publish_post(text, user_info, author, pdf_link)
        
        if success:
            await msg.edit_text(
                f"✅ **پست جدید منتشر شد!**\n\n"
                f"📝 **{post_data.get('title', 'بدون عنوان')}**\n"
                f"👤 {post_data.get('author', '')}\n"
                f"🏷️ {', '.join(post_data.get('tags', []))}\n\n"
                f"🔗 https://druggable-code.vercel.app"
            )
        else:
            await msg.edit_text(
                f"⚠️ محتوا فرمت شد ولی دیپلوی با مشکل مواجه شد.\n"
                f"دسترسی به سرور برای رفع مشکل نیاز هست."
            )
    except Exception as e:
        log.error(f"❌ Error processing: {e}", exc_info=True)
        await msg.edit_text(f"❌ خطا در پردازش: {str(e)[:200]}\n\nلطفاً بعداً دوباره تلاش کن.")


async def get_reply_or_send(update, context, text):
    """Try to reply to a previous message, or send a new one"""
    try:
        if update.message:
            return await update.message.reply_text(text)
    except:
        pass
    return await context.bot.send_message(chat_id=update.effective_chat.id, text=text)


async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle errors"""
    log.error(f"Update {update} caused error {context.error}")


def run_bot():
    """Run the Telegram bot"""
    # Create application
    app = Application.builder().token(BOT_TOKEN).build()
    
    # Add handlers
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("about", about))
    app.add_handler(CommandHandler("status", status))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_document))
    
    # Error handler
    app.add_error_handler(error_handler)
    
    log.info("🤖 Bot started! Waiting for messages...")
    
    # Start polling
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    # Load env vars
    env_file = BOT_DIR / ".env"
    if env_file.exists():
        with open(env_file) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, value = line.split("=", 1)
                    os.environ.setdefault(key.strip(), value.strip())
    
    BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
    VERCEL_TOKEN = os.environ.get("VERCEL_TOKEN", "")
    OPENROUTER_KEY = os.environ.get("OPENROUTER_API_KEY", "")
    
    if not BOT_TOKEN:
        log.error("❌ BOT_TOKEN not set!")
        sys.exit(1)
    
    log.info("🚀 Starting Druggable Code Bot...")
    run_bot()