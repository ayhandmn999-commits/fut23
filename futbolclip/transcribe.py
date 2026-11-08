"""Türkçe konuşma tanıma.

Sağlayıcılar (TRANSCRIBE_PROVIDER=auto sırası): faster-whisper (yerel) → Groq → OpenAI → hazır altyazı (SRT/VTT).
Çıktı: [{"start","end","text","speaker","words":[{"s","e","t"}]}]
"""
from __future__ import annotations

import html
import re
import time
from pathlib import Path
from typing import Callable, Optional

import requests

from .config import Settings, transcription_providers
from .util import MissingService, PipelineError, ServiceError, run_ffmpeg

Progress = Callable[[float, str], None]

PROMPT = (
    "Futbol maçı yorumu ve analizi. Süper Lig, Galatasaray, Fenerbahçe, Beşiktaş, Trabzonspor, "
    "penaltı, ofsayt, VAR, hakem, derbi, transfer."
)
HALLUCINATIONS = (
    "altyazı m.k", "abone olmayı unutmayın", "izlediğiniz için teşekkür", "bir sonraki videoda görüşmek",
    "altyazı", "iyi seyirler", "videoyu beğenmeyi unutmayın",
)
_END = re.compile(r"[.!?…]+[\"')\]]*$")


# --------------------------------------------------------------------------- #
# SRT / VTT
# --------------------------------------------------------------------------- #
_TS = re.compile(
    r"(?:(\d{1,2}):)?(\d{2}):(\d{2})[.,](\d{1,3})\s*-->\s*(?:(\d{1,2}):)?(\d{2}):(\d{2})[.,](\d{1,3})"
)


def _sec(h, m, s, ms) -> float:
    return int(h or 0) * 3600 + int(m) * 60 + int(s) + int(ms.ljust(3, "0")) / 1000


def parse_subtitles(text: str) -> list[dict]:
    text = text.replace("\r", "").lstrip("﻿")
    cues: list[tuple[float, float, str]] = []
    for block in re.split(r"\n\s*\n", text):
        lines = [ln for ln in block.split("\n") if ln.strip()]
        for idx, ln in enumerate(lines):
            m = _TS.search(ln)
            if not m:
                continue
            a = _sec(m.group(1), m.group(2), m.group(3), m.group(4))
            b = _sec(m.group(5), m.group(6), m.group(7), m.group(8))
            body = " ".join(lines[idx + 1:])
            body = re.sub(r"<[^>]+>", "", body)
            body = re.sub(r"\{[^}]*\}", "", body)
            body = html.unescape(re.sub(r"\s+", " ", body)).strip()
            if body and b > a:
                cues.append((a, b, body))
            break
    out: list[tuple[float, float, str]] = []
    for a, b, t in cues:  # YouTube otomatik altyazılarındaki kayan tekrarları ayıkla
        if out and t == out[-1][2]:
            out[-1] = (out[-1][0], max(out[-1][1], b), t)
        elif out and out[-1][2] and out[-1][2] in t:
            new = t.split(out[-1][2], 1)[1].strip()
            if new:
                out.append((max(a, out[-1][1]), b, new))
        else:
            out.append((a, b, t))
    return [
        {"start": a, "end": b, "text": t, "speaker": None, "words": synth_words(a, b, t)} for a, b, t in out
    ]


def synth_words(start: float, end: float, text: str) -> list[dict]:
    toks = text.split()
    if not toks:
        return []
    weights = [len(t) + 1 for t in toks]
    total, acc, words = float(sum(weights)), 0.0, []
    for t, w in zip(toks, weights):
        s = start + (end - start) * acc / total
        acc += w
        words.append({"s": round(s, 3), "e": round(start + (end - start) * acc / total, 3), "t": t})
    return words


# --------------------------------------------------------------------------- #
# Sağlayıcılar
# --------------------------------------------------------------------------- #
def _chunks(video: Path, workdir: Path, chunk_s: int = 600) -> list[tuple[Path, float]]:
    for old in workdir.glob("chunk_*.mp3"):
        old.unlink()
    run_ffmpeg([
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(video), "-vn", "-ac", "1", "-ar", "16000",
        "-c:a", "libmp3lame", "-b:a", "48k", "-f", "segment", "-segment_time", str(chunk_s), "-reset_timestamps", "1",
        str(workdir / "chunk_%03d.mp3"),
    ])
    files = sorted(workdir.glob("chunk_*.mp3"))
    if not files:
        raise PipelineError("Ses çıkarılamadı.")
    return [(f, i * float(chunk_s)) for i, f in enumerate(files)]


def _align_words(segments: list[dict], words: list[dict]) -> None:
    wi = 0
    for seg in segments:
        seg["words"] = []
        while wi < len(words) and words[wi]["e"] <= seg["start"] - 0.05:
            wi += 1
        j = wi
        while j < len(words) and words[j]["s"] < seg["end"] - 0.02:
            seg["words"].append(words[j])
            j += 1
        if seg["words"]:
            wi = j
        else:
            seg["words"] = synth_words(seg["start"], seg["end"], seg["text"])


