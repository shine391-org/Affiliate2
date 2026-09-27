# Affiliate2 V1.0.0 — Windows Release Guide

## 1. Supported system

Affiliate2 V1 is packaged for **Windows 11 x64**.

Required:

- Python **3.11.x** (the V1 Python lock is generated for 3.11)
- Node.js **22.x or newer** and npm **10.x or newer**
- FFmpeg and ffprobe on `PATH`; use a build with `libass` for burned captions
- At least 20 GB free disk space is recommended for the Python environment, model caches and media outputs

Optional:

- Microsoft Edge for authenticated Douyin/CDP workflows
- NVIDIA GPU/CUDA compatible with PyTorch 2.8 for faster WhisperX alignment and Demucs separation
- ImageMagick for inherited/advanced media paths that call it
- An AI provider, LM Studio, or another OpenAI-compatible endpoint

The package does not bundle Python, Node, FFmpeg, AI models, browser profiles, cookies, API keys or media files.

## 2. Install on a fresh Windows machine

1. Extract `Affiliate2-v1.0.0.zip` to a normal writable directory, for example `C:\Affiliate2`.
2. Install Python 3.11 x64 and enable the Python launcher (`py`).
3. Install Node.js 22 LTS or newer.
4. Install FFmpeg and ensure both commands work in a new PowerShell window:

   ```powershell
   ffmpeg -version
   ffprobe -version
   ```

5. In the extracted Affiliate2 directory run:

   ```powershell
   Set-ExecutionPolicy -Scope Process Bypass
   .\setup.ps1
   ```

`setup.ps1` creates `.venv`, installs the exact Windows/Python 3.11 dependency lock, runs `npm ci`, creates `.env` from `.env.example`, prepares writable runtime directories and runs the pre-flight check.

The first install downloads several gigabytes because PyTorch, WhisperX, Demucs and their dependencies are part of the V1 localization path.

## 3. Configuration

`setup.ps1` copies `.env.example` to `.env` only when `.env` does not already exist. Never commit or distribute `.env`.

Basic startup requires no provider key. Affiliate2 generates `SECRET_KEY` and `ENCRYPTION_KEY` locally on first application import and saves them to `.env`.

To keep runtime state outside the extracted application directory, set:

```dotenv
VIRALMINT_DATA_DIR=C:\Affiliate2Data
```

This directory must be writable. It owns the SQLite database, media, generated files, thumbnails, temporary files, localization jobs, tool input/output and downloaded model/plugin state.

## 4. Start and stop

Start the production-like local application:

```powershell
.\start.ps1
```

The script rebuilds the frontend, runs the pre-flight check, starts one stable FastAPI process and opens the UI. Default address:

- UI: `http://127.0.0.1:16888`
- API health: `http://127.0.0.1:16888/api/health`

`HOST` and `PORT` are configurable in `.env`. Loopback is the safe default. Setting `HOST=0.0.0.0` exposes the app and its local library on the LAN.

Stop processes started by Affiliate2:

```powershell
.\stop.ps1
```

## 5. AI provider

### LM Studio or another OpenAI-compatible endpoint

Start the local server and load a model, then set values matching the server:

```dotenv
AI_PROVIDER=openai_compatible
AI_BASE_URL=http://127.0.0.1:1234/v1
AI_API_KEY=lm-studio
AI_MODEL=your-loaded-model-id
```

The pre-flight check probes `<AI_BASE_URL>/models` only when both provider and base URL are configured. An unavailable optional AI endpoint produces a warning rather than blocking the UI/API.

The implemented application also supports provider credentials configured through Settings and direct Anthropic, OpenAI, OpenRouter and Gemini-compatible configuration. Use only providers visible in the current Settings UI; do not add hypothetical provider names to `.env`.

## 6. FFmpeg

Affiliate2 invokes `ffmpeg` and `ffprobe` by command name, so both must be on `PATH` for the account that launches the app. The setup/pre-flight scripts execute both commands before declaring readiness.

Caption burn-in uses FFmpeg's `ass` filter. Verify support with:

```powershell
ffmpeg -filters | Select-String " ass "
```

## 7. Douyin / Edge CDP

Normal UI startup does not require a browser session. Authenticated Douyin discovery/download requires an Edge profile where the user has signed in.

Example dedicated Edge session:

```powershell
& "$env:ProgramFiles(x86)\Microsoft\Edge\Application\msedge.exe" `
  --remote-debugging-port=9222 `
  --user-data-dir="$env:LOCALAPPDATA\Affiliate2\edge-douyin-profile" `
  --no-first-run https://www.douyin.com
```

After signing in, configure:

```dotenv
AFFILIATE2_DOUYIN_CDP_URL=http://127.0.0.1:9222
```

The pre-flight check probes `/json/version` only when this variable is set. Browser profiles and cookies are deliberately excluded from the release archive.

Playwright is installed as a Python dependency because current Douyin and upload integrations import it. The normal CDP connection uses the installed Edge browser; it does not require downloading a bundled Playwright Chromium browser.

## 8. WhisperX, Demucs, Hugging Face and GPU

- Standard faster-whisper transcription currently runs on CPU with int8 compute.
- WhisperX alignment and Demucs separation automatically use CUDA when the installed PyTorch runtime detects it; otherwise they fall back to CPU.
- CPU fallback is functional but long media can be substantially slower. No V1 runtime guarantee is made for processing speed.
- Whisper/faster-whisper, WhisperX alignment and Demucs models download on first use and are not included in the ZIP.
- Set `HF_HOME` to relocate the Hugging Face cache.
- `HUGGINGFACE_TOKEN` is required only when a localization profile explicitly enables WhisperX/pyannote speaker diarization that needs gated Hugging Face assets.

Default free preview TTS uses Edge TTS and requires internet access. OpenAI-compatible, Azure, ElevenLabs and GPT-SoVITS TTS are available only when selected and configured in the application.

## 9. Writable directories

By default, runtime state is stored below the extracted release directory:

- `viralmint.db`
- `.env`
- `.runtime/`
- `storage/videos/`
- `storage/audio/`
- `storage/generated/`
- `storage/thumbnails/`
- `storage/tmp/`
- `storage/localization_jobs/`
- `storage/tools/in/` and `storage/tools/out/`
- `motion/` for on-demand Motion Graphics runtime/cache

Use `VIRALMINT_DATA_DIR` when the application directory is read-only or when media should live on another drive.

## 10. Pre-flight check

Run at any time:

```powershell
.\.venv\Scripts\python.exe .\scripts\preflight.py --root .
```

Mandatory failures return exit code 1. Optional AI/CDP integration failures are warnings.

## 11. Confirmed V1 limitations

- Windows 11 x64 is the tested release target; other operating systems are not covered by this release artifact.
- The initial Python install and first model use require substantial downloads and disk space.
- Standard faster-whisper transcription is CPU-bound in the current implementation.
- Authenticated Douyin behavior depends on the user's live Edge login/session and Douyin's current anti-bot behavior.
- Paid/cloud AI, stock and publishing workflows cannot operate without their own credentials.
- Human publish approval remains mandatory; setup does not publish or upload anything automatically.
