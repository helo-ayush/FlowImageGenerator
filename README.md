---
title: ImageGeneratorFlow
emoji: ⚡
colorFrom: green
colorTo: yellow
sdk: gradio
sdk_version: 6.27.0
app_file: app.py
pinned: false
---

# Google Flow Headless Automation & Gradio Wrapper (V2)

Production-grade automated image generation pipeline for **Google Flow** using Playwright, pre-authenticated session persistence, residential proxy routing, and Gradio/FastAPI for both local environments and Hugging Face Spaces deployments.

---

## 📁 Repository Structure

```
├── app.py                   # Production Gradio Web UI & FastAPI server for local & Spaces
├── engine.py                # Core headless automation engine & generation pipeline (V2)
├── login_session.py         # One-time stealth login & session state exporter
├── storage_state.json       # Active authenticated Google Flow session state
├── .env                     # Runtime configuration (FLOW_URL, PROXY_INDEX, etc.)
├── .env.example             # Environment variables template
├── requirements.txt         # Python dependencies
├── packages.txt             # Debian system packages for container environments
├── .gitignore               # Git ignore rules
├── README.md                # Comprehensive documentation & setup guide
│
├── backups/                 # Archived account states and historical configurations
│   ├── storage_state_account1_backup.json
│   ├── storage_state_account2_backup.json
│   ├── storage_state_account3_fresh.json
│   ├── .env.account1.backup
│   ├── .env.account2.backup
│   └── .env.account3.backup
│
├── tests/                   # Verification and test suites
│   ├── test_client.py       # API verification client (gradio_client & REST)
│   ├── test_suite.py        # Comprehensive test runner for all endpoints
│   └── verify_secrets.py    # Pre-flight secret validation for container environments
│
└── outputs/                 # Output directory for rendered generation images
    └── samples/             # Verified sample generations
```

---

## 🏗️ Architecture & Generation Flow

```mermaid
flowchart TD
    Client[API / Web UI Client] -->|Submit Prompt + Reference Images| App[app.py FastAPI & Gradio]
    App -->|Concurrency Guardrail Semaphore| Engine[engine.py Automation Engine]
    Engine -->|Webshare Static Residential Proxy| Browser[Stealth Playwright Chromium]
    Browser -->|Session Cookies & Tokens| GoogleFlow[Google Flow Project Canvas]
    GoogleFlow -->|Upload Media & Agree Modal| AssetDrawer[Ingredient Drawer]
    AssetDrawer -->|Explicit Asset Row Selection| PromptDock[Prompt Bar & Ingredient Chip]
    PromptDock -->|Submit & DOM Stamp UUID| GenerationGrid[Canvas Grid Rendering]
    GenerationGrid -->|Track Progress 0% to 100%| Download[Direct Blob/CDN Image Extraction]
    Download -->|Save Disk & Base64 Response| Client
```

---

## 🚀 Quickstart Guide

### 1. Install Dependencies

```bash
pip install -r requirements.txt
playwright install chromium
```

---

### 2. Authenticate Session (One-Time)

To log into your Google Account and capture the authenticated session state:

#### For an Existing Account:
```bash
python login_session.py
```

#### For a Fresh / New Google Account:
```bash
python login_session.py --new
```

**Step-by-step**:
1. A genuine Chrome browser launches using residential proxy routing and stealth anti-detection flags (`navigator.webdriver` stripped).
2. Sign in with your Google account and complete 2FA verification.
3. Once your Google Flow project workspace is visible (e.g. `https://flow.google.com/project/<project-id>`), return to the terminal and press **[ENTER]**.
4. The script exports all cookies, cryptographic tokens, and localStorage to `storage_state.json`, and automatically syncs `FLOW_URL` in `.env`.

---

### 3. Run Locally

#### CLI Generation Test:
```bash
# Basic Text-to-Image
python engine.py --prompt "A futuristic cyberpunk city at sunset, 8k resolution" --output "outputs/cyberpunk.png"

# Image-to-Image Conditioning (Reference Image)
python engine.py --prompt "remove all text from image" --reference-images "path/to/image.jpg" --output "outputs/cleaned.png"
```

#### Launch Interactive Gradio UI & REST API:
```bash
python app.py
```
- **Web UI**: Open `http://localhost:7860` in your browser.
- **Health Check**: `GET http://localhost:7860/api/health`
- **Direct REST API**: `POST http://localhost:7860/api/generate_image`

---

## ⚙️ Control Parameters Matrix

| Parameter | Type | Options / Default | Description |
| :--- | :--- | :--- | :--- |
| `prompt` | `str` | Required | Main prompt describing desired scene or modifications |
| `negative_prompt` | `str` | Optional (`None`) | Elements or artifacts to exclude from generation |
| `num_outputs` | `int` | `1, 2, 3, 4` (default: `1`) | Number of parallel variations |
| `aspect_ratio` | `str` | `["1:1", "16:9", "9:16", "4:3", "3:4"]` | Output aspect ratio dimensions |
| `model_variant` | `str` | `["Nano Banana 2", "Nano Banana Pro", "Nano Banana 2 Lite"]` | Google Flow model architecture |
| `reference_images` | `list` | Up to 3 images | Reference ingredients for image-to-image conditioning |
| `image_strength` | `float` | `0.1` to `1.0` (default: `0.75`) | Influence weight of attached reference image(s) |
| `style_preset` | `str` | `["None", "Photorealistic", "Cinematic", "Anime", "Digital Art", "3D Render"]` | Curated artistic modifier injection |
| `seed` | `int` | `-1` for random or fixed integer | Reproducible generation seed |

