#!/usr/bin/env python3
"""Videos and more picture formats in the Hub (Files and Photos).

What the browser can play it plays itself, straight from the NAS with
Range requests. This module adds what a browser cannot do:

- a still of a video for the grid and the Photos timeline (a frame found by
  ffmpeg in the first few megabytes);
- a copy of a video in a format every browser plays (H.264 + AAC in MP4,
  at most 720 pixels high), made on request in the background and kept in the
  Hub cache, for AVI, WMV, HEVC from an iPhone and so on;
- a JPEG of pictures a browser cannot show (HEIC from phones, TIFF).

ffmpeg is optional: without it the Hub still plays MP4, WebM, MOV and MKV that
the browser knows, and says so when something needs ffmpeg. It reads files
only through what the Hub hands it (never a path of its own choosing), the
demuxer is chosen by the file's extension (never guessed from the content, so a
playlist disguised as a video cannot make ffmpeg read other files or the
network), it runs at low priority with limits, and its input is a temporary
copy in the Hub cache.
"""

import hashlib
import os
import re
import shutil
import subprocess
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
from typing import Any, Callable, Dict, Iterable, Optional

try:
    import resource
except ImportError:  # not on Linux
    resource = None  # type: ignore[assignment]

VIDEO_TYPES = ('.mp4', '.m4v', '.mov', '.mkv', '.webm', '.3gp', '.3g2', '.avi', '.wmv', '.mpg', '.mpeg',
               '.ogv', '.flv', '.ts', '.mts', '.m2ts')
# What a browser plays by itself (the viewer tries these first).
NATIVE_TYPES = ('.mp4', '.m4v', '.mov', '.webm', '.mkv', '.ogv', '.3gp')
HEIF_TYPES = ('.heic', '.heif')
PREVIEW_TYPES = HEIF_TYPES + ('.tif', '.tiff')

# The demuxer ffmpeg is told to use, by extension. Never `-f` unset: ffmpeg would guess it
# from the content, and some formats (playlists) can name other files and addresses.
DEMUXERS = {
    '.mp4': 'mov', '.m4v': 'mov', '.mov': 'mov', '.3gp': 'mov', '.3g2': 'mov',
    '.mkv': 'matroska', '.webm': 'matroska', '.avi': 'avi', '.wmv': 'asf',
    '.mpg': 'mpeg', '.mpeg': 'mpeg', '.ogv': 'ogg', '.flv': 'flv',
    '.ts': 'mpegts', '.mts': 'mpegts', '.m2ts': 'mpegts',
}

HEAD_BYTES = 24 * 1024 ** 2          # the start of a video holds its first pictures
TAIL_BYTES = 24 * 1024 ** 2          # an MP4 from a phone often keeps its index at the end
STILL_SIZE = 360                     # width of a still, like the other thumbnails
STILL_SECONDS = 25                   # a still must not take longer than this
MAX_CONVERT_BYTES = 8 * 1024 ** 3    # larger videos are not converted
CACHE_LIMIT_BYTES = 4 * 1024 ** 3    # converted copies kept; the oldest go first
MAX_PREVIEW_SIDE = 2400

_run_lock = threading.Lock()


def ffmpeg() -> Optional[str]:
    return shutil.which('ffmpeg')


def ffprobe() -> Optional[str]:
    return shutil.which('ffprobe')


def heif_support() -> bool:
    """Whether HEIC pictures can be opened: pillow-heif, or heif-convert (libheif-examples)."""
    try:
        import pillow_heif  # noqa: F401
        return True
    except ImportError:
        return shutil.which('heif-convert') is not None


def features() -> Dict[str, bool]:
    return {'ffmpeg': ffmpeg() is not None, 'heif': heif_support()}


def is_video(name: str) -> bool:
    return name.lower().endswith(VIDEO_TYPES)


def demuxer(name: str) -> Optional[str]:
    return DEMUXERS.get(os.path.splitext(name)[1].lower())


def _limits() -> None:
    """For ffmpeg: low priority, and no more memory or time than a video needs."""
    try:
        os.nice(12)
        if resource is not None:
            resource.setrlimit(resource.RLIMIT_AS, (4 * 1024 ** 3, 4 * 1024 ** 3))
            resource.setrlimit(resource.RLIMIT_CPU, (6 * 3600, 6 * 3600))
            resource.setrlimit(resource.RLIMIT_FSIZE, (MAX_CONVERT_BYTES, MAX_CONVERT_BYTES))
    except (OSError, ValueError):
        pass


def _base_command(binary: str, name: str, source: str) -> list:
    return [binary, '-hide_banner', '-nostdin', '-v', 'error', '-protocol_whitelist', 'file',
            '-f', DEMUXERS[os.path.splitext(name)[1].lower()], '-i', source]


# ── A still of a video ──────────────────────────────────────────────────────

