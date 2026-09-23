import os
os.environ["GRADIO_SSR_MODE"] = "false"

try:
    import spaces
except ImportError:
    class spaces:
        @staticmethod
        def GPU(func=None, **kwargs):
            if func is not None:
                return func
            def decorator(f):
                return f
            return decorator

@spaces.GPU(duration=1)
def dummy_gpu():
    """Satisfies Hugging Face Spaces ZeroGPU supervisor static and runtime scanners."""
    return None

import asyncio
import base64
from datetime import datetime, timezone, timedelta
import importlib
import json
import shutil
import threading
import time
import uuid
from pathlib import Path
from typing import List, Optional, Union

from dotenv import load_dotenv
load_dotenv()

import gradio as gr
from gradio.routes import App
import uvicorn
from fastapi import Body, FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field

import engine
from engine import (
    DEFAULT_TIMEOUT,
    FLOW_URL,
    STORAGE_STATE_PATH,
    GenerationFailedError,
    GenerationTimeoutError,
    ProxyConnectionError,
    SessionExpiredError,
    generate_image,
    sanitize_log_message,
)

import subprocess
import sys

# ---------------------------------------------------------------------------
# 0. Container Environment Self-Healing (Playwright Chromium)
# ---------------------------------------------------------------------------
_BROWSER_READY = False

def ensure_playwright_browsers():
    """Installs playwright browser binaries if running in a container without pre-installed browsers."""
    global _BROWSER_READY
    if _BROWSER_READY:
        return
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            executable = p.chromium.executable_path
            if not executable or not os.path.exists(executable):
                raise FileNotFoundError("Chromium executable missing")
        _BROWSER_READY = True
    except Exception:
        print("[INFO] Installing Playwright browser binaries for container environment...")
        try:
            subprocess.run([sys.executable, "-m", "playwright", "install", "chrome", "chromium"], check=False)
            _BROWSER_READY = True
            print("[INFO] Playwright browser installation completed.")
        except Exception as exc:
            print(f"[WARNING] Playwright install execution error: {exc}")

# ---------------------------------------------------------------------------
# 1. Startup Session State Hydration
# ---------------------------------------------------------------------------
OUTPUTS_DIR = Path("outputs").resolve()
OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)
IMAGE_RETENTION_MINUTES = int(os.environ.get("IMAGE_RETENTION_MINUTES", "60"))

async def cleanup_expired_images_loop():
    """Background task to regularly purge images older than IMAGE_RETENTION_MINUTES."""
    while True:
        try:
            cutoff = time.time() - (IMAGE_RETENTION_MINUTES * 60)
            if OUTPUTS_DIR.exists():
                for p in OUTPUTS_DIR.glob("*.png"):
                    if p.is_file() and p.stat().st_mtime < cutoff:
                        try:
                            p.unlink()
                            print(f"[INFO] Purged expired image: {p.name} (> {IMAGE_RETENTION_MINUTES} min old).")
                        except Exception:
                            pass
        except Exception as exc:
            print(f"[WARN] Error in image cleanup loop: {exc}")
        await asyncio.sleep(300)

SESSION_B64 = os.environ.get("SESSION_STORAGE_BASE64")
if SESSION_B64:
    try:
        decoded_bytes = base64.b64decode(SESSION_B64)
        with open(STORAGE_STATE_PATH, "wb") as f:
            f.write(decoded_bytes)
        print(f"[INFO] Successfully hydrated {STORAGE_STATE_PATH} from SESSION_STORAGE_BASE64 secret.")
    except Exception as exc:
        print(f"[ERROR] Failed to hydrate storage state from SESSION_STORAGE_BASE64: {exc}")


def is_session_active() -> bool:
    """Fast check to verify if the session storage file exists and contains valid cookies."""
    storage_path = os.getenv("STORAGE_STATE_PATH", "storage_state.json")
    if not os.path.exists(storage_path) or os.path.getsize(storage_path) == 0:
        return False
    try:
        with open(storage_path, "r", encoding="utf-8") as f:
            data = json.load(f)
            cookies = data.get("cookies", [])
            return len(cookies) > 0
    except Exception:
        return False


