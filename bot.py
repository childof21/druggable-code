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
    """Use AI to rewrite the content in clear, educational Persian. NO summarization."""
    import httpx, json, re
    
    # Build the prompt - AI returns JSON with: title, tags, and enhanced text
    system_prompt = """You are a blog formatting assistant for a Persian medical/biotech blog. Return JSON with:
{
  "title": "Persian title (5-12 words, with emoji)",
  "titleEn": "English title (optional)",
  "tags": ["3-5 Persian tags"],
  "enhanced_text": "the COMPLETE text rewritten in clear, educational Persian with highlights, emojis, and tables"
}

RULES:
0. **NO SUMMARIZATION - ABSOLUTELY FORBIDDEN: Rewrite the FULL text.** You must NOT summarize, shorten, compress, merge, or omit ANY part of the input. Every sentence, every paragraph, every number, every table cell, every list item, every heading MUST appear in the output. The output must contain 100% of the information — nothing less. You are REWRITING for clarity, NOT condensing.
0b. **LANGUAGE: ALL enhanced_text MUST be in Persian (فارسی).** If the input is English, translate EVERY sentence to Persian. Keep technical terms, drug names, company names, and numbers in Latin form where appropriate, but the surrounding text is Persian.
1. **EDUCATIONAL REWRITE:** Make the text clear, fluent, and easy to understand for medical students and professionals. Explain complex concepts with simpler phrasing. Use a teaching tone. BUT: rewriting for clarity NEVER means removing content — keep every fact, number, and detail.
2. Tables: convert markdown tables (| separated) to proper table format — EVERY row and EVERY column preserved exactly.
3. Use <span class="hl">IMPORTANT TERMS</span> (purple highlight) for: drug names, protein names, model names, key numbers, breakthroughs. NOT <mark>.
4. Use <strong>BOLD</strong> for emphasis on key points.
5. Add emojis at section starts: 🔬 💊 🤖 🧬 🧪 📊 🚀 🧫 💉 ⚕️
6. Keep ## headings, ### headings, - lists, * lists — ALL of them, exactly as many as in the input.
7. Use ONLY HTML tags. NEVER use markdown (** or * for bold/italic). NEVER use <mark>. NEVER use backticks (`) for code.
8. Split text into readable paragraphs (2-4 sentences each). Use blank lines between paragraph breaks. Preserve ALL paragraphs from the original.
9. Tags from: ['هوش مصنوعی', 'مدل‌های زبانی', 'مهندسی پروتئین', 'ESM3', 'PLM', 'طراحی دارو', 'سرمایه‌گذاری', 'تولید دارو', 'آنتی‌بادی مونوکلونال', 'بیوتکنولوژی', 'سلول مجازی', 'اوپن‌سورس', 'ایمونولوژی', 'بیوانفورماتیک']

Output ONLY valid JSON."""

    # Chunk long texts - LIMITED number of chunks (15000 chars each) to keep quality high
    CHUNK_SIZE = 15000
    chunks = [raw_text[i:i+CHUNK_SIZE] for i in range(0, len(raw_text), CHUNK_SIZE)]
    log.info(f"📚 Text split into {len(chunks)} chunk(s) (15000 chars each)")
    
    all_enhanced = []
    meta = {"title": "📄 پست جدید", "titleEn": "", "tags": ["عمومی"]}
    
    async with httpx.AsyncClient(timeout=180.0) as client:
        for idx, chunk in enumerate(chunks):
            is_first = (idx == 0)
            
            if is_first:
                user_prompt = f"Rewrite this content in clear educational Persian, keeping EVERYTHING. Return JSON with title, tags, and enhanced_text:\n\n{chunk}"
            else:
                user_prompt = f"Continue rewriting the following continuation chunk in clear educational Persian. Return ONLY the enhanced_text as plain text (NOT wrapped in JSON). Keep EVERYTHING - no summarization:\n\n{chunk}"
            
            try:
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
                        "max_tokens": 8000,
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
                
                if is_first:
                    try:
                        meta = json.loads(content)
                        enhanced = meta.get("enhanced_text", chunk)
                    except json.JSONDecodeError as e:
                        log.error(f"Failed to parse AI response: {e}")
                        # Never leak raw JSON — keep the ORIGINAL chunk text
                        enhanced = chunk
                        meta = {"title": "📄 پست جدید", "titleEn": "", "tags": ["عمومی"]}
                else:
                    # Continuation chunk - try JSON, else plain text
                    try:
                        parsed = json.loads(content)
                        enhanced = parsed.get("enhanced_text", content)
                    except json.JSONDecodeError:
                        enhanced = chunk
                
                if enhanced:
                    all_enhanced.append(enhanced)
                log.info(f"✅ Chunk {idx+1}/{len(chunks)} processed ({len(enhanced)} chars)")
            except Exception as e:
                log.error(f"❌ Chunk {idx+1} failed: {e}", exc_info=True)
                # Keep original chunk text as fallback so no content is lost
                all_enhanced.append(chunk)
    
    # Join all chunks
    enhanced = "\n\n".join(all_enhanced) if all_enhanced else raw_text
    
    # Append PDF link at the end for direct study
    if pdf_link:
        enhanced += f"\n\n---\n\n📄 <strong>مطالعه کامل (PDF اصلی):</strong> <a href=\"{pdf_link}\">دانلود و مطالعه مستقیم</a>"
    
    enhanced = enhanced.strip()
    # Convert AI HTML block tags (<h2>/<h3>/<p>) to parser-friendly markers
    enhanced = html_block_tags_to_markdown(enhanced)
    
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


