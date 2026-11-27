from __future__ import annotations

import ctypes
import hashlib
import io
import os
import socket
import tempfile
import time
import urllib.request
from collections.abc import Callable
from ctypes import wintypes
from pathlib import Path
from threading import Event, Lock, Thread, local
from typing import Any
from urllib.parse import urlparse

from PIL import Image
from PySide6.QtCore import QObject, QRect, QRectF, QRunnable, QSize, Qt, QThreadPool, Signal, Slot
from PySide6.QtGui import QColor, QFont, QIcon, QImageReader, QPainter, QPainterPath, QPen, QPixmap

from .config import CACHE_DIR

MAX_ICON_DIMENSION = 4096
MAX_ICON_PIXELS = 4_000_000
MAX_CACHED_ICON_SIZE = 256
_RESPONSE_HOOK = local()

user32 = ctypes.windll.user32
gdi32 = ctypes.windll.gdi32
kernel32 = ctypes.windll.kernel32


class ICONINFO(ctypes.Structure):
    _fields_ = [
        ("fIcon", wintypes.BOOL),
        ("xHotspot", wintypes.DWORD),
        ("yHotspot", wintypes.DWORD),
        ("hbmMask", wintypes.HBITMAP),
        ("hbmColor", wintypes.HBITMAP),
    ]


class BITMAP(ctypes.Structure):
    _fields_ = [
        ("bmType", wintypes.LONG),
        ("bmWidth", wintypes.LONG),
        ("bmHeight", wintypes.LONG),
        ("bmWidthBytes", wintypes.LONG),
        ("bmPlanes", wintypes.WORD),
        ("bmBitsPixel", wintypes.WORD),
        ("bmBits", ctypes.c_void_p),
    ]


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("biSize", wintypes.DWORD),
        ("biWidth", wintypes.LONG),
        ("biHeight", wintypes.LONG),
        ("biPlanes", wintypes.WORD),
        ("biBitCount", wintypes.WORD),
        ("biCompression", wintypes.DWORD),
        ("biSizeImage", wintypes.DWORD),
        ("biXPelsPerMeter", wintypes.LONG),
        ("biYPelsPerMeter", wintypes.LONG),
        ("biClrUsed", wintypes.DWORD),
        ("biClrImportant", wintypes.DWORD),
    ]


class BITMAPINFO(ctypes.Structure):
    _fields_ = [
        ("bmiHeader", BITMAPINFOHEADER),
        ("bmiColors", wintypes.DWORD * 3),
    ]


BI_RGB = 0
DIB_RGB_COLORS = 0
ERROR_INSUFFICIENT_BUFFER = 122

user32.PrivateExtractIconsW.argtypes = [
    wintypes.LPCWSTR,
    ctypes.c_int,
    ctypes.c_int,
    ctypes.c_int,
    ctypes.POINTER(wintypes.HICON),
    ctypes.POINTER(wintypes.UINT),
    wintypes.UINT,
    wintypes.UINT,
]
user32.PrivateExtractIconsW.restype = wintypes.UINT
user32.GetIconInfo.argtypes = [wintypes.HICON, ctypes.POINTER(ICONINFO)]
user32.GetIconInfo.restype = wintypes.BOOL
user32.DestroyIcon.argtypes = [wintypes.HICON]
user32.DestroyIcon.restype = wintypes.BOOL

gdi32.GetObjectW.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p]
gdi32.GetObjectW.restype = ctypes.c_int
gdi32.DeleteObject.argtypes = [wintypes.HGDIOBJ]
gdi32.DeleteObject.restype = wintypes.BOOL
gdi32.GetDIBits.argtypes = [
    wintypes.HDC,
    wintypes.HBITMAP,
    wintypes.UINT,
    wintypes.UINT,
    ctypes.c_void_p,
    ctypes.POINTER(BITMAPINFO),
    wintypes.UINT,
]
gdi32.GetDIBits.restype = ctypes.c_int

user32.GetDC.argtypes = [wintypes.HWND]
user32.GetDC.restype = wintypes.HDC
user32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
user32.ReleaseDC.restype = ctypes.c_int