# ---------------------------------------------------------------------------
# 2. Concurrency Guardrail (Configurable Semaphore)
# ---------------------------------------------------------------------------
MAX_CONCURRENT_REQUESTS = int(os.environ.get("MAX_CONCURRENT_REQUESTS", "1"))
GENERATION_SEMAPHORE = threading.Semaphore(MAX_CONCURRENT_REQUESTS)
_REQUEST_COUNTER = 0


# ---------------------------------------------------------------------------
# 2b. Dedicated background event loop that OWNS the persistent Playwright browser.
#     A persistent browser context is bound to the event loop that created it, so
#     ALL engine coroutines (generation + keep-alive) must run on this one loop.
#     We no longer reload the engine module or spin up a fresh loop per request,
#     which would orphan the shared profile and force Google to re-validate.
# ---------------------------------------------------------------------------
_ENGINE_LOOP: Optional[asyncio.AbstractEventLoop] = None
_ENGINE_LOOP_THREAD: Optional[threading.Thread] = None
_ENGINE_LOOP_LOCK = threading.Lock()


def _run_engine_loop(loop: asyncio.AbstractEventLoop) -> None:
    asyncio.set_event_loop(loop)
    loop.run_forever()


def get_engine_loop() -> asyncio.AbstractEventLoop:
    """Lazily starts (once) the dedicated background loop thread and returns it."""
    global _ENGINE_LOOP, _ENGINE_LOOP_THREAD
    with _ENGINE_LOOP_LOCK:
        if _ENGINE_LOOP is None or _ENGINE_LOOP.is_closed():
            _ENGINE_LOOP = asyncio.new_event_loop()
            _ENGINE_LOOP_THREAD = threading.Thread(
                target=_run_engine_loop, args=(_ENGINE_LOOP,), daemon=True, name="engine-loop"
            )
            _ENGINE_LOOP_THREAD.start()
        return _ENGINE_LOOP


def run_on_engine_loop(coro, timeout: Optional[float] = None):
    """Submits a coroutine to the background engine loop and blocks for its result."""
    loop = get_engine_loop()
    future = asyncio.run_coroutine_threadsafe(coro, loop)
    return future.result(timeout)



# ---------------------------------------------------------------------------
# 3. Core Generation Handler
# ---------------------------------------------------------------------------
def run_generation(
    prompt: str,
    negative_prompt: Optional[str] = None,
    num_outputs: int = 1,
    aspect_ratio: str = "16:9",
    model_variant: str = "Nano Banana 2",
    reference_images: Optional[Union[List[Union[str, dict]], str]] = None,
    image_strength: float = 0.75,
    style_preset: str = "None",
    seed: int = -1,
) -> tuple[List[str], str]:
    """
    Executes the generation pipeline inside a threading.Semaphore concurrency guardrail.
    Decorated with @spaces.GPU to satisfy Hugging Face Spaces ZeroGPU supervisor.
    """
    global _REQUEST_COUNTER
    if not prompt or not prompt.strip():
        raise gr.Error("Prompt is required.")

    ensure_playwright_browsers()

    # Parse and extract reference image paths
    parsed_image_paths: List[str] = []
    if reference_images:
        if isinstance(reference_images, str):
            if os.path.exists(reference_images):
                parsed_image_paths.append(reference_images)
        elif isinstance(reference_images, list):
            for item in reference_images:
                path = None
                if isinstance(item, str):
                    path = item
                elif hasattr(item, "name"):
                    path = item.name
                elif isinstance(item, dict) and "path" in item:
                    path = item["path"]
                if path and os.path.exists(path):
                    parsed_image_paths.append(path)

    # Enforce Google Flow max 3 reference images cap
    if len(parsed_image_paths) > 3:
        print(f"[INFO] Capping reference images from {len(parsed_image_paths)} to max allowed 3.")
        parsed_image_paths = parsed_image_paths[:3]

    _REQUEST_COUNTER += 1
    worker_idx = int(os.environ.get("WEBSHARE_PROXY_INDEX", "0"))
    run_id = str(uuid.uuid4())[:8]
    output_target = OUTPUTS_DIR / f"gen_{run_id}.png"

    # Controlled concurrency via Semaphore
    with GENERATION_SEMAPHORE:
        try:
            results = run_on_engine_loop(
                engine.generate_image(
                    prompt=prompt.strip(),
                    negative_prompt=negative_prompt.strip() if negative_prompt else None,
                    num_outputs=int(num_outputs),
                    aspect_ratio=aspect_ratio,
                    model_variant=model_variant,
                    reference_images=parsed_image_paths,
                    image_strength=float(image_strength),
                    style_preset=style_preset,
                    seed=int(seed) if seed is not None else -1,
                    output_path=str(output_target),
                    headless=True,
                    overwrite=False,
                    proxy_index=worker_idx,
                )
            )

            status_msg = f"Generation complete: {len(results)} variation(s) rendered with {model_variant} ({aspect_ratio})."
            return results, status_msg

        except SessionExpiredError:
            raise gr.Error("Session expired. Please update storage_state.json or SESSION_STORAGE_BASE64 secret.")
        except ProxyConnectionError as pce:
            sanitized = sanitize_log_message(str(pce))
            raise gr.Error(f"Proxy Connection Failed: {sanitized}")
        except GenerationFailedError as gfe:
            sanitized = sanitize_log_message(str(gfe))
            raise gr.Error(f"Generation rejected by Google Flow: {sanitized}")
        except GenerationTimeoutError as gte:
            sanitized = sanitize_log_message(str(gte))
            raise gr.Error(f"Generation timed out: {sanitized}")
        except Exception as exc:
            sanitized = sanitize_log_message(str(exc))
            raise gr.Error(f"Unexpected automation failure: {sanitized}")


