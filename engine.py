"""
engine.py
=========
Core automation engine for generating images via Google Flow using Playwright
and a pre-authenticated session state.

Features:
- Validates session state file existence before launching.
- Uses headless Chromium with anti-detection and stealth arguments.
- Detects Google session expiry and redirection to accounts.google.com.
- Advanced settings control:
    * Aspect ratios: 1:1, 16:9, 9:16, 4:3, 3:4
    * Model variants: Nano Banana 2, Nano Banana Pro, Nano Banana 2 Lite
    * Batch outputs: 1, 2, 3, 4 parallel generations
    * Reference images upload: Multi-image ingredients (capped at max 3)
    * Style presets: Photorealistic, Cinematic, Anime, Digital Art, 3D Render
    * Negative prompt & seed controls
- Bulletproof per-prompt DOM stamping (`data-generation-uuid`):
  Each generation request is bound to a unique UUID from creation to completion.
- Multi-variation extraction: Captures all variations (1 to 4) per generation batch
  and tracks their unique Google Flow Media IDs (`data-media-id`).
- Non-destructive output file preservation: Safely numbers existing files unless `--overwrite` is specified.
- Instant error detection: Catches policy violations and safety rejections immediately (`GenerationFailedError`).
"""

import argparse
import asyncio
import base64
import io
import os
import random
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Optional, Union, List

from dotenv import load_dotenv
from PIL import Image
from playwright.async_api import (
    Browser,
    BrowserContext,
    Page,
    TimeoutError as PlaywrightTimeoutError,
    async_playwright,
)

# Load environment configuration
load_dotenv()

FLOW_URL = os.getenv("FLOW_URL", "https://flow.google")
STORAGE_STATE_PATH = os.getenv("STORAGE_STATE_PATH", "storage_state.json")
DEFAULT_TIMEOUT = int(os.getenv("TIMEOUT_SECONDS", "30"))
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/128.0.0.0 Safari/537.36"
)

# Style Preset Prompt Modifiers
STYLE_PRESETS = {
    "None": "",
    "Photorealistic": "photorealistic, hyperrealistic 8k, highly detailed photography, natural lighting, shot on 35mm lens",
    "Cinematic": "cinematic film still, 35mm movie photography, dramatic volumetric lighting, anamorphic, color graded",
    "Anime": "vibrant anime style, Japanese animation aesthetic, clean crisp linework, studio ghibli and makoto shinkai inspired",
    "Digital Art": "digital concept art, trending on artstation, intricate digital painting, vibrant vivid details",
    "3D Render": "3D render, octane render, unreal engine 5, raytracing, subsurface scattering, 8k resolution",
}

# Candidate selectors for prompt input across Google Flow / Labs UI variations
PROMPT_SELECTORS = [
    "div.ProseMirror",
    "[contenteditable='true']",
    "[placeholder*='What do you want to create' i]",
    "textarea[placeholder*='create' i]",
    "input[placeholder*='create' i]",
    "textarea",
    "[role='textbox']",
]

# Candidate selectors for submit / generate action
SUBMIT_SELECTORS = [
    "button[aria-label='Start generation']",
    "button.generate-icon-button",
    "button[type='submit'][aria-label*='generation' i]",
    "button:has-text('Start generation')",
    "button[aria-label*='Generate' i]",
    "button[aria-label*='Submit' i]",
    "button[aria-label*='Send' i]",
    "button:has([data-icon*='arrow' i])",
]


class SessionExpiredError(Exception):
    """Raised when the session has expired and Google redirects to accounts.google.com."""
    pass


class GenerationFailedError(Exception):
    """Raised when Google Flow rejects a prompt or reports a generation failure / policy violation."""
    pass


class GenerationTimeoutError(Exception):
    """Raised when image generation times out without producing output."""
    pass


class ProxyConnectionError(Exception):
    """Raised when Webshare or upstream proxy connection fails after retries."""
    pass


# Global persistent session identifier for legacy ScraperAPI fallback
_PERSISTENT_SCRAPERAPI_SESSION_ID = os.environ.get("SCRAPERAPI_SESSION_ID", "").strip() or f"flow_{uuid.uuid4().hex[:8]}"


def get_current_scraperapi_session_id() -> str:
    """Retrieves the active sticky session ID for legacy ScraperAPI fallback."""
    global _PERSISTENT_SCRAPERAPI_SESSION_ID
    env_id = os.environ.get("SCRAPERAPI_SESSION_ID", "").strip()
    if env_id:
        return env_id
    if not _PERSISTENT_SCRAPERAPI_SESSION_ID:
        _PERSISTENT_SCRAPERAPI_SESSION_ID = f"flow_{uuid.uuid4().hex[:8]}"
    return _PERSISTENT_SCRAPERAPI_SESSION_ID


def rotate_scraperapi_session_id() -> str:
    """Rotates to a new sticky session ID for legacy ScraperAPI fallback."""
    global _PERSISTENT_SCRAPERAPI_SESSION_ID
    _PERSISTENT_SCRAPERAPI_SESSION_ID = f"flow_{uuid.uuid4().hex[:8]}"
    print(f"[INFO] Rotated ScraperAPI session ID to: {_PERSISTENT_SCRAPERAPI_SESSION_ID}")
    return _PERSISTENT_SCRAPERAPI_SESSION_ID


import hashlib
import re
from urllib.parse import urlsplit


def _to_session_integer(session_val: Union[str, int]) -> int:
    """Converts string or int into a deterministic positive integer for ScraperAPI session_number."""
    if isinstance(session_val, int):
        return abs(session_val)
    val_str = str(session_val).strip()
    if val_str.isdigit():
        return int(val_str)
    h = hashlib.md5(val_str.encode("utf-8")).hexdigest()
    return int(h[:7], 16) % 900000 + 100000


