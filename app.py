"""
app.py
======
Production-grade Gradio wrapper for Google Flow Image Generation.
Designed for local execution and Hugging Face Spaces deployments.

Features:
- Automatic session hydration from SESSION_STORAGE_BASE64 environment secret.
- Full control parameter matrix:
    * prompt (Required)
    * negative_prompt (Optional)
    * num_outputs (1 to 4 parallel variations)
    * aspect_ratio ("1:1", "16:9", "9:16", "4:3", "3:4")
    * model_variant ("Nano Banana 2", "Nano Banana Pro", "Nano Banana 2 Lite")
    * reference_images (Multiple image upload, capped at Google Flow's max 3 cap)
    * image_strength (0.1 to 1.0)
    * style_preset ("None", "Photorealistic", "Cinematic", "Anime", "Digital Art", "3D Render")
    * seed (-1 for random)
- Strict FIFO concurrency guardrail via asyncio.Lock() to prevent container OOM.
- Gallery display for multi-variation outputs.
- Dual API exposure:
    * Gradio API route: api_name="generate_image"
    * REST API route: POST /api/generate_image
    * REST Health check route: GET /api/health
"""

import asyncio
import base64
import importlib
import json
import os
import shutil
import uuid
from pathlib import Path
from typing import List, Optional, Union

from dotenv import load_dotenv
load_dotenv()

# Explicitly disable Gradio 6 Node.js SSR sidecar to avoid port 7860 collisions
os.environ["GRADIO_SSR_MODE"] = "false"

import gradio as gr
import uvicorn
from fastapi import Body, FastAPI, Request
from fastapi.responses import JSONResponse
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
        print("[INFO] Installing Playwright Chromium browser binaries for container environment...")
        try:
            subprocess.run([sys.executable, "-m", "playwright", "install", "chromium"], check=True)
            _BROWSER_READY = True
            print("[INFO] Playwright Chromium installation successful.")
        except Exception as exc:
            print(f"[WARNING] Playwright install execution error: {exc}")

# ---------------------------------------------------------------------------
# 1. Startup Session State Hydration
# ---------------------------------------------------------------------------
OUTPUTS_DIR = Path("outputs").resolve()
OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)

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
MAX_CONCURRENT_REQUESTS = int(os.environ.get("MAX_CONCURRENT_REQUESTS", "2"))
GENERATION_SEMAPHORE = asyncio.Semaphore(MAX_CONCURRENT_REQUESTS)
_REQUEST_COUNTER = 0


# ---------------------------------------------------------------------------
# 3. Core Generation Handler
# ---------------------------------------------------------------------------
async def run_generation(
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
    Executes the generation pipeline inside an asyncio.Semaphore concurrency guardrail.
    Distributes requests across Webshare static proxies via proxy_index.
    """
    global _REQUEST_COUNTER
    if not prompt or not prompt.strip():
        raise gr.Error("Prompt is required.")

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

    # Generate unique base path for this run inside outputs directory
    _REQUEST_COUNTER += 1
    worker_idx = _REQUEST_COUNTER
    run_id = str(uuid.uuid4())[:8]
    output_target = OUTPUTS_DIR / f"gen_{run_id}.png"

    # Controlled concurrency via Semaphore
    async with GENERATION_SEMAPHORE:
        try:
            importlib.reload(engine)
            results = await engine.generate_image(
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

fastapi_app = FastAPI(
    title="Google Flow Custom API Wrapper",
    description="REST & Gradio API for headless Google Flow image generation",
    version="2.0.0"
)


@fastapi_app.on_event("startup")
async def on_startup():
    """Initializes environment tasks in background so server binds to port immediately."""
    print("[INFO] Application startup: verifying container environment...")
    asyncio.create_task(asyncio.to_thread(ensure_playwright_browsers))


@fastapi_app.middleware("http")
async def security_auth_middleware(request: Request, call_next):
    """
    Enforces Bearer token authentication strictly on protected generation endpoints if API_BEARER_TOKEN is set.
    The web UI, health checks (/api/health), static assets, and OpenAPI docs remain publicly accessible.
    """
    if API_BEARER_TOKEN and request.url.path == "/api/generate_image":
        auth_header = request.headers.get("Authorization", "")
        token = ""
        if auth_header.lower().startswith("bearer "):
            token = auth_header[7:].strip()
        elif "token" in request.query_params:
            token = request.query_params.get("token", "").strip()

        if token != API_BEARER_TOKEN:
            return JSONResponse(
                status_code=401,
                content={
                    "status": "error",
                    "error": "Unauthorized",
                    "message": "Missing or invalid Bearer token. Provide 'Authorization: Bearer <TOKEN>' header or '?token=<TOKEN>'."
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
        content={"status": "ok" if active else "unauthenticated", "session_active": active}
    )


@fastapi_app.post("/api/generate_image")
async def api_generate_image(payload: GenerateRequest = Body(...)):
    """
    Direct REST POST endpoint for programmatic API integration without gradio_client.
    """
    try:
        images, status = await run_generation(
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
        return {"status": "success", "images": images, "count": len(images), "details": status}
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
# 6. Mount Gradio onto FastAPI (ssr_mode=False disables Node.js server)
# ---------------------------------------------------------------------------
app = gr.mount_gradio_app(fastapi_app, demo, path="/", ssr_mode=False)

# ---------------------------------------------------------------------------
# 7. Server Execution
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    server_port = int(os.environ.get("PORT", "7860"))
    server_name = os.environ.get("HOST", "0.0.0.0")
    print(f"[INFO] Starting Google Flow Server on {server_name}:{server_port}...")
    uvicorn.run(app, host=server_name, port=server_port)