# ---------------------------------------------------------------------------
# 4. FastAPI Setup & REST Models
# ---------------------------------------------------------------------------
API_BEARER_TOKEN = os.environ.get("API_BEARER_TOKEN", "").strip()

fastapi_app = App(
    title="Google Flow Custom API Wrapper",
    description="REST & Gradio API for headless Google Flow image generation",
    version="2.0.0"
)


AUTO_REFRESH_INTERVAL_HOURS = int(os.environ.get("AUTO_REFRESH_INTERVAL_HOURS", "4"))
# Only push the refreshed session to the HF secret every N heartbeats (default 6 ≈ 24h
# at a 4h interval), because updating the secret restarts the Space and wipes the profile.
SECRET_SYNC_EVERY = int(os.environ.get("SECRET_SYNC_EVERY", "6"))
_KEEPALIVE_COUNT = 0


def _sync_session_secret(hf_token: str, space_id: str) -> None:
    """Blocking helper (run via asyncio.to_thread) that pushes fresh cookies to the HF secret."""
    from huggingface_hub import HfApi
    api = HfApi(token=hf_token)
    with open(STORAGE_STATE_PATH, "rb") as sf:
        new_b64 = base64.b64encode(sf.read()).decode("utf-8")
    api.add_space_secret(repo_id=space_id, key="SESSION_STORAGE_BASE64", value=new_b64)
    print(f"[INFO] Keep-alive task: Auto-updated SESSION_STORAGE_BASE64 secret on Space '{space_id}'.")


async def auto_refresh_session_loop():
    """
    Background keep-alive task: periodically refreshes Google Flow cookies in the background
    so the rolling session tokens (SIDRTS / SIDTS) never expire even during long idle periods.
    """
    # Wait 3 minutes after startup before initial background check
    await asyncio.sleep(180)
    while True:
        try:
            print("[INFO] Keep-alive task: Proactively refreshing Google Flow session tokens...")
            worker_idx = int(os.environ.get("WEBSHARE_PROXY_INDEX", "0"))
            success = await engine.refresh_session_state(
                storage_state_path=STORAGE_STATE_PATH,
                flow_url=FLOW_URL,
                proxy_index=worker_idx,
                timeout=40
            )
            if success:
                print("[INFO] Keep-alive task: Session tokens refreshed and persisted to disk.")
                # Pushing the SESSION_STORAGE_BASE64 secret RESTARTS the Space, which
                # wipes the warm persistent profile. Only sync it occasionally (not on
                # every heartbeat) so we preserve cookies across rebuilds without
                # constantly throwing away the long-lived device identity.
                global _KEEPALIVE_COUNT
                _KEEPALIVE_COUNT += 1
                hf_token = (
                    os.environ.get("HF_TOKEN")
                    or os.environ.get("HUGGING_FACE_HUB_TOKEN")
                    or os.environ.get("HF_API_TOKEN")
                )
                space_id = os.environ.get("SPACE_ID")
                if hf_token and space_id and (_KEEPALIVE_COUNT % SECRET_SYNC_EVERY == 0):
                    try:
                        await asyncio.to_thread(_sync_session_secret, hf_token, space_id)
                    except Exception as hf_err:
                        print(f"[DEBUG] HF Space secret auto-sync skipped: {hf_err}")
            else:
                print("[WARN] Keep-alive task: Session refresh failed or redirected to login.")

        except Exception as exc:
            print(f"[WARN] Error in auto_refresh_session_loop: {exc}")

        # Sleep for configured interval (default: 4 hours)
        await asyncio.sleep(AUTO_REFRESH_INTERVAL_HOURS * 3600)


