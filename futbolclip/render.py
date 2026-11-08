"""Render: ASS altyazı, 9:16 düzenler (bulanık arka plan / akıllı kırpma / merkez), platform ön ayarları, MP4."""
from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Callable, Optional

from .config import settings
from .framing import crop_trajectory, crop_window, x_expression
from .util import PipelineError, probe, run_ffmpeg, tr_upper

PLATFORMS = {
    "tiktok": {"ad": "TikTok", "max_s": 60, "margin_v": 440, "top": 210, "maxrate": "10M"},
    "reels": {"ad": "Instagram Reels", "max_s": 90, "margin_v": 400, "top": 230, "maxrate": "10M"},
    "shorts": {"ad": "YouTube Shorts", "max_s": 60, "margin_v": 360, "top": 180, "maxrate": "12M"},
}
LAYOUTS = {"blur": "Bulanık arka plan", "smart": "Akıllı kırpma", "center": "Merkez kırpma"}
COLORS = {
    "sari": ("Klasik Sarı", "#FFD60A"), "yesil": ("Neon Yeşil", "#22E57A"), "beyaz": ("Beyaz", "#FFFFFF"),
    "kirmizi": ("Kırmızı", "#FF4D4D"), "turuncu": ("Turuncu", "#FF9F1A"), "mavi": ("Gök Mavisi", "#4DB8FF"),
}
SPEAKER_BASE = ["#FFFFFF", "#9EE7FF", "#FFD6A5", "#C7F9CC"]
DEFAULT_STYLE = {
    "color": "sari", "uppercase": True, "captions": True, "speaker_labels": False, "credit": True, "font_size": 74,
}
Progress = Callable[[float], None]


def _font() -> str:
    want = settings.caption_font
    try:
        out = subprocess.run(["fc-match", want, "-f", "%{family}"], capture_output=True, text=True, timeout=5).stdout
        if want.lower() in out.lower():
            return want
    except Exception:
        pass
    return "DejaVu Sans"


def _bgr(hex_: str) -> str:
    h = hex_.lstrip("#")
    return f"{h[4:6]}{h[2:4]}{h[0:2]}"


def _ts(t: float) -> str:
    t = max(0.0, t)
    cs = int(round(t * 100))
    return f"{cs // 360000}:{cs // 6000 % 60:02d}:{cs // 100 % 60:02d}.{cs % 100:02d}"


def _esc(s: str) -> str:
    return s.replace("\\", "/").replace("{", "(").replace("}", ")").replace("\n", " ")


def build_ass(captions: list[dict], *, duration: float, style: dict, platform: str, credit: str = "",
              speakers: Optional[list[str]] = None) -> str:
    st = {**DEFAULT_STYLE, **(style or {})}
    pf = PLATFORMS.get(platform, PLATFORMS["tiktok"])
    font = _font()
    size = int(st["font_size"])
    hl = _bgr(COLORS.get(st["color"], COLORS["sari"])[1])
    lines = [
        "[Script Info]", "ScriptType: v4.00+", "PlayResX: 1080", "PlayResY: 1920", "WrapStyle: 0",
        "ScaledBorderAndShadow: yes", "", "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, "
        "Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, "
        "MarginR, MarginV, Encoding",
    ]
    for i, col in enumerate(SPEAKER_BASE):
        lines.append(
            f"Style: Cap{i},{font},{size},&H00{_bgr(col)},&H00{_bgr(col)},&H00000000,&H78000000,-1,0,0,0,100,100,0,0,"
            f"1,7,3,2,90,90,{pf['margin_v']},1"
        )
    lines.append(f"Style: Tag,{font},{int(size * 0.5)},&H00{hl},&H00{hl},&H00000000,&H78000000,-1,0,0,0,100,100,2,0,"
                 f"1,4,2,2,90,90,{pf['margin_v'] + int(size * 2.3)},1")
    lines.append(f"Style: Credit,{font},34,&H00FFFFFF,&H00FFFFFF,&H00000000,&H80000000,0,0,0,0,100,100,0,0,1,3,1,8,"
                 f"60,60,{pf['top']},1")
    lines += ["", "[Events]", "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text"]

    multi = len({c.get("speaker") for c in captions if c.get("speaker") is not None}) > 1
    last_sp = None
    for cap in captions if st["captions"] else []:
        words = cap.get("words") or []
        if not words:
            continue
        sp = cap.get("speaker")
        style_name = f"Cap{(sp or 0) % 4}" if multi else "Cap0"
        disp = [tr_upper(w["t"]) if st["uppercase"] else w["t"] for w in words]
        label = ""
        if st["speaker_labels"] and multi and sp is not None and sp != last_sp:
            name = (speakers[sp] if speakers and sp < len(speakers) else f"Konuşmacı {sp + 1}")
            label = f"{{\\rTag}}{_esc(tr_upper(name))}\\N{{\\r}}"
        last_sp = sp
        for i, w in enumerate(words):
            a = w["s"] if i else cap["start"]
            b = words[i + 1]["s"] if i + 1 < len(words) else cap["end"]
            b = max(b, a + 0.08)
            parts = []
            for k, t in enumerate(disp):
                t = _esc(t)
                parts.append(f"{{\\1c&H{hl}&\\fscx114\\fscy114}}{t}{{\\r}}" if k == i else t)
            lines.append(f"Dialogue: 0,{_ts(a)},{_ts(b)},{style_name},,0,0,0,,{label if i == 0 else ''}{' '.join(parts)}")
    if st["credit"] and credit:
        lines.append(f"Dialogue: 1,{_ts(0)},{_ts(duration)},Credit,,0,0,0,,{{\\alpha&H30&}}{_esc(credit)[:90]}")
    return "\n".join(lines) + "\n"


