"""
engine/media.py — Centralized media pipeline.

All mod-produced attachments pass through here before reaching the LLM.
Mods declare what they have (type, bytes or path, optional mime hint).
The engine handles validation, normalization, provider capability checks,
and provider-specific serialization.

"See once, discard": strip_attachments_from_history() removes serialized
image/audio blocks from historical messages so bytes never accumulate
across turns.
"""

from __future__ import annotations

import base64
import subprocess
import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from config import PROVIDERS
from core.log import log


# ── MediaAttachment ───────────────────────────────────────────────────────────

@dataclass
class MediaAttachment:
    """
    Describes a media item produced by a mod.

    Mods supply either:
      - data: raw bytes (the engine does not re-read from disk)
      - path: a file path (the engine reads it during validate)

    mime_type is optional — the engine detects it from magic bytes first,
    falling back to whatever the mod declared.
    """
    type:      Literal["image", "audio", "video", "file"]
    data:      bytes | None = None
    path:      str   | None = None
    mime_type: str   | None = None
    metadata:  dict         = field(default_factory=dict)


# ── Provider capability map ───────────────────────────────────────────────────

# Built from config.PROVIDERS — each provider declares its media_caps list.
# Video is handled by extracting a frame first (see _normalize), so video/*
# does not appear here even if a provider supports it via frames.
_PROVIDER_CAPS: dict[str, frozenset[str]] = {
    name: frozenset(cfg.get("media_caps", []))
    for name, cfg in PROVIDERS.items()
}


# ── Public API ────────────────────────────────────────────────────────────────

def process(attachment: MediaAttachment, provider: str) -> dict | None:
    """
    Run a single attachment through the five-step pipeline.

    Steps:
      1. Validate   — file exists / bytes non-empty / header plausible
      2. Normalize  — canonical MIME detection; video → first frame (ffmpeg)
      3. Cap check  — does the active provider support this MIME?
      4. Serialize  — build provider-specific LLM content block

    Returns a content block dict, or None if the attachment should be skipped
    (invalid, unsupported, or conversion failed). Errors are non-fatal — the
    text portion of the message still reaches the LLM.
    """
    # 1. Validate
    data = _validate(attachment)
    if data is None:
        return None

    # 2. Normalize
    mime, data = _normalize(attachment, data)

    # 3. Capability check
    caps = _PROVIDER_CAPS.get(provider, frozenset())
    if mime not in caps:
        log.error(
            f"Provider '{provider}' does not support {mime} — attachment skipped.",
            source="media",
        )
        return None

    # 4. Serialize
    return _serialize(data, mime, provider)


def build_message(
    text: str,
    attachments: list[MediaAttachment],
    provider: str,
) -> dict:
    """
    Build a single LLM message dict containing text + serialized attachments.

    If no attachments survive the pipeline, returns a plain text message.
    """
    if not attachments:
        return {"role": "user", "content": text}

    blocks: list[dict] = []
    for att in attachments:
        block = process(att, provider)
        if block is not None:
            blocks.append(block)

    if not blocks:
        return {"role": "user", "content": text}

    content: list[dict] = [{"type": "text", "text": text}] + blocks
    return {"role": "user", "content": content}


def strip_attachments_from_history(messages: list[dict]) -> list[dict]:
    """
    "See once, discard" — remove image/audio content blocks from all
    messages in the history list.

    When a message's content is a list of blocks, only the text blocks
    survive; image/audio/video blocks are dropped. This prevents attachment
    bytes from accumulating across turns when state["messages"] is re-used.

    Plain string content is left untouched.
    """
    result: list[dict] = []
    for msg in messages:
        content = msg.get("content")
        if isinstance(content, list):
            text_blocks = [
                b for b in content
                if isinstance(b, dict) and b.get("type") == "text"
            ]
            if not text_blocks:
                result.append({**msg, "content": ""})
            elif len(text_blocks) == len(content):
                result.append(msg)
            else:
                merged = " ".join(b.get("text", "") for b in text_blocks)
                result.append({**msg, "content": merged})
        else:
            result.append(msg)
    return result


_IMAGE_TYPES = frozenset(("image", "image_url"))


def strip_images_if_over_budget(
    messages: list[dict],
    system: str,
    limit: int,
) -> list[dict]:
    """
    Drop all image blocks from messages when the estimated token count
    (system + messages) would exceed *limit*.

    Uses the same len//4 approximation used throughout the codebase.
    Called by the worker node after strip_all_but_last_image so that a
    single large screenshot cannot push the request over the API ceiling.
    """
    def _estimate(msgs: list[dict]) -> int:
        chars = 0
        for msg in msgs:
            content = msg.get("content", "")
            if isinstance(content, str):
                chars += len(content)
            elif isinstance(content, list):
                for block in content:
                    if not isinstance(block, dict):
                        continue
                    btype = block.get("type", "")
                    if btype == "text":
                        chars += len(block.get("text", ""))
                    elif btype == "image":
                        chars += len(block.get("source", {}).get("data", ""))
                    elif btype == "image_url":
                        url = block.get("image_url", {}).get("url", "")
                        if "base64," in url:
                            chars += len(url.split("base64,", 1)[1])
        return chars // 4

    total = len(system) // 4 + _estimate(messages)
    if total <= limit:
        return messages

    log.warning(
        f"Estimated {total:,} tokens exceeds budget of {limit:,} — dropping image(s) from context.",
        source="media",
    )
    return strip_attachments_from_history(messages)


