"""İşlem hattı orkestrasyonu ve arka plan iş kuyruğu."""
from __future__ import annotations

import json
import logging
import re
import traceback
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Optional

from . import audio, db, fetch, framing, highlights, render, search, transcribe
from .config import settings, transcription_providers
from .util import MissingService, PipelineError, ServiceError, probe

log = logging.getLogger("futbolclip")
_EXEC = ThreadPoolExecutor(max_workers=settings.workers, thread_name_prefix="fc")


def submit(fn, *args) -> None:
    def guard():
        try:
            fn(*args)
        except Exception:  # son çare: iş parçacığı sessizce ölmesin
            log.error("Görev hatası:\n%s", traceback.format_exc())

    _EXEC.submit(guard)


class Progress:
    def __init__(self, job_id: str):
        self.job_id = job_id
        self.warnings: list[str] = []

    def set(self, stage: str, pct: float, msg: str = "") -> None:
        db.update("jobs", self.job_id, stage=stage, progress=round(min(99.0, pct), 1), message=msg)

    def warn(self, text: str) -> None:
        if text not in self.warnings:
            self.warnings.append(text)
            db.update("jobs", self.job_id, warnings=self.warnings)


def recover_stale() -> None:
    for j in db.select("jobs", "status IN ('queued','running')"):
        db.update("jobs", j["id"], status="error", error="Sunucu yeniden başlatıldığı için işlem yarım kaldı.")
    for c in db.select("clips", "status='rendering'"):
        ok = c.get("preview_path") and Path(c["preview_path"]).exists()
        db.update("clips", c["id"], status="ready" if ok else "error", error=None if ok else "Render yarım kaldı.")
    for e in db.select("exports", "status IN ('queued','rendering')"):
        db.update("exports", e["id"], status="error", error="Sunucu yeniden başlatıldı; yeniden dışa aktarın.")


# --------------------------------------------------------------------------- #
# Ana iş
# --------------------------------------------------------------------------- #
def run_job(job_id: str) -> None:
    job = db.get("jobs", job_id)
    if not job:
        return
    pr = Progress(job_id)
    db.update("jobs", job_id, status="running", stage="arama", progress=1, message="Başlıyor…", error=None)
    try:
        if job["kind"] == "search":
            pool = _search_flow(job, pr)
        else:
            pool = _single_flow(job, pr)
        _finalize(job, pr, pool)
        db.update("jobs", job_id, status="done", stage="tamam", progress=100, message="Klipler hazır")
    except MissingService as e:
        db.update("jobs", job_id, status="error", error=f"Eksik servis: {e.service}. {e.hint}")
    except PipelineError as e:
        db.update("jobs", job_id, status="error", error=str(e))
    except Exception as e:  # beklenmeyen
        log.error("İş hatası:\n%s", traceback.format_exc())
        db.update("jobs", job_id, status="error", error=f"Beklenmeyen hata: {e.__class__.__name__}: {e}")


def _search_flow(job: dict, pr: Progress) -> list[dict]:
    p = job["params"] or {}
    query = job["query"]
    max_videos = max(1, min(5, int(p.get("max_videos", 3))))
    pr.set("arama", 3, "Lisanslı kaynaklar taranıyor…")
    found, warns = search.search_all(
        query, settings, sources=p.get("sources") or ["youtube", "commons", "archive"], limit=max(6, max_videos * 2),
        allowed_channels=p.get("allowed_channels") or None,
    )
    for w in warns:
        pr.warn(w)
    if not found:
        raise PipelineError(
            "Bu konuda kullanım izni olan (lisanslı) video bulunamadı. Aşağıdaki uyarılara ve Servis Durumu "
            "sayfasına bakın; YouTube araması için YOUTUBE_API_KEY gerekir. Dilerseniz kendi videonuzu yükleyin."
        )
    return _process_found(job, pr, found[:max_videos], query)


def _process_found(job: dict, pr: Progress, picks: list[search.Found], query: str) -> list[dict]:
    pool: list[dict] = []
    first_missing: Optional[MissingService] = None
    span = 80.0 / len(picks)
    pr.set("arama", 7, f"{len(picks)} aday video bulundu")
    for i, f in enumerate(picks):
        base = 8 + i * span
        fd = f.to_dict()
        vid_id = db.insert(
            "videos", job_id=job["id"], source=f.source, source_id=f.source_id, title=f.title, channel=f.channel,
            license=f.license, url=f.url, duration=f.duration, thumbnail=f.thumbnail, status="indiriliyor", meta=fd,
        )
        wdir = settings.data_dir / "jobs" / job["id"] / vid_id
        label = f.title[:42]
        try:
            def dl(frac: float, msg: str, _b=base, _l=label):
                pr.set("indirme", _b + span * 0.25 * frac, f"{_l} — {msg}")

            path = fetch.download(fd, wdir, settings, dl)
            subs = None
            if f.source == "youtube" and not any(transcription_providers(settings).values()):
                subs = fetch.fetch_youtube_captions(fd, wdir)
            pool += _analyze(vid_id, path, wdir, query, subs, pr, base + span * 0.25, span * 0.75, label)
        except MissingService as e:
            first_missing = first_missing or e
            db.update("videos", vid_id, status="hata")
            pr.warn(f"«{label}» işlenemedi — eksik servis: {e.service}. {e.hint}")
        except PipelineError as e:
            db.update("videos", vid_id, status="hata")
            pr.warn(f"«{label}» işlenemedi: {e}")
    if not pool:
        if first_missing:
            raise first_missing
        raise PipelineError("Bulunan videolardan uygun bir bölüm çıkarılamadı.")
    return pool


