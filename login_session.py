"""
login_session.py
================
Clean, one-time login utility to authenticate into Google Flow and export
the exact browser session state to storage_state.json.

Stealth Features:
- Disables automation flags (no 'Chrome is being controlled by automated test software').
- Removes navigator.webdriver so Google does NOT block sign-in.
- Uses real Google Chrome to pass all security checks.
- Captures 100% of cookies, local storage, and cryptographic session tokens.

Usage:
    python login_session.py
"""

import asyncio
import os
import sys
from pathlib import Path
from dotenv import load_dotenv
from playwright.async_api import async_playwright

load_dotenv()

FLOW_URL = os.getenv("FLOW_URL", "https://flow.google.com/")
STORAGE_STATE_PATH = os.getenv("STORAGE_STATE_PATH", "storage_state.json")
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/128.0.0.0 Safari/537.36"
)


async def login_and_export():
    is_new = "--new" in sys.argv or "-n" in sys.argv
    target_url = "https://flow.google.com/" if is_new else FLOW_URL

    print("=" * 68)
    print(" Google Flow - One-Time Interactive Authentication")
    print("=" * 68)
    print(f"Target Project URL : {target_url}")
    print(f"Output State File  : {STORAGE_STATE_PATH}")
    if is_new:
        print("Mode               : Fresh Account Login (Starting at Google Flow home)")
    print("-" * 68)

    async with async_playwright() as p:
        print("[INFO] Launching Google Chrome in stealth mode...")

        launch_kwargs = {
            "headless": False,
            "args": [
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--start-maximized",
                "--disable-http2",
                "--disable-quic",
                "--ignore-certificate-errors",
            ],
            "ignore_default_args": [
                "--enable-automation",
            ],
        }

        try:
            browser = await p.chromium.launch(channel="chrome", **launch_kwargs)
        except Exception:
            browser = await p.chromium.launch(**launch_kwargs)

        proxy_cfg = None
        try:
            import engine
            proxy_cfg = engine.get_configured_proxy(0)
        except Exception:
            pass

        ctx_kwargs = {
            "viewport": None,  # Uses full maximized window
            "user_agent": DEFAULT_USER_AGENT,
            "ignore_https_errors": True,
        }
        if proxy_cfg:
            ctx_kwargs["proxy"] = proxy_cfg
            try:
                import engine
                print(f"[INFO] Routing interactive login via proxy: {engine.format_proxy_for_log(proxy_cfg)}")
            except Exception:
                print(f"[INFO] Routing interactive login via configured proxy.")
        else:
            print("[INFO] No proxy configured. Launching login browser in direct connection mode.")

        context = await browser.new_context(**ctx_kwargs)

        page = await context.new_page()

        # Mask automation footprint so Google Account login succeeds
        await page.add_init_script(
            """
            Object.defineProperty(navigator, 'webdriver', {
                get: () => undefined
            });
            """
        )

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
                    import re
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
            print("[SUCCESS] All cookies, device tokens, and localStorage have been captured.")
            print("=" * 68)
            print("\nYou can now run image generation in headless mode anytime:")
            print("    python engine.py --prompt \"A futuristic cyberpunk city at sunset, 8k resolution\" --output \"output.png\"")
            print("=" * 68 + "\n")
        else:
            print(f"[ERROR] Failed to save session to '{STORAGE_STATE_PATH}'.")

        await browser.close()


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