def parse_single_proxy(proxy_str: str) -> Optional[dict]:
    """
    Parses a proxy string into a Playwright-compatible proxy dictionary:
    {'server': 'http://host:port', 'username': '...', 'password': '...'}

    Supports multiple formats:
      - Standard URI: http://username:password@ip:port or socks5://...
      - User:Pass before host: username:password@ip:port
      - Webshare list export: ip:port:username:password
      - Direct IP:port (IP authorization): ip:port or http://ip:port
    """
    if not proxy_str or not proxy_str.strip():
        return None
    raw = proxy_str.strip()

    # Format 1: ip:port:username:password (Standard Webshare proxy list export)
    parts = raw.split(":")
    if len(parts) == 4 and not raw.startswith("http://") and not raw.startswith("https://") and not raw.startswith("socks5://"):
        host, port, user, pwd = parts
        return {
            "server": f"http://{host.strip()}:{port.strip()}",
            "username": user.strip(),
            "password": pwd.strip(),
        }

    # Format 2: Standard URI scheme (http://, https://, socks5://) or user:pass@host:port
    if not (raw.startswith("http://") or raw.startswith("https://") or raw.startswith("socks5://")):
        raw = f"http://{raw}"

    try:
        parsed = urlsplit(raw)
        scheme = parsed.scheme or "http"
        port_suffix = f":{parsed.port}" if parsed.port else ""
        server_url = f"{scheme}://{parsed.hostname}{port_suffix}"
        cfg = {"server": server_url}
        if parsed.username:
            cfg["username"] = parsed.username
        if parsed.password:
            cfg["password"] = parsed.password
        return cfg
    except Exception as exc:
        print(f"[WARNING] Could not parse proxy string '{raw}': {exc}")
        return None


def get_webshare_proxies() -> List[dict]:
    """
    Reads Webshare or static proxy configurations from environment variables.
    Checks:
      - WEBSHARE_PROXY_URL
      - WEBSHARE_PROXY_LIST
      - PROXY_URL
      - RESIDENTIAL_PROXY_URL

    Supports single proxy URL or comma/newline-separated lists of multiple proxies.
    """
    raw = (
        os.environ.get("WEBSHARE_PROXY_URL", "")
        or os.environ.get("WEBSHARE_PROXY_LIST", "")
        or os.environ.get("PROXY_URL", "")
        or os.environ.get("RESIDENTIAL_PROXY_URL", "")
    ).strip()
    if not raw:
        return []

    entries = re.split(r'[\r\n,;]+', raw)
    proxies = []
    for entry in entries:
        cleaned = entry.strip()
        if cleaned:
            parsed = parse_single_proxy(cleaned)
            if parsed:
                proxies.append(parsed)
    return proxies


def get_configured_proxy(proxy_index: int = 0) -> Optional[dict]:
    """
    Resolves the active proxy configuration for a generation request:
    1. Checks Webshare / static proxy list.
       If WEBSHARE_PROXY_INDEX is set in .env, it forces that specific proxy index.
       Otherwise, uses proxy_index (allowing round-robin / worker distribution across the 10 IPs).
    2. Falls back to legacy ScraperAPI if SCRAPERAPI_KEY is present and no Webshare proxy is defined.
    3. Returns None for direct connection.
    """
    webshare_list = get_webshare_proxies()
    if webshare_list:
        env_idx = os.environ.get("WEBSHARE_PROXY_INDEX", "").strip().strip("\"'")
        if env_idx.isdigit():
            idx = int(env_idx)
        else:
            idx = proxy_index
        return webshare_list[idx % len(webshare_list)]

    scraperapi_key = os.environ.get("SCRAPERAPI_KEY", "").strip()
    if scraperapi_key:
        session_id = get_current_scraperapi_session_id()
        session_int = _to_session_integer(session_id)
        return {
            "server": "http://proxy-server.scraperapi.com:8001",
            "username": f"scraperapi.session_number={session_int}",
            "password": scraperapi_key,
        }

    return None


def format_proxy_for_log(proxy_cfg: Optional[dict]) -> str:
    """Returns a safe, masked representation of the proxy for logging."""
    if not proxy_cfg:
        return "Direct Connection (No Proxy)"
    server = proxy_cfg.get("server", "")
    username = proxy_cfg.get("username", "")
    if username:
        return f"{server} (User: {username})"
    return server


def get_scraperapi_proxy_config(session_id: Optional[str] = None) -> Optional[dict]:
    """Legacy helper for backward compatibility."""
    return get_configured_proxy()


def is_proxy_network_error(exc: Exception) -> bool:
    """Detects if an exception is related to proxy gateway or connection failure."""
    err_str = str(exc).lower()
    proxy_indicators = [
        "502",
        "504",
        "bad gateway",
        "gateway timeout",
        "err_proxy_connection_failed",
        "err_tunnel_connection_failed",
        "err_connection_reset",
        "err_connection_refused",
        "err_proxy_auth_unsupported",
        "err_empty_response",
        "proxy-server.scraperapi.com",
        "webshare",
        "p.webshare.io",
        "proxy",
    ]
    return any(ind in err_str for ind in proxy_indicators)


STEALTH_INIT_SCRIPT = """
// 1. Webdriver evasion
Object.defineProperty(navigator, 'webdriver', {
    get: () => undefined
});

// 2. Languages
Object.defineProperty(navigator, 'languages', {
    get: () => ['en-US', 'en']
});

// 3. Plugins & MimeTypes
const mockPlugins = [
    { name: 'Chrome PDF Plugin', filename: 'internal-pdf-viewer', description: 'Portable Document Format' },
    { name: 'Chrome PDF Viewer', filename: 'mhjfbmdgcfjbbpaeojofohoefgiehjai', description: '' },
    { name: 'Native Client', filename: 'internal-nacl-plugin', description: '' }
];
Object.defineProperty(navigator, 'plugins', {
    get: () => mockPlugins
});

// 4. Chrome object
window.chrome = {
    app: { isInstalled: false },
    runtime: {},
    loadTimes: () => {},
    csi: () => {}
};

// 5. WebGL Vendor & Renderer spoofing
const getParameter = WebGLRenderingContext.prototype.getParameter;
WebGLRenderingContext.prototype.getParameter = function(parameter) {
    if (parameter === 37445) return 'Intel Inc.';
    if (parameter === 37446) return 'Intel(R) Iris(R) Xe Graphics Direct3D11 vs_5_0 ps_5_0';
    return getParameter.apply(this, arguments);
};
if (window.WebGL2RenderingContext) {
    const getParameter2 = WebGL2RenderingContext.prototype.getParameter;
    WebGL2RenderingContext.prototype.getParameter = function(parameter) {
        if (parameter === 37445) return 'Intel Inc.';
        if (parameter === 37446) return 'Intel(R) Iris(R) Xe Graphics Direct3D11 vs_5_0 ps_5_0';
        return getParameter2.apply(this, arguments);
    };
}
"""