async def detect_sections(raw_text):
    """Ask AI to identify the main sections of the document. Returns list of {title, marker}."""
    import httpx, json
    system_prompt = """You are a document structure analyzer. Given the text of a document, identify its MAIN sections.
Return ONLY valid JSON:
{"sections": [{"title": "Section title in English (original)", "marker": "exact 5-10 word phrase from the text that marks the START of this section"}, ...]}

RULES:
- Identify 2 to 12 major sections (the top-level structure of the document). Do NOT include the title page, author info, copyright, or table of contents as sections.
- A section usually begins right after a heading. The "marker" must be a UNIQUE phrase (5-10 consecutive words) that appears verbatim at the beginning of that section's body text.
- Use the LAST section marker to capture everything up to the end of the document.
- If the document has no clear sections (short text, single topic), return {"sections": []}."""

    user_prompt = "Analyze this document and identify its main sections:\n\n" + raw_text[:60000]
    try:
        async with httpx.AsyncClient(timeout=120.0) as client:
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
                    "temperature": 0.2,
                    "max_tokens": 2000,
                }
            )
            result = response.json()
            content_out = result["choices"][0]["message"]["content"].strip()
            if content_out.startswith("```"):
                content_out = content_out.split("\n", 1)[1]
            if content_out.endswith("```"):
                content_out = content_out.rsplit("```", 1)[0]
            sections = json.loads(content_out).get("sections", [])
            log.info(f"🔍 Detected {len(sections)} sections: {[s['title'][:40] for s in sections]}")
            return sections
    except Exception as e:
        log.error(f"❌ Section detection failed: {e}", exc_info=True)
        return []


def split_sections(raw_text, sections):
    """Split raw_text into section chunks using the detected markers."""
    if not sections or len(sections) < 2:
        return []
    
    # Find each marker's position in the text (case-insensitive, normalized)
    positions = []
    for s in sections:
        marker = s.get("marker", "").strip()
        if not marker:
            continue
        # Try exact match first, then case-insensitive
        idx = raw_text.find(marker)
        if idx == -1:
            idx = raw_text.lower().find(marker.lower())
        if idx == -1:
            # Try first 6 words
            words = marker.split()[:6]
            partial = " ".join(words)
            idx = raw_text.find(partial)
            if idx == -1:
                idx = raw_text.lower().find(partial.lower())
        positions.append((idx, s))
    
    # Sort by position, drop misses
    positions = [(idx, s) for idx, s in positions if idx >= 0]
    positions.sort(key=lambda x: x[0])
    
    if len(positions) < 2:
        return []
    
    chunks = []
    for i, (idx, s) in enumerate(positions):
        start = idx
        end = positions[i+1][0] if i+1 < len(positions) else len(raw_text)
        chunk_text = raw_text[start:end].strip()
        if chunk_text:
            chunks.append({"title": s.get("title", f"Section {i+1}"), "text": chunk_text})
    
    # Split any oversized chunks (>15000 chars) evenly so AI quality stays high
    MAX_CHUNK = 15000
    final_chunks = []
    for ch in chunks:
        text = ch["text"]
        if len(text) <= MAX_CHUNK:
            final_chunks.append(ch)
            continue
        # Split evenly into ~15000-char pieces
        n = (len(text) + MAX_CHUNK - 1) // MAX_CHUNK
        piece_size = (len(text) + n - 1) // n
        for j in range(n):
            piece = text[j*piece_size:(j+1)*piece_size].strip()
            if piece:
                suffix = f" (بخش {j+1})" if n > 1 else ""
                final_chunks.append({"title": f"{ch['title']}{suffix}", "text": piece})
        log.info(f"📐 Split oversized section '{ch['title'][:30]}' ({len(text)} chars) into {n} pieces")
    return final_chunks