kernel32.GetPackagesByPackageFamily.argtypes = [
    wintypes.LPCWSTR,
    ctypes.POINTER(wintypes.UINT),
    ctypes.POINTER(wintypes.LPWSTR),
    ctypes.POINTER(wintypes.UINT),
    wintypes.LPWSTR,
]
kernel32.GetPackagesByPackageFamily.restype = wintypes.LONG
kernel32.GetPackagePathByFullName.argtypes = [
    wintypes.LPCWSTR,
    ctypes.POINTER(wintypes.UINT),
    wintypes.LPWSTR,
]
kernel32.GetPackagePathByFullName.restype = wintypes.LONG


def _cache_key(prefix: str, value: str) -> Path:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256(value.lower().encode("utf-8")).hexdigest()[:16]
    return CACHE_DIR / f"{prefix}_{digest}.png"


def _hicon_to_pil(hicon: int) -> Image.Image | None:
    info = ICONINFO()
    if not user32.GetIconInfo(hicon, ctypes.byref(info)):
        return None
    hdc = user32.GetDC(None)
    try:
        if not info.hbmColor:
            return None
        bmp = BITMAP()
        if not gdi32.GetObjectW(info.hbmColor, ctypes.sizeof(BITMAP), ctypes.byref(bmp)):
            return None
        width = bmp.bmWidth
        height = bmp.bmHeight
        if width <= 0 or height <= 0:
            return None

        bmi = BITMAPINFO()
        bmi.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
        bmi.bmiHeader.biWidth = width
        bmi.bmiHeader.biHeight = -height  # top-down DIB
        bmi.bmiHeader.biPlanes = 1
        bmi.bmiHeader.biBitCount = 32
        bmi.bmiHeader.biCompression = BI_RGB

        buf_size = width * height * 4
        color_buf = (ctypes.c_ubyte * buf_size)()
        lines = gdi32.GetDIBits(
            hdc, info.hbmColor, 0, height, color_buf, ctypes.byref(bmi), DIB_RGB_COLORS
        )
        if lines == 0:
            return None

        raw = bytes(color_buf)
        img = Image.frombuffer("RGBA", (width, height), raw, "raw", "BGRA", 0, 1).copy()

        # Check if alpha channel is completely zero (legacy icon relying on mask)
        alpha_extrema = img.getchannel("A").getextrema()
        if alpha_extrema == (0, 0) and info.hbmMask:
            mask_buf = (ctypes.c_ubyte * buf_size)()
            mlines = gdi32.GetDIBits(
                hdc, info.hbmMask, 0, height, mask_buf, ctypes.byref(bmi), DIB_RGB_COLORS
            )
            if mlines > 0:
                mask_img = Image.frombuffer(
                    "RGBA", (width, height), bytes(mask_buf), "raw", "BGRA", 0, 1
                )
                # In AND mask, white (255) = transparent, black (0) = opaque
                mask_r = mask_img.getchannel("R")
                alpha = mask_r.point(lambda p: 0 if p > 128 else 255)
                img.putalpha(alpha)
        return img
    finally:
        user32.ReleaseDC(None, hdc)
        if info.hbmColor:
            gdi32.DeleteObject(info.hbmColor)
        if info.hbmMask:
            gdi32.DeleteObject(info.hbmMask)


def extract_exe_icon_to_png(exe_path: str, force: bool = False) -> Path | None:
    p = Path(exe_path)
    if not p.exists():
        return None

    # Resolve .lnk if needed
    icon_source = str(p)
    icon_index = 0
    if p.suffix.lower() == ".lnk":
        try:
            import win32com.client

            shell = win32com.client.Dispatch("WScript.Shell")
            shortcut = shell.CreateShortcut(str(p))
            loc = shortcut.IconLocation or ""
            if loc and "," in loc:
                src, idx_str = loc.rsplit(",", 1)
                src = os.path.expandvars(src.strip())
                if src and Path(src).exists():
                    icon_source = src
                    icon_index = int(idx_str.strip() or "0")
            if icon_source == str(p) and shortcut.Targetpath and Path(shortcut.Targetpath).exists():
                icon_source = shortcut.Targetpath
        except Exception:
            pass

    cache_file = _cache_key("exe", f"{icon_source}#{icon_index}")
    if cache_file.exists() and not force:
        return cache_file

    best_img: Image.Image | None = None
    for size in (256, 128, 64, 48, 32):
        hicon = wintypes.HICON()
        icon_id = wintypes.UINT()
        count = user32.PrivateExtractIconsW(
            icon_source,
            icon_index,
            size,
            size,
            ctypes.byref(hicon),
            ctypes.byref(icon_id),
            1,
            0,
        )
        if count > 0 and hicon.value:
            try:
                img = _hicon_to_pil(hicon.value)
                if img is not None:
                    best_img = img
                    break
            finally:
                user32.DestroyIcon(hicon)

    if best_img is not None:
        best_img.save(cache_file, format="PNG")
        return cache_file
    return None


