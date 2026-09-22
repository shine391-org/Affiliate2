# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2025-2026 ViralMint Contributors
"""
AI-enhanced thumbnail generation service.
Extracts candidate frames, picks the sharpest one, generates an AI headline,
and composites text overlay using Pillow (no external dependencies).
"""
import asyncio
import logging
import subprocess
from pathlib import Path
from uuid import uuid4

from backend.config import settings

logger = logging.getLogger(__name__)


async def generate_ai_thumbnail(
    video_path: str | Path,
    script: str = "",
    title: str = "",
    output_path: Path = None,
    user_settings=None,
) -> Path:
    """
    Full AI thumbnail pipeline:
    1. Extract candidate frames at 10%, 40%, 70%
    2. Pick sharpest frame (Laplacian variance via Pillow)
    3. AI generates 2-5 word ALL CAPS headline
    4. Composite text overlay with gradient bar
    Falls back to plain extract_thumbnail on any failure.
    """
    video_path = Path(video_path)

    if output_path is None:
        output_path = settings.THUMBNAILS_DIR / f"thumb_{uuid4().hex[:8]}.jpg"
    output_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        # Step 1: Extract candidate frames
        candidates = await _extract_candidate_frames(video_path, n_frames=3)
        if not candidates:
            raise ValueError("No candidate frames extracted")

        # Step 2: Pick sharpest frame
        best_frame = await asyncio.to_thread(_pick_best_frame, candidates)

        # Step 3: AI generates headline
        headline = await _get_ai_headline(script, title, user_settings)

        # Step 4: Composite text overlay
        await asyncio.to_thread(_composite_thumbnail, best_frame, headline, output_path)

        # Cleanup temp frames
        for f in candidates:
            try:
                f.unlink(missing_ok=True)
            except Exception:
                pass

        logger.info(f"AI thumbnail generated: {output_path}")
        return output_path

    except Exception as e:
        logger.warning(f"AI thumbnail generation failed, falling back to plain frame: {e}")
        from backend.services.ffmpeg_service import extract_thumbnail
        return await extract_thumbnail(video_path, output_path=output_path)


async def _extract_candidate_frames(video_path: Path, n_frames: int = 3) -> list[Path]:
    """Extract frames at 10%, 40%, 70% of the video duration."""
    # Get duration via ffprobe
    duration = await _get_video_duration(video_path)
    if not duration or duration < 1:
        duration = 10.0  # fallback

    percentages = [0.10, 0.40, 0.70]
    timestamps = [duration * p for p in percentages[:n_frames]]
    frames = []

    for i, ts in enumerate(timestamps):
        frame_path = settings.TMP_DIR / f"thumb_candidate_{uuid4().hex[:6]}_{i}.jpg"
        frame_path.parent.mkdir(parents=True, exist_ok=True)

        def _extract(ts=ts, out=frame_path):
            cmd = [
                "ffmpeg", "-y",
                "-ss", str(ts),
                "-i", str(video_path),
                "-vframes", "1",
                "-q:v", "2",
                str(out),
            ]
            proc = subprocess.run(cmd, capture_output=True, timeout=30)
            return proc.returncode == 0

        success = await asyncio.to_thread(_extract)
        if success and frame_path.exists():
            frames.append(frame_path)

    return frames


