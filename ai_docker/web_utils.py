from __future__ import annotations

import configparser
from pathlib import Path
from urllib.parse import urlparse

_GENERIC_SUBDOMAINS = frozenset(
    {"www", "chat", "app", "ai", "web", "console", "portal", "m", "beta", "my"}
)


def normalize_web_url(raw_url: str) -> str | None:
    """Validate and normalize an http(s) URL while preserving full path and query parameters."""
    text = (raw_url or "").strip()
    if not text or any(ch.isspace() or ord(ch) < 32 or ord(ch) == 127 for ch in text):
        return None
    if "://" not in text:
        text = "https://" + text
    try:
        parsed = urlparse(text)
        _ = parsed.port
    except ValueError:
        return None
    if parsed.scheme.lower() not in ("http", "https") or not parsed.hostname:
        return None
    if "\\" in parsed.netloc or "@" in parsed.netloc or "%" in parsed.hostname:
        return None
    return text


def suggest_web_agent_name(url: str) -> str:
    """Derive a clean default display name from a web URL's hostname."""
    normalized = normalize_web_url(url)
    if not normalized:
        return "Web Agent"
    host = (urlparse(normalized).hostname or "").strip().lower()
    if not host:
        return "Web Agent"
    labels = [part for part in host.split(".") if part]
    if len(labels) >= 2 and labels[0] in _GENERIC_SUBDOMAINS:
        labels = labels[1:]
    primary = labels[0] if labels else host
    return primary.replace("-", " ").title()


def read_internet_shortcut_url(file_path: str) -> str | None:
    """Extract the target http(s) URL from a Windows .url Internet Shortcut file."""
    p = Path(file_path)
    if p.suffix.lower() != ".url" or not p.exists():
        return None
    parser = configparser.ConfigParser(interpolation=None)
    try:
        parser.read(p, encoding="utf-8-sig")
    except Exception:
        try:
            parser.read(p, encoding="mbcs")
        except Exception:
            return None
    raw = parser.get("InternetShortcut", "URL", fallback="").strip()
    return normalize_web_url(raw)


def detect_web_browser_matching(preferred_browser: str | None = None) -> tuple[str, list[str]]:
    """Infer match_process and notification_handlers from the active Chromium browser."""
    from .launcher import _find_browser

    browser = preferred_browser or _find_browser()
    if browser and Path(browser).name.lower() == "msedge.exe":
        return "msedge.exe", ["MSEdge"]
    return "chrome.exe", ["Chrome"]
