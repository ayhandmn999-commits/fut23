"""Ortak yardımcılar: Türkçe metin işlemleri, ffprobe/ffmpeg sarmalayıcıları, hata türleri."""
from __future__ import annotations

import json
import re
import subprocess
import unicodedata
from collections import deque
from pathlib import Path
from typing import Callable, Optional


class PipelineError(RuntimeError):
    """İşlem hattında oluşan kullanıcıya gösterilebilir hata."""


class ServiceError(PipelineError):
    """Harici servis hatası (ağ, kota, yetki)."""


class MissingService(PipelineError):
    """Gerekli bir servis/araç kurulu değil ya da anahtarı tanımlı değil."""

    def __init__(self, service: str, hint: str):
        super().__init__(f"{service} — {hint}")
        self.service = service
        self.hint = hint


# --------------------------------------------------------------------------- #
# Türkçe metin
# --------------------------------------------------------------------------- #
_UP = str.maketrans({"i": "İ", "ı": "I"})
_LOW = str.maketrans({"İ": "i", "I": "ı"})
_ASCII = str.maketrans("çğıöşüÇĞİÖŞÜâîûÂÎÛ", "cgiosuCGIOSUaiuAIU")


def tr_upper(s: str) -> str:
    return s.translate(_UP).upper()


def tr_lower(s: str) -> str:
    return s.translate(_LOW).lower()


def tr_capital(s: str) -> str:
    return tr_upper(s[:1]) + s[1:] if s else s


def slugify(s: str, maxlen: int = 60) -> str:
    s = unicodedata.normalize("NFKD", s.translate(_ASCII))
    s = s.encode("ascii", "ignore").decode()
    s = re.sub(r"[^A-Za-z0-9]+", "-", s).strip("-").lower()
    return (s[:maxlen].strip("-")) or "klip"


def fmt_ts(sec: float) -> str:
    sec = max(0.0, float(sec))
    return f"{int(sec // 60):02d}:{sec % 60:04.1f}"


# --------------------------------------------------------------------------- #
# FFmpeg / FFprobe
# --------------------------------------------------------------------------- #
def probe(path: str | Path) -> dict:
    p = subprocess.run(
        ["ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)],
        capture_output=True, text=True,
    )
    if p.returncode != 0:
        raise PipelineError(f"Video okunamadı: {p.stderr.strip()[-200:]}")
    j = json.loads(p.stdout or "{}")
    streams = j.get("streams", [])
    v = next(
        (s for s in streams if s.get("codec_type") == "video" and not s.get("disposition", {}).get("attached_pic")),
        None,
    )
    a = next((s for s in streams if s.get("codec_type") == "audio"), None)
    if not v:
        raise PipelineError("Dosyada görüntü akışı bulunamadı.")
    fmt = j.get("format", {})
    try:
        duration = float(fmt.get("duration") or v.get("duration") or 0)
    except ValueError:
        duration = 0.0
    fps = 25.0
    try:
        num, den = (v.get("avg_frame_rate") or "25/1").split("/")
        fps = float(num) / float(den) if float(den) else 25.0
    except Exception:
        pass
    return {
        "duration": duration,
        "width": int(v.get("width") or 0),
        "height": int(v.get("height") or 0),
        "fps": fps or 25.0,
        "vcodec": v.get("codec_name"),
        "has_audio": a is not None,
        "container": fmt.get("format_name", ""),
    }


_PROGRESS_KEY = re.compile(
    r"^(frame|fps|stream_\d+_\d+_q|bitrate|total_size|out_time_us|out_time_ms|out_time|dup_frames|"
    r"drop_frames|speed|progress)=",
)


def run_ffmpeg(
    cmd: list[str],
    total: Optional[float] = None,
    cb: Optional[Callable[[float], None]] = None,
    cwd: Optional[str | Path] = None,
) -> None:
    """FFmpeg'i `-progress pipe:1` ile çalıştırır; 0..1 arası ilerleme bildirir."""
    proc = subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, cwd=cwd, bufsize=1
    )
    tail: deque[str] = deque(maxlen=25)
    assert proc.stdout is not None
    for line in proc.stdout:
        line = line.strip()
        if line.startswith("out_time_ms="):
            try:
                sec = int(line.split("=", 1)[1]) / 1_000_000
            except ValueError:
                continue
            if cb and total:
                cb(max(0.0, min(1.0, sec / total)))
        elif line and not _PROGRESS_KEY.match(line):
            tail.append(line)
    code = proc.wait()
    if code != 0:
        raise PipelineError("FFmpeg hatası: " + " | ".join(list(tail)[-6:]))
    if cb:
        cb(1.0)
