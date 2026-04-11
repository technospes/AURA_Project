"""
PAGE CONTEXT ENGINE — Screen Awareness + Site Summarizer + Smart URL Opener
============================================================================
Three features in one module:

  1. SMART OPEN — "open Notion" / "open Jarvis GitHub" with unknown extensions
     → Searches DuckDuckGo, takes the top organic result URL, opens it directly
     No more "https://notion.com vs notion.app vs notion.so" guessing.

  2. READ ALOUD — "read this" / "read aloud"
     → Grabs current browser tab text (Ctrl+A, Ctrl+C)
     → Cleans it (strips nav/ads/boilerplate)
     → Speaks it in chunks so TTS doesn't time out

  3. PAGE SUMMARY — "what is this site about" / "summarize this page"
     → Extracts page text
     → Uses LLM to generate a complete brief:
       - What the site/page is (type, purpose)
       - Key topics covered
       - Who it's for
       - Any important facts, prices, specs if present
     → Speaks the brief, stores full summary in memory

Place at: agent/page_context.py
Used by: agent/core.py, executor/runner.py
"""

import asyncio
import logging
import re
import time
from typing import Optional, Tuple

logger = logging.getLogger(__name__)

# ── SMART URL OPENER ────────────────────────────────────────────────────────

async def smart_open(query: str) -> Tuple[str, str]:
    """
    Find the real URL for a vague site name and open it.

    "open Notion"             → notion.so
    "open Jarvis on GitHub"   → github.com/...
    "open Hugging Face"       → huggingface.co
    "open the IRCTC site"     → irctc.co.in

    Returns: (url_opened, site_title)
    """
    import webbrowser

    # First: check if it's in our known registry
    url = _registry_lookup(query)
    if url:
        logger.info(f"🔗 Registry match: {query} → {url}")
        webbrowser.open(url)
        return url, query

    # Second: DuckDuckGo instant answer
    url, title = await _ddg_first_result(query)
    if url:
        logger.info(f"🔗 DDG result: {query} → {url}")
        webbrowser.open(url)
        return url, title

    # Fallback: Google search
    from urllib.parse import quote_plus
    search_url = f"https://www.google.com/search?q={quote_plus(query)}"
    logger.info(f"🔗 Fallback Google: {search_url}")
    webbrowser.open(search_url)
    return search_url, query


def _registry_lookup(query: str) -> Optional[str]:
    """Fast lookup for common sites where we know the exact URL."""
    q = query.lower().strip()
    # Strip "open", "launch", "go to", "visit", "the", "website", "site"
    for word in ["open ", "launch ", "go to ", "visit ", "the ", " website", " site", " app"]:
        q = q.replace(word, "").strip()

    KNOWN_SITES = {
        # Productivity
        "notion": "https://notion.so",
        "obsidian": "https://obsidian.md",
        "trello": "https://trello.com",
        "asana": "https://app.asana.com",
        "linear": "https://linear.app",
        "jira": "https://jira.atlassian.com",
        "confluence": "https://confluence.atlassian.com",
        "monday": "https://monday.com",
        "clickup": "https://app.clickup.com",
        "todoist": "https://todoist.com",

        # AI
        "claude": "https://claude.ai",
        "chatgpt": "https://chat.openai.com",
        "gemini": "https://gemini.google.com",
        "perplexity": "https://perplexity.ai",
        "huggingface": "https://huggingface.co",
        "hugging face": "https://huggingface.co",
        "groq": "https://console.groq.com",
        "replicate": "https://replicate.com",
        "midjourney": "https://www.midjourney.com",
        "elevenlabs": "https://elevenlabs.io",

        # Dev
        "github": "https://github.com",
        "vercel": "https://vercel.com",
        "netlify": "https://netlify.com",
        "render": "https://render.com",
        "railway": "https://railway.app",
        "supabase": "https://supabase.com",
        "mongodb atlas": "https://cloud.mongodb.com",
        "planetscale": "https://planetscale.com",
        "docker hub": "https://hub.docker.com",
        "npm": "https://npmjs.com",
        "pypi": "https://pypi.org",
        "stack overflow": "https://stackoverflow.com",
        "mdn": "https://developer.mozilla.org",

        # Shopping India
        "flipkart": "https://flipkart.com",
        "amazon india": "https://amazon.in",
        "myntra": "https://myntra.com",
        "meesho": "https://meesho.com",
        "nykaa": "https://nykaa.com",
        "ajio": "https://ajio.com",

        # Indian utilities
        "irctc": "https://irctc.co.in",
        "gpay": "https://pay.google.com",
        "phonepe": "https://phonepe.com",
        "paytm": "https://paytm.com",
        "cred": "https://cred.club",
        "zerodha": "https://zerodha.com",
        "groww": "https://groww.in",

        # Social
        "twitter": "https://twitter.com",
        "x": "https://x.com",
        "instagram": "https://instagram.com",
        "linkedin": "https://linkedin.com",
        "reddit": "https://reddit.com",
        "pinterest": "https://pinterest.com",
        "quora": "https://quora.com",
        "mastodon": "https://mastodon.social",
        "bluesky": "https://bsky.app",
        "threads": "https://threads.net",

        # Entertainment
        "spotify": "https://open.spotify.com",
        "youtube music": "https://music.youtube.com",
        "netflix": "https://netflix.com",
        "hotstar": "https://hotstar.com",
        "prime video": "https://primevideo.com",
        "jiocinema": "https://jiocinema.com",
        "zee5": "https://zee5.com",
        "sony liv": "https://sonyliv.com",

        # Google services
        "google drive": "https://drive.google.com",
        "google docs": "https://docs.google.com",
        "google sheets": "https://sheets.google.com",
        "google slides": "https://slides.google.com",
        "google meet": "https://meet.google.com",
        "google photos": "https://photos.google.com",
        "gmail": "https://mail.google.com",
        "google maps": "https://maps.google.com",
        "google translate": "https://translate.google.com",
    }
    return KNOWN_SITES.get(q)