def get_msix_install_dir(package_family_name: str) -> Path | None:
    """Resolve current MSIX install folder dynamically via kernel32 AppModel API."""
    count = wintypes.UINT(0)
    buf_len = wintypes.UINT(0)
    res = kernel32.GetPackagesByPackageFamily(
        ctypes.c_wchar_p(package_family_name),
        ctypes.byref(count),
        None,
        ctypes.byref(buf_len),
        None,
    )
    if res != ERROR_INSUFFICIENT_BUFFER or count.value == 0:
        return None

    full_names = (ctypes.c_wchar_p * count.value)()
    buf = ctypes.create_unicode_buffer(buf_len.value)
    res = kernel32.GetPackagesByPackageFamily(
        ctypes.c_wchar_p(package_family_name),
        ctypes.byref(count),
        full_names,
        ctypes.byref(buf_len),
        buf,
    )
    if res != 0 or not full_names[0]:
        return None

    pkg_full_name = full_names[0]
    path_len = wintypes.UINT(0)
    res = kernel32.GetPackagePathByFullName(
        ctypes.c_wchar_p(pkg_full_name), ctypes.byref(path_len), None
    )
    if res != ERROR_INSUFFICIENT_BUFFER or path_len.value == 0:
        return None

    path_buf = ctypes.create_unicode_buffer(path_len.value)
    res = kernel32.GetPackagePathByFullName(
        ctypes.c_wchar_p(pkg_full_name), ctypes.byref(path_len), path_buf
    )
    if res != 0:
        return None
    p = Path(path_buf.value)
    return p if p.exists() else None


def find_msix_icon(aumid: str, theme: str = "dark") -> Path | None:
    family = aumid.split("!", 1)[0]
    install_dir = get_msix_install_dir(family)
    if not install_dir:
        return None

    light_first = [
        "Square44x44Logo.targetsize-256_altform-lightunplated.png",
        "Square44x44Logo.targetsize-96_altform-lightunplated.png",
        "Square44x44Logo.targetsize-64_altform-lightunplated.png",
        "Square44x44Logo.targetsize-48_altform-lightunplated.png",
    ]
    dark_first = [
        "Square44x44Logo.targetsize-256_altform-unplated.png",
        "Square44x44Logo.targetsize-96_altform-unplated.png",
        "Square44x44Logo.targetsize-64_altform-unplated.png",
        "Square44x44Logo.targetsize-48_altform-unplated.png",
    ]
    fallbacks = [
        "Square44x44Logo.targetsize-256.png",
        "Square150x150Logo.scale-200.png",
        "Square150x150Logo.png",
        "Square44x44Logo.scale-400.png",
        "Square44x44Logo.png",
        "StoreLogo.scale-400.png",
        "StoreLogo.png",
        "icon.png",
    ]
    preferred_names = (
        light_first + dark_first + fallbacks
        if theme == "light"
        else dark_first + light_first + fallbacks
    )
    asset_dirs = [
        install_dir / "Assets",
        install_dir / "assets",
        install_dir / "Images",
        install_dir,
    ]
    for adir in asset_dirs:
        if not adir.exists():
            continue
        for name in preferred_names:
            candidate = adir / name
            if candidate.exists():
                return candidate
    return None