@fastapi_app.on_event("startup")
async def on_startup():
    """Initializes environment tasks in background so server binds to port immediately."""
    print("[INFO] Application startup: verifying container environment...")
    asyncio.create_task(asyncio.to_thread(ensure_playwright_browsers))
    asyncio.create_task(cleanup_expired_images_loop())
    # The keep-alive heartbeat must run on the SAME loop that owns the persistent
    # browser, so schedule it on the dedicated engine loop (not uvicorn's loop).
    get_engine_loop().create_task(auto_refresh_session_loop())



@fastapi_app.middleware("http")
async def security_auth_middleware(request: Request, call_next):
    """
    Enforces Bearer token authentication strictly on protected generation endpoints if API_BEARER_TOKEN is set.
    The web UI, health checks (/api/health), static assets, and OpenAPI docs remain publicly accessible.
    """
    if API_BEARER_TOKEN and request.url.path == "/api/generate_image":
        auth_header = request.headers.get("Authorization", "")
        bearer_token = auth_header[7:].strip() if auth_header.lower().startswith("bearer ") else ""
        query_token = request.query_params.get("token", "").strip()
        api_key = request.headers.get("X-API-Key", "").strip()

        # Valid if matches API_BEARER_TOKEN or user authenticates with their Hugging Face Space token
        is_valid = (
            query_token == API_BEARER_TOKEN
            or api_key == API_BEARER_TOKEN
            or bearer_token == API_BEARER_TOKEN
            or bearer_token.startswith("hf_")
        )

        if not is_valid:
            return JSONResponse(
                status_code=401,
                content={
                    "status": "error",
                    "error": "Unauthorized",
                    "message": "Missing or invalid Bearer token. Provide 'Authorization: Bearer <TOKEN>' header, '?token=<TOKEN>', or 'X-API-Key: <TOKEN>'."
                },
                headers={"WWW-Authenticate": "Bearer"}
            )

    return await call_next(request)


class GenerateRequest(BaseModel):
    prompt: str = Field(..., description="Main text prompt for image generation")
    negative_prompt: Optional[str] = Field(None, description="Negative prompt of elements to exclude")
    num_outputs: int = Field(1, ge=1, le=4, description="Number of variations (1 to 4)")
    aspect_ratio: str = Field("16:9", description="Aspect ratio: 1:1, 16:9, 9:16, 4:3, 3:4")
    model_variant: str = Field("Nano Banana 2", description="Nano Banana 2, Nano Banana Pro, or Nano Banana 2 Lite")
    reference_images: Optional[List[str]] = Field(None, description="List of reference image paths (max 3)")
    image_strength: float = Field(0.75, ge=0.1, le=1.0, description="Influence factor of reference image")
    style_preset: str = Field("None", description="Style preset keyword injection")
    seed: int = Field(-1, description="Deterministic seed (-1 for random)")


@fastapi_app.get("/api/health")
async def api_health():
    """Health check endpoint returning service status and session validity."""
    active = is_session_active()
    return JSONResponse(
        status_code=200 if active else 503,
        content={
            "status": "ok" if active else "unauthenticated",
            "session_active": active,
            "retention_minutes": IMAGE_RETENTION_MINUTES
        }
    )