async def _ddg_first_result(query: str) -> Tuple[Optional[str], str]:
    """Search DuckDuckGo and return the first organic (non-ad) URL."""
    try:
        from duckduckgo_search import DDGS
        loop = asyncio.get_event_loop()

        def _search():
            with DDGS(timeout=8) as ddgs:
                results = list(ddgs.text(
                    f"{query} official site",
                    max_results=3,
                    safesearch="off"
                ))
            return results

        results = await loop.run_in_executor(None, _search)
        if not results:
            return None, ""

        # Prefer results that look like official sites (short domain, no Wikipedia)
        for r in results:
            url = r.get("href", "")
            title = r.get("title", "")
            if url and "wikipedia" not in url and "wikia" not in url:
                return url, title

        # Fallback to first result
        first = results[0]
        return first.get("href"), first.get("title", query)

    except Exception as e:
        logger.warning(f"DDG smart open failed: {e}")
        return None, ""


# ── PAGE TEXT EXTRACTOR ────────────────────────────────────────────────────

async def extract_page_text(max_chars: int = 6000) -> str:
    """
    Extract visible text from the currently focused browser tab.
    
    Method 1: Win32 accessibility API (most accurate)
    Method 2: Ctrl+A → Ctrl+C clipboard (fallback)
    Method 3: Screenshot + OCR (heavy fallback)
    
    Returns cleaned text ready for TTS or LLM summarization.
    """
    loop = asyncio.get_event_loop()

    # Method 1: Try clipboard approach (most compatible)
    text = await loop.run_in_executor(None, _extract_via_clipboard)
    if text and len(text.strip()) > 100:
        return _clean_page_text(text, max_chars)

    # Method 2: Try win32com browser DOM (if available)
    text = await loop.run_in_executor(None, _extract_via_accessibility)
    if text and len(text.strip()) > 100:
        return _clean_page_text(text, max_chars)

    return "I couldn't read the page content. Make sure a browser tab is active and focused."


def _extract_via_clipboard() -> str:
    """Ctrl+A, Ctrl+C to grab page text into clipboard."""
    try:
        import pyautogui
        import pyperclip
        import time

        # Save current clipboard
        try:
            old_clip = pyperclip.paste()
        except Exception:
            old_clip = ""

        # Select all + copy
        pyautogui.hotkey("ctrl", "a")
        time.sleep(0.15)
        pyautogui.hotkey("ctrl", "c")
        time.sleep(0.3)

        text = pyperclip.paste()

        # Restore clipboard
        try:
            pyperclip.copy(old_clip)
        except Exception:
            pass

        if text and text != old_clip:
            return text
        return ""

    except Exception as e:
        logger.debug(f"Clipboard extraction failed: {e}")
        return ""


def _extract_via_accessibility() -> str:
    """Use Windows accessibility APIs to get browser text."""
    try:
        import subprocess
        # Try using UI automation via PowerShell
        ps_script = """
        Add-Type -AssemblyName UIAutomationClient
        $root = [System.Windows.Automation.AutomationElement]::RootElement
        $condition = [System.Windows.Automation.Condition]::TrueCondition
        $focusedWindow = [System.Windows.Automation.AutomationElement]::FocusedElement
        $text = $focusedWindow.GetCurrentPropertyValue([System.Windows.Automation.AutomationElement]::NameProperty)
        Write-Output $text
        """
        result = subprocess.run(
            ["powershell", "-Command", ps_script],
            capture_output=True, text=True, timeout=5
        )
        return result.stdout.strip()
    except Exception:
        return ""


