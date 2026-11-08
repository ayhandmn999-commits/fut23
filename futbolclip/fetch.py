"""Bulunan videoyu indirir ve analiz için tek biçime (H.264/MP4, ≤720p) getirir."""
from __future__ import annotations

import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Callable, Optional

import requests

from .config import Settings, has_module
from .util import MissingService, PipelineError, ServiceError, probe, run_ffmpeg

Progress = Callable[[float, str], None]


def _ytdlp_cmd() -> list[str]:
    exe = shutil.which("yt-dlp")
    if exe:
        return [exe]
    if has_module("yt_dlp"):
        return [sys.executable, "-m", "yt_dlp"]
    raise MissingService("yt-dlp", "YouTube videolarını indirmek için `pip install yt-dlp` gerekli.")


def normalize_source(src: Path, dst: Path, s: Settings, progress: Optional[Progress] = None) -> dict:
    """Videoyu H.264/AAC MP4'e (≤ max_source_height) ve en çok `max_analyze_minutes` süreye getirir."""
    info = probe(src)
    cap = min(info["duration"] or s.max_analyze_s, s.max_analyze_s)
    ok = (
        info["vcodec"] == "h264" and src.suffix.lower() == ".mp4" and info["height"] <= s.max_source_height
        and info["duration"] <= s.max_analyze_s + 1 and info["has_audio"]
    )
    if ok:
        if src != dst:
            shutil.move(str(src), str(dst))
        return probe(dst)
    cmd = [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(src), "-t", f"{cap:.2f}",
        "-vf", f"scale=-2:'min({s.max_source_height},ih)'", "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
        "-pix_fmt", "yuv420p", "-g", "60", "-c:a", "aac", "-b:a", "128k", "-ac", "2", "-movflags", "+faststart",
        "-progress", "pipe:1", "-nostats", str(dst),
    ]
    if not info["has_audio"]:
        raise PipelineError("Videoda ses akışı yok; konuşma analizi yapılamaz.")
    run_ffmpeg(cmd, cap, (lambda f: progress(f, "Video standartlaştırılıyor…")) if progress else None)
    if src != dst:
        src.unlink(missing_ok=True)
    return probe(dst)


def _stream_download(url: str, dest: Path, s: Settings, progress: Optional[Progress]) -> None:
    try:
        with requests.get(url, stream=True, timeout=(8, 60), headers={"User-Agent": s.user_agent}) as r:
            if r.status_code != 200:
                raise ServiceError(f"Dosya indirilemedi (HTTP {r.status_code}).")
            total = int(r.headers.get("Content-Length") or 0)
            if total and total > s.max_download_mb * 1024 * 1024:
                raise PipelineError(f"Dosya çok büyük ({total // (1024 * 1024)} MB > {s.max_download_mb} MB).")
            got = 0
            with open(dest, "wb") as fh:
                for chunk in r.iter_content(1024 * 512):
                    fh.write(chunk)
                    got += len(chunk)
                    if got > s.max_download_mb * 1024 * 1024:
                        raise PipelineError("İndirme boyut sınırını aştı.")
                    if progress and total:
                        progress(got / total, "Video indiriliyor…")
    except requests.RequestException as e:
        raise ServiceError(f"İndirme ağ hatası: {e.__class__.__name__}") from e


def _download_youtube(f: dict, workdir: Path, s: Settings, progress: Optional[Progress]) -> Path:
    cmd = _ytdlp_cmd()
    h = s.max_source_height
    cmd += [
        "--no-playlist", "--no-warnings", "--newline", "--max-filesize", f"{s.max_download_mb}M",
        "-f", f"bv*[height<={h}][ext=mp4]+ba[ext=m4a]/b[height<={h}][ext=mp4]/bv*[height<={h}]+ba/b[height<={h}]",
        "--merge-output-format", "mp4", "-o", str(workdir / "raw.%(ext)s"),
    ]
    if (f.get("duration") or 0) > s.max_analyze_s:
        cmd += ["--download-sections", f"*0-{int(s.max_analyze_s)}"]
    cmd.append(f["url"])
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    tail: list[str] = []
    assert p.stdout is not None
    for line in p.stdout:
        m = re.search(r"\[download\]\s+([\d.]+)%", line)
        if m and progress:
            progress(float(m.group(1)) / 100, "YouTube'dan indiriliyor…")
        elif line.strip():
            tail.append(line.strip())
    if p.wait() != 0:
        raise ServiceError("yt-dlp indiremedi: " + " | ".join(tail[-3:])[:300])
    files = sorted(workdir.glob("raw.*"))
    if not files:
        raise ServiceError("yt-dlp çıktı dosyası üretmedi.")
    return files[0]


def fetch_youtube_captions(f: dict, workdir: Path) -> Optional[str]:
    """yt-dlp ile (varsa) Türkçe altyazı metnini alır. Başarısızsa None."""
    try:
        cmd = _ytdlp_cmd()
    except MissingService:
        return None
    cmd += [
        "--skip-download", "--write-subs", "--write-auto-subs", "--sub-langs", "tr.*,tr", "--sub-format", "vtt",
        "--no-warnings", "-o", str(workdir / "cap.%(ext)s"), f["url"],
    ]
    try:
        subprocess.run(cmd, capture_output=True, timeout=120)
    except Exception:
        return None
    vtts = sorted(workdir.glob("cap*.vtt"))
    return vtts[0].read_text(encoding="utf-8", errors="ignore") if vtts else None


def _archive_file_url(identifier: str, s: Settings) -> tuple[str, float]:
    try:
        r = requests.get(f"https://archive.org/metadata/{identifier}", timeout=(8, 30),
                         headers={"User-Agent": s.user_agent})
        files = r.json().get("files", [])
    except Exception as e:
        raise ServiceError("Internet Archive dosya listesi alınamadı.") from e
    cands = [x for x in files if str(x.get("name", "")).lower().endswith((".mp4", ".ogv", ".webm", ".mkv", ".avi"))]
    if not cands:
        raise ServiceError("Bu kayıtta indirilebilir video dosyası yok.")
    limit = s.max_download_mb * 1024 * 1024

    def key(x):
        size = int(x.get("size", 0) or 0)
        return (size <= limit, str(x.get("format", "")).lower() in {"h.264", "mpeg4", "512kb mpeg4"}, -size)

    best = sorted(cands, key=key, reverse=True)[0]
    return f"https://archive.org/download/{identifier}/{best['name']}", float(best.get("length") or 0)


def download(f: dict, workdir: Path, s: Settings, progress: Optional[Progress] = None) -> Path:
    """Kaynağa göre indirir ve `workdir/source.mp4` olarak döndürür."""
    workdir.mkdir(parents=True, exist_ok=True)
    src = f["source"]
    if src == "youtube":
        raw = _download_youtube(f, workdir, s, progress)
    elif src == "commons":
        url = (f.get("extra") or {}).get("file_url")
        if not url:
            raise ServiceError("Commons dosya adresi bulunamadı.")
        raw = workdir / ("raw" + Path(url.split("?")[0]).suffix)
        _stream_download(url, raw, s, progress)
    elif src == "archive":
        url, _ = _archive_file_url(f["source_id"], s)
        raw = workdir / ("raw" + Path(url).suffix)
        _stream_download(url, raw, s, progress)
    else:
        raise PipelineError(f"Bilinmeyen kaynak: {src}")
    dst = workdir / "source.mp4"
    normalize_source(raw, dst, s, progress)
    return dst