async def format_section_ai(section_text, section_title, part, total, user_info=""):
    """Educational rewrite of ONE section - full text, NO summarization."""
    import httpx, json, re
    system_prompt = """You are a blog formatting assistant for a Persian medical/biotech blog. Return JSON with:
{
  "title": "Persian title for this section (4-12 words, with emoji, starting with the part number)",
  "tags": ["3-5 Persian tags"],
  "enhanced_text": "the COMPLETE section text rewritten in clear, educational Persian with highlights, emojis, and tables"
}

RULES:
0. **NO SUMMARIZATION - ABSOLUTELY FORBIDDEN: Rewrite the FULL section text.** Every sentence, every number, every table cell, every list item MUST appear in the output. You are REWRITING for clarity, NOT condensing.
0b. **LANGUAGE: ALL enhanced_text MUST be in Persian (فارسی).** Translate every sentence. Keep technical terms, drug names, company names, numbers in Latin form where appropriate.
1. **EDUCATIONAL REWRITE:** Clear, fluent, teaching tone for medical students and professionals. Explain complex concepts simply. Never remove content.
2. Tables: convert markdown tables (| separated) to proper table format - EVERY row and column preserved.
3. Use <span class="hl">IMPORTANT TERMS</span> (purple highlight) for: drug names, protein names, model names, key numbers, breakthroughs. NOT <mark>.
4. Use <strong>BOLD</strong> for key points. Add emojis at section starts: 🔬 💊 🤖 🧬 🧪 📊 🚀 🧫 💉 ⚕️
5. Keep ## headings, ### headings, - lists, * lists - ALL of them, exactly as many as in the input.
6. Use ONLY HTML tags. NEVER markdown (** or *). NEVER <mark>. NEVER backticks.
7. Split into readable paragraphs (2-4 sentences). Blank lines between paragraph breaks. Preserve ALL paragraphs.
8. Tags from: ['هوش مصنوعی', 'مدل‌های زبانی', 'مهندسی پروتئین', 'ESM3', 'PLM', 'طراحی دارو', 'سرمایه‌گذاری', 'تولید دارو', 'آنتی‌بادی مونوکلونال', 'بیوتکنولوژی', 'سلول مجازی', 'اوپن‌سورس', 'ایمونولوژی', 'بیوانفورماتیک']

Output ONLY valid JSON."""

    user_prompt = f"""Rewrite this section (Part {part} of {total}) in clear educational Persian. Section title: {section_title}
Return JSON with title, tags, and enhanced_text:

{section_text[:15000]}"""

    meta = {"title": f"[{part}/{total}] {section_title}", "tags": ["عمومی"]}
    enhanced = section_text
    
    try:
        async with httpx.AsyncClient(timeout=180.0) as client:
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
                    "max_tokens": 8000,
                }
            )
            result = response.json()
            content_out = result["choices"][0]["message"]["content"].strip()
            if content_out.startswith("```"):
                content_out = content_out.split("\n", 1)[1]
            if content_out.endswith("```"):
                content_out = content_out.rsplit("```", 1)[0]
            try:
                meta = json.loads(content_out)
                enhanced = meta.get("enhanced_text", section_text)
            except json.JSONDecodeError:
                # Broken JSON (unescaped quotes/newlines in the text) — NEVER leak the raw JSON.
                # Fall back to the ORIGINAL section text so no content is lost.
                log.warning(f"⚠️ Section '{section_title[:30]}': AI returned broken JSON — using original text ({len(section_text)} chars)")
                enhanced = section_text
            log.info(f"✅ Section '{section_title[:40]}' rewritten ({len(enhanced)} chars)")
    except Exception as e:
        log.error(f"❌ Section '{section_title[:30]}' failed: {e}", exc_info=True)
        enhanced = section_text[:4000]
    
    return meta, enhanced


def html_block_tags_to_markdown(text):
    """The AI is told 'use ONLY HTML tags', so it emits <h2>/<h3>/<p> block tags that the
    markdown-style block parser doesn't understand. Convert block-level HTML to the
    markers the parser handles (## headings, newline paragraphs). Inline HTML stays."""
    if not text:
        return text
    text = re.sub(r'<h[12][^>]*>(.*?)</h[12]>', lambda m: '\n## ' + re.sub(r'<[^>]+>', '', m.group(1)).strip() + '\n', text, flags=re.S)
    text = re.sub(r'<h3[^>]*>(.*?)</h3>', lambda m: '\n## ' + re.sub(r'<[^>]+>', '', m.group(1)).strip() + '\n', text, flags=re.S)
    text = re.sub(r'<h4[^>]*>(.*?)</h4>', lambda m: '\n### ' + re.sub(r'<[^>]+>', '', m.group(1)).strip() + '\n', text, flags=re.S)
    text = re.sub(r'<p[^>]*>', '\n', text)
    text = re.sub(r'</p>', '\n', text)
    text = re.sub(r'<br\s*/?>', '\n', text)
    return text