def _clean_page_text(raw: str, max_chars: int) -> str:
    """Strip boilerplate, navigation, and whitespace from page text."""
    lines = raw.split("\n")
    cleaned = []
    skip_patterns = re.compile(
        r'^(cookie|privacy policy|terms of service|sign in|log in|'
        r'subscribe|newsletter|advertisement|loading|skip to|'
        r'navigation|menu|search|home|about|contact|copyright|©)',
        re.IGNORECASE
    )

    for line in lines:
        line = line.strip()
        if not line or len(line) < 3:
            continue
        if skip_patterns.match(line):
            continue
        cleaned.append(line)

    # Deduplicate adjacent identical lines
    deduped = []
    prev = None
    for line in cleaned:
        if line != prev:
            deduped.append(line)
            prev = line

    result = "\n".join(deduped)
    return result[:max_chars]


# ── PAGE SUMMARIZER ────────────────────────────────────────────────────────

async def summarize_current_page(groq_api_key: str, context: dict = None) -> Tuple[str, str]:
    """
    Extracts and summarizes the current browser page.
    
    Returns: (spoken_summary, full_summary)
    Spoken summary: 3-4 sentences, TTS-friendly
    Full summary: detailed markdown, stored in memory
    """
    logger.info("📄 Extracting page text...")
    page_text = await extract_page_text(max_chars=5000)

    if "couldn't read" in page_text or len(page_text.strip()) < 50:
        msg = "I couldn't read the page content. Please make sure a browser window is active."
        return msg, msg

    # Get page URL from context or active window title for reference
    page_url = (context or {}).get("last_url", "")
    active_window = (context or {}).get("active_window_title", "current page")

    spoken, full = await _llm_summarize(page_text, active_window, page_url, groq_api_key)
    return spoken, full


async def _llm_summarize(text: str, title: str, url: str, api_key: str) -> Tuple[str, str]:
    """Use LLM to generate both a spoken and a detailed summary."""
    try:
        from groq import Groq
        client = Groq(api_key=api_key)
        loop = asyncio.get_event_loop()

        prompt = f"""You are Jarvis. Analyze this page content and give a complete brief.

Page title/window: {title}
URL: {url}

Page content (first 4000 chars):
{text[:4000]}

Provide TWO outputs:

SPOKEN (3-4 sentences, conversational, no markdown, no bullet points):
[Write as if speaking naturally: "This appears to be... It covers... The main purpose is..."]

FULL (detailed brief with sections):
**What this is:** [type of site/page — news article, product page, documentation, forum, etc.]
**Main topic:** [1-2 sentences]
**Key points:** [3-5 bullet points of the most important information]
**Who it's for:** [target audience]
**Notable details:** [prices, dates, specs, names, statistics if present]

Return both sections clearly labeled as SPOKEN: and FULL:"""

        def _call():
            return client.chat.completions.create(
                model="llama-3.1-8b-instant",
                messages=[
                    {"role": "system", "content": "You are Jarvis. Be precise, factual, and concise."},
                    {"role": "user", "content": prompt}
                ],
                temperature=0.2,
                max_tokens=600
            )

        resp = await loop.run_in_executor(None, _call)
        content = resp.choices[0].message.content.strip()

        # Parse SPOKEN and FULL sections
        spoken = _extract_section(content, "SPOKEN")
        full = _extract_section(content, "FULL")

        if not spoken:
            spoken = content[:300]
        if not full:
            full = content

        return spoken.strip(), full.strip()

    except Exception as e:
        logger.error(f"Page summarization LLM failed: {e}")
        # Simple fallback: first 200 chars of cleaned text
        fallback = text[:300].strip()
        return fallback, text[:2000]


def _extract_section(text: str, label: str) -> str:
    """Extract a labeled section from LLM output."""
    pattern = re.compile(
        rf'{label}:?\s*(.*?)(?=(?:SPOKEN|FULL):|$)',
        re.DOTALL | re.IGNORECASE
    )
    m = pattern.search(text)
    return m.group(1).strip() if m else ""


# ── READ ALOUD ─────────────────────────────────────────────────────────────

async def read_page_aloud(speak_fn, groq_api_key: str, context: dict = None):
    """
    Read current page content aloud in natural chunks.
    Cleans the text first, then speaks it in ~30-second segments.
    """
    text = await extract_page_text(max_chars=3000)

    if "couldn't read" in text or len(text.strip()) < 50:
        speak_fn("I couldn't read the page. Please make sure a browser window is focused.")
        return

    # Clean and chunk for TTS
    sentences = re.split(r'(?<=[.!?])\s+', text)

    chunk = []
    chunk_len = 0

    for sentence in sentences:
        sentence = sentence.strip()
        if not sentence:
            continue
        chunk.append(sentence)
        chunk_len += len(sentence)

        # Speak every ~400 chars (roughly 20-25 seconds of speech)
        if chunk_len > 400:
            speak_fn(" ".join(chunk))
            chunk = []
            chunk_len = 0
            await asyncio.sleep(0.1)  # Small gap between chunks

    if chunk:
        speak_fn(" ".join(chunk))
