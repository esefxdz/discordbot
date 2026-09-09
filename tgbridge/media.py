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

class _Oversize:
    __slots__ = ("size",)

    def __init__(self, size: int):
        self.size = size

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
                   args, timeout: float, max_bytes: int = MAX_CONVERTED_BYTES):
    """Shared conversion body.

    Returns bytes, `_OVERSIZE` if the encode worked but blew the budget, or
    None if it genuinely failed.
    """
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
        if size > max_bytes:
            log.debug("encode over budget: %.2f MB > %.2f MB",
                      size / 1048576, max_bytes / 1048576)
            return _Oversize(size)
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
    out = await _convert(
        data, in_suffix, ".webp",
        ["-c:v", "libwebp_anim", "-pix_fmt", "yuva420p", "-lossless", "0",
         "-q:v", "70", "-loop", "0", "-an", "-fps_mode", "passthrough"],
        timeout=45.0)
    if isinstance(out, _Oversize):
        log.info("animated webp too large — keeping original")
        return None
    return out


def _gif_filter(fps=None, width=None, per_frame=True, dither="sierra2_4a"):
    pre = []
    if fps:
        pre.append(f"fps={fps}")
    if width:
        # min() so we never upscale something already smaller than the cap
        pre.append(f"scale='min({width},iw)':-1:flags=lanczos")
    prefix = ",".join(pre) + "," if pre else ""

    if per_frame:
        gen = "palettegen=stats_mode=single:reserve_transparent=0"
        use = f"paletteuse=new=1:dither={dither}"
    else:
        gen = "palettegen=stats_mode=diff"
        use = f"paletteuse=dither={dither}"
    return f"{prefix}split[s0][s1];[s0]{gen}[p];[s1][p]{use}"


# Quality ladder, best first. Each rung gives up the least it can:
# per-frame palette -> global palette -> cheaper dither -> framerate -> size.
# Dither drops before framerate deliberately: dithering breaks up LZW runs, so
# it costs more bytes than resolution does. Measured on a 6s 640x480 clip,
# sierra2_4a -> bayer:5 went 13.8 MB -> 8.4 MB with no visible loss in motion.
# The last rung is the old hardcoded behaviour, kept as a floor, not a default.
_GIF_TIERS = (
    dict(fps=None, width=None, per_frame=True,  dither="sierra2_4a"),
    dict(fps=None, width=None, per_frame=False, dither="sierra2_4a"),
    dict(fps=None, width=None, per_frame=False, dither="bayer:bayer_scale=5"),
    dict(fps=24,   width=None, per_frame=False, dither="bayer:bayer_scale=5"),
    dict(fps=20,   width=720,  per_frame=False, dither="bayer:bayer_scale=5"),
    dict(fps=20,   width=560,  per_frame=False, dither="bayer:bayer_scale=5"),
    dict(fps=15,   width=420,  per_frame=False, dither="bayer:bayer_scale=5"),
    dict(fps=15,   width=320,  per_frame=False, dither="bayer:bayer_scale=5"),
)

_GIF_BYTES_PER_PXFRAME = 0.03

GIF_ATTEMPT_TIMEOUT = 120.0     # native-res per-frame palettes are not fast
GIF_TOTAL_BUDGET = 240.0        # ceiling across the whole ladder


async def probe_video(data: bytes, suffix: str = ".mp4"):
    """-> (width, height, duration, fps), or None if ffprobe cannot say."""
    if not FFPROBE:
        return None
    src = None
    try:
        src = await asyncio.to_thread(_write_temp, data, suffix)
        code, out = await _run([
            FFPROBE, "-v", "error", "-select_streams", "v:0", "-show_entries",
            "stream=width,height,avg_frame_rate:format=duration",
            "-of", "default=noprint_wrappers=1:nokey=0", src,
        ], timeout=15.0)
        if code != 0:
            return None
        fields = dict(
            line.split("=", 1)
            for line in out.decode(errors="replace").splitlines() if "=" in line)
        width = int(fields.get("width", 0))
        height = int(fields.get("height", 0))
        duration = float(fields.get("duration", 0) or 0)
        num, _, den = fields.get("avg_frame_rate", "0/0").partition("/")
        fps = float(num) / float(den) if float(den or 0) else 0.0
        if not (width and height and duration > 0 and fps > 0):
            return None
        return width, height, duration, fps
    except Exception as exc:
        log.debug("video probe failed (%s) — trying every tier", exc)
        return None
    finally:
        await asyncio.to_thread(_unlink, src)


def _tier_cost(tier) -> float:
    return 1.0 if tier["dither"].startswith("sierra") else 0.61


def _pixel_frames(tier, probed):
    if not probed:
        return None
    width, height, duration, fps = probed
    out_w = min(tier["width"], width) if tier["width"] else width
    out_h = height * (out_w / width)
    out_fps = min(tier["fps"], fps) if tier["fps"] else fps
    return out_w * out_h * duration * out_fps * _tier_cost(tier)


async def to_gif(data: bytes, in_suffix: str = ".mp4",
                 max_bytes: int = MAX_CONVERTED_BYTES):
    if not FFMPEG:
        return None

    probed = await probe_video(data, in_suffix)
    loop = asyncio.get_running_loop()
    started = loop.time()
    rate = _GIF_BYTES_PER_PXFRAME       # refined once this clip tells us better

    for index, tier in enumerate(_GIF_TIERS):
        weighted = _pixel_frames(tier, probed)
        if weighted and weighted * rate > max_bytes:
            continue

        remaining = GIF_TOTAL_BUDGET - (loop.time() - started)
        if remaining <= 5.0:
            log.info("gif ladder out of time after tier %d", index)
            break

        try:
            out = await _convert(
                data, in_suffix, ".gif",
                ["-filter_complex", _gif_filter(**tier), "-loop", "0"],
                timeout=min(GIF_ATTEMPT_TIMEOUT, remaining),
                max_bytes=max_bytes)
        except asyncio.TimeoutError:
            continue                    # too slow at this quality, drop a rung

        if isinstance(out, bytes):
            log.info("gif tier %d: fps=%s width=%s per_frame=%s -> %.2f MB",
                     index, tier["fps"] or "native", tier["width"] or "native",
                     tier["per_frame"], len(out) / 1048576)
            return out
        if out is None:
            # a real ffmpeg failure — every other tier reads the same input
            log.debug("gif encode failed outright at tier %d — giving up", index)
            return None

        if weighted:
            rate = max(rate, out.size / weighted * 0.9)

    log.info("no gif tier fit under %.1f MB — keeping original",
             max_bytes / 1048576)
    return None


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