def validate_session_state(storage_path: str = STORAGE_STATE_PATH) -> None:
    """Verifies that the session state JSON file exists and is non-empty."""
    if not os.path.exists(storage_path) or os.path.getsize(storage_path) == 0:
        error_msg = (
            f"\n[ERROR] Session state file '{storage_path}' was not found or is empty.\n"
            f"Please run the interactive login script first to export your authenticated session:\n"
            f"    python login_session.py\n"
        )
        print(error_msg, file=sys.stderr)
        raise FileNotFoundError(f"Missing session state: '{storage_path}'")


def is_google_signin_url(url: str) -> bool:
    """Detects if a URL is an explicit Google sign-in / login redirect."""
    u = (url or "").lower()
    return "accounts.google.com" in u and any(
        term in u for term in ["signin", "servicelogin", "v3/signin", "identifier", "accountchooser"]
    )


def _check_url_for_expiry(url: str) -> None:
    """Detects if page was redirected to Google Accounts login page."""
    if is_google_signin_url(url):
        print("SESSION_EXPIRED: Please update storage_state.json")
        raise SessionExpiredError("SESSION_EXPIRED: Please update storage_state.json")


def _get_safe_destination_path(
    base_path: Path,
    variation_idx: int,
    overwrite: bool = False
) -> Path:
    """
    Computes a non-conflicting destination file path.
    - Variation 0 -> base_path (e.g. output.png)
    - Variation 1 -> base_stem_2.png, etc.
    """
    base_dir = base_path.parent
    base_stem = base_path.stem
    base_suffix = base_path.suffix or ".png"

    if variation_idx == 0:
        target = base_path
    else:
        target = base_dir / f"{base_stem}_{variation_idx + 1}{base_suffix}"

    if overwrite or not target.exists():
        return target

    counter = 1
    while True:
        if variation_idx == 0:
            alt = base_dir / f"{base_stem}_({counter}){base_suffix}"
        else:
            alt = base_dir / f"{base_stem}_{variation_idx + 1}_({counter}){base_suffix}"
        if not alt.exists():
            print(f"[INFO] '{target.name}' already exists. Preserving existing file by saving to '{alt.name}'.")
            return alt
        counter += 1


async def _extract_image_bytes(page: Page, target_or_src) -> bytes:
    """Extracts raw image bytes from an <img>, <canvas>, or direct image URL string."""
    if isinstance(target_or_src, str):
        src = target_or_src
    else:
        tag_name = await target_or_src.evaluate("el => el.tagName.toLowerCase()")
        if tag_name == "canvas":
            data_url = await target_or_src.evaluate("el => el.toDataURL('image/png')")
            header, encoded = data_url.split(",", 1)
            return base64.b64decode(encoded)

        src = await target_or_src.get_attribute("src")
        if not src:
            style = await target_or_src.get_attribute("style") or ""
            if "url(" in style:
                src = style.split("url(")[1].split(")")[0].strip("\"'")

    if not src:
        raise ValueError("Could not find image source URL or canvas data on rendered element.")

    if src.startswith("data:image/"):
        header, encoded = src.split(",", 1)
        return base64.b64decode(encoded)

    if src.startswith("blob:"):
        binary_data = await page.evaluate(
            """async (blobUrl) => {
                const response = await fetch(blobUrl);
                const arrayBuffer = await response.arrayBuffer();
                return Array.from(new Uint8Array(arrayBuffer));
            }""",
            src
        )
        return bytes(binary_data)

    if src.startswith("http://") or src.startswith("https://"):
        try:
            resp = await page.request.get(src)
            if resp.status == 200:
                return await resp.body()
        except Exception:
            pass

        binary_data = await page.evaluate(
            """async (url) => {
                const response = await fetch(url, { credentials: 'include' });
                const arrayBuffer = await response.arrayBuffer();
                return Array.from(new Uint8Array(arrayBuffer));
            }""",
            src
        )
        return bytes(binary_data)

    raise ValueError(f"Unsupported image source scheme: {src[:50]}...")


async def _apply_ui_settings(
    page: Page,
    aspect_ratio: str,
    model_variant: str,
    num_outputs: int
) -> None:
    """
    Configures aspect ratio, model family, and batch output count in Google Flow.
    """
    print(f"[INFO] Configuring generation settings (Model={model_variant}, AspectRatio={aspect_ratio}, Outputs={num_outputs})...")
    settings_btn = await page.query_selector('button[aria-label="Settings trigger"]')
    if not settings_btn:
        print("[WARNING] Settings trigger button not found; continuing with current page settings.")
        return

    await settings_btn.click()
    await page.wait_for_timeout(700)

    # 1. Aspect Ratio Toggle
    if aspect_ratio:
        ar_btn = await page.query_selector(f'mat-button-toggle:has-text("{aspect_ratio}")')
        if ar_btn:
            classes = await ar_btn.get_attribute("class") or ""
            if "mat-button-toggle-checked" not in classes:
                await ar_btn.click()
                await page.wait_for_timeout(300)

    # 2. Number of Outputs Toggle (x1, x2, x3, x4)
    clamped_outputs = max(1, min(4, int(num_outputs)))
    batch_btn = await page.query_selector(f'mat-button-toggle:has-text("x{clamped_outputs}")')
    if batch_btn:
        classes = await batch_btn.get_attribute("class") or ""
        if "mat-button-toggle-checked" not in classes:
            await batch_btn.click()
            await page.wait_for_timeout(300)

    # 3. Model Variant Selection
    if model_variant:
        model_btn = await page.query_selector('button[aria-label="Select model family"]')
        if model_btn:
            curr_text = (await model_btn.inner_text()).lower()
            if model_variant.lower() not in curr_text:
                await model_btn.click()
                await page.wait_for_timeout(500)
                target_option = await page.wait_for_selector(
                    f'.cdk-overlay-pane button:has-text("{model_variant}"), .cdk-overlay-pane [role="menuitem"]:has-text("{model_variant}")',
                    timeout=3000
                )
                if target_option:
                    await target_option.click()
                    await page.wait_for_timeout(400)

    # Close settings menu
    await page.keyboard.press("Escape")
    await page.wait_for_timeout(400)