async def _get_video_duration(video_path: Path) -> float:
    """Get video duration in seconds via ffprobe."""
    def _probe():
        cmd = [
            "ffprobe", "-v", "quiet",
            "-show_entries", "format=duration",
            "-of", "csv=p=0",
            str(video_path),
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
        if proc.returncode == 0 and proc.stdout.strip():
            return float(proc.stdout.strip())
        return 0.0

    try:
        return await asyncio.to_thread(_probe)
    except Exception:
        return 0.0


def _pick_best_frame(frame_paths: list[Path]) -> Path:
    """Score frames by sharpness (Laplacian variance) and return the sharpest."""
    from PIL import Image, ImageFilter

    best_path = frame_paths[0]
    best_score = -1.0

    for path in frame_paths:
        try:
            img = Image.open(path).convert("L")  # grayscale
            # Approximate Laplacian variance using edge detection
            edges = img.filter(ImageFilter.FIND_EDGES)
            pixels = list(edges.getdata())
            if not pixels:
                continue
            mean = sum(pixels) / len(pixels)
            variance = sum((p - mean) ** 2 for p in pixels) / len(pixels)
            if variance > best_score:
                best_score = variance
                best_path = path
        except Exception as e:
            logger.debug(f"Could not score frame {path}: {e}")

    return best_path


async def _get_ai_headline(script: str, title: str, user_settings) -> str:
    """Use AI to generate a 2-5 word ALL CAPS headline for the thumbnail."""
    if not script and not title:
        return ""

    try:
        from backend.core.ai_provider import get_ai_client
        ai = get_ai_client(user_settings)

        context = title or ""
        if script:
            context += f"\n\nScript excerpt:\n{script[:1500]}"

        prompt = f"""Generate a short, punchy thumbnail headline for this video.

Video context:
{context}

Rules:
- 2-5 words ONLY
- ALL CAPS
- Must grab attention and create curiosity
- No quotes, no punctuation except ! or ?
- Think YouTube thumbnail style — bold, shocking, intriguing

Return ONLY the headline text, nothing else."""

        headline = await ai.chat(
            messages=[{"role": "user", "content": prompt}],
            max_tokens=50,
        )
        # Clean up: remove quotes, extra whitespace
        headline = headline.strip().strip('"\'').upper()
        # Limit to ~5 words
        words = headline.split()
        if len(words) > 6:
            headline = " ".join(words[:5])
        return headline
    except Exception as e:
        logger.warning(f"AI headline generation failed: {e}")
        # Fallback: use first few words of title
        if title:
            words = title.upper().split()[:4]
            return " ".join(words)
        return ""


def _composite_thumbnail(frame_path: Path, headline: str, output_path: Path):
    """Composite text overlay onto the frame image using Pillow."""
    from PIL import Image, ImageDraw, ImageFont

    img = Image.open(frame_path).convert("RGB")
    w, h = img.size

    # Resize to standard thumbnail dimensions if needed (maintain aspect)
    target_w = 1280
    target_h = 720
    if w != target_w or h != target_h:
        img = img.resize((target_w, target_h), Image.LANCZOS)
        w, h = target_w, target_h

    if not headline:
        img.save(str(output_path), "JPEG", quality=92)
        return

    draw = ImageDraw.Draw(img, "RGBA")

    # Draw semi-transparent dark gradient at bottom 30%
    gradient_height = int(h * 0.30)
    for y in range(h - gradient_height, h):
        # Opacity increases from top to bottom of gradient
        progress = (y - (h - gradient_height)) / gradient_height
        alpha = int(180 * progress)
        draw.rectangle([(0, y), (w, y + 1)], fill=(0, 0, 0, alpha))

    # Load font — try system bold fonts with Pillow fallback
    font = _load_bold_font(w)

    # Auto-size text to fit within 90% of frame width
    font = _auto_size_font(draw, headline, font, max_width=int(w * 0.90))

    # Calculate text position (centered, in bottom 20%)
    bbox = draw.textbbox((0, 0), headline, font=font)
    text_w = bbox[2] - bbox[0]
    text_h = bbox[3] - bbox[1]
    text_x = (w - text_w) // 2
    text_y = h - int(gradient_height * 0.65) - text_h // 2

    # Draw text shadow
    shadow_offset = max(3, int(w * 0.003))
    draw.text((text_x + shadow_offset, text_y + shadow_offset), headline, font=font, fill=(0, 0, 0, 200))

    # Draw headline in white
    draw.text((text_x, text_y), headline, font=font, fill=(255, 255, 255, 255))

    # Save
    img = img.convert("RGB")
    img.save(str(output_path), "JPEG", quality=92)


def _load_bold_font(frame_width: int):
    """Try system bold fonts, fall back to Pillow default."""
    from PIL import ImageFont
    import platform

    size = max(48, int(frame_width * 0.05))

    # Try common system bold fonts
    font_paths = []
    system = platform.system()
    if system == "Darwin":
        font_paths = [
            "/System/Library/Fonts/Helvetica.ttc",
            "/Library/Fonts/Arial Bold.ttf",
            "/System/Library/Fonts/SFCompact.ttf",
        ]
    elif system == "Windows":
        font_paths = [
            "C:/Windows/Fonts/arialbd.ttf",
            "C:/Windows/Fonts/impact.ttf",
        ]
    else:  # Linux
        font_paths = [
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
            "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
        ]

    for font_path in font_paths:
        try:
            return ImageFont.truetype(font_path, size)
        except (OSError, IOError):
            continue

    # Pillow default
    try:
        return ImageFont.load_default(size=size)
    except TypeError:
        return ImageFont.load_default()


def _auto_size_font(draw, text: str, font, max_width: int):
    """Reduce font size until text fits within max_width."""
    from PIL import ImageFont

    bbox = draw.textbbox((0, 0), text, font=font)
    text_w = bbox[2] - bbox[0]

    if text_w <= max_width:
        return font

    # Get the font path and reduce size
    try:
        font_path = font.path
        current_size = font.size
        while text_w > max_width and current_size > 20:
            current_size -= 4
            font = ImageFont.truetype(font_path, current_size)
            bbox = draw.textbbox((0, 0), text, font=font)
            text_w = bbox[2] - bbox[0]
    except (AttributeError, OSError):
        pass  # Can't resize default font

    return font