def _ico_has_safe_dimensions(data: bytes) -> bool:
    """Validate ICO/CUR directory entries and embedded image headers before decoding."""
    if len(data) < 6:
        return False
    reserved = int.from_bytes(data[0:2], "little")
    ico_type = int.from_bytes(data[2:4], "little")
    count = int.from_bytes(data[4:6], "little")
    if reserved != 0 or ico_type not in (1, 2) or count <= 0 or count > 64:
        return False
    dir_end = 6 + count * 16
    if dir_end > len(data):
        return False
    for idx in range(count):
        entry_off = 6 + idx * 16
        img_off = int.from_bytes(data[entry_off + 12 : entry_off + 16], "little")
        if img_off < dir_end or img_off + 16 > len(data):
            return False
        if data[img_off : img_off + 8] == b"\x89PNG\r\n\x1a\n":
            if img_off + 24 > len(data) or data[img_off + 12 : img_off + 16] != b"IHDR":
                return False
            w = int.from_bytes(data[img_off + 16 : img_off + 20], "big")
            h = int.from_bytes(data[img_off + 20 : img_off + 24], "big")
        else:
            bi_size = int.from_bytes(data[img_off : img_off + 4], "little")
            if bi_size < 16:
                return False
            w = abs(int.from_bytes(data[img_off + 4 : img_off + 8], "little", signed=True))
            raw_h = abs(int.from_bytes(data[img_off + 8 : img_off + 12], "little", signed=True))
            h = raw_h // 2 if raw_h > 1 else raw_h
        if (
            w <= 0
            or h <= 0
            or w > MAX_ICON_DIMENSION
            or h > MAX_ICON_DIMENSION
            or (w * h) > MAX_ICON_PIXELS
        ):
            return False
    return True


def _load_bounded_pixmap(file_path: str | Path) -> QPixmap | None:
    """Decode a local or cached icon image safely within pixel and size bounds."""
    p = Path(file_path)
    try:
        if not p.is_file() or p.stat().st_size > 4 * 1024 * 1024:
            return None
        if p.suffix.lower() in (".ico", ".cur") and not _ico_has_safe_dimensions(p.read_bytes()):
            return None
        reader = QImageReader(str(p))
        reader.setAutoTransform(True)
        sz = reader.size()
        if sz.isValid():
            w, h = sz.width(), sz.height()
            if (
                w <= 0
                or h <= 0
                or w > MAX_ICON_DIMENSION
                or h > MAX_ICON_DIMENSION
                or (w * h) > MAX_ICON_PIXELS
            ):
                return None
            if w > MAX_CACHED_ICON_SIZE or h > MAX_CACHED_ICON_SIZE:
                scaled = sz.scaled(
                    MAX_CACHED_ICON_SIZE,
                    MAX_CACHED_ICON_SIZE,
                    Qt.AspectRatioMode.KeepAspectRatio,
                )
                reader.setScaledSize(QSize(max(1, scaled.width()), max(1, scaled.height())))
        img = reader.read()
        if img.isNull():
            return None
        pm = QPixmap.fromImage(img)
        return None if pm.isNull() else pm
    except Exception:
        return None


def _abort_response_nonblocking(resp: Any) -> None:
    """Shutdown and close an active HTTP response on a detached daemon thread."""
    if resp is None:
        return

    def _close_worker() -> None:
        try:
            fp = getattr(resp, "fp", None)
            raw = getattr(fp, "raw", None)
            sock = getattr(raw, "_sock", None) or getattr(fp, "_sock", None)
            if sock is not None and hasattr(sock, "shutdown"):
                try:
                    sock.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
            resp.close()
        except Exception:
            pass

    Thread(target=_close_worker, daemon=True).start()


