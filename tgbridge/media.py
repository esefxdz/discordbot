#ffmpeg + lottie conversion for stickers, gifs and video##
"""Media conversion for the bridge.

Two rules here: a timed-out subprocess is always killed and reaped, and temp
paths are bound before the try so the finally can never UnboundLocalError.
"""
######################################################################
import asyncio
import logging
import os
import shutil
import tempfile

log = logging.getLogger(__name__)

FFMPEG = shutil.which("ffmpeg")
FFPROBE = shutil.which("ffprobe")

MAX_CONVERTED_BYTES = 8 * 1024 * 1024   # bigger than this, discord rejects it

_GIF_FILTER = ("fps=15,scale=320:-1:flags=lanczos,split[s0][s1];"
               "[s0]palettegen[p];[s1][p]paletteuse")

_lottie_state = None                    # None = not yet checked


def have_ffmpeg() -> bool:
    return FFMPEG is not None


async def _run(args, timeout: float) -> tuple[int, bytes]:
    """Run a subprocess, always killing and reaping it on timeout."""
    proc = await asyncio.create_subprocess_exec(
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        return proc.returncode, (stdout or b"") + (stderr or b"")
    except asyncio.TimeoutError:
        log.warning("%s timed out after %ss — killing", args[0], timeout)
        try:
            proc.kill()
        except ProcessLookupError:
            pass
        try:
            await proc.wait()
        except Exception:
            pass
        raise


def _write_temp(data: bytes, suffix: str) -> str:
    fd, path = tempfile.mkstemp(suffix=suffix)
    with os.fdopen(fd, "wb") as handle:
        handle.write(data)
    return path


def _unlink(path):
    if path and os.path.exists(path):
        try:
            os.remove(path)
        except OSError as exc:
            log.debug("could not remove temp file %s: %s", path, exc)


async def has_audio_track(data: bytes, suffix: str = ".mp4") -> bool:
    """True if the clip carries audio — i.e. it is a real video, not a GIF."""
    if not FFPROBE:
        return False
    src = None
    try:
        src = await asyncio.to_thread(_write_temp, data, suffix)
        code, out = await _run([
            FFPROBE, "-v", "error", "-select_streams", "a",
            "-show_entries", "stream=codec_type", "-of", "csv=p=0", src,
        ], timeout=15.0)
        return b"audio" in out
    except Exception as exc:
        log.debug("ffprobe failed (%s) — assuming no audio", exc)
        return False
    finally:
        await asyncio.to_thread(_unlink, src)


def _decoder_args(in_suffix: str) -> list[str]:
    """Input flags that go before -i.

    ffmpeg's native vp9 decoder silently drops the alpha of VP9-in-WebM (every
    transparent pixel comes out opaque black). Only libvpx-vp9 reads it, and
    only if selected before the input. Telegram video stickers are this format.
    """
    return ["-c:v", "libvpx-vp9"] if in_suffix == ".webm" else []


async def _convert(data: bytes, in_suffix: str, out_suffix: str,
                   args, timeout: float):
    """Shared conversion body. Returns bytes, or None if it did not work out."""
    if not FFMPEG:
        return None
    src = dst = None
    try:
        src = await asyncio.to_thread(_write_temp, data, in_suffix)
        dst = src + out_suffix
        code, out = await _run([FFMPEG, "-y", "-v", "error"]
                               + _decoder_args(in_suffix) + ["-i", src]
                               + list(args) + [dst], timeout=timeout)
        if code != 0 or not os.path.exists(dst):
            log.debug("ffmpeg rc=%s: %s", code, out.decode(errors="replace")[:300])
            return None
        size = os.path.getsize(dst)
        if size > MAX_CONVERTED_BYTES:
            log.info("converted media too large (%d bytes) — keeping original", size)
            return None
        with open(dst, "rb") as handle:
            return handle.read()
    except Exception as exc:
        log.warning("conversion failed: %s", exc)
        return None
    finally:
        await asyncio.to_thread(_unlink, src)
        await asyncio.to_thread(_unlink, dst)


async def to_animated_webp(data: bytes, in_suffix: str = ".webm"):
    """Convert to animated WebP — discord loops it and it keeps 8-bit alpha.

    GIF only has on/off transparency, which is what haloed every sticker edge.
    """
    return await _convert(
        data, in_suffix, ".webp",
        ["-c:v", "libwebp_anim", "-pix_fmt", "yuva420p", "-lossless", "0",
         "-q:v", "70", "-loop", "0", "-an", "-fps_mode", "passthrough"],
        timeout=45.0)


async def to_gif(data: bytes, in_suffix: str = ".mp4"):
    """Convert a silent clip to a looping GIF (Discord autoplays these)."""
    return await _convert(data, in_suffix, ".gif",
                          ["-filter_complex", _GIF_FILTER, "-loop", "0"],
                          timeout=45.0)


# ── lottie (.tgs animated stickers) ────────────────────────────────────────
# .tgs is gzipped lottie json; ffmpeg exits 183 on it. optional — without the
# lib the caller falls back to the sticker's static thumbnail.

def lottie_available() -> bool:
    global _lottie_state
    if _lottie_state is None:
        try:
            import lottie  # noqa: F401
            from lottie.parsers.tgs import parse_tgs  # noqa: F401
            _lottie_state = True
            log.info("lottie renderer available — .tgs stickers will animate")
        except Exception:
            _lottie_state = False
            log.info("lottie not installed — .tgs stickers fall back to thumbnail "
                     "(pip install lottie to animate them)")
    return _lottie_state


def _render_tgs_sync(data: bytes):
    from lottie.parsers.tgs import parse_tgs
    from lottie.exporters.gif import export_gif

    src = dst = None
    try:
        src = _write_temp(data, ".tgs")
        dst = src + ".gif"
        animation = parse_tgs(src)
        export_gif(animation, dst, skip_frames=2, fps=25)
        if not os.path.exists(dst):
            return None
        if os.path.getsize(dst) > MAX_CONVERTED_BYTES:
            log.info("rendered .tgs too large — falling back to thumbnail")
            return None
        with open(dst, "rb") as handle:
            return handle.read()
    finally:
        _unlink(src)
        _unlink(dst)


async def render_tgs(data: bytes):
    """Render an animated .tgs sticker, or None if that isn't possible here."""
    if not lottie_available():
        return None
    try:
        return await asyncio.wait_for(
            asyncio.to_thread(_render_tgs_sync, data), timeout=60.0)
    except Exception as exc:
        log.warning("tgs render failed (%s) — falling back to thumbnail", exc)
        return None