async def _upload_reference_images(
    page: Page,
    reference_images: List[str]
) -> int:
    """
    Uploads up to 3 reference images (Google Flow max ingredient limit) to the prompt box.
    Handles 'Rights to use this image' agreement modal and commits via 'Add to prompt'.
    """
    valid_paths = [p for p in reference_images if p and os.path.exists(p)][:3]
    if not valid_paths:
        return 0

    print(f"[INFO] Attaching {len(valid_paths)} reference image(s) to prompt...")
    attached_count = 0
    for idx, img_path in enumerate(valid_paths, start=1):
        try:
            add_btn = await page.wait_for_selector(
                'button[aria-label="Add ingredients to the prompt box"]',
                state="visible",
                timeout=8000
            )
            await add_btn.click()
            await page.wait_for_timeout(1000)

            upload_btn = await page.wait_for_selector('button:has-text("Upload media")', timeout=5000)
            if upload_btn:
                async with page.expect_file_chooser(timeout=10000) as fc_info:
                    await upload_btn.click()
                file_chooser = await fc_info.value
                await file_chooser.set_files(img_path)
                print(f"[INFO] Uploaded reference image {idx}: {os.path.basename(img_path)}")

                # Handle Google Flow's 'Rights to use this image' / 'I agree' modal if displayed
                try:
                    agree_btn = await page.wait_for_selector('button:has-text("I agree")', state="visible", timeout=3000)
                    if agree_btn:
                        print(f"[INFO] Accepted 'Rights to use this image' policy dialog for image {idx}.")
                        await agree_btn.click()
                        await page.wait_for_timeout(1000)
                except Exception:
                    pass

                fname = os.path.basename(img_path)
                stem = Path(img_path).stem

                # Critical: In Google Flow's asset drawer, the preview pane defaults to whatever
                # asset was previously selected (e.g. earlier generations).
                # We must explicitly find and click the newly uploaded asset row so the preview
                # switches to our uploaded image BEFORE clicking 'Add to prompt'.
                target_sel = f'button.asset-item:has-text("{fname}"), button.asset-item:has-text("{stem}")'
                try:
                    target_row = await page.wait_for_selector(target_sel, state="visible", timeout=15000)
                    if target_row:
                        await target_row.click()
                        print(f"[INFO] Selected newly uploaded asset item '{fname}' in drawer.")
                        await page.wait_for_timeout(1000)
                except Exception:
                    try:
                        first_row = await page.wait_for_selector('button.asset-item', state="visible", timeout=5000)
                        if first_row:
                            await first_row.click()
                            print(f"[INFO] Selected top asset item in drawer for image {idx}.")
                            await page.wait_for_timeout(1000)
                    except Exception:
                        pass

                # Wait for 'Add to prompt' button to become visible and enabled
                add_to_prompt = await page.wait_for_selector(
                    'button:has-text("Add to prompt"), button.detail-add-to-prompt-btn',
                    state="visible",
                    timeout=15000
                )
                await add_to_prompt.click()
                print(f"[INFO] Clicked 'Add to prompt' for reference image {idx} ({fname}).")
                await page.wait_for_timeout(1500)

                # Confirm ingredient chip attached in prompt dock
                try:
                    await page.wait_for_selector(
                        'flow-ingredient-bar, [class*="ingredient-bar"], [class*="chip-container"]',
                        state="visible",
                        timeout=5000
                    )
                    print(f"[INFO] Reference image {idx} confirmed in prompt dock.")
                except Exception:
                    pass
                attached_count += 1
        except Exception as e:
            print(f"[WARNING] Could not attach reference image {img_path}: {e}")
            await page.keyboard.press("Escape")

    return attached_count


def compose_full_prompt(
    prompt: str,
    negative_prompt: Optional[str] = None,
    style_preset: str = "None",
    seed: int = -1,
    image_strength: float = 0.75
) -> str:
    """Composes base prompt with style preset, negative constraints, and seed modifier."""
    parts = [prompt.strip()]

    # Style Preset
    preset_suffix = STYLE_PRESETS.get(style_preset, "")
    if preset_suffix:
        parts.append(preset_suffix)

    full = ", ".join(parts)

    # Negative Prompt
    if negative_prompt and negative_prompt.strip():
        full = f"{full} --no {negative_prompt.strip()}"

    # Seed
    if seed is not None and seed >= 0:
        full = f"{full} --seed {seed}"

    return full


import re

SENSITIVE_PATTERNS = [
    (re.compile(r'(cookie:\s*)[^\r\n]+', re.IGNORECASE), r'\1[REDACTED]'),
    (re.compile(r'(authorization:\s*bearer\s+)[^\r\n]+', re.IGNORECASE), r'\1[REDACTED]'),
    (re.compile(r'(__Secure-[^=;\s]+)=([^=;\s&"\']+)', re.IGNORECASE), r'\1=[REDACTED]'),
    (re.compile(r'((?:SAPISID|APISID|HSID|SSID|SID|OSID|__Secure-1PSID|__Secure-3PSID))=([^=;\s&"\']+)', re.IGNORECASE), r'\1=[REDACTED]'),
    (re.compile(r'(api_bearer_token|session_storage_base64|scraperapi_key|webshare_password|proxy_password)=([^=;\s&"\']+)', re.IGNORECASE), r'\1=[REDACTED]'),
    (re.compile(r'(https?://[^:@\s]+):([^@\s]+)@', re.IGNORECASE), r'\1:[REDACTED]@'),
    (re.compile(r'(scraperapi\.session_number=[^:]+):([^@\s]+)@', re.IGNORECASE), r'\1:[REDACTED]@'),
]


def sanitize_log_message(msg: str) -> str:
    """Strips sensitive auth headers, cookies, passwords, and tokens from error strings and tracebacks."""
    if not isinstance(msg, str):
        msg = str(msg)
    for pattern, replacement in SENSITIVE_PATTERNS:
        msg = pattern.sub(replacement, msg)
    return msg