def fetch_web_icon_to_png(
    url: str,
    force: bool = False,
    *,
    cancelled: Event | None = None,
) -> Path | None:
    try:
        parsed = urlparse(url)
        if not parsed.scheme:
            parsed = urlparse(f"https://{url.lstrip('/')}")
        hostname, port = parsed.hostname, parsed.port
    except ValueError:
        return None
    if parsed.scheme not in ("http", "https") or not hostname:
        return None

    host = f"[{hostname}]" if ":" in hostname else hostname
    if port is not None:
        host = f"{host}:{port}"
    candidates = [
        f"{parsed.scheme}://{host}/apple-touch-icon.png",
        f"{parsed.scheme}://{host}/favicon.ico",
    ]
    cache_key = host
    if url.lower().startswith(("http://", "https://")) and url.lower().endswith(
        (".png", ".ico", ".jpg", ".jpeg", ".webp")
    ):
        cache_key = parsed._replace(netloc=host, fragment="").geturl()
        candidates.insert(0, cache_key)
    cache_file = _cache_key("web", cache_key)
    if cache_file.exists() and not force:
        return cache_file

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
        )
    }
    total_deadline = time.monotonic() + 2.5
    reg_cb = getattr(_RESPONSE_HOOK, "register", None)
    unreg_cb = getattr(_RESPONSE_HOOK, "unregister", None)
    for cand in candidates:
        if cancelled is not None and cancelled.is_set():
            return None
        remaining = total_deadline - time.monotonic()
        if remaining <= 0.05:
            break

        done = Event()
        state: dict[str, Any] = {"resp": None, "data": None, "error": None}

        def _download_worker(candidate: str = cand, timeout_val: float = min(1.5, remaining)) -> None:
            try:
                req = urllib.request.Request(candidate, headers=headers)
                with urllib.request.urlopen(req, timeout=timeout_val) as resp:
                    state["resp"] = resp
                    if reg_cb is not None:
                        reg_cb(resp)
                    try:
                        buf = bytearray()
                        while chunk := resp.read1(65536):
                            if (cancelled is not None and cancelled.is_set()) or time.monotonic() >= total_deadline:
                                return
                            buf.extend(chunk)
                            if len(buf) > 2 * 1024 * 1024 or time.monotonic() >= total_deadline:
                                raise ValueError("Icon download exceeded its limit")
                        state["data"] = buf
                    finally:
                        if unreg_cb is not None:
                            unreg_cb(resp)
            except Exception as exc:
                state["error"] = exc
            finally:
                state["resp"] = None
                done.set()

        Thread(target=_download_worker, daemon=True).start()
        while not done.wait(0.05):
            if (cancelled is not None and cancelled.is_set()) or time.monotonic() >= total_deadline:
                _abort_response_nonblocking(state.get("resp"))
                return None

        if cancelled is not None and cancelled.is_set():
            return None
        data = state.get("data")
        if not data:
            continue
        raw_bytes = bytes(data)
        if len(raw_bytes) >= 4 and raw_bytes[:4] in (b"\x00\x00\x01\x00", b"\x00\x00\x02\x00"):
            if not _ico_has_safe_dimensions(raw_bytes):
                continue
        try:
            with Image.open(io.BytesIO(raw_bytes)) as img:
                w, h = img.size
                if (
                    w <= 0
                    or h <= 0
                    or w > MAX_ICON_DIMENSION
                    or h > MAX_ICON_DIMENSION
                    or (w * h) > MAX_ICON_PIXELS
                ):
                    continue
                rgba = img.convert("RGBA")
                # Skip tiny 16x16 blank placeholders if larger source exists
                if rgba.width < 24 and cand != candidates[-1]:
                    continue
                if rgba.width > MAX_CACHED_ICON_SIZE or rgba.height > MAX_CACHED_ICON_SIZE:
                    rgba.thumbnail((MAX_CACHED_ICON_SIZE, MAX_CACHED_ICON_SIZE), Image.Resampling.LANCZOS)
                # Other workers may read the same cache entry while it is being refreshed.
                with tempfile.NamedTemporaryFile(dir=cache_file.parent, delete=False) as temporary:
                    temporary_path = Path(temporary.name)
                try:
                    rgba.save(temporary_path, format="PNG")
                    temporary_path.replace(cache_file)
                finally:
                    temporary_path.unlink(missing_ok=True)
                return cache_file
        except Exception:
            continue
    return None


class _WebIconRequest(QRunnable):
    def __init__(
        self,
        url: str,
        force: bool,
        generation: int,
        cancelled: Event,
        complete: Callable[..., None],
        register_response: Callable[[Any], None] | None = None,
        unregister_response: Callable[[Any], None] | None = None,
    ) -> None:
        super().__init__()
        self.url = url
        self.force = force
        self.generation = generation
        self.cancelled = cancelled
        self.complete = complete
        self._register_response = register_response
        self._unregister_response = unregister_response

    def run(self) -> None:
        path = None
        _RESPONSE_HOOK.register = self._register_response
        _RESPONSE_HOOK.unregister = self._unregister_response
        try:
            if not self.cancelled.is_set():
                path = fetch_web_icon_to_png(self.url, self.force, cancelled=self.cancelled)
        except Exception:
            pass
        finally:
            _RESPONSE_HOOK.register = None
            _RESPONSE_HOOK.unregister = None
        try:
            self.complete(self.generation, self.url, path)
        except RuntimeError:
            # The owning widget can close while a request is in progress.
            pass