def _single_flow(job: dict, pr: Progress) -> list[dict]:
    p = job["params"] or {}
    if job["kind"] == "url":
        return _url_flow(job, pr)
    vid = db.get("videos", p["video_id"])
    wdir = Path(vid["path"]).parent
    raw = Path(vid["path"])
    pr.set("indirme", 6, "Video hazırlanıyor…")
    src = wdir / "source.mp4"
    info = fetch.normalize_source(raw, src, settings, lambda fr, m: pr.set("indirme", 6 + 14 * fr, m))
    db.update("videos", vid["id"], path=str(src), duration=info["duration"])
    srt = wdir / "subs.txt"
    subs = srt.read_text(encoding="utf-8", errors="ignore") if srt.exists() else None
    return _analyze(vid["id"], src, wdir, job.get("query") or "", subs, pr, 20, 62, vid["title"][:42])


_YT_ID = re.compile(r"(?:v=|youtu\.be/|shorts/|embed/)([A-Za-z0-9_-]{11})")


def _url_flow(job: dict, pr: Progress) -> list[dict]:
    p = job["params"] or {}
    m = _YT_ID.search(p.get("url", ""))
    if not m:
        raise PipelineError("Yalnızca YouTube bağlantıları desteklenir (youtube.com/watch?v=… ya da youtu.be/…).")
    pr.set("arama", 4, "Video bilgileri alınıyor…")
    info = search.youtube_video_info(m.group(1), settings)
    if info is None:
        info = search.Found(source="youtube", source_id=m.group(1), title=f"YouTube {m.group(1)}", channel="",
                            url=f"https://www.youtube.com/watch?v={m.group(1)}", license="Kullanıcı beyanı (izinli)")
    elif "Standart" in info.license:
        if not p.get("attested"):
            raise PipelineError("Bu video CC lisanslı değil; işlemek için izniniz olduğunu beyan etmelisiniz.")
        info.license = "Kullanıcı beyanı (izinli)"
    return _process_found(job, pr, [info], job.get("query") or "")


# --------------------------------------------------------------------------- #
# Analiz
# --------------------------------------------------------------------------- #
def _analyze(video_id, path, wdir, query, subs_text, pr: Progress, base, span, label) -> list[dict]:
    path = Path(path)
    info = probe(path)
    dur = min(info["duration"], settings.max_analyze_s)
    pr.set("analiz", base, f"{label} — ses enerjisi analiz ediliyor…")
    y = audio.load_pcm(str(path), duration=dur)
    if len(y) < audio.SR * 5:
        raise PipelineError("Videoda analiz edilebilir ses bulunamadı.")
    exc = audio.excitement_curve(y)

    def tp(frac: float, msg: str) -> None:
        pr.set("transkripsiyon", base + span * (0.10 + 0.50 * frac), f"{label} — {msg}")

    pr.set("transkripsiyon", base + span * 0.1, f"{label} — konuşma metne dökülüyor…")
    segs, provider = transcribe.transcribe(path, wdir, settings, tp, subs_text)
    if not segs:
        raise PipelineError("Videoda Türkçe konuşma algılanamadı.")
    pr.set("konusmaci", base + span * 0.65, f"{label} — konuşmacılar ayrıştırılıyor…")
    labels = audio.diarize(y, segs)
    for g, l in zip(segs, labels):
        g["speaker"] = int(l)
    n_sp = len(set(labels)) or 1
    tpath = Path(wdir) / "transcript.json"
    tpath.write_text(json.dumps({"provider": provider, "speakers": n_sp, "segments": segs}, ensure_ascii=False))
    v = db.get("videos", video_id)
    meta = {**(v.get("meta") or {}), "transcript_provider": provider, "speakers": n_sp}
    db.update("videos", video_id, path=str(path), transcript_path=str(tpath), duration=info["duration"],
              status="analiz edildi", meta=meta)
    pr.set("secim", base + span * 0.8, f"{label} — en iyi bölümler aranıyor…")
    cands = highlights.find_candidates(segs, exc, query, min_s=settings.clip_min, max_s=settings.clip_max)
    for c in cands:
        c["video_id"] = video_id
    return cands


# --------------------------------------------------------------------------- #
# Seçim, kayıt, önizleme
# --------------------------------------------------------------------------- #
def _credit(video: dict) -> str:
    if video["source"] == "upload":
        return ""
    who = video.get("channel") or video.get("title", "")[:30]
    return f"Kaynak: {who} • {video.get('license', '')}".strip(" •")