def video_still(name: str, size: int, read: Callable[[int, int], Optional[bytes]],
                workdir: Optional[str] = None) -> Optional[bytes]:
    """A JPEG of a picture in the video, or None. `read(start, length)` gives bytes of the
    file (as the signed-in person). Only the start and the end are read: the middle is a
    hole in a sparse temporary file, which is all ffmpeg needs to find the first pictures
    and the index."""
    binary = ffmpeg()
    if not binary or not demuxer(name) or size <= 0:
        return None
    temp = tempfile.NamedTemporaryFile(dir=workdir, prefix='still-', suffix='.part', delete=False)
    try:
        with temp:
            temp.truncate(size)
            head = read(0, min(size, HEAD_BYTES))
            if not head:
                return None
            temp.seek(0)
            temp.write(head)
            if size > HEAD_BYTES:
                tail_start = max(HEAD_BYTES, size - TAIL_BYTES)
                tail = read(tail_start, size - tail_start)
                if tail:
                    temp.seek(tail_start)
                    temp.write(tail)
        command = _base_command(binary, name, temp.name) + [
            '-an', '-sn', '-frames:v', '1', '-vf', f'thumbnail=40,scale={STILL_SIZE}:-2',
            '-c:v', 'mjpeg', '-q:v', '4', '-f', 'image2pipe', '-']
        with _run_lock:     # one at a time: stills of a whole folder must not crowd out the NAS
            result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                    timeout=STILL_SECONDS, preexec_fn=_limits, check=False)
        return result.stdout if result.returncode == 0 and result.stdout[:2] == b'\xff\xd8' else None
    except (OSError, subprocess.SubprocessError, ValueError):
        return None
    finally:
        try:
            os.unlink(temp.name)
        except OSError:
            pass


# ── Pictures a browser cannot show ──────────────────────────────────────────

def open_image(data: bytes, name: str):
    """A Pillow image from the bytes, or None; HEIC through pillow-heif or heif-convert."""
    from PIL import Image
    try:
        return Image.open(BytesIO(data))
    except Exception:  # noqa: BLE001
        pass
    if not name.lower().endswith(HEIF_TYPES):
        return None
    try:
        import pillow_heif
        pillow_heif.register_heif_opener()
        return Image.open(BytesIO(data))
    except Exception:  # noqa: BLE001
        pass
    converter = shutil.which('heif-convert')
    if not converter:
        return None
    with tempfile.TemporaryDirectory(prefix='heif-') as folder:
        source = os.path.join(folder, 'in.heic')
        target = os.path.join(folder, 'out.jpg')
        with open(source, 'wb') as f:
            f.write(data)
        try:
            subprocess.run([converter, source, target], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                           timeout=60, preexec_fn=_limits, check=True)
            with open(target, 'rb') as f:
                return Image.open(BytesIO(f.read()))
        except Exception:  # noqa: BLE001 - no preview then
            return None


def jpeg(data: bytes, name: str, side: int, quality: int = 80) -> Optional[bytes]:
    """A JPEG of the picture, at most `side` pixels on its long side (the EXIF turn applied)."""
    try:
        from PIL import ImageOps
        source = open_image(data, name)
        if source is None:
            return None
        with source:
            img = ImageOps.exif_transpose(source)
            img.thumbnail((side, side))
            if img.mode not in ('RGB', 'L'):
                img = img.convert('RGB')
            out = BytesIO()
            img.save(out, 'JPEG', quality=quality)
            return out.getvalue()
    except Exception:  # noqa: BLE001 - a broken picture just has no preview
        return None


# ── A copy every browser plays ──────────────────────────────────────────────

