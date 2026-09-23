"""
login_session.py
================
Clean, one-time login utility to authenticate into Google Flow and export
the exact browser session state to storage_state.json.

Reliability model (V3 - persistent profile):
- Logs in INTO the same on-disk Chrome profile (USER_DATA_DIR) that the runtime
  engine reuses, so cookies, device tokens and browser fingerprint are all
  issued to ONE consistent identity instead of being replayed onto a fresh,
  mismatched environment.
- Uses the identical launch args, user-agent and stealth script as engine.py.
- No longer disables HTTP/2 or QUIC (real Chrome negotiates both; forcing
  HTTP/1.1 makes the TLS/HTTP2 fingerprint diverge from genuine Chrome).
- Still exports storage_state.json so the profile can be seeded on cold starts
  (e.g. a fresh Hugging Face container hydrated from SESSION_STORAGE_BASE64).

Usage:
    python login_session.py
"""

import asyncio
import os
import re
import sys
from pathlib import Path
from dotenv import load_dotenv
from playwright.async_api import async_playwright

load_dotenv()

# Reuse the EXACT profile dir, launch args, user-agent and stealth script that the
# runtime engine uses. Logging in with a different fingerprint/profile than runtime
# is a primary cause of fast session invalidation, so we keep them identical.
import engine
from engine import (
    USER_DATA_DIR,
    PERSISTENT_LAUNCH_ARGS,
    STEALTH_INIT_SCRIPT,
    DEFAULT_USER_AGENT,
    get_configured_proxy,
    format_proxy_for_log,
)

FLOW_URL = os.getenv("FLOW_URL", "https://flow.google.com/")
STORAGE_STATE_PATH = os.getenv("STORAGE_STATE_PATH", "storage_state.json")


async def login_and_export():
    is_new = "--new" in sys.argv or "-n" in sys.argv
    target_url = "https://flow.google.com/" if is_new else FLOW_URL

    print("=" * 68)
    print(" Google Flow - One-Time Interactive Authentication")
    print("=" * 68)
    print(f"Target Project URL : {target_url}")
    print(f"Output State File  : {STORAGE_STATE_PATH}")
    print(f"Persistent Profile : {USER_DATA_DIR}")
    if is_new:
        print("Mode               : Fresh Account Login (Starting at Google Flow home)")
    print("-" * 68)

    async with async_playwright() as p:
        print("[INFO] Launching Google Chrome into the persistent profile (stealth mode)...")

        proxy_cfg = get_configured_proxy(0)

        ctx_kwargs = {
            "headless": False,
            "viewport": None,  # Uses full maximized window
            "user_agent": DEFAULT_USER_AGENT,
            "ignore_https_errors": True,
            "args": PERSISTENT_LAUNCH_ARGS + ["--start-maximized"],
            "ignore_default_args": ["--enable-automation"],
        }
        if proxy_cfg:
            ctx_kwargs["proxy"] = proxy_cfg
            print(f"[INFO] Routing interactive login via proxy: {format_proxy_for_log(proxy_cfg)}")
        else:
            print("[INFO] No proxy configured. Launching login browser in direct connection mode.")

        # Log in INTO the same persistent profile the server reuses, so the cookies,
        # device tokens and browser fingerprint are all issued to one consistent identity.
        Path(USER_DATA_DIR).mkdir(parents=True, exist_ok=True)
        try:
            context = await p.chromium.launch_persistent_context(
                USER_DATA_DIR, channel="chrome", **ctx_kwargs
            )
        except Exception:
            context = await p.chromium.launch_persistent_context(USER_DATA_DIR, **ctx_kwargs)

        # Full stealth suite (identical to the runtime engine), applied to the next navigation.
        await context.add_init_script(STEALTH_INIT_SCRIPT)

        page = context.pages[0] if context.pages else await context.new_page()

        print(f"[INFO] Navigating to {target_url}...")
        try:
            await page.goto(target_url, wait_until="domcontentloaded", timeout=60000)
        except Exception as e:
            print(f"[INFO] Navigation: {e}")

        print("\n" + "=" * 68)
        print(" ACTION IN THE OPEN CHROME WINDOW:")
        print(" 1. Sign in with your Google Account.")
        print("    (Notice: Google will NOT block you with 'browser not secure')")
        print(" 2. Complete any 2FA or verification prompt.")
        print(" 3. Wait until your Google Flow project workspace is visible.")
        print(" 4. Return here to this terminal and press [ENTER].")
        print("=" * 68)

        await asyncio.to_thread(
            input,
            "\n>>> Press [ENTER] when you are inside your project workspace... "
        )

        current_url = page.url
        print(f"\n[INFO] Current URL: {current_url}")
        if "/project/" in current_url:
            try:
                env_path = Path(".env")
                if env_path.exists():
                    content = env_path.read_text(encoding="utf-8")
                    if re.search(r"^FLOW_URL=.*", content, flags=re.MULTILINE):
                        new_content = re.sub(r"^FLOW_URL=.*", f"FLOW_URL={current_url}", content, flags=re.MULTILINE)
                        env_path.write_text(new_content, encoding="utf-8")
                        print(f"[INFO] Automatically synced FLOW_URL in .env to current workspace: {current_url}")
            except Exception as e:
                print(f"[WARN] Could not update .env: {e}")

        print(f"[INFO] Exporting full authenticated session to '{STORAGE_STATE_PATH}'...")

        Path(STORAGE_STATE_PATH).parent.mkdir(parents=True, exist_ok=True)
        await context.storage_state(path=STORAGE_STATE_PATH)

        if os.path.exists(STORAGE_STATE_PATH) and os.path.getsize(STORAGE_STATE_PATH) > 0:
            print("\n" + "=" * 68)
            print(f"[SUCCESS] Session saved successfully ({os.path.getsize(STORAGE_STATE_PATH)} bytes)!")
            print("[SUCCESS] Cookies, device tokens and localStorage captured into the persistent profile.")
            print("=" * 68)
            print("\nYou can now run image generation in headless mode anytime:")
            print("    python engine.py --prompt \"A futuristic cyberpunk city at sunset, 8k resolution\" --output \"output.png\"")
            print("=" * 68 + "\n")
        else:
            print(f"[ERROR] Failed to save session to '{STORAGE_STATE_PATH}'.")

        await context.close()


def main():
    try:
        asyncio.run(login_and_export())
    except KeyboardInterrupt:
        print("\n[INFO] Exited by user.")
        sys.exit(0)
    except Exception as err:
        print(f"\n[ERROR] Unexpected error: {err}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
