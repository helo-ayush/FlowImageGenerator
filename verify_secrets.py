"""
verify_secrets.py
=================
Pre-flight environment and secret validation script for container startup
(e.g., Hugging Face Spaces, Docker, Kubernetes).

Validates:
1. API_BEARER_TOKEN is configured for API protection.
2. SESSION_STORAGE_BASE64 or storage_state.json is present and valid JSON.
3. FLOW_URL is configured.

Exits with code 0 if all checks pass.
Exits with code 1 if any critical secret or configuration is missing.
"""

import base64
import json
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


def mask_secret(secret: str, show_chars: int = 4) -> str:
    """Safely masks a secret string for logging."""
    if not secret:
        return "<NOT_SET>"
    if len(secret) <= show_chars * 2:
        return "***"
    return f"{secret[:show_chars]}...{secret[-show_chars:]}"


def verify_all_secrets() -> bool:
    print("=" * 65)
    print(" Google Flow Container Pre-Flight Secret Verification")
    print("=" * 65)

    all_passed = True

    # 1. Check API_BEARER_TOKEN
    bearer_token = os.environ.get("API_BEARER_TOKEN", "").strip()
    if not bearer_token:
        print("[FAIL] 'API_BEARER_TOKEN' secret is MISSING or empty.")
        print("       Please set 'API_BEARER_TOKEN' in Space Settings -> Variables and secrets.")
        all_passed = False
    else:
        print(f"[OK]   'API_BEARER_TOKEN' configured : {mask_secret(bearer_token)}")

    # 2. Check SESSION_STORAGE_BASE64 or storage_state.json
    storage_b64 = os.environ.get("SESSION_STORAGE_BASE64", "").strip()
    storage_file = Path(os.environ.get("STORAGE_STATE_PATH", "storage_state.json"))

    if storage_b64:
        try:
            decoded = base64.b64decode(storage_b64).decode("utf-8")
            data = json.loads(decoded)
            cookie_count = len(data.get("cookies", []))
            print(f"[OK]   'SESSION_STORAGE_BASE64' valid : Contains {cookie_count} cookies/tokens.")
        except Exception as exc:
            print(f"[FAIL] 'SESSION_STORAGE_BASE64' failed decoding or JSON parsing: {exc}")
            all_passed = False
    elif storage_file.exists() and storage_file.stat().st_size > 0:
        try:
            with open(storage_file, "r", encoding="utf-8") as f:
                data = json.load(f)
                cookie_count = len(data.get("cookies", []))
                print(f"[OK]   '{storage_file.name}' file found : Contains {cookie_count} cookies/tokens.")
        except Exception as exc:
            print(f"[FAIL] '{storage_file.name}' failed JSON parsing: {exc}")
            all_passed = False
    else:
        print("[FAIL] Neither 'SESSION_STORAGE_BASE64' secret nor 'storage_state.json' file found.")
        print("       Please add 'SESSION_STORAGE_BASE64' to your Space secrets.")
        all_passed = False

    # 3. Check FLOW_URL
    flow_url = os.environ.get("FLOW_URL", "https://flow.google")
    print(f"[INFO] Target FLOW_URL               : {flow_url}")

    # 4. Check Webshare / Residential Proxy
    webshare_proxy = (
        os.environ.get("WEBSHARE_PROXY_URL", "")
        or os.environ.get("WEBSHARE_PROXY_LIST", "")
        or os.environ.get("PROXY_URL", "")
        or os.environ.get("RESIDENTIAL_PROXY_URL", "")
    ).strip()
    scraperapi_key = os.environ.get("SCRAPERAPI_KEY", "").strip()

    if webshare_proxy:
        try:
            import engine
            proxies = engine.get_webshare_proxies()
            if proxies:
                print(f"[OK]   Webshare Proxy active         : {len(proxies)} static IP(s) loaded.")
                for i, p in enumerate(proxies[:3], 1):
                    server = p.get("server", "")
                    user = p.get("username", "")
                    print(f"       * Static IP #{i}             : {server} (User: {user or 'None'})")
                if len(proxies) > 3:
                    print(f"       * ... and {len(proxies) - 3} additional static proxy IPs.")
            else:
                print(f"[WARN] 'WEBSHARE_PROXY_URL' present but could not parse proxy endpoints.")
        except Exception as exc:
            print(f"[WARN] Failed to load Webshare proxies from engine: {exc}")
    elif scraperapi_key:
        print(f"[OK]   'SCRAPERAPI_KEY' configured    : {mask_secret(scraperapi_key)} (Legacy ScraperAPI Active)")
    else:
        print("[INFO] No proxy configured           : Direct connection mode active (local dev).")
        print("       Set 'WEBSHARE_PROXY_URL' to route traffic through your Webshare static IPs.")

    print("-" * 65)
    if all_passed:
        print("[SUCCESS] All pre-flight security checks passed. Ready to start.")
        print("=" * 65)
        return True
    else:
        print("[ERROR] Container pre-flight verification FAILED. Aborting launch.")
        print("=" * 65)
        return False


if __name__ == "__main__":
    success = verify_all_secrets()
    sys.exit(0 if success else 1)