def _api(chunk: Path, offset: float, url: str, key: str, model: str, s: Settings) -> list[dict]:
    data = [("model", model), ("language", "tr"), ("response_format", "verbose_json"), ("prompt", PROMPT),
            ("temperature", "0"), ("timestamp_granularities[]", "word"), ("timestamp_granularities[]", "segment")]
    last = ""
    for attempt in range(3):
        try:
            with open(chunk, "rb") as fh:
                r = requests.post(url, headers={"Authorization": f"Bearer {key}"}, data=data,
                                  files={"file": (chunk.name, fh, "audio/mpeg")}, timeout=(10, 300))
        except requests.RequestException as e:
            last = e.__class__.__name__
            time.sleep(2 * (attempt + 1))
            continue
        if r.status_code == 200:
            j = r.json()
            words = [{"s": offset + w["start"], "e": offset + w["end"], "t": w["word"].strip()}
                     for w in j.get("words", []) if w.get("word", "").strip()]
            segs = [{"start": offset + g["start"], "end": offset + g["end"], "text": g["text"].strip(),
                     "speaker": None, "words": []} for g in j.get("segments", []) if g.get("text", "").strip()]
            if not segs and j.get("text"):
                segs = [{"start": offset, "end": offset + 5, "text": j["text"].strip(), "speaker": None, "words": []}]
            _align_words(segs, words)
            return segs
        last = f"HTTP {r.status_code}: {r.text[:160]}"
        if r.status_code in (401, 403):
            raise ServiceError(f"Transkripsiyon API anahtarı reddedildi ({r.status_code}).")
        if r.status_code in (429, 500, 502, 503):
            time.sleep(3 * (attempt + 1))
            continue
        break
    raise ServiceError(f"Transkripsiyon API hatası: {last}")


def _via_api(video: Path, workdir: Path, name: str, s: Settings, progress: Optional[Progress]) -> list[dict]:
    if name == "groq":
        url, key, model = "https://api.groq.com/openai/v1/audio/transcriptions", s.groq_api_key, s.groq_transcribe_model
    else:
        url, key, model = "https://api.openai.com/v1/audio/transcriptions", s.openai_api_key, s.openai_transcribe_model
    parts = _chunks(video, workdir)
    segs: list[dict] = []
    for i, (chunk, off) in enumerate(parts):
        segs += _api(chunk, off, url, key, model, s)
        if progress:
            progress((i + 1) / len(parts), f"Konuşma metne dökülüyor ({i + 1}/{len(parts)})…")
    return segs


_FW_MODEL = {}


def _via_faster_whisper(video: Path, s: Settings, progress: Optional[Progress]) -> list[dict]:
    from faster_whisper import WhisperModel  # type: ignore

    if s.whisper_model not in _FW_MODEL:
        _FW_MODEL[s.whisper_model] = WhisperModel(s.whisper_model, device="auto", compute_type="int8")
    model = _FW_MODEL[s.whisper_model]
    it, info = model.transcribe(str(video), language="tr", word_timestamps=True, vad_filter=True,
                                initial_prompt=PROMPT, condition_on_previous_text=False)
    segs = []
    for g in it:
        words = [{"s": w.start, "e": w.end, "t": w.word.strip()} for w in (g.words or []) if w.word.strip()]
        text = g.text.strip()
        if text:
            segs.append({"start": g.start, "end": g.end, "text": text, "speaker": None,
                         "words": words or synth_words(g.start, g.end, text)})
        if progress and info.duration:
            progress(min(1.0, g.end / info.duration), "Konuşma metne dökülüyor…")
    return segs


# --------------------------------------------------------------------------- #
# Son işlem
# --------------------------------------------------------------------------- #
def resegment(segments: list[dict], max_len: float = 14.0) -> list[dict]:
    """Uzun Whisper bölümlerini cümle sınırlarından böler (kırpma sınırları için daha ince ayrıntı)."""
    out: list[dict] = []
    for seg in segments:
        words = seg.get("words") or []
        if len(words) < 6 or (seg["end"] - seg["start"]) <= 5:
            out.append(seg)
            continue
        cur: list[dict] = []
        for w in words:
            cur.append(w)
            dur = cur[-1]["e"] - cur[0]["s"]
            if (_END.search(w["t"]) and len(cur) >= 3) or dur > max_len:
                out.append(_mk(cur, seg))
                cur = []
        if cur:
            out.append(_mk(cur, seg))
    return out


def _mk(words: list[dict], seg: dict) -> dict:
    return {"start": words[0]["s"], "end": words[-1]["e"], "text": " ".join(w["t"] for w in words),
            "speaker": seg.get("speaker"), "words": words}


def _clean(segs: list[dict]) -> list[dict]:
    out = []
    for g in sorted(segs, key=lambda x: x["start"]):
        t = g["text"].strip()
        low = t.lower()
        if not t or (g["end"] - g["start"] < 6 and any(h in low for h in HALLUCINATIONS) and len(t) < 60):
            continue
        if out and out[-1]["text"] == t:
            continue
        out.append(g)
    return out


def transcribe(
    video: Path, workdir: Path, s: Settings, progress: Optional[Progress] = None,
    subs_text: Optional[str] = None,
) -> tuple[list[dict], str]:
    """(segmentler, kullanılan_sağlayıcı) döndürür."""
    workdir.mkdir(parents=True, exist_ok=True)
    avail = transcription_providers(s)
    want = s.transcribe_provider
    order = ["faster_whisper", "groq", "openai"] if want == "auto" else [want]
    for name in order:
        if not avail.get(name):
            continue
        try:
            if name == "faster_whisper":
                segs = _via_faster_whisper(video, s, progress)
            else:
                segs = _via_api(video, workdir, name, s, progress)
            return resegment(_clean(segs)), name
        except ServiceError:
            if want != "auto":
                raise
            continue
    if subs_text:
        segs = parse_subtitles(subs_text)
        if segs:
            return resegment(_clean(segs)), "subs"
    raise MissingService(
        "Konuşma tanıma",
        "Türkçe transkripsiyon için faster-whisper kurun ya da GROQ_API_KEY / OPENAI_API_KEY tanımlayın; "
        "alternatif olarak yükleme ekranından SRT/VTT altyazı dosyası ekleyin.",
    )