class Converter:
    """Makes browser-friendly copies one at a time in the background.

    A job is named by a key (the person, the file, its size and date), so each
    video is converted once and a changed file is converted again. State is kept in memory
    (a restart forgets a running job; the finished copies stay on disk)."""

    def __init__(self, cache_dir: Callable[[], str]):
        self._cache_dir = cache_dir
        self._jobs: Dict[str, Dict[str, Any]] = {}
        self._lock = threading.Lock()
        self._pool: Optional[ThreadPoolExecutor] = None

    @staticmethod
    def key(user: Optional[str], path: str, size: int, stamp: str) -> str:
        return hashlib.sha256(f'{user}|{path}|{size}|{stamp}'.encode()).hexdigest()

    def output(self, key: str) -> str:
        return os.path.join(self._cache_dir(), 'video', key[:2], key + '.mp4')

    def status(self, key: str) -> Dict[str, Any]:
        out = self.output(key)
        if os.path.isfile(out):
            os.utime(out, None)           # used: stays longest in the cache
            return {'state': 'ready', 'percent': 100}
        with self._lock:
            job = self._jobs.get(key)
            return {'state': job['state'], 'percent': job['percent'], 'error': job.get('error', '')} if job \
                else {'state': 'idle', 'percent': 0}

    def start(self, key: str, name: str, size: int, open_source: Callable[[], Iterable[bytes]]) -> Dict[str, Any]:
        """Queues the conversion unless there is one or a finished copy."""
        current = self.status(key)
        if current['state'] in ('ready', 'working', 'queued'):
            return current
        if not ffmpeg() or not demuxer(name):
            return {'state': 'failed', 'percent': 0, 'error': 'This NAS cannot convert videos (ffmpeg is not installed).'}
        if size > MAX_CONVERT_BYTES:
            return {'state': 'failed', 'percent': 0, 'error': 'This video is too big to convert.'}
        with self._lock:
            self._jobs[key] = {'state': 'queued', 'percent': 0, 'error': ''}
            if self._pool is None:
                self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix='convert')
            self._pool.submit(self._run, key, name, size, open_source)
        return {'state': 'queued', 'percent': 0}

    def _set(self, key: str, **fields: Any) -> None:
        with self._lock:
            self._jobs.setdefault(key, {})
            self._jobs[key].update(fields)

    def _fail(self, key: str, error: str) -> None:
        self._set(key, state='failed', error=error)

    def _run(self, key: str, name: str, size: int, open_source: Callable[[], Iterable[bytes]]) -> None:
        out = self.output(key)
        work = os.path.join(self._cache_dir(), 'video', 'work')
        source = os.path.join(work, key + '.src')
        part = out + '.part'
        try:
            os.makedirs(work, exist_ok=True)
            os.makedirs(os.path.dirname(out), exist_ok=True)
            free = shutil.disk_usage(work).free
            if free < size + size // 4 + 512 * 1024 ** 2:
                self._fail(key, 'There is not enough free space on the NAS to convert this video.')
                return
            self._set(key, state='working', percent=0)
            done = 0
            with open(source, 'wb') as f:                      # a copy: ffmpeg needs to seek in it
                for piece in open_source():
                    f.write(piece)
                    done += len(piece)
                    self._set(key, percent=min(15, int(15 * done / max(size, 1))))
            seconds = self._duration(name, source)
            error = self._convert(key, name, source, part, seconds)
            if error:
                self._fail(key, error)
                return
            os.replace(part, out)
            self._set(key, state='ready', percent=100)
            self._prune()
        except OSError as e:
            self._fail(key, f'The video could not be converted: {e.strerror or e}')
        finally:
            for leftover in (source, part):
                try:
                    os.unlink(leftover)
                except OSError:
                    pass

    @staticmethod
    def _duration(name: str, source: str) -> float:
        binary = ffprobe()
        if not binary or not demuxer(name):
            return 0.0
        try:
            res = subprocess.run([binary, '-v', 'error', '-protocol_whitelist', 'file', '-f', DEMUXERS[os.path.splitext(name)[1].lower()],
                                  '-show_entries', 'format=duration', '-of', 'default=nw=1:nk=1', source],
                                 stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=60, preexec_fn=_limits, check=False)
            return max(0.0, float(res.stdout.strip() or 0))
        except (OSError, subprocess.SubprocessError, ValueError):
            return 0.0

    def _convert(self, key: str, name: str, source: str, target: str, seconds: float) -> str:
        binary = ffmpeg()
        if not binary:
            return 'ffmpeg is not installed.'
        command = _base_command(binary, name, source) + [
            '-map', '0:v:0', '-map', '0:a:0?', '-sn', '-dn',
            '-c:v', 'libx264', '-preset', 'veryfast', '-crf', '25', '-pix_fmt', 'yuv420p',
            '-vf', "scale=-2:'min(720,ih)'",
            '-c:a', 'aac', '-b:a', '128k', '-ac', '2',
            '-movflags', '+faststart', '-f', 'mp4', '-progress', 'pipe:1', '-y', target]
        with tempfile.TemporaryFile() as errors:
            proc = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=errors, preexec_fn=_limits, text=True)
            try:
                for line in proc.stdout or []:
                    m = re.match(r'out_time_(?:us|ms)=(\d+)', line)
                    if m and seconds > 0:
                        # out_time_ms is in microseconds too (an old ffmpeg naming slip)
                        done = int(m.group(1)) / 1_000_000
                        self._set(key, percent=15 + min(84, int(84 * done / seconds)))
                code = proc.wait()
            finally:
                if proc.poll() is None:
                    proc.kill()
            if code != 0:
                errors.seek(0)
                tail = errors.read().decode('utf-8', 'replace').strip().splitlines()[-1:] or ['unknown error']
                return f'ffmpeg could not convert this video: {tail[0][:200]}'
        return ''

    def _prune(self) -> None:
        """Keeps the converted copies under the cache limit, the least recently used going first."""
        root = os.path.join(self._cache_dir(), 'video')
        files = []
        for folder, _dirs, names in os.walk(root):
            if os.path.basename(folder) == 'work':
                continue
            for n in names:
                if n.endswith('.mp4'):
                    p = os.path.join(folder, n)
                    try:
                        st = os.stat(p)
                    except OSError:
                        continue
                    files.append((st.st_mtime, st.st_size, p))
        total = sum(f[1] for f in files)
        for _mtime, size, p in sorted(files):
            if total <= CACHE_LIMIT_BYTES:
                break
            try:
                os.unlink(p)
                total -= size
            except OSError:
                pass

    def forget_work(self) -> None:
        """At start: leftovers of a conversion that was cut short."""
        work = os.path.join(self._cache_dir(), 'video', 'work')
        shutil.rmtree(work, ignore_errors=True)
