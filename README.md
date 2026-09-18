---
title: ImageGeneratorFlow
emoji: ⚡
colorFrom: green
colorTo: yellow
sdk: gradio
sdk_version: 4.44.1
app_file: app.py
pinned: false
---

# Google Flow Headless Automation & Gradio Wrapper (V2)

Production-grade automated image generation pipeline for Google Flow using Playwright, pre-authenticated session persistence, and Gradio/FastAPI for local and Hugging Face Spaces deployments.

---

## 📁 Repository Structure

```
├── requirements.txt         # Python dependencies (playwright, gradio, pillow, fastapi, etc.)
├── packages.txt             # Debian system packages for Hugging Face Spaces Playwright
├── .env                     # Runtime configurations (FLOW_URL, STORAGE_STATE_PATH, etc.)
├── .env.example             # Environment variables template
├── login_session.py         # One-time stealth login & session state exporter
├── engine.py                # Headless automation engine & CLI test runner (V2)
├── app.py                   # Production Gradio wrapper & REST API for Hugging Face Spaces
├── test_client.py           # Client script demonstrating gradio_client and REST API calls
├── storage_state.json       # Authenticated Google Flow session state
├── outputs/                 # Output directory for generated images
└── README.md                # Setup, API documentation, and deployment guide
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

To log into your Google Flow workspace and export your authenticated session state:

```bash
python login_session.py
```

1. A genuine Chrome window launches with stealth anti-detection flags.
2. Sign in with your Google account and complete 2FA if prompted.
3. Once inside your project workspace, return to the terminal and press **[ENTER]**.
4. The script saves your authenticated state to `storage_state.json` and automatically syncs `FLOW_URL` in `.env`.

---

### 3. Launch Gradio Web UI & REST API

```bash
python app.py
```

- **Interactive UI**: Open `http://localhost:7860`
- **Health Check Endpoint**: `GET http://localhost:7860/api/health`
- **Direct REST Endpoint**: `POST http://localhost:7860/api/generate_image`
- **Gradio API Client**: Accessible via `api_name="generate_image"`

---

### 4. Test the API

Run the automated test client to test health, `gradio_client`, and direct REST API execution:

```bash
python test_client.py --url http://127.0.0.1:7860
```

---

## ⚙️ Control Parameters Matrix

| Parameter | Type | Options / Default | Description |
| :--- | :--- | :--- | :--- |
| `prompt` | `str` | Required | Main text prompt describing image |
| `negative_prompt` | `str` | Optional | Elements or artifacts to exclude (`--no <text>`) |
| `num_outputs` | `int` | `1, 2, 3, 4` (default: `1`) | Number of parallel variations |
| `aspect_ratio` | `str` | `["1:1", "16:9", "9:16", "4:3", "3:4"]` | Output dimensions |
| `model_variant` | `str` | `["Nano Banana 2", "Nano Banana Pro", "Nano Banana 2 Lite"]` | Google Flow engine variant |
| `reference_images` | `list` | Optional (Capped at 3) | Multi-image reference ingredients (Google Flow max 3 cap) |
| `image_strength` | `float` | `0.1` to `1.0` (default: `0.75`) | Influence factor of reference image |
| `style_preset` | `str` | `["None", "Photorealistic", "Cinematic", "Anime", "Digital Art", "3D Render"]` | Curated artistic keyword injection |
| `seed` | `int` | `-1` for random or fixed integer | Reproducible generation seed |

---

## 🛡️ Webshare Proxy Integration (Residential / Static ISP)

Hugging Face Spaces and cloud datacenters (AWS, GCP) use datacenter IP ranges that Google may throttle or challenge. Routing browser automation through **Webshare static/dedicated proxies** guarantees high reliability without Google security locks:

- **No 15-Minute Expiration**: Webshare provides static/dedicated IPs that do not expire or rotate mid-session. The IP remains fixed, eliminating Google session invalidations and security checkpoints.
- **Bandwidth Billing (Zero Credit Burn)**: Unlike per-request API gateways, Webshare does not charge per HTTP sub-request. The 500+ script and telemetry assets requested by Google Flow cost only ~10 MB of bandwidth.
- **Multi-IP Support**: If you have 10 static IPs, you can configure them all in `WEBSHARE_PROXY_URL` (comma-separated). The engine automatically distributes concurrent workers across the proxy pool.

### Configuration (`.env` or Space Secrets):
```dotenv
# Option 1: Single Webshare proxy URL
WEBSHARE_PROXY_URL=http://username:password@p.webshare.io:80

# Option 2: Comma-separated list of your 10 static IPs
WEBSHARE_PROXY_URL=http://user:pass@ip1:port,http://user:pass@ip2:port

# Option 3: Webshare export format (ip:port:user:pass)
WEBSHARE_PROXY_URL=185.x.x.x:port:username:password
```

---

## 🌐 Deploying to Hugging Face Spaces

1. Create a new Space on [Hugging Face](https://huggingface.co/new-space) (Select **Gradio** as SDK).
2. Push all repository files (`app.py`, `engine.py`, `requirements.txt`, `packages.txt`).
3. In your Space **Settings -> Variables and secrets**:
   - **Secret** `SESSION_STORAGE_BASE64`: Paste the base64-encoded string of your `storage_state.json`:
     ```powershell
     [Convert]::ToBase64String([IO.File]::ReadAllBytes("storage_state.json")) | Set-Clipboard
     ```
   - **Secret** `WEBSHARE_PROXY_URL`: Set your Webshare proxy URL or comma-separated proxy list.
   - **Secret** `API_BEARER_TOKEN`: Set your API authentication token.
   - **Variable** `FLOW_URL`: Set your project workspace URL (e.g. `https://flow.google.com/project/<id>`).
4. Hugging Face Spaces will automatically launch `app.py` with full Playwright dependencies installed via `packages.txt`.

---

## ⚡ Key Features

- **Deterministic DOM Stamping (`data-generation-uuid`)**: Each prompt generation is bound to a unique UUID upon submission, isolating concurrent prompts and preventing cross-contamination or out-of-order mix-ups.
- **Strict FIFO Concurrency Guardrail**: `asyncio.Lock()` enforces sequential processing of requests to prevent container OOM on 2vCPU / 16GB free instances.
- **Media UUID Tracking**: Automatically extracts and displays Google Flow's internal `data-media-id` for every generated image variation.
- **Batch Variation Support**: Downloads all variations produced for each prompt (e.g. `output.png`, `output_2.png`).
- **Instant Error & Safety Detection**: Rapidly flags content policy rejections and toast errors (`GenerationFailedError`).
