# 🎨 Google Flow Image Generator — Developer & Public API Guide

[![GitHub Repository](https://img.shields.io/badge/GitHub-helo--ayush%2FFlowImageGenerator-blue?logo=github)](https://github.com/helo-ayush/FlowImageGenerator)
[![Hugging Face Space](https://img.shields.io/badge/%F0%9F%A4%97%20Hugging%20Face-Spaces-yellow)](https://huggingface.co/spaces/helo-ayush/ImageGeneratorFlow)
[![Swagger API Docs](https://img.shields.io/badge/OpenAPI-Swagger%20UI-green)](https://helo-ayush-imagegeneratorflow.hf.space/docs)
[![License: MIT](https://img.shields.io/badge/License-MIT-purple.svg)](https://opensource.org/licenses/MIT)

Complete developer reference, REST API specification, input/output schemas, and plug-and-play code integrations (**TypeScript / Node.js**, **Python**, **cURL**) for the **Google Flow Image Generator** (`FlowImageGenerator`).

---

## 📌 1. Project Overview & Public Endpoints

The **FlowImageGenerator** is a production-grade headless automation service and REST/Gradio API wrapper around **Google Flow** (`Nano Banana 2` and `Nano Banana Pro`). It features stealth Playwright anti-detection, residential proxy routing, automated session keep-alive token rotation, and multi-format image generation.

### Public Service Links

| Resource | Public URL | Description |
| :--- | :--- | :--- |
| **GitHub Repository** | [github.com/helo-ayush/FlowImageGenerator](https://github.com/helo-ayush/FlowImageGenerator) | Full open-source codebase, engine, and setup scripts |
| **Hosted API Base URL** | `https://helo-ayush-imagegeneratorflow.hf.space` | Public Hugging Face Space endpoint |
| **Interactive OpenAPI Docs** | [https://helo-ayush-imagegeneratorflow.hf.space/docs](https://helo-ayush-imagegeneratorflow.hf.space/docs) | Swagger UI for testing endpoints in the browser |
| **Interactive Web UI** | [https://helo-ayush-imagegeneratorflow.hf.space/](https://helo-ayush-imagegeneratorflow.hf.space/) | Visual Gradio generation interface |
| **Liveness Health Check** | `https://helo-ayush-imagegeneratorflow.hf.space/api/health` | Public health probe (no auth required) |

---

## 🚀 2. Usage Modes: Hosted Endpoint vs. Self-Hosting

### Mode A: Consuming the Hosted Public Endpoint

You can send HTTP requests directly to the hosted Hugging Face Space from your backend services, automation scripts, YouTube pipelines, or Discord bots.

* **Base URL:** `https://helo-ayush-imagegeneratorflow.hf.space`
* **Authentication:** Pass your Bearer token in the `Authorization` header.

### Mode B: Self-Hosting & Running Locally

If you want to run your own instance with your own Google Flow account and proxy:

1. **Clone the repository:**
   ```bash
   git clone https://github.com/helo-ayush/FlowImageGenerator.git
   cd FlowImageGenerator
   ```

2. **Install dependencies:**
   ```bash
   pip install -r requirements.txt
   playwright install chromium
   ```

3. **Capture your Google Flow session (one-time interactive login):**
   ```bash
   python login_session.py --new
   ```
   *Sign in to your Google Account, navigate to your Google Flow project, and press Enter in the terminal to generate `storage_state.json`.*

4. **Launch the local server:**
   ```bash
   python app.py
   ```
   *The server starts at `http://localhost:7860` with both the Gradio Web UI and FastAPI REST endpoints.*

5. **Deploy to your own free Hugging Face Space:**
   * Push repo files to your Space.
   * Add secret `SESSION_STORAGE_BASE64` (Base64 string of `storage_state.json`).
   * Add secret `WEBSHARE_PROXY_URL` (your static residential proxy).
   * Add secret `API_BEARER_TOKEN` (your secret access token).

---

## 🔑 3. Authentication

To prevent quota exhaustion, generation endpoints (`/api/generate_image`) are protected by a Bearer token.

You can supply the token in any of the following three ways:

1. **Standard Header (Recommended):**
   ```http
   Authorization: Bearer dev_secret_token_123
   ```

2. **Custom Header:**
   ```http
   X-API-Key: dev_secret_token_123
   ```

3. **URL Query Parameter:**
   ```http
   POST https://helo-ayush-imagegeneratorflow.hf.space/api/generate_image?token=dev_secret_token_123
   ```

> **Note:** The health check probe (`GET /api/health`), interactive Web UI, and OpenAPI docs (`/docs`) are public and do not require authentication.

---

## 📡 4. REST API Specification

### Endpoint 1: Generate Image (`POST /api/generate_image`)

Generates high-resolution images using Google Flow with full control over aspect ratios, models, style presets, and optional image-to-image reference conditioning.

* **Method:** `POST`
* **Path:** `/api/generate_image`
* **Content-Type:** `application/json`

#### Request Headers:
```http
Authorization: Bearer dev_secret_token_123
Content-Type: application/json
```

#### Request Body Schema (JSON):
```json
{
  "prompt": "Cinematic YouTube thumbnail of an AI robot with glowing neon blue circuits holding an energy orb, 8k resolution, volumetric dramatic lighting, photorealistic",
  "negative_prompt": "blurry, low quality, distorted, extra limbs, watermark, text",
  "num_outputs": 1,
  "aspect_ratio": "16:9",
  "model_variant": "Nano Banana 2",
  "style_preset": "Cinematic",
  "reference_images": [],
  "image_strength": 0.75,
  "seed": -1
}
```

#### Detailed Parameter Reference:

| Field | Type | Required | Default | Allowed Values & Description |
| :--- | :---: | :---: | :---: | :--- |
| `prompt` | `string` | **Yes** | — | Descriptive prompt for image generation. |
| `negative_prompt` | `string` | No | `null` | Elements, distortions, or text to exclude. |
| `num_outputs` | `integer`| No | `1` | Number of image variations to generate (`1`, `2`, `3`, `4`). |
| `aspect_ratio` | `string` | No | `"16:9"` | Aspect ratio dimensions:<br>• `"16:9"` — YouTube Thumbnail, Landscape<br>• `"9:16"` — YouTube Shorts, TikTok, Reels<br>• `"1:1"` — Instagram Square, Avatar<br>• `"4:3"` — Standard Landscape<br>• `"3:4"` — Standard Portrait |
| `model_variant` | `string` | No | `"Nano Banana 2"` | Google Flow model architecture:<br>• `"Nano Banana 2"` (Recommended, state-of-the-art)<br>• `"Nano Banana Pro"` (Ultra-detailed renders)<br>• `"Nano Banana 2 Lite"` (Fast generation) |
| `style_preset` | `string` | No | `"None"` | Curated stylistic modifier injection:<br>`"None"`, `"Cinematic"`, `"Photorealistic"`, `"Anime"`, `"Digital Art"`, `"3D Render"`. |
| `reference_images` | `array` | No | `[]` | Up to 3 Base64 strings or URLs for Image-to-Image reference conditioning. |
| `image_strength` | `float` | No | `0.75` | Weight of reference images (`0.1` to `1.0`). Higher values adhere closer to reference input. |
| `seed` | `integer`| No | `-1` | Random seed for generation reproducibility (`-1` generates randomly). |

---

#### Success Response (`200 OK`):
```json
{
  "status": "success",
  "count": 1,
  "model": "Nano Banana 2",
  "aspect_ratio": "16:9",
  "images": [
    {
      "filename": "gen_9bd1d31b.png",
      "download_url": "/api/download?file=gen_9bd1d31b.png",
      "base64": "iVBORw0KGgoAAAANSUhEUgAABYAAAAQACA...",
      "mime_type": "image/png"
    }
  ],
  "message": "Generation complete: 1 variation(s) rendered with Nano Banana 2 (16:9)."
}
```

> ⚡ **Zero-Latency File Saving:** Each image object includes the full image as a standard **Base64 string**. You can save it directly to disk in your application with zero secondary download calls.

---

### Endpoint 2: Service Health Probe (`GET /api/health`)

Public endpoint to check if the generation engine is online and the Google session is authenticated.

* **Method:** `GET`
* **Path:** `/api/health`
* **Authentication:** None required

#### Success Response (`200 OK`):
```json
{
  "status": "ok",
  "session_active": true,
  "retention_minutes": 60
}
```

*If the session is invalid or expired, this returns HTTP `503` with `"session_active": false`.*

---

### Endpoint 3: Direct File Download (`GET /api/download`)

Directly streams the raw PNG binary file for a previously generated image.

* **Method:** `GET`
* **Path:** `/api/download?file=<filename>`
* **Authentication:** Optional / Public within TTL window

```bash
curl "https://helo-ayush-imagegeneratorflow.hf.space/api/download?file=gen_9bd1d31b.png" \
  --output downloaded_image.png
```

---

## 💻 5. Production Integration Code Examples

### TypeScript / Node.js (Full Client Implementation)

Ideal for YouTube Automation, Discord bots, or Node.js web services.

```typescript
import * as fs from 'fs';
import * as path from 'path';

export interface GenerationOptions {
  negativePrompt?: string;
  numOutputs?: number;
  aspectRatio?: '16:9' | '9:16' | '1:1' | '4:3' | '3:4';
  modelVariant?: 'Nano Banana 2' | 'Nano Banana Pro' | 'Nano Banana 2 Lite';
  stylePreset?: 'None' | 'Cinematic' | 'Photorealistic' | 'Anime' | 'Digital Art' | '3D Render';
  referenceImages?: string[];
  imageStrength?: number;
  seed?: number;
}

export interface GeneratedImageItem {
  filename: string;
  download_url: string;
  base64: string;
  mime_type: string;
}

export interface GenerationResponse {
  status: string;
  count: number;
  model: string;
  aspect_ratio: string;
  images: GeneratedImageItem[];
  message: string;
}

/**
 * Generates an image via FlowImageGenerator and saves it directly to outputPath.
 */
export async function generateFlowImage(
  prompt: string,
  outputPath: string,
  options: GenerationOptions = {}
): Promise<string> {
  const BASE_URL = process.env.FLOW_API_URL || 'https://helo-ayush-imagegeneratorflow.hf.space';
  const API_TOKEN = process.env.FLOW_API_TOKEN || 'dev_secret_token_123';

  const payload = {
    prompt,
    negative_prompt: options.negativePrompt || 'blurry, low quality, bad anatomy, watermark, text',
    num_outputs: options.numOutputs ?? 1,
    aspect_ratio: options.aspectRatio || '16:9',
    model_variant: options.modelVariant || 'Nano Banana 2',
    style_preset: options.stylePreset || 'Cinematic',
    reference_images: options.referenceImages || [],
    image_strength: options.imageStrength ?? 0.75,
    seed: options.seed ?? -1,
  };

  const response = await fetch(`${BASE_URL.replace(/\/$/, '')}/api/generate_image`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'Authorization': `Bearer ${API_TOKEN}`,
    },
    body: JSON.stringify(payload),
  });

  if (!response.ok) {
    const errorText = await response.text();
    throw new Error(`Flow API Error [${response.status}]: ${errorText}`);
  }

  const data = (await response.json()) as GenerationResponse;
  if (!data.images || data.images.length === 0) {
    throw new Error('Flow generation succeeded but returned no images.');
  }

  // Decode base64 and write directly to disk
  const buffer = Buffer.from(data.images[0].base64, 'base64');
  fs.mkdirSync(path.dirname(outputPath), { recursive: true });
  fs.writeFileSync(outputPath, buffer);

  console.log(`[FlowImageGenerator] Image saved successfully to: ${outputPath}`);
  return outputPath;
}

// ---------------------------------------------------------------------------
// Convenience Helper: YouTube Thumbnail Generator (16:9)
// ---------------------------------------------------------------------------
export async function generateYouTubeThumbnail(prompt: string, outputPath: string): Promise<string> {
  return generateFlowImage(prompt, outputPath, {
    aspectRatio: '16:9',
    modelVariant: 'Nano Banana 2',
    stylePreset: 'Cinematic',
  });
}

// ---------------------------------------------------------------------------
// Convenience Helper: YouTube Shorts / TikTok Cover Generator (9:16)
// ---------------------------------------------------------------------------
export async function generateYouTubeShortsCover(prompt: string, outputPath: string): Promise<string> {
  return generateFlowImage(prompt, outputPath, {
    aspectRatio: '9:16',
    modelVariant: 'Nano Banana 2',
    stylePreset: 'Photorealistic',
  });
}
```

---

### Python (Direct REST with `requests`)

```python
import base64
import os
import requests

API_URL = os.getenv("FLOW_API_URL", "https://helo-ayush-imagegeneratorflow.hf.space/api/generate_image")
API_TOKEN = os.getenv("FLOW_API_TOKEN", "dev_secret_token_123")

payload = {
    "prompt": "Cinematic YouTube thumbnail of a futuristic cyber city at dusk, 8k resolution",
    "negative_prompt": "blurry, low quality, artifacts",
    "num_outputs": 1,
    "aspect_ratio": "16:9",
    "model_variant": "Nano Banana 2",
    "style_preset": "Cinematic"
}

headers = {
    "Content-Type": "application/json",
    "Authorization": f"Bearer {API_TOKEN}"
}

response = requests.post(API_URL, json=payload, headers=headers, timeout=180)
response.raise_for_status()

result = response.json()
image_base64 = result["images"][0]["base64"]

# Save decoded image
output_filename = "thumbnail.png"
with open(output_filename, "wb") as f:
    f.write(base64.b64decode(image_base64))

print(f"Image successfully rendered and saved to {output_filename}!")
```

---

### Python (`gradio_client` Integration)

If you prefer using Hugging Face's official Python SDK:

```python
from gradio_client import Client, handle_file

# Connect to the Hugging Face Space
client = Client("helo-ayush/ImageGeneratorFlow")

# Run generation
result = client.predict(
    prompt="A surreal floating island in the clouds with waterfalls, 8k, vibrant lighting",
    negative_prompt="blurry, distorted",
    num_outputs=1,
    aspect_ratio="16:9",
    model_variant="Nano Banana 2",
    reference_images=None,
    image_strength=0.75,
    style_preset="Cinematic",
    seed=-1,
    api_name="/generate_image"
)

gallery_images, status_message = result
print("Rendered Image Path:", gallery_images[0]["image"])
```

---

### cURL

#### Generate 16:9 Thumbnail:
```bash
curl -X POST "https://helo-ayush-imagegeneratorflow.hf.space/api/generate_image" \
  -H "Authorization: Bearer dev_secret_token_123" \
  -H "Content-Type: application/json" \
  -d '{
    "prompt": "Dramatic view of a volcano erupting with purple neon lightning, cinematic 8k",
    "aspect_ratio": "16:9",
    "model_variant": "Nano Banana 2",
    "style_preset": "Cinematic",
    "num_outputs": 1
  }'
```

#### Health Probe:
```bash
curl -X GET "https://helo-ayush-imagegeneratorflow.hf.space/api/health"
```

---

## ⚡ 6. Architecture & Resiliency Engineering

1. **Automated Token Keep-Alive (`auto_refresh_session_loop`)**:
   Google uses rolling session tokens (`__Secure-1PSIDRTS` and `__Secure-1PSIDTS`) that expire within hours if idle. The engine features an autonomous background keep-alive task running every 4 hours. It opens Google Flow through the pinned residential proxy, triggers Google's internal cookie rotation, saves the refreshed session to disk, and automatically syncs the updated base64 session back to Hugging Face Spaces via `HF_TOKEN`.

2. **BotGuard Anti-Detection & Platform Emulation**:
   On Linux container hosts (such as Hugging Face Spaces or Docker), Playwright Chromium by default leaks `navigator.platform = "Linux x86_64"`, conflicting with Windows 10 User-Agents and triggering Google BotGuard challenges. The engine injects a comprehensive stealth script overriding `navigator.platform` to `Win32`, spoofing `navigator.userAgentData`, screen dimensions, and branded `window.chrome`.

3. **Static Residential Proxy Pinning**:
   Cloud IP ranges (Hugging Face, AWS, GCP) are flagged by Google. The engine routes all Playwright browser traffic through dedicated Webshare static ISP/residential proxies, pinned to index `0` to ensure IP stability across requests.

4. **Deterministic Canvas Stamping (`data-generation-uuid`)**:
   Prevents race conditions by stamping a UUID on the newly spawned generation tile in the Google Flow DOM, strictly polling until that specific tile reaches 100% completion before extracting the CDN image blob.

---

## ⚠️ 7. Status Codes & Error Handling Matrix

| HTTP Status | Meaning | Typical Root Cause | Recommended Action |
| :---: | :--- | :--- | :--- |
| `200` | **Success** | Generation succeeded; image returned in base64. | Decode base64 string or download via `download_url`. |
| `400` | **Bad Request** | Missing prompt or empty payload. | Provide a non-empty `prompt` string. |
| `401` | **Unauthorized** | Missing or invalid Bearer token. | Provide `Authorization: Bearer <TOKEN>` header or `?token=<TOKEN>`. |
| `422` | **Validation Error** | Schema error (e.g. invalid `aspect_ratio` or `num_outputs > 4`). | Verify parameters against the parameter table above. |
| `500` | **Generation Error** | Google content filter blocked prompt or browser timed out. | Rephrase prompt to comply with Google safety guidelines. |
| `503` | **Session Inactive** | Google session cookies expired or account locked. | Run `python login_session.py --new` to export fresh session state. |

---

## 🔒 8. Security & Secret Management

When contributing to or forking this public repository:

- **NEVER** commit `storage_state.json` or `.env` files to Git. Both are strictly excluded in `.gitignore`.
- For cloud container deployments (Hugging Face Spaces), store your credentials securely under **Space Settings -> Variables and secrets**:
  - `SESSION_STORAGE_BASE64`: Base64 string of `storage_state.json`
  - `WEBSHARE_PROXY_URL`: Static proxy credentials
  - `API_BEARER_TOKEN`: Secret Bearer token for API protection
  - `HF_TOKEN`: Hugging Face write token for automated session secret updates