async def generate_image(
    prompt: str,
    negative_prompt: Optional[str] = None,
    num_outputs: int = 1,
    aspect_ratio: str = "16:9",
    model_variant: str = "Nano Banana 2",
    reference_images: Optional[Union[List[str], str]] = None,
    image_strength: float = 0.75,
    style_preset: str = "None",
    seed: int = -1,
    output_path: str = "output.png",
    flow_url: str = FLOW_URL,
    storage_state_path: str = STORAGE_STATE_PATH,
    headless: bool = True,
    timeout: int = DEFAULT_TIMEOUT,
    overwrite: bool = False,
    proxy_index: int = 0,
) -> List[str]:
    """
    Automates image generation on Google Flow with complete parameter mapping
    and deterministic per-prompt UUID binding.
    """
    # 1. Verify session state existence
    print("[INFO] Checking session state...")
    validate_session_state(storage_state_path)

    browser: Optional[Browser] = None
    context: Optional[BrowserContext] = None

    try:
        async with async_playwright() as p:
            # 2. Launch Chromium with anti-detection flags & WebRTC leak prevention
            print(f"[INFO] Launching Browser (headless={headless})...")
            launch_kwargs = {
                "headless": headless,
                "args": [
                    "--disable-blink-features=AutomationControlled",
                    "--disable-features=IsolateOrigins,site-per-process",
                    "--no-sandbox",
                    "--disable-setuid-sandbox",
                    "--disable-dev-shm-usage",
                    "--disable-infobars",
                    "--window-size=1920,1080",
                    "--force-webrtc-ip-handling-policy=disable_non_proxied_udp",
                    "--disable-http2",
                    "--disable-quic",
                    "--ignore-certificate-errors",
                ],
            }

            try:
                browser = await p.chromium.launch(**launch_kwargs)
            except Exception:
                try:
                    browser = await p.chromium.launch(channel="chrome", **launch_kwargs)
                except Exception as launch_exc:
                    if "playwright install" in str(launch_exc).lower() or "doesn't exist" in str(launch_exc).lower():
                        print("[INFO] Browser executable missing; running playwright install...")
                        subprocess.run([sys.executable, "-m", "playwright", "install"], check=True)
                        browser = await p.chromium.launch(**launch_kwargs)
                    else:
                        raise launch_exc

            # 3. Helper to create context with Webshare/Residential proxy & stealth
            async def _init_context_and_page(active_proxy: Optional[dict]) -> tuple[BrowserContext, Page]:
                ctx_kwargs = {
                    "storage_state": storage_state_path,
                    "viewport": {"width": 1920, "height": 1080},
                    "user_agent": DEFAULT_USER_AGENT,
                    "locale": "en-US",
                    "timezone_id": "America/New_York",
                    "ignore_https_errors": True,
                }
                if active_proxy:
                    ctx_kwargs["proxy"] = active_proxy
                    print(f"[INFO] Routing traffic via proxy: {format_proxy_for_log(active_proxy)}")
                else:
                    print("[INFO] No proxy configured. Operating in direct network connection mode.")

                ctx = await browser.new_context(**ctx_kwargs)
                pg = await ctx.new_page()
                await pg.add_init_script(STEALTH_INIT_SCRIPT)
                return ctx, pg

            # 4. Proxy Failover & Initialization Retry Loop
            webshare_list = get_webshare_proxies()
            has_proxies = bool(webshare_list) or bool(os.environ.get("SCRAPERAPI_KEY", "").strip())
            max_attempts = 2 if has_proxies else 1
            current_proxy_idx = proxy_index
            prompt_el = None
            session_status = {"expired": False}

            def check_session():
                if session_status["expired"] or (page and is_google_signin_url(page.url)):
                    print("SESSION_EXPIRED: Please update storage_state.json")
                    raise SessionExpiredError("SESSION_EXPIRED: Please update storage_state.json")

            for attempt in range(1, max_attempts + 1):
                try:
                    if context:
                        try:
                            await context.close()
                        except Exception:
                            pass

                    active_proxy = get_configured_proxy(current_proxy_idx)
                    context, page = await _init_context_and_page(active_proxy)

                    def _on_frame_navigated(frame):
                        if frame == page.main_frame and is_google_signin_url(frame.url):
                            session_status["expired"] = True

                    page.on("framenavigated", _on_frame_navigated)

                    proxy_desc = format_proxy_for_log(active_proxy)
                    print(f"[INFO] Navigating to {flow_url} (Proxy: {proxy_desc}, Attempt {attempt}/{max_attempts})...")
                    try:
                        resp = await page.goto(flow_url, timeout=timeout * 1000)
                        if resp and resp.status in [502, 504]:
                            raise ProxyConnectionError(f"Proxy gateway error HTTP {resp.status}")
                    except Exception as nav_err:
                        if is_proxy_network_error(nav_err) or "502" in str(nav_err) or "504" in str(nav_err):
                            raise ProxyConnectionError(f"Proxy network error during navigation: {nav_err}")
                        print(f"[INFO] Initial navigation encountered {nav_err}; retrying...")
                        await asyncio.sleep(1)
                        resp = await page.goto(flow_url, timeout=timeout * 1000)
                        if resp and resp.status in [502, 504]:
                            raise ProxyConnectionError(f"Proxy gateway error HTTP {resp.status}")

                    await page.wait_for_timeout(4000)
                    check_session()

                    # Handle landing page if presented (e.g. redirected to /about)
                    if "/about" in page.url:
                        landing_sel = 'a:has-text("Create with Google Flow"), button:has-text("Create with Google Flow"), [role="button"]:has-text("Create with Google Flow")'
                        create_btn = await page.query_selector(landing_sel)
                        if create_btn and await create_btn.is_visible():
                            print("[INFO] Google Flow landing page detected. Clicking 'Create with Google Flow'...")
                            await create_btn.click()
                            await page.wait_for_timeout(4000)
                            check_session()

                    # 5. Wait for the main prompt input element
                    print(f"[INFO] Waiting for prompt input element at {page.url}...")
                    combined_selector = ", ".join(PROMPT_SELECTORS)
                    try:
                        prompt_el = await page.wait_for_selector(
                            combined_selector,
                            state="visible",
                            timeout=timeout * 1000,
                        )
                    except PlaywrightTimeoutError:
                        check_session()
                        # Fallback check for landing page
                        create_btn = await page.query_selector('text="Create with Google Flow", a:has-text("Create with Google Flow"), button:has-text("Create with Google Flow")')
                        if create_btn and await create_btn.is_visible():
                            print("[INFO] Landing page detected on prompt wait timeout. Clicking 'Create with Google Flow'...")
                            await create_btn.click()
                            await page.wait_for_timeout(3000)
                            await page.goto(flow_url, timeout=timeout * 1000)
                            prompt_el = await page.wait_for_selector(combined_selector, state="visible", timeout=timeout * 1000)
                        else:
                            await page.screenshot(path="debug_error.png")
                            raise TimeoutError(
                                f"Prompt input element did not appear within {timeout}s at {page.url}."
                            )

                    # Successfully established page & prompt input
                    break

                except (ProxyConnectionError, Exception) as exc:
                    if is_proxy_network_error(exc) and attempt < max_attempts:
                        current_proxy_idx += 1
                        if os.environ.get("SCRAPERAPI_KEY", "").strip():
                            rotate_scraperapi_session_id()
                        print(f"[WARNING] Proxy failure on attempt {attempt}: {exc}. Retrying with alternate proxy (index {current_proxy_idx})...")
                        await asyncio.sleep(1.0)
                        continue
                    elif is_proxy_network_error(exc):
                        sanitized = sanitize_log_message(str(exc))
                        print(f"[ERROR] Proxy connection failed after {attempt} attempts: {sanitized}")
                        raise ProxyConnectionError(f"Proxy Connection Failed: {sanitized}")
                    else:
                        raise

            # 6. Apply UI Settings (Aspect Ratio, Model Variant, Num Outputs)
            await _apply_ui_settings(
                page=page,
                aspect_ratio=aspect_ratio,
                model_variant=model_variant,
                num_outputs=num_outputs
            )

            # 7. Upload Reference Images if provided (up to 3)
            ref_list: List[str] = []
            if reference_images:
                if isinstance(reference_images, str):
                    ref_list = [reference_images]
                elif isinstance(reference_images, list):
                    ref_list = reference_images
                await _upload_reference_images(page, ref_list)

            # 8. Compose and Inject Prompt
            full_prompt = compose_full_prompt(
                prompt=prompt,
                negative_prompt=negative_prompt,
                style_preset=style_preset,
                seed=seed,
                image_strength=image_strength
            )

            # Re-query prompt element fresh, as adding ingredients or settings triggers
            # causes Google Flow / Angular to re-render the prompt dock
            prompt_sel = ", ".join(PROMPT_SELECTORS)
            try:
                prompt_el = await page.wait_for_selector(prompt_sel, state="visible", timeout=10000)
            except Exception:
                pass

            print("[INFO] Injecting prompt with human-like keypress delays...")
            try:
                backdrops = await page.query_selector_all(".cdk-overlay-backdrop")
                if backdrops:
                    for _ in range(2):
                        await page.keyboard.press("Escape")
                        await page.wait_for_timeout(200)
                await prompt_el.click(timeout=3000)
            except Exception:
                try:
                    prompt_el = await page.wait_for_selector(prompt_sel, state="visible", timeout=5000)
                    await prompt_el.click(force=True)
                except Exception:
                    pass
            try:
                await prompt_el.focus()
            except Exception:
                pass
            await asyncio.sleep(0.3)

            await page.keyboard.press("Control+A")
            await page.keyboard.press("Backspace")
            await asyncio.sleep(0.2)

            for char in full_prompt:
                await page.keyboard.type(char)
                await asyncio.sleep(random.uniform(0.03, 0.07))

            print("[INFO] Prompt injected successfully.")

            # Assign unique task UUID
            task_uuid = str(uuid.uuid4())
            prompt_prefix = full_prompt.lower().strip()[:30]

            # 9. Click submit button or press Enter
            print(f"[INFO] Submitting prompt with task UUID: {task_uuid}...")
            submitted = False
            for submit_sel in SUBMIT_SELECTORS:
                try:
                    btn = await page.wait_for_selector(submit_sel, state="visible", timeout=3000)
                    if btn:
                        for _ in range(15):
                            classes = (await btn.get_attribute("class")) or ""
                            disabled = await btn.get_attribute("disabled")
                            aria_disabled = await btn.get_attribute("aria-disabled")
                            if not disabled and aria_disabled != "true" and "disabled" not in classes:
                                break
                            await asyncio.sleep(0.3)
                        try:
                            await btn.click(timeout=3000)
                        except Exception:
                            await page.keyboard.press("Escape")
                            await page.wait_for_timeout(200)
                            await btn.click(force=True)
                        submitted = True
                        print("[INFO] Submitted prompt via generation button.")
                        break
                except Exception:
                    continue

            if not submitted:
                print("[INFO] No visible submit button detected; sending 'Enter' key...")
                await page.keyboard.press("Enter")

            # Error checker
            async def _check_page_errors():
                return await page.evaluate("""() => {
                    const alertSelectors = [
                        'mat-snack-bar-container',
                        '[role="alert"]',
                        '.notification-banner',
                        '.error-message',
                        '[class*="toast" i]',
                        '[class*="snackbar" i]'
                    ];
                    for (const sel of alertSelectors) {
                        for (const el of document.querySelectorAll(sel)) {
                            const txt = (el.innerText || '').trim();
                            if (txt && (
                                txt.toLowerCase().includes('error') ||
                                txt.toLowerCase().includes('failed') ||
                                txt.toLowerCase().includes('policy') ||
                                txt.toLowerCase().includes('violate') ||
                                txt.toLowerCase().includes('cannot') ||
                                txt.toLowerCase().includes('try again') ||
                                txt.toLowerCase().includes('unable')
                            )) {
                                return txt;
                            }
                        }
                    }
                    return null;
                }""")

            # 10. Bind newly spawned pending tiles to task_uuid
            print(f"[INFO] Binding generation batch to UUID: {task_uuid}...")
            stamped_count = 0
            has_refs = bool(ref_list)
            stamp_wait = 15 if has_refs else 10  # reference image jobs take longer to spawn tiles
            stamp_deadline = asyncio.get_event_loop().time() + stamp_wait

            while asyncio.get_event_loop().time() < stamp_deadline:
                check_session()
                err = await _check_page_errors()
                if err:
                    await page.screenshot(path="debug_error.png")
                    print(f"[ERROR] Google Flow error detected: {err}", file=sys.stderr)
                    raise GenerationFailedError(f"Generation rejected by Google Flow: {err}")

                stamped = await page.evaluate(
                    """({taskId}) => {
                        let count = 0;
                        // Broad selector: match any tile container variant
                        const containers = document.querySelectorAll(
                            'flow-grid-tile-container, [class*="tile-container"], [class*="generation-tile"]'
                        );
                        for (const c of containers) {
                            const pendingTile = c.querySelector('flow-pending-tile, [class*="pending"], [class*="loading"], mat-progress-bar');
                            const img = c.querySelector('img');
                            if ((pendingTile || (!img && c.querySelector('[class*="progress"], [role="progressbar"]')))
                                && !c.hasAttribute('data-generation-uuid')) {
                                c.setAttribute('data-generation-uuid', taskId);
                                count++;
                            }
                        }
                        return count;
                    }""",
                    {"taskId": task_uuid}
                )

                if stamped > 0:
                    stamped_count += stamped
                    await asyncio.sleep(0.5)
                    stamped_more = await page.evaluate(
                        """({taskId}) => {
                            let count = 0;
                            const containers = document.querySelectorAll(
                                'flow-grid-tile-container, [class*="tile-container"], [class*="generation-tile"]'
                            );
                            for (const c of containers) {
                                const pendingTile = c.querySelector('flow-pending-tile, [class*="pending"], [class*="loading"], mat-progress-bar');
                                const img = c.querySelector('img');
                                if ((pendingTile || (!img && c.querySelector('[class*="progress"], [role="progressbar"]')))
                                    && !c.hasAttribute('data-generation-uuid')) {
                                    c.setAttribute('data-generation-uuid', taskId);
                                    count++;
                                }
                            }
                            return count;
                        }""",
                        {"taskId": task_uuid}
                    )
                    stamped_count += stamped_more
                    print(f"[INFO] Successfully stamped {stamped_count} generation tile(s) with UUID {task_uuid}.")
                    break

                await asyncio.sleep(0.5)

            if stamped_count == 0:
                print(f"[WARNING] No generation tiles found with standard selectors after {stamp_wait}s. Attempting broader fallback scan...")

            # 11. Monitor generation status strictly querying [data-generation-uuid="<task_uuid>"]
            print(f"[INFO] Monitoring generation status for prompt: '{prompt[:45]}...'")
            start_time = asyncio.get_event_loop().time()
            render_timeout = max(timeout, 180 if has_refs else 90)
            min_monitor_seconds = 8  # Don't accept "complete" in the first 8s — prevents grabbing old tiles

            last_progress = None
            completed_images = []
            ever_saw_generating = (stamped_count > 0)

            while asyncio.get_event_loop().time() - start_time < render_timeout:
                check_session()
                elapsed = asyncio.get_event_loop().time() - start_time

                err = await _check_page_errors()
                if err:
                    await page.screenshot(path="debug_error.png")
                    print(f"[ERROR] Google Flow error detected: {err}", file=sys.stderr)
                    raise GenerationFailedError(f"Generation rejected by Google Flow: {err}")

                scan_result = await page.evaluate(
                    """({taskId, promptPrefix}) => {
                        // 1. First look for UUID-tagged containers
                        let containers = document.querySelectorAll(
                            '[data-generation-uuid="' + taskId + '"]'
                        );

                        // 2. Fallback: find any unstamped tiles that are ACTIVELY generating
                        //    (have pending indicators, progress bars, or NO completed image)
                        //    NEVER stamp tiles that already have a completed <img> — those are old generations
                        if (containers.length === 0) {
                            const allContainers = document.querySelectorAll(
                                'flow-grid-tile-container, [class*="tile-container"], [class*="generation-tile"]'
                            );
                            for (const c of allContainers) {
                                if (c.hasAttribute('data-generation-uuid')) continue;

                                const completedImg = c.querySelector('img');
                                const hasCompletedImage = completedImg && completedImg.complete && completedImg.naturalWidth > 200;

                                // Only stamp tiles that are IN-PROGRESS (not already completed)
                                if (hasCompletedImage) continue;

                                const text = (c.innerText || '').toLowerCase();
                                const aria = (c.getAttribute('aria-label') || '').toLowerCase();
                                const hasPending = c.querySelector('flow-pending-tile, [class*="pending"], [class*="loading"], mat-progress-bar, [role="progressbar"]');
                                const hasProgress = text.match(/\\d{1,3}%/);

                                if (hasPending || hasProgress || text.includes(promptPrefix) || aria.includes(promptPrefix)) {
                                    c.setAttribute('data-generation-uuid', taskId);
                                }
                            }
                            containers = document.querySelectorAll('[data-generation-uuid="' + taskId + '"]');
                        }

                        if (containers.length === 0) {
                            return { isGenerating: false, progress: null, completedCount: 0, totalCount: 0, images: [] };
                        }

                        let isGenerating = false;
                        let progress = null;
                        const images = [];

                        for (const c of containers) {
                            const text = (c.innerText || '').trim();
                            const percentMatch = text.match(/(\\d{1,3}%)/);
                            if (percentMatch) {
                                isGenerating = true;
                                progress = percentMatch[1];
                            }

                            const img = c.querySelector('img');
                            if (img && img.complete && img.naturalWidth > 200) {
                                const mid = img.getAttribute('data-media-id') || '';
                                const src = img.src || '';
                                images.push({
                                    mediaId: mid,
                                    src: src,
                                    width: img.naturalWidth,
                                    height: img.naturalHeight
                                });
                            } else {
                                isGenerating = true;
                            }
                        }

                        return {
                            isGenerating: isGenerating && (images.length < containers.length),
                            progress: progress,
                            completedCount: images.length,
                            totalCount: containers.length,
                            images: images
                        };
                    }""",
                    {"taskId": task_uuid, "promptPrefix": prompt_prefix}
                )

                if scan_result["isGenerating"] and scan_result["progress"] and scan_result["progress"] != last_progress:
                    last_progress = scan_result["progress"]
                    ever_saw_generating = True
                    print(f"[INFO] Generation progress: {last_progress}...")

                if scan_result["totalCount"] > 0 and scan_result["isGenerating"]:
                    ever_saw_generating = True

                if scan_result["totalCount"] > 0 and scan_result["completedCount"] == scan_result["totalCount"]:
                    # Safety: don't accept instant completions unless we saw generating state first
                    if elapsed < min_monitor_seconds and not ever_saw_generating:
                        print(f"[INFO] Potential stale tile detected (completed in {elapsed:.1f}s with no progress). Waiting for real generation...")
                        # Un-stamp these tiles — they are likely old
                        await page.evaluate(
                            """({taskId}) => {
                                const tagged = document.querySelectorAll('[data-generation-uuid="' + taskId + '"]');
                                for (const c of tagged) {
                                    c.removeAttribute('data-generation-uuid');
                                }
                            }""",
                            {"taskId": task_uuid}
                        )
                        await asyncio.sleep(1.5)
                        continue

                    completed_images = scan_result["images"]
                    print(f"[INFO] Generation complete! Captured {len(completed_images)} variation(s) for UUID {task_uuid}.")
                    break

                await asyncio.sleep(1.5)

            if not completed_images:
                await page.screenshot(path="debug_error.png")
                raise GenerationTimeoutError(
                    f"Image generation timed out after {render_timeout}s without producing completed output."
                )

            # 12. Extract and save images
            print(f"[INFO] Extracting {len(completed_images)} rendered image(s)...")
            saved_paths = []
            base_path = Path(output_path).resolve()
            base_path.parent.mkdir(parents=True, exist_ok=True)

            for idx, img_info in enumerate(completed_images):
                src = img_info["src"]
                media_id = img_info.get("mediaId") or "unknown"
                dest_file = _get_safe_destination_path(base_path, idx, overwrite=overwrite)

                raw_bytes = await _extract_image_bytes(page, src)
                img = Image.open(io.BytesIO(raw_bytes))
                img.verify()
                img = Image.open(io.BytesIO(raw_bytes))
                img.save(dest_file)
                saved_paths.append((str(dest_file), media_id))
                print(
                    f"[INFO] Saved variation {idx + 1} to {dest_file} "
                    f"({img.format}, {img.width}x{img.height}) [Media UUID: {media_id}]"
                )

            return [p[0] for p in saved_paths]

    except SessionExpiredError:
        raise
    except (GenerationFailedError, GenerationTimeoutError):
        raise
    except Exception as e:
        sanitized = sanitize_log_message(str(e))
        print(f"[ERROR] Automation error: {sanitized}", file=sys.stderr)
        raise
    finally:
        try:
            if context:
                await context.close()
        except Exception:
            pass
        try:
            if browser:
                await browser.close()
        except Exception:
            pass