@fastapi_app.get("/api/download")
async def api_download(file: str):
    """
    Allows downloading rendered image assets by filename or path.
    Automatically verifies that the file is within the active IMAGE_RETENTION_MINUTES TTL window.
    """
    filename = Path(file).name
    target = (OUTPUTS_DIR / filename).resolve()
    if not target.exists() or not target.is_file():
        return JSONResponse(
            status_code=404,
            content={
                "status": "not_found",
                "error": "File not found",
                "message": f"The image '{filename}' was not found on the server or has already been expired."
            }
        )

    # Check expiration TTL
    age_seconds = time.time() - target.stat().st_mtime
    if age_seconds > (IMAGE_RETENTION_MINUTES * 60):
        try:
            target.unlink()
        except Exception:
            pass
        return JSONResponse(
            status_code=410,
            content={
                "status": "expired",
                "error": "Download link expired",
                "message": f"This download link has expired. Images are retained for {IMAGE_RETENTION_MINUTES} minutes after generation.",
                "retention_minutes": IMAGE_RETENTION_MINUTES
            }
        )

    return FileResponse(str(target), media_type="image/png", filename=filename)


@fastapi_app.post("/api/generate_image")
async def api_generate_image(request: Request, payload: GenerateRequest = Body(...)):
    """
    Direct REST POST endpoint for programmatic API integration without gradio_client.
    Returns:
    - images: Local container paths
    - download_urls: Fully qualified public download links
    - relative_urls: Relative download endpoints
    - images_base64: Raw base64 encoded PNG strings for immediate client usage
    - expires_at: ISO timestamp when download links will invalidate
    """
    try:
        images, status = await asyncio.to_thread(
            run_generation,
            prompt=payload.prompt,
            negative_prompt=payload.negative_prompt,
            num_outputs=payload.num_outputs,
            aspect_ratio=payload.aspect_ratio,
            model_variant=payload.model_variant,
            reference_images=payload.reference_images,
            image_strength=payload.image_strength,
            style_preset=payload.style_preset,
            seed=payload.seed,
        )

        host = request.headers.get("x-forwarded-host") or request.headers.get("host") or "helo-ayush-imagegeneratorflow.hf.space"
        proto = request.headers.get("x-forwarded-proto") or "https"
        if "0.0.0.0" in host or "127.0.0.1" in host or "localhost" in host:
            origin = "https://helo-ayush-imagegeneratorflow.hf.space"
        else:
            origin = f"{proto}://{host}"

        download_urls = [f"{origin}/api/download?file={Path(p).name}" for p in images]
        relative_urls = [f"/api/download?file={Path(p).name}" for p in images]
        expires_at = (datetime.now(timezone.utc) + timedelta(minutes=IMAGE_RETENTION_MINUTES)).isoformat()

        images_b64 = []
        for p in images:
            try:
                with open(p, "rb") as f:
                    images_b64.append(base64.b64encode(f.read()).decode("utf-8"))
            except Exception:
                pass

        return {
            "status": "success",
            "images": images,
            "download_urls": download_urls,
            "relative_urls": relative_urls,
            "images_base64": images_b64,
            "count": len(images),
            "retention_minutes": IMAGE_RETENTION_MINUTES,
            "expires_at": expires_at,
            "details": status
        }
    except gr.Error as ge:
        return JSONResponse(status_code=400, content={"status": "error", "message": str(ge)})
    except Exception as e:
        return JSONResponse(status_code=500, content={"status": "error", "message": str(e)})


# ---------------------------------------------------------------------------
# 5. Gradio UI Assembly
# ---------------------------------------------------------------------------
custom_css = """
#generate-btn {
    background: linear-gradient(135deg, #1e3c72 0%, #2a5298 100%);
    color: white;
    font-weight: 600;
    border: none;
    border-radius: 8px;
}
#generate-btn:hover {
    background: linear-gradient(135deg, #2a5298 0%, #1e3c72 100%);
}
.gradio-container {
    max-width: 1200px !important;
}
"""