class WebIconLoader(QObject):
    """Resolve web icon files in workers and deliver paths back to the GUI thread."""

    ready = Signal(str)
    _downloaded = Signal(int, str, object)

    def __init__(self, parent: QObject) -> None:
        super().__init__(parent)
        self._generation = 0
        self._cancelled = Event()
        self._lock = Lock()
        self._active_responses: set[Any] = set()
        self._paths: dict[str, Path | None] = {}
        self._in_flight: set[str] = set()
        self._deferred: dict[str, bool] = {}
        self._downloaded.connect(self._store_path, Qt.ConnectionType.QueuedConnection)

    def _register_response(self, resp: Any) -> None:
        with self._lock:
            if self._cancelled.is_set():
                _abort_response_nonblocking(resp)
                return
            self._active_responses.add(resp)

    def _unregister_response(self, resp: Any) -> None:
        with self._lock:
            self._active_responses.discard(resp)

    def invalidate(self) -> None:
        with self._lock:
            self._cancelled.set()
            active = list(self._active_responses)
            self._active_responses.clear()
            self._cancelled = Event()
        for resp in active:
            _abort_response_nonblocking(resp)
        self._generation += 1
        self._paths.clear()
        self._deferred.clear()

    def resolve(self, url: str, force: bool = False) -> Path | None:
        if url not in self._paths:
            self._paths[url] = None
            if url in self._in_flight:
                self._deferred[url] = force
            else:
                self._start(url, force)
        return self._paths[url]

    def _start(self, url: str, force: bool) -> None:
        self._in_flight.add(url)
        request = _WebIconRequest(
            url,
            force,
            self._generation,
            self._cancelled,
            self._downloaded.emit,
            register_response=self._register_response,
            unregister_response=self._unregister_response,
        )
        QThreadPool.globalInstance().start(request)

    @Slot(int, str, object)
    def _store_path(self, generation: int, url: str, path: Path | None) -> None:
        self._in_flight.remove(url)
        if generation != self._generation:
            if url in self._deferred:
                self._start(url, self._deferred.pop(url))
        elif path is None:
            self._paths.pop(url, None)
        else:
            self._paths[url] = path
            self.ready.emit(url)


def _make_monogram_pixmap(name: str, size: int = 64, accent: str | None = None) -> QPixmap:
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    painter = QPainter(pm)
    painter.setRenderHint(QPainter.Antialiasing, True)

    palette = [
        "#3b82f6",
        "#8b5cf6",
        "#10b981",
        "#f59e0b",
        "#ec4899",
        "#06b6d4",
    ]
    if accent:
        color = QColor(accent)
    else:
        idx = sum(ord(c) for c in name) % len(palette)
        color = QColor(palette[idx])

    rect = QRectF(2, 2, size - 4, size - 4)
    path = QPainterPath()
    path.addRoundedRect(rect, size * 0.24, size * 0.24)
    painter.fillPath(path, color)

    text = "".join(w[0].upper() for w in name.split()[:2]) or name[:2].upper()
    font = QFont("Segoe UI", int(size * 0.36), QFont.Bold)
    painter.setFont(font)
    painter.setPen(QColor("#ffffff"))
    painter.drawText(QRect(0, 0, size, size), Qt.AlignCenter, text)
    painter.end()
    return pm


def _round_pixmap(pm: QPixmap, radius_ratio: float = 0.22) -> QPixmap:
    size = min(max(pm.width(), pm.height(), 64), MAX_CACHED_ICON_SIZE)
    canvas = QPixmap(size, size)
    canvas.fill(Qt.transparent)

    painter = QPainter(canvas)
    painter.setRenderHint(QPainter.Antialiasing, True)
    painter.setRenderHint(QPainter.SmoothPixmapTransform, True)

    path = QPainterPath()
    radius = size * radius_ratio
    path.addRoundedRect(QRectF(0, 0, size, size), radius, radius)
    painter.setClipPath(path)

    scaled = pm.scaled(size, size, Qt.KeepAspectRatio, Qt.SmoothTransformation)
    ox = (size - scaled.width()) // 2
    oy = (size - scaled.height()) // 2
    painter.drawPixmap(ox, oy, scaled)
    painter.end()
    return canvas