def parse_args():
    parser = argparse.ArgumentParser(
        description="Headless Google Flow Image Generation Automation Engine"
    )
    parser.add_argument(
        "--prompt",
        "-p",
        type=str,
        default="A futuristic cyberpunk city at sunset, 8k resolution",
        help="Prompt description for the generated image",
    )
    parser.add_argument(
        "--negative-prompt",
        "-n",
        type=str,
        default=None,
        help="Negative prompt of elements to exclude",
    )
    parser.add_argument(
        "--outputs",
        type=int,
        default=1,
        choices=[1, 2, 3, 4],
        help="Number of variations (1 to 4)",
    )
    parser.add_argument(
        "--aspect-ratio",
        "-ar",
        type=str,
        default="16:9",
        choices=["1:1", "16:9", "9:16", "4:3", "3:4"],
        help="Aspect ratio choice",
    )
    parser.add_argument(
        "--model",
        type=str,
        default="Nano Banana 2",
        choices=["Nano Banana 2", "Nano Banana Pro", "Nano Banana 2 Lite"],
        help="Model variant choice",
    )
    parser.add_argument(
        "--style",
        type=str,
        default="None",
        choices=list(STYLE_PRESETS.keys()),
        help="Style preset",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=-1,
        help="Deterministic seed (-1 for random)",
    )
    parser.add_argument(
        "--output",
        "-o",
        type=str,
        default="output.png",
        help="Destination path for the generated image file (default: output.png)",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        default=False,
        help="Overwrite existing output files on disk (default: False, preserves existing files)",
    )
    parser.add_argument(
        "--headless",
        action="store_true",
        default=os.getenv("HEADLESS", "True").lower() in ("true", "1", "yes"),
        help="Run browser in headless mode",
    )
    parser.add_argument(
        "--no-headless",
        dest="headless",
        action="store_false",
        help="Run browser with visible window for debugging",
    )
    parser.add_argument(
        "--url",
        "-u",
        type=str,
        default=os.getenv("FLOW_URL", "https://flow.google.com/"),
        help="Google Flow Project or Workspace URL",
    )
    parser.add_argument(
        "--timeout",
        "-t",
        type=int,
        default=DEFAULT_TIMEOUT,
        help=f"Timeout in seconds (default: {DEFAULT_TIMEOUT})",
    )
    parser.add_argument(
        "--proxy-index",
        type=int,
        default=0,
        help="Index of proxy to select from Webshare proxy list (default: 0)",
    )
    parser.add_argument(
        "--reference-images",
        "-ref",
        type=str,
        nargs="*",
        default=None,
        help="Paths to up to 3 reference images for image-to-image conditioning",
    )
    return parser.parse_args()