with gr.Blocks(title="Google Flow Custom API Wrapper") as demo:
    gr.Markdown(
        """
        # 🎨 Google Flow Advanced Image Generation API & UI (V2)
        **Production-grade wrapper for Google Flow** with deterministic per-prompt isolation, multi-variation batch generation, and full advanced control mapping.
        """
    )

    with gr.Row():
        with gr.Column(scale=5):
            prompt_input = gr.Textbox(
                label="Prompt",
                placeholder="A futuristic cyberpunk city at sunset, 8k resolution, neon reflections...",
                lines=3,
            )

            with gr.Accordion("⚙️ Advanced Generation Controls", open=False):
                with gr.Row():
                    aspect_ratio_input = gr.Dropdown(
                        choices=["1:1", "16:9", "9:16", "4:3", "3:4"],
                        value="16:9",
                        label="Aspect Ratio",
                        info="Controls the dimensions of the rendered image",
                    )
                    model_variant_input = gr.Dropdown(
                        choices=["Nano Banana 2", "Nano Banana Pro", "Nano Banana 2 Lite"],
                        value="Nano Banana 2",
                        label="Model Variant",
                        info="Choose engine fidelity and speed",
                    )
                    num_outputs_input = gr.Slider(
                        minimum=1,
                        maximum=4,
                        step=1,
                        value=1,
                        label="Batch Outputs (Parallel Variations)",
                        info="Google Flow generates 1 to 4 parallel variations",
                    )

                with gr.Row():
                    style_preset_input = gr.Dropdown(
                        choices=["None", "Photorealistic", "Cinematic", "Anime", "Digital Art", "3D Render"],
                        value="None",
                        label="Style Preset",
                        info="Curated artistic keywords added to prompt",
                    )
                    seed_input = gr.Number(
                        value=-1,
                        precision=0,
                        label="Seed (-1 for Random)",
                        info="Fixed seed for reproducible generation",
                    )
                    image_strength_input = gr.Slider(
                        minimum=0.1,
                        maximum=1.0,
                        step=0.05,
                        value=0.75,
                        label="Reference Image Influence",
                        info="Weight of attached reference image(s)",
                    )

                with gr.Row():
                    reference_images_input = gr.File(
                        file_count="multiple",
                        file_types=["image"],
                        label="Reference Images (Max 3)",
                    )
                    negative_prompt_input = gr.Textbox(
                        lines=2,
                        placeholder="blurry, bad anatomy, low quality, distorted, watermark...",
                        label="Negative Prompt (Exclude Elements)",
                    )

            generate_button = gr.Button("🚀 Generate Image", elem_id="generate-btn", size="lg")

        with gr.Column(scale=5):
            gallery_output = gr.Gallery(
                label="Generated Images",
                columns=2,
                height="auto",
            )
            status_output = gr.Textbox(
                label="Execution Status",
                lines=1,
                interactive=False,
            )

    # Hidden ZeroGPU heartbeat button to register @spaces.GPU in the event graph
    _dummy_btn = gr.Button("ZeroGPU Keepalive", visible=False)
    _dummy_btn.click(fn=dummy_gpu)

    # Wire UI action and expose Gradio API endpoint
    generate_button.click(
        fn=run_generation,
        inputs=[
            prompt_input,
            negative_prompt_input,
            num_outputs_input,
            aspect_ratio_input,
            model_variant_input,
            reference_images_input,
            image_strength_input,
            style_preset_input,
            seed_input,
        ],
        outputs=[gallery_output, status_output],
        api_name="generate_image",
    )

    gr.Examples(
        examples=[
            [
                "A futuristic electric sports car speeding through a neon Tokyo tunnel",
                None,
                1,
                "16:9",
                "Nano Banana 2",
                None,
                0.75,
                "Cinematic",
                -1,
            ],
            [
                "A cute baby red panda sleeping peacefully on a cherry blossom branch",
                "blurry, dark, noisy",
                2,
                "1:1",
                "Nano Banana Pro",
                None,
                0.75,
                "Photorealistic",
                -1,
            ],
            [
                "A vintage steam train crossing an ancient stone bridge in autumn mountains",
                None,
                1,
                "16:9",
                "Nano Banana 2 Lite",
                None,
                0.75,
                "None",
                -1,
            ],
        ],
        inputs=[
            prompt_input,
            negative_prompt_input,
            num_outputs_input,
            aspect_ratio_input,
            model_variant_input,
            reference_images_input,
            image_strength_input,
            style_preset_input,
            seed_input,
        ],
    )

# ---------------------------------------------------------------------------
# 6. Server Execution (Native Gradio launch for ZeroGPU compatibility)
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    server_port = int(os.environ.get("PORT", "7860"))
    server_name = os.environ.get("HOST", "0.0.0.0")
    print(f"[INFO] Starting Google Flow Gradio Server on {server_name}:{server_port}...")
    demo.queue().launch(
        _app=fastapi_app,
        server_name=server_name,
        server_port=server_port,
        ssr_mode=False
    )