def blocks_from_text(enhanced):
    """Convert enhanced markdown-ish text into content blocks (same logic as format_with_ai)."""
    enhanced = html_block_tags_to_markdown(enhanced)
    import re
    content_blocks = []
    current_para = []
    in_list = False
    list_items = []
    in_table = False
    table_headers = []
    table_rows = []

    def flush_para(blocks, para, items, in_list):
        if in_list and items:
            blocks.append({"type": "ul", "fa": items})
        if para:
            blocks.append({"type": "p", "fa": "\n".join(para)})

    def flush_para_no_list(blocks, para):
        if para:
            blocks.append({"type": "p", "fa": "\n".join(para)})

    def flush_list(blocks, items):
        if items:
            blocks.append({"type": "ul", "fa": items})

    def flush_table(blocks, headers, rows):
        if headers and rows:
            blocks.append({"type": "table", "fa": {"headers": headers, "rows": rows}})

    for line in enhanced.strip().split('\n'):
        stripped = line.strip()
        if not stripped:
            flush_para(content_blocks, current_para, list_items, in_list)
            current_para, list_items, in_list = [], [], False
            continue
        if stripped.startswith('|') and stripped.endswith('|') and stripped.count('|') > 2:
            flush_para(content_blocks, current_para, list_items, in_list)
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
                flush_table(content_blocks, table_headers, table_rows)
                table_headers, table_rows, in_table = [], [], False
        if stripped.startswith('##') or stripped.startswith('###'):
            flush_para(content_blocks, current_para, list_items, in_list)
            current_para, list_items, in_list = [], [], False
            content_blocks.append({"type": "h3", "fa": re.sub(r'^#+\s*', '', stripped)})
        elif stripped.startswith('- ') or stripped.startswith('* ') or stripped.startswith('• '):
            flush_para_no_list(content_blocks, current_para)
            current_para = []
            in_list = True
            list_items.append(re.sub(r'^[-*•]\s*', '', stripped))
        else:
            if in_list:
                flush_list(content_blocks, list_items)
                list_items, in_list = [], False
            current_para.append(stripped)

    if in_table:
        flush_table(content_blocks, table_headers, table_rows)
    if in_list and list_items:
        flush_list(content_blocks, list_items)
    if current_para:
        content_blocks.append({"type": "p", "fa": "\n".join(current_para)})
    if not content_blocks:
        content_blocks.append({"type": "p", "fa": enhanced.strip()})
    return content_blocks