def main():
    args = parse_args()
    print("=" * 65)
    print(" Google Flow Image Generation Engine (V2)")
    print("=" * 65)
    print(f"Target URL   : {args.url}")
    print(f"Prompt       : {args.prompt}")
    print(f"Model        : {args.model}")
    print(f"Aspect Ratio : {args.aspect_ratio}")
    print(f"Outputs      : {args.outputs}")
    print(f"Ref Images   : {args.reference_images}")
    print(f"Style Preset : {args.style}")
    print(f"Output Path  : {args.output}")
    print(f"Overwrite    : {args.overwrite}")
    print(f"Headless     : {args.headless}")
    print(f"Proxy Index  : {args.proxy_index}")
    print("-" * 65)

    try:
        saved_files = asyncio.run(
            generate_image(
                prompt=args.prompt,
                negative_prompt=args.negative_prompt,
                num_outputs=args.outputs,
                aspect_ratio=args.aspect_ratio,
                model_variant=args.model,
                reference_images=args.reference_images,
                style_preset=args.style,
                seed=args.seed,
                output_path=args.output,
                flow_url=args.url,
                headless=args.headless,
                timeout=args.timeout,
                overwrite=args.overwrite,
                proxy_index=args.proxy_index,
            )
        )
        print("=" * 65)
        if isinstance(saved_files, list):
            print(f"[COMPLETE] Image generation succeeded ({len(saved_files)} variation(s)):")
            for idx, pth in enumerate(saved_files, start=1):
                print(f"  {idx}. {pth}")
        else:
            print(f"[COMPLETE] Image generation succeeded: {saved_files}")
        print("=" * 65)
    except FileNotFoundError:
        sys.exit(1)
    except SessionExpiredError:
        sys.exit(2)
    except GenerationFailedError as gfe:
        print(f"\n[FATAL] Generation failed: {gfe}", file=sys.stderr)
        sys.exit(4)
    except GenerationTimeoutError as gte:
        print(f"\n[FATAL] Generation timeout: {gte}", file=sys.stderr)
        sys.exit(5)
    except Exception as err:
        print(f"\n[FATAL] Execution failed: {err}", file=sys.stderr)
        sys.exit(3)


if __name__ == "__main__":
    main()