---

## 🔌 API Usage Guide

### 1. Python `gradio_client` (Recommended)

```python
from gradio_client import Client, handle_file

# For Local App: Client("http://127.0.0.1:7860")
# For Hugging Face Spaces:
client = Client("your-username/ImageGeneratorFlow", token="hf_your_token")

# Text-to-Image
result = client.predict(
    prompt="A magical floating island in clouds, fantasy landscape, 8k",
    negative_prompt="blurry, low quality",
    num_outputs=1,
    aspect_ratio="16:9",
    model_variant="Nano Banana 2",
    reference_images=None,
    image_strength=0.75,
    style_preset="Cinematic",
    seed=-1,
    api_name="/generate_image"
)
images, status = result
print("Output Image:", images[0]["image"])

# Image-to-Image Conditioning
result = client.predict(
    prompt="remove all text from image",
    negative_prompt=None,
    num_outputs=1,
    aspect_ratio="16:9",
    model_variant="Nano Banana 2",
    reference_images=[handle_file("path/to/reference.jpg")],
    image_strength=0.75,
    style_preset="None",
    seed=-1,
    api_name="/generate_image"
)
```

### 2. Standard REST API (cURL)

```bash
# Health Check
curl -X GET http://localhost:7860/api/health

# Image Generation
curl -X POST http://localhost:7860/api/generate_image \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer dev_secret_token_123" \
  -d '{
    "prompt": "Cybernetic golden eagle perched on neon skyscraper",
    "num_outputs": 1,
    "aspect_ratio": "16:9",
    "model_variant": "Nano Banana 2",
    "style_preset": "Cinematic"
  }'
```

---

## 🛡️ Webshare Proxy Integration (Residential / Static ISP)

Hugging Face Spaces and cloud datacenters (AWS, GCP) use datacenter IP ranges that Google may challenge. Routing browser automation through **Webshare static/dedicated proxies** guarantees high reliability without Google security locks:

- **No 15-Minute Expiration**: Webshare provides static/dedicated IPs that do not expire or rotate mid-session. The IP remains fixed, eliminating Google session invalidations.
- **Zero Credit Burn**: Unlike per-request API gateways, Webshare does not charge per HTTP sub-request.
- **Fixed Pinning**: Pin all requests to a specific proxy via `WEBSHARE_PROXY_INDEX=0`.

```dotenv
# In .env or Space Secrets:
WEBSHARE_PROXY_INDEX=0
WEBSHARE_PROXY_URL=38.154.185.97:6370:username:password
```

---

## 🌐 Deploying to Hugging Face Spaces

1. Create a new Space on [Hugging Face](https://huggingface.co/new-space) (Select **Gradio** as SDK).
2. Push all repository files (`app.py`, `engine.py`, `requirements.txt`, `packages.txt`, `README.md`).
3. In your Space **Settings -> Variables and secrets**:
   - **Secret** `SESSION_STORAGE_BASE64`: Paste the base64-encoded string of your `storage_state.json`:
     ```powershell
     [Convert]::ToBase64String([IO.File]::ReadAllBytes("storage_state.json")) | Set-Clipboard
     ```
   - **Secret** `WEBSHARE_PROXY_URL`: Set your Webshare proxy credentials.
   - **Secret** `API_BEARER_TOKEN`: Set your secret bearer token for REST endpoint protection.
   - **Variable** `FLOW_URL`: Set your project workspace URL (e.g. `https://flow.google.com/project/<id>`).
   - **Variable** `WEBSHARE_PROXY_INDEX`: Set to `0`.
   - **Variable** `HEADLESS`: Set to `True`.
4. Hugging Face Spaces will automatically launch `app.py` with full Playwright dependencies installed via `packages.txt`.

---

## ⚡ Key Engineering Highlights

1. **Deterministic DOM Stamping (`data-generation-uuid`)**:
   Every submission generates a unique UUID stamped directly onto the newly spawned `<flow-grid-tile-container>`. Generation monitoring tracks this exact container, preventing stale tile collisions or race conditions.
2. **Explicit Ingredient Selection in Asset Drawer**:
   When uploading reference images, Google Flow defaults to whatever media was previously active in the drawer. The engine explicitly queries and clicks `button.asset-item:has-text(...)` before clicking `Add to prompt`, guaranteeing the correct image is attached.
3. **Angular DOM Re-querying**:
   Adding an ingredient re-renders the dock (`<flow-ingredient-bar>`). The engine re-queries `prompt_el` fresh from the DOM after uploading, ensuring human-like keypresses are never sent to detached nodes.
4. **FIFO Concurrency Guardrail**:
   `threading.Semaphore(MAX_CONCURRENT_REQUESTS)` serializes generation requests to avoid container out-of-memory errors on free instances.