async def publish_post(raw_text, user_info="", author="", pdf_link=""):
    """Main function: detect sections -> publish each section as a series post."""
    log.info(f"Starting AI formatting... (author: {author})")
    
    # Load current posts
    posts = load_posts()
    date_str = get_persian_date()
    
    # Detect sections first
    sections = await detect_sections(raw_text)
    chunks = split_sections(raw_text, sections)
    
    # If no clear sections -> single post (old behavior)
    if len(chunks) < 2:
        log.info("📄 No multiple sections detected - publishing as single post")
        post_data = await format_with_ai(raw_text, user_info, pdf_link)
        post_data["id"] = get_next_id(posts)
        post_data["date"] = date_str
        post_data["lang"] = "fa"
        if author:
            post_data["author"] = author
        posts.append(post_data)
        save_posts(posts)
        log.info("Deploying to Vercel...")
        success = deploy_to_vercel()
        return post_data, success
    
    # Multiple sections -> create series: 1 parent + N children
    total = len(chunks)
    log.info(f"📚 Publishing {total} sections as a series...")
    
    # Build parent post: series overview with links to all parts
    parent_id = get_next_id(posts)
    first_section_title = chunks[0]["title"]
    parent_title = first_section_title.split(":")[0].strip() if ":" in first_section_title else first_section_title
    parent_title = f"📚 {parent_title} - {total} بخش"
    
    parent_blocks = [{"type": "p", "fa": f"🔖 این مجموعه شامل <strong>{total} بخش</strong> است که هر بخش به یکی از سرفصل‌های اصلی این سند می‌پردازد:"}]
    for i, ch in enumerate(chunks):
        link = f"#post-{parent_id + i + 1}"
        item = f'<a href="{link}">{i+1}. {ch["title"]}</a>'
        parent_blocks.append({"type": "ul", "fa": [item]})
    if pdf_link:
        parent_blocks.append({"type": "p", "fa": f"📄 <strong>مطالعه کامل (PDF اصلی):</strong> <a href=\"{pdf_link}\">دانلود و مطالعه مستقیم</a>"})
    
    parent_post = {
        "id": parent_id,
        "title": parent_title,
        "tags": ["مجموعه", "هوش مصنوعی"],
        "date": date_str,
        "lang": "fa",
        "content": parent_blocks,
        "series": True,
    }
    if author:
        parent_post["author"] = author
    posts.append(parent_post)
    
    # Process each section -> child post
    for i, ch in enumerate(chunks):
        part_num = i + 1
        meta, enhanced = await format_section_ai(ch["text"], ch["title"], part_num, total, user_info)
        if pdf_link:
            enhanced += f"\n\n---\n\n📄 <strong>مطالعه کامل (PDF اصلی):</strong> <a href=\"{pdf_link}\">دانلود و مطالعه مستقیم</a>"
        # Normalize the title: strip AI/source junk ([1/5], بخش ششم:, leading numbers)
        # and force a clean Persian part number prefix so series order is always readable.
        raw_title = meta.get("title", "") or f"[{part_num}/{total}] {ch['title']}"
        t = re.sub(r'^\s*[\[\(]?\d+\s*/\s*\d+[\]\)]?\s*[:：\-–—]?\s*', '', raw_title)          # [1/5]
        t = re.sub(r'^\s*🔟\s*', '', t)                                                        # 🔟 emoji alone
        t = re.sub(r'^\s*[۰-۹0-9]+\s*[\.\-–—]\s*', '', t)                                       # leading "۴." / "5." only WITH separator
        t = re.sub(r'^\s*بخش\s*(?:[۰-۹0-9]+|اول|دوم|سوم|چهارم|پنجم|ششم|هفتم|هشتم|نهم|دهم)\s*[:：\-–—]?\s*', '', t)  # بخش 2:/بخش ششم:
        t = re.sub(r'^\s*🔟\s*', '', t)                                                        # again after بخش strip
        t = re.sub(r'^\s*[۰-۹0-9]+\s*[\.\-–—]\s*', '', t)                                       # again after بخش strip
        t = re.sub(r'\s*[\(（]بخش\s*[۰-۹0-9]+\s*[\)）]\s*$', '', t)                             # trailing (بخش 1)
        t = t.strip() or ch["title"]
        persian_parts = {1: '۱', 2: '۲', 3: '۳', 4: '۴', 5: '۵', 6: '۶', 7: '۷', 8: '۸', 9: '۹', 10: '۱۰'}
        clean_title = f"{persian_parts.get(part_num, str(part_num))}. {t}"
        child_post = {
            "id": parent_id + part_num,
            "parentId": parent_id,
            "title": clean_title,
            "tags": meta.get("tags", ["عمومی"]),
            "date": date_str,
            "lang": "fa",
            "series": parent_title,
            "seriesPart": part_num,
            "seriesTotal": total,
            "content": blocks_from_text(enhanced),
        }
        if author:
            child_post["author"] = author
        posts.append(child_post)
        log.info(f"✅ Published part {part_num}/{total}: {child_post['title'][:50]}")
    
    # Save once and deploy once
    save_posts(posts)
    log.info("Deploying to Vercel...")
    success = deploy_to_vercel()
    return parent_post, success


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
            
        elif file_name.endswith('.docx'):
            # Extract text from DOCX — it's a ZIP containing word/document.xml
            import zipfile, io
            try:
                with zipfile.ZipFile(io.BytesIO(bytes(file_bytes))) as z:
                    xml = z.read('word/document.xml').decode('utf-8', errors='replace')
                # Paragraph boundaries, then strip all XML tags, then unescape entities
                xml = re.sub(r'</w:p>', '\n', xml)
                text = re.sub(r'<[^>]+>', '', xml)
                text = text.replace('&amp;', '&').replace('&lt;', '<').replace('&gt;', '>').replace('&quot;', '"').replace('&#39;', "'")
                lines = [l.strip() for l in text.split('\n') if l.strip()]
                text = '\n'.join(lines)
                log.info(f"📄 Extracted {len(text)} chars from DOCX: {doc.file_name}")
            except Exception as e:
                log.error(f"⚠️ DOCX extraction failed: {e}", exc_info=True)
                await msg.edit_text("❌ استخراج متن از DOCX ناموفق بود. لطفاً PDF یا TXT بفرست.")
                return

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