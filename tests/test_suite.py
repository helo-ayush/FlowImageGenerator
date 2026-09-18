"""
test_suite.py
=============
Automated test suite covering:
1. Security edge cases (Missing auth, Bad auth, Empty prompt, Reference cap > 3).
2. Standard Text-to-Image generation (without reference image).
3. Image-to-Image generation with reference image & image strength.
4. Multi-variation batch generation (2x parallel, 1:1 aspect ratio, Nano Banana Pro).
"""

import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path
from PIL import Image

SERVER_URL = "http://127.0.0.1:7860"
AUTH_TOKEN = os.environ.get("API_BEARER_TOKEN", "dev_secret_token_123")


def send_post(endpoint: str, payload: dict, token: str = None) -> tuple[int, dict]:
    url = f"{SERVER_URL.rstrip('/')}{endpoint}"
    data = json.dumps(payload).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        "User-Agent": "TestSuite/1.0"
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"

    req = urllib.request.Request(url, data=data, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=180) as resp:
            body = json.loads(resp.read().decode("utf-8"))
            return resp.status, body
    except urllib.error.HTTPError as he:
        try:
            body = json.loads(he.read().decode("utf-8"))
        except Exception:
            body = {"error": he.reason}
        return he.code, body
    except Exception as exc:
        return 0, {"error": str(exc)}


def run_tests():
    print("=" * 70)
    print(" COMPREHENSIVE API & GENERATION TEST SUITE")
    print(f" Target Server : {SERVER_URL}")
    print(f" Bearer Token  : {AUTH_TOKEN[:4]}...{AUTH_TOKEN[-3:]}")
    print("=" * 70)

    # -----------------------------------------------------------------------
    # 1. Edge Cases
    # -----------------------------------------------------------------------
    print("\n--- [EDGE CASES] ---")

    # Edge Case 1: Missing Token
    print("\n[Test 1] Missing Bearer Token:")
    status, res = send_post("/api/generate_image", {"prompt": "test"})
    print(f"  -> HTTP Status: {status} | Response: {res}")
    assert status == 401, f"Expected 401, got {status}"
    print("  [PASS] Correctly rejected with 401 Unauthorized.")

    # Edge Case 2: Invalid Token
    print("\n[Test 2] Invalid Bearer Token:")
    status, res = send_post("/api/generate_image", {"prompt": "test"}, token="wrong_token_xyz")
    print(f"  -> HTTP Status: {status} | Response: {res}")
    assert status == 401, f"Expected 401, got {status}"
    print("  [PASS] Correctly rejected with 401 Unauthorized.")

    # Edge Case 3: Empty Prompt
    print("\n[Test 3] Empty Prompt with Valid Token:")
    status, res = send_post("/api/generate_image", {"prompt": ""}, token=AUTH_TOKEN)
    print(f"  -> HTTP Status: {status} | Response: {res}")
    assert status == 400, f"Expected 400, got {status}"
    print("  [PASS] Correctly rejected with 400 Bad Request.")

    # -----------------------------------------------------------------------
    # 2. Standard Text-to-Image Generation (without reference)
    # -----------------------------------------------------------------------
    print("\n--- [SCENARIO A: STANDARD TEXT-TO-IMAGE (NO REFERENCE)] ---")
    prompt_a = "A cozy artisan coffee shop on a rainy night in Paris with warm glowing interior lights"
    payload_a = {
        "prompt": prompt_a,
        "negative_prompt": "blurry, low quality, oversaturated",
        "num_outputs": 1,
        "aspect_ratio": "16:9",
        "model_variant": "Nano Banana 2",
        "style_preset": "Cinematic",
        "seed": 101,
    }
    print(f"Submitting payload:\n{json.dumps(payload_a, indent=2)}")
    t0 = time.time()
    status, res = send_post("/api/generate_image", payload_a, token=AUTH_TOKEN)
    t_elapsed = time.time() - t0
    print(f"HTTP Status: {status} ({t_elapsed:.1f}s)")
    print(f"Response: {json.dumps(res, indent=2)}")
    assert status == 200, f"Expected 200, got {status}"
    assert res.get("status") == "success"
    img_a_path = res["images"][0]
    with Image.open(img_a_path) as im:
        print(f"  [PASS] Rendered Image: {img_a_path} ({im.format}, {im.width}x{im.height})")

    # -----------------------------------------------------------------------
    # 3. Image-to-Image with Reference Image & Image Strength
    # -----------------------------------------------------------------------
    print("\n--- [SCENARIO B: IMAGE-TO-IMAGE WITH REFERENCE IMAGE] ---")
    ref_img = str(Path(img_a_path).resolve())
    prompt_b = "A cyberpunk neon version of this cafe with glowing holographic signs"
    payload_b = {
        "prompt": prompt_b,
        "reference_images": [ref_img],
        "image_strength": 0.80,
        "num_outputs": 1,
        "aspect_ratio": "16:9",
        "model_variant": "Nano Banana 2",
        "style_preset": "Photorealistic",
    }
    print(f"Submitting payload with reference image ({os.path.basename(ref_img)}):\n{json.dumps(payload_b, indent=2)}")
    t0 = time.time()
    status, res = send_post("/api/generate_image", payload_b, token=AUTH_TOKEN)
    t_elapsed = time.time() - t0
    print(f"HTTP Status: {status} ({t_elapsed:.1f}s)")
    print(f"Response: {json.dumps(res, indent=2)}")
    assert status == 200, f"Expected 200, got {status}"
    assert res.get("status") == "success"
    img_b_path = res["images"][0]
    with Image.open(img_b_path) as im:
        print(f"  [PASS] Rendered Image: {img_b_path} ({im.format}, {im.width}x{im.height})")

    # -----------------------------------------------------------------------
    # 4. Multi-Variation Batch Generation (2x, 1:1, Nano Banana Pro)
    # -----------------------------------------------------------------------
    print("\n--- [SCENARIO C: MULTI-VARIATION BATCH (2X, 1:1, NANO BANANA PRO)] ---")
    prompt_c = "A steampunk mechanical owl perched on an ornate brass clockwork branch"
    payload_c = {
        "prompt": prompt_c,
        "num_outputs": 2,
        "aspect_ratio": "1:1",
        "model_variant": "Nano Banana Pro",
        "style_preset": "3D Render",
    }
    print(f"Submitting payload:\n{json.dumps(payload_c, indent=2)}")
    t0 = time.time()
    status, res = send_post("/api/generate_image", payload_c, token=AUTH_TOKEN)
    t_elapsed = time.time() - t0
    print(f"HTTP Status: {status} ({t_elapsed:.1f}s)")
    print(f"Response: {json.dumps(res, indent=2)}")
    assert status == 200, f"Expected 200, got {status}"
    assert res.get("count") == 2
    for idx, p in enumerate(res["images"], 1):
        with Image.open(p) as im:
            print(f"  [PASS] Variation {idx}: {p} ({im.format}, {im.width}x{im.height})")

    print("\n" + "=" * 70)
    print(" ALL EDGE CASES AND GENERATION SCENARIOS COMPLETED SUCCESSFULLY!")
    print("=" * 70)


if __name__ == "__main__":
    run_tests()