def strip_all_but_last_image(messages: list[dict]) -> list[dict]:
    """
    Strip all image blocks from history except the most recent one.

    Keeps exactly 1 screenshot in context (the latest) so the worker can
    see current UI state without accumulating images across turns.
    Planner/replanner should use strip_attachments_from_history instead.
    """
    last_idx: int = -1
    last_block: dict | None = None

    for i, msg in enumerate(messages):
        content = msg.get("content")
        if isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") in _IMAGE_TYPES:
                    last_idx = i
                    last_block = block

    stripped = strip_attachments_from_history(messages)

    if last_idx >= 0 and last_block is not None:
        msg = stripped[last_idx]
        content = msg["content"]
        if isinstance(content, str):
            stripped[last_idx] = {
                **msg,
                "content": [{"type": "text", "text": content}, last_block],
            }
        elif isinstance(content, list):
            stripped[last_idx] = {**msg, "content": content + [last_block]}

    return stripped


# ── Validation ────────────────────────────────────────────────────────────────

def _validate(att: MediaAttachment) -> bytes | None:
    """Return raw bytes if valid, None if the attachment should be skipped."""
    data = att.data

    if data is None and att.path:
        p = Path(att.path)
        if not p.exists():
            log.error(f"Attachment path not found: {att.path}", source="media")
            return None
        try:
            data = p.read_bytes()
        except Exception as e:
            log.error(f"Failed to read attachment at {att.path}: {e}", source="media")
            return None

    if not data:
        log.error("Attachment has no data and no path — skipped.", source="media")
        return None

    if len(data) < 8:
        log.error("Attachment too small to be a valid media file — skipped.", source="media")
        return None

    return data


# ── Normalization ─────────────────────────────────────────────────────────────

_PNG_MAGIC  = b"\x89PNG\r\n\x1a\n"
_JPEG_MAGIC = b"\xff\xd8\xff"
_WEBP_RIFF  = b"RIFF"


def _normalize(att: MediaAttachment, data: bytes) -> tuple[str, bytes]:
    """
    Return (canonical_mime_type, bytes_to_send).

    Detects MIME from magic bytes first, falls back to att.mime_type, then
    'application/octet-stream'. For video, attempts to extract the first
    frame via ffmpeg and returns it as image/png.
    """
    detected = _detect_mime(data)
    mime = detected or att.mime_type or "application/octet-stream"

    if mime == "video/mp4":
        src_path = att.path if att.path and Path(att.path).exists() else None
        frame = _extract_video_frame(src_path, data)
        if frame:
            return "image/png", frame
        log.error(
            "ffmpeg not available or frame extraction failed — video attachment skipped.",
            source="media",
        )
        # Return the detected MIME unchanged; the capability check will reject it.

    return mime, data


def _detect_mime(data: bytes) -> str | None:
    """Detect MIME type from magic bytes. Returns None if unrecognised."""
    if data[:8] == _PNG_MAGIC:
        return "image/png"
    if data[:3] == _JPEG_MAGIC:
        return "image/jpeg"
    if data[:4] == _WEBP_RIFF and len(data) >= 12 and data[8:12] == b"WEBP":
        return "image/webp"
    # MP3: ID3 header or sync frame
    if data[:3] == b"ID3":
        return "audio/mp3"
    if len(data) >= 2 and data[0] == 0xFF and (data[1] & 0xE0) == 0xE0:
        return "audio/mp3"
    # WAV
    if data[:4] == b"RIFF" and len(data) >= 12 and data[8:12] == b"WAVE":
        return "audio/wav"
    # MP4 / MOV — ftyp box at offset 4
    if len(data) >= 8 and data[4:8] == b"ftyp":
        return "video/mp4"
    return None


def _extract_video_frame(path: str | None, data: bytes) -> bytes | None:
    """Extract the first frame from a video using ffmpeg. Returns PNG bytes or None."""
    if not shutil.which("ffmpeg"):
        return None

    with tempfile.TemporaryDirectory() as tmp:
        if path and Path(path).exists():
            in_path = path
            cleanup_input = False
        else:
            in_path = str(Path(tmp) / "input.mp4")
            Path(in_path).write_bytes(data)
            cleanup_input = True  # noqa: F841 — cleaned by tempdir context manager

        out_path = str(Path(tmp) / "frame.png")
        try:
            subprocess.run(
                [
                    "ffmpeg", "-i", in_path,
                    "-frames:v", "1",
                    "-q:v", "2",
                    "-y", out_path,
                ],
                capture_output=True,
                timeout=15,
                check=True,
            )
            return Path(out_path).read_bytes()
        except subprocess.TimeoutExpired:
            log.error("ffmpeg timed out during frame extraction.", source="media")
        except subprocess.CalledProcessError as e:
            log.error(f"ffmpeg error: {e.stderr.decode(errors='replace')[:200]}", source="media")
        except Exception as e:
            log.error(f"Unexpected error during frame extraction: {e}", source="media")

    return None


# ── Serialization ─────────────────────────────────────────────────────────────

def _serialize(data: bytes, mime: str, provider: str) -> dict:
    """
    Build a provider-specific LLM content block from validated bytes.
    Format is determined by the provider's 'media_format' key in config.PROVIDERS.
    Unknown providers default to the 'anthropic' block format.
    """
    b64  = base64.b64encode(data).decode("ascii")
    fmt  = PROVIDERS.get(provider, {}).get("media_format", "anthropic")

    if fmt == "openai":
        return {
            "type": "image_url",
            "image_url": {"url": f"data:{mime};base64,{b64}", "detail": "low"},
        }

    # Anthropic format (default for any unrecognised format value)
    return {
        "type": "image",
        "source": {"type": "base64", "media_type": mime, "data": b64},
    }