def _graph(layout: str, src: dict, W: int, H: int, crop_expr: Optional[str]) -> str:
    if layout in ("smart", "center"):
        cw, ch = crop_window(src["width"], src["height"])
        if src["width"] / src["height"] > 9 / 16 + 0.02:
            x = crop_expr or f"{max(0, (src['width'] - cw)) / 2:.1f}"
            return (f"[0:v]crop=w={cw}:h={ch}:x='{x}':y=0,scale={W}:{H}:flags=lanczos,"
                    f"ass=subs.ass,format=yuv420p[v]")
    return (
        f"[0:v]split=2[a][b];"
        f"[a]scale=270:480:force_original_aspect_ratio=increase,crop=270:480,gblur=sigma=7,"
        f"scale={W}:{H},eq=brightness=-0.10:saturation=1.15[bg];"
        f"[b]scale=w={W}:h={H}:force_original_aspect_ratio=decrease:force_divisible_by=2[fg];"
        f"[bg][fg]overlay=(W-w)/2:(H-h)/2,ass=subs.ass,format=yuv420p[v]"
    )


def render_clip(
    source: str | Path, out: str | Path, *, start: float, end: float, captions: list[dict], platform: str,
    layout: str, style: dict, credit: str = "", size: str = "export", title: str = "",
    progress: Optional[Progress] = None, speakers: Optional[list[str]] = None,
) -> None:
    """Kaynaktan [start, end] aralığını 9:16 MP4'e dönüştürür (altyazı gömülü)."""
    src = probe(source)
    dur = max(1.0, end - start)
    if start < 0 or start >= src["duration"]:
        raise PipelineError("Klip başlangıcı video süresinin dışında.")
    dur = min(dur, src["duration"] - start)
    preview = size == "preview"
    W, H = (540, 960) if preview else (1080, 1920)
    pf = PLATFORMS.get(platform, PLATFORMS["tiktok"])

    crop_expr = None
    if layout == "smart":
        try:
            crop_expr = x_expression(crop_trajectory(source, start, dur, src["width"], src["height"]))
        except Exception:
            crop_expr = None

    tmp = Path(tempfile.mkdtemp(prefix="render_", dir=settings.data_dir / "tmp"))
    try:
        (tmp / "subs.ass").write_text(
            build_ass(captions, duration=dur, style=style, platform=platform, credit=credit, speakers=speakers),
            encoding="utf-8",
        )
        (tmp / "graph.txt").write_text(_graph(layout, src, W, H, crop_expr), encoding="utf-8")
        tmp_out = tmp / "out.mp4"
        cmd = [
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-ss", f"{start:.3f}", "-t", f"{dur:.3f}",
            "-i", str(source), "-filter_complex_script", "graph.txt", "-map", "[v]",
        ]
        if src["has_audio"]:
            cmd += ["-map", "0:a:0"]
            if not preview:
                cmd += ["-af", "loudnorm=I=-14:TP=-1.5:LRA=11"]
            cmd += ["-c:a", "aac", "-b:a", "96k" if preview else "160k", "-ar", "44100", "-ac", "2"]
        else:
            cmd += ["-an"]
        cmd += ["-c:v", "libx264", "-pix_fmt", "yuv420p", "-r", "30", "-threads", "2", "-movflags", "+faststart"]
        if preview:
            cmd += ["-preset", "ultrafast", "-crf", "29"]
        else:
            cmd += ["-preset", "medium", "-profile:v", "high", "-crf", "19", "-maxrate", pf["maxrate"],
                    "-bufsize", "20M"]
        if title:
            cmd += ["-metadata", f"title={title}"]
        if credit:
            cmd += ["-metadata", f"comment={credit}"]
        cmd += ["-progress", "pipe:1", "-nostats", str(tmp_out)]
        run_ffmpeg(cmd, dur, progress, cwd=tmp)
        Path(out).parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(tmp_out), str(out))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def make_thumb(video: str | Path, out: str | Path, at: float = 1.0) -> None:
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-ss", f"{at:.2f}", "-i", str(video), "-frames:v", "1", "-q:v", "4", str(out)],
        check=False,
    )