def _finalize(job: dict, pr: Progress, pool: list[dict]) -> None:
    p = job["params"] or {}
    query = job.get("query") or ""
    count = max(5, min(10, int(p.get("count", 8))))
    pr.set("secim", 82, "En iyi bölümler seçiliyor…")
    top = highlights.select_final(pool, count * 2)
    if settings.anthropic_api_key:
        pr.set("secim", 83, "Claude ile başlık ve viral puanı üretiliyor…")
        try:
            highlights.llm_refine(top, query, settings)
        except ServiceError as e:
            pr.warn(f"Claude ile yeniden puanlama atlandı: {e}")
    final = highlights.select_final(top, count)
    if len(final) < count:
        pr.warn(f"İstenen {count} yerine {len(final)} uygun bölüm bulundu (video kısa ya da konuşma az olabilir).")

    videos = {v["id"]: v for v in db.select("videos", "job_id=?", (job["id"],))}
    transcripts: dict[str, list[dict]] = {}
    clip_ids = []
    pr.set("secim", 85, "Sahne kesimleri kontrol ediliyor…")
    for c in final:
        v = videos[c["video_id"]]
        if c["video_id"] not in transcripts:
            transcripts[c["video_id"]] = json.loads(Path(v["transcript_path"]).read_text())["segments"]
        try:
            cuts = framing.scene_cuts(v["path"], max(0, c["start"] - 3), c["end"] + 3)
            s, e = framing.snap_to_cuts(c["start"], c["end"], cuts)
            if e - s > settings.clip_max:
                e = s + settings.clip_max
            c["start"], c["end"] = round(s, 2), round(e, 2)
            c["scenes"] = len([x for x in cuts if c["start"] < x < c["end"]]) + 1
        except Exception:
            c["scenes"] = 1
        caps = highlights.build_captions(transcripts[c["video_id"]], c["start"], c["end"])
        n_sp = (v.get("meta") or {}).get("speakers", 1)
        style = {**render.DEFAULT_STYLE, "captions": bool(p.get("captions", True)), "credit": v["source"] != "upload"}
        clip_ids.append(db.insert(
            "clips", job_id=job["id"], video_id=c["video_id"], start=c["start"], end=c["end"], score=c["score"],
            title=c["title"], reason=c["reason"], tags=c["tags"], hashtags=c["hashtags"],
            speakers=[f"Konuşmacı {i + 1}" for i in range(n_sp)], captions=caps,
            layout=p.get("layout", "blur"), style=style, status="rendering",
            meta={"platform": p.get("platform", "tiktok"), "scenes": c.get("scenes", 1), "speaker_ids": c["speakers"],
                  "components": {k: round(float(x), 3) for k, x in c["components"].items()}},
        ))
    for k, cid in enumerate(clip_ids):
        pr.set("onizleme", 86 + 13 * k / len(clip_ids), f"Önizleme hazırlanıyor ({k + 1}/{len(clip_ids)})…")
        render_preview(cid)


def render_preview(clip_id: str) -> None:
    clip = db.get("clips", clip_id)
    if not clip:
        return
    vid = db.get("videos", clip["video_id"])
    cdir = settings.data_dir / "clips" / clip_id
    db.update("clips", clip_id, status="rendering", error=None)
    try:
        out = cdir / "preview.mp4"
        render.render_clip(
            vid["path"], out, start=clip["start"], end=clip["end"], captions=clip["captions"],
            platform=(clip.get("meta") or {}).get("platform", "tiktok"), layout=clip["layout"], style=clip["style"],
            credit=_credit(vid), size="preview", speakers=clip["speakers"], title=clip["title"],
        )
        thumb = cdir / "thumb.jpg"
        render.make_thumb(out, thumb, at=min(2.0, (clip["end"] - clip["start"]) / 3))
        db.update("clips", clip_id, status="ready", preview_path=str(out), thumb_path=str(thumb))
    except Exception as e:
        log.error("Önizleme hatası:\n%s", traceback.format_exc())
        db.update("clips", clip_id, status="error", error=str(e)[:300])


def render_export(export_id: str) -> None:
    ex = db.get("exports", export_id)
    if not ex:
        return
    clip = db.get("clips", ex["clip_id"])
    vid = db.get("videos", clip["video_id"])
    out = settings.data_dir / "exports" / f"{export_id}.mp4"
    db.update("exports", export_id, status="rendering", progress=0, error=None)
    last = [0.0]

    def prog(f: float) -> None:
        if f - last[0] >= 0.03 or f >= 1:
            last[0] = f
            db.update("exports", export_id, progress=round(f * 100, 1))

    try:
        render.render_clip(
            vid["path"], out, start=clip["start"], end=clip["end"], captions=clip["captions"],
            platform=ex["platform"], layout=clip["layout"], style=clip["style"], credit=_credit(vid), size="export",
            progress=prog, speakers=clip["speakers"], title=clip["title"],
        )
        db.update("exports", export_id, status="ready", progress=100, path=str(out), size=out.stat().st_size)
    except Exception as e:
        log.error("Dışa aktarma hatası:\n%s", traceback.format_exc())
        db.update("exports", export_id, status="error", error=str(e)[:300])
