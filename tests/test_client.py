"""
test_client.py
==============
Demonstration client script for testing the Google Flow Gradio wrapper.
Demonstrates:
1. Health check verification (/api/health).
2. Unauthorized request rejection verification (401 Unauthorized).
3. Authenticated multi-parameter generation via gradio_client.
4. Authenticated direct REST API execution via standard HTTP POST (/api/generate_image).
"""

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from PIL import Image

try:
    from gradio_client import Client, handle_file
except ImportError:
    print("[ERROR] gradio_client not found. Install via: pip install gradio_client")
    sys.exit(1)


def check_health(server_url: str) -> bool:
    """Verifies service health and session status via GET /api/health (public probe)."""
    health_url = f"{server_url.rstrip('/')}/api/health"
    print(f"\n[1/4] Querying service health at {health_url}...")
    try:
        req = urllib.request.Request(health_url, headers={"User-Agent": "TestClient/1.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            print(f"      Status Code : {resp.status}")
            print(f"      Response    : {data}")
            return data.get("session_active", False)
    except Exception as exc:
        print(f"      [WARNING] Health check failed or server not ready: {exc}")
        return False


def test_unauthorized_rejection(server_url: str, token_expected: bool = True):
    """Verifies that requests missing a Bearer token are rejected with HTTP 401."""
    endpoint = f"{server_url.rstrip('/')}/api/generate_image"
    print(f"\n[2/4] Testing unauthorized request rejection at {endpoint}...")

    payload = {"prompt": "Testing unauthorized rejection"}
    req_data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        endpoint,
        data=req_data,
        headers={"Content-Type": "application/json", "User-Agent": "TestClient/1.0"}
    )

    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            if token_expected:
                print(f"      [FAIL] Request succeeded with {resp.status} but should have been rejected with 401!")
            else:
                print(f"      [INFO] Server has no API_BEARER_TOKEN configured; request allowed.")
    except urllib.error.HTTPError as he:
        if he.code == 401:
            print(f"      [SUCCESS] Correctly rejected unauthorized request with HTTP 401 Unauthorized!")
            err_body = he.read().decode("utf-8")
            print(f"      Response    : {err_body}")
        else:
            print(f"      [WARNING] Server responded with HTTP {he.code}: {he}")
    except Exception as exc:
        print(f"      [ERROR] Unexpected error: {exc}")


def test_gradio_client(server_url: str, token: str = ""):
    """Executes multi-parameter payload using gradio_client with authentication."""
    print(f"\n[3/4] Connecting gradio_client to {server_url} (Auth: {'Configured' if token else 'None'})...")
    client_headers = {"Authorization": f"Bearer {token}"} if token else None
    client = Client(server_url, headers=client_headers)

    prompt = "A majestic golden eagle soaring over snow-capped mountains at sunrise"
    print(f"      Sending generation request:")
    print(f"      - Prompt         : '{prompt}'")
    print(f"      - Model Variant  : 'Nano Banana 2'")
    print(f"      - Aspect Ratio   : '1:1'")
    print(f"      - Outputs Count  : 1")
    print(f"      - Style Preset   : 'Photorealistic'")

    start_time = time.time()
    try:
        result = client.predict(
            prompt=prompt,
            negative_prompt="blurry, distorted, artifacts",
            num_outputs=1,
            aspect_ratio="1:1",
            model_variant="Nano Banana 2",
            reference_images=None,
            image_strength=0.75,
            style_preset="Photorealistic",
            seed=-1,
            api_name="/generate_image",
        )

        elapsed = time.time() - start_time
        print(f"      [SUCCESS] Generation returned in {elapsed:.1f}s!")

        gallery_images, status_message = result
        print(f"      Status Message : {status_message}")
        print(f"      Images Count   : {len(gallery_images)}")

        for idx, img_entry in enumerate(gallery_images, start=1):
            img_path = None
            if isinstance(img_entry, str):
                img_path = img_entry
            elif isinstance(img_entry, dict):
                img_val = img_entry.get("image")
                if isinstance(img_val, dict):
                    img_path = img_val.get("path")
                else:
                    img_path = img_val or img_entry.get("path")
            else:
                img_path = str(img_entry)

            if img_path and Path(img_path).exists():
                with Image.open(img_path) as im:
                    print(f"        {idx}. {img_path} ({im.format}, {im.width}x{im.height})")
            else:
                print(f"        {idx}. {img_path}")

    except Exception as exc:
        print(f"      [ERROR] gradio_client execution error: {exc}")


def test_rest_api(server_url: str, token: str = ""):
    """Demonstrates direct authenticated HTTP POST request to /api/generate_image."""
    endpoint = f"{server_url.rstrip('/')}/api/generate_image"
    print(f"\n[4/4] Testing direct REST POST endpoint at {endpoint} (Auth: {'Configured' if token else 'None'})...")

    payload = {
        "prompt": "An astronaut discovering an ancient neon temple on Mars",
        "negative_prompt": "cartoon, sketch",
        "num_outputs": 1,
        "aspect_ratio": "16:9",
        "model_variant": "Nano Banana 2",
        "style_preset": "Cinematic",
        "seed": 42,
        "image_strength": 0.75
    }

    try:
        req_data = json.dumps(payload).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "User-Agent": "TestClient/1.0"
        }
        if token:
            headers["Authorization"] = f"Bearer {token}"

        req = urllib.request.Request(endpoint, data=req_data, headers=headers)

        start_time = time.time()
        with urllib.request.urlopen(req, timeout=120) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            elapsed = time.time() - start_time
            print(f"      Status Code : {resp.status} ({elapsed:.1f}s)")
            print(f"      Response    : {data}")
    except Exception as exc:
        print(f"      [ERROR] REST API request failed: {exc}")


def main():
    parser = argparse.ArgumentParser(description="Test Client for Google Flow Gradio Wrapper")
    parser.add_argument(
        "--url",
        default="http://127.0.0.1:7860",
        help="Target Gradio / Spaces server URL (default: http://127.0.0.1:7860)"
    )
    parser.add_argument(
        "--token",
        default=os.environ.get("API_BEARER_TOKEN", ""),
        help="API Bearer token for authentication (default: $API_BEARER_TOKEN)"
    )
    args = parser.parse_args()

    print("=" * 65)
    print(" Google Flow Gradio API Client Test Runner")
    print("=" * 65)
    print(f"Server Target : {args.url}")
    print(f"Auth Token    : {'Configured' if args.token else 'Not Set'}")

    # 1. Health check (public probe)
    check_health(args.url)

    # 2. Unauthorized rejection test (should return 401 when token is enabled)
    test_unauthorized_rejection(args.url, token_expected=bool(args.token))

    # 3. Authenticated REST POST test
    if args.token:
        test_rest_api(args.url, token=args.token)

    print("\n" + "=" * 65)
    print(" Client testing complete.")
    print("=" * 65)


if __name__ == "__main__":
    main()