def _apply_cli_badge(pm: QPixmap) -> QPixmap:
    size = min(max(pm.width(), pm.height(), 64), MAX_CACHED_ICON_SIZE)
    canvas = QPixmap(size, size)
    canvas.fill(Qt.transparent)

    painter = QPainter(canvas)
    painter.setRenderHint(QPainter.Antialiasing, True)
    painter.setRenderHint(QPainter.SmoothPixmapTransform, True)

    scaled = pm.scaled(size, size, Qt.KeepAspectRatio, Qt.SmoothTransformation)
    ox = (size - scaled.width()) // 2
    oy = (size - scaled.height()) // 2
    painter.drawPixmap(ox, oy, scaled)

    # Subtle terminal badge ">_" in bottom-right corner
    bw = int(size * 0.44)
    bh = int(size * 0.30)
    bx = size - bw - 1
    by = size - bh - 1
    badge_rect = QRectF(bx, by, bw, bh)

    path = QPainterPath()
    path.addRoundedRect(badge_rect, bh * 0.32, bh * 0.32)
    painter.fillPath(path, QColor(18, 20, 28, 240))
    painter.setPen(QPen(QColor(122, 162, 247, 220), max(1.0, size * 0.025)))
    painter.drawPath(path)

    font = QFont("Consolas", max(7, int(bh * 0.56)), QFont.Bold)
    painter.setFont(font)
    painter.setPen(QColor("#e5e9f0"))
    painter.drawText(badge_rect, Qt.AlignCenter, ">_")
    painter.end()
    return canvas


def load_agent_icon(
    agent: dict[str, Any],
    force_refresh: bool = False,
    theme: str = "dark",
    *,
    resolve_web_icon: Callable[[str, bool], Path | None] | None = None,
) -> QIcon:
    """Resolve the crispest real icon for an agent (exe/lnk, msix, web, or explicit path)."""
    pm: QPixmap | None = None

    # 1. Explicit icon file path
    icon_file = agent.get("icon")
    if icon_file and Path(icon_file).exists():
        pm = _load_bounded_pixmap(icon_file)

    # 2. Explicit icon_source (can be exe, aumid, or url)
    icon_src = agent.get("icon_source")
    if pm is None and icon_src:
        if icon_src.lower().startswith(("http://", "https://")):
            web_png = resolve_web_icon(icon_src, force_refresh) if resolve_web_icon else None
            if web_png:
                pm = _load_bounded_pixmap(web_png)
        elif "!" in icon_src and not Path(icon_src).exists():
            msix_png = find_msix_icon(icon_src, theme=theme)
            if msix_png:
                pm = _load_bounded_pixmap(msix_png)
        elif Path(icon_src).exists():
            if Path(icon_src).suffix.lower() in (".png", ".ico", ".svg", ".jpg", ".webp"):
                pm = _load_bounded_pixmap(icon_src)
            else:
                exe_png = extract_exe_icon_to_png(icon_src, force=force_refresh)
                if exe_png:
                    pm = _load_bounded_pixmap(exe_png)

    # 3. Auto-extract by agent type
    atype = agent.get("type", "exe")
    if pm is None or pm.isNull():
        if atype == "exe" and agent.get("path"):
            exe_png = extract_exe_icon_to_png(agent["path"], force=force_refresh)
            if exe_png:
                pm = _load_bounded_pixmap(exe_png)
        elif atype == "msix" and agent.get("aumid"):
            msix_png = find_msix_icon(agent["aumid"], theme=theme)
            if msix_png:
                pm = _load_bounded_pixmap(msix_png)
        elif atype == "web" and agent.get("url") and resolve_web_icon:
            web_png = resolve_web_icon(agent["url"], force_refresh)
            if web_png:
                pm = _load_bounded_pixmap(web_png)
        elif atype == "cli":
            # Fallback to Windows Terminal real icon if no specific brand icon_source
            wt_png = find_msix_icon("Microsoft.WindowsTerminal_8wekyb3d8bbwe!App", theme=theme)
            if wt_png:
                pm = _load_bounded_pixmap(wt_png)

    if pm is None or pm.isNull():
        pm = _make_monogram_pixmap(agent.get("name", "?"), 64, agent.get("accent"))

    pm = _round_pixmap(pm, radius_ratio=0.22)

    if agent.get("cli_badge"):
        pm = _apply_cli_badge(pm)

    return QIcon(pm)
