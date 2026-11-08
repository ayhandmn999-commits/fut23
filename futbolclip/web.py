"""Flask uygulaması: kimlik doğrulama, kullanıcı paneli API'si, medya sunumu."""
from __future__ import annotations

import re
import secrets
import shutil
import time
from datetime import timedelta
from functools import wraps
from pathlib import Path
from typing import Optional

from flask import Flask, abort, jsonify, render_template, request, send_file, session
from werkzeug.exceptions import HTTPException, RequestEntityTooLarge
from werkzeug.security import check_password_hash, generate_password_hash

from . import db, highlights, pipeline, render, search
from .config import ROOT, capabilities, settings
from .util import PipelineError, probe, slugify

VIDEO_EXT = {".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v", ".mpg", ".mpeg", ".ts"}
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_FAILS: dict[str, list[float]] = {}


def create_app() -> Flask:
    app = Flask(__name__, static_folder=str(ROOT / "static"), template_folder=str(ROOT / "templates"))
    app.config.update(
        SECRET_KEY=settings.secret_key, MAX_CONTENT_LENGTH=settings.max_upload_mb * 1024 * 1024,
        SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Lax", SESSION_COOKIE_SECURE=settings.cookie_secure,
        PERMANENT_SESSION_LIFETIME=timedelta(days=14), JSON_AS_ASCII=False,
    )
    db.init_db()
    pipeline.recover_stale()

    # ------------------------------------------------------------------ yardımcılar
    def err(msg: str, code: int = 400):
        return jsonify({"error": msg}), code

    def me() -> Optional[dict]:
        uid = session.get("uid")
        return db.get("users", uid) if uid else None

    def login_required(fn):
        @wraps(fn)
        def wrapper(*a, **kw):
            u = me()
            if not u:
                return err("Oturum gerekli.", 401)
            return fn(u, *a, **kw)
        return wrapper

    def owned_job(u: dict, jid: str) -> dict:
        j = db.get("jobs", jid)
        if not j or j["user_id"] != u["id"]:
            abort(404)
        return j

    def owned_clip(u: dict, cid: str) -> tuple[dict, dict]:
        c = db.get("clips", cid)
        if not c:
            abort(404)
        j = db.get("jobs", c["job_id"])
        if not j or j["user_id"] != u["id"]:
            abort(404)
        return c, db.get("videos", c["video_id"])

    def owned_export(u: dict, eid: str) -> dict:
        e = db.get("exports", eid)
        if not e or e["user_id"] != u["id"]:
            abort(404)
        return e

    def _ts(path: Optional[str]) -> int:
        try:
            return int(Path(path).stat().st_mtime) if path else 0
        except OSError:
            return 0

    def clip_json(c: dict, v: Optional[dict] = None) -> dict:
        v = v or db.get("videos", c["video_id"]) or {}
        meta = c.get("meta") or {}
        t = _ts(c.get("preview_path"))
        return {
            "id": c["id"], "job_id": c["job_id"], "start": c["start"], "end": c["end"],
            "duration": round(c["end"] - c["start"], 2), "score": c["score"], "title": c["title"],
            "reason": c["reason"], "tags": c["tags"] or [], "hashtags": c["hashtags"] or [],
            "speakers": c["speakers"] or [], "captions": c["captions"] or [], "layout": c["layout"],
            "style": c["style"] or {}, "status": c["status"], "error": c["error"],
            "preview_url": f"/media/clips/{c['id']}/preview.mp4?v={t}" if t else None,
            "thumb_url": f"/media/clips/{c['id']}/thumb.jpg?v={t}" if t else None,
            "scenes": meta.get("scenes", 1), "platform": meta.get("platform", "tiktok"),
            "source": {"title": v.get("title"), "channel": v.get("channel"), "license": v.get("license"),
                       "url": v.get("url"), "duration": v.get("duration"), "kind": v.get("source")},
        }

    def export_json(e: dict) -> dict:
        meta = e.get("meta") or {}
        return {
            "id": e["id"], "clip_id": e["clip_id"], "platform": e["platform"],
            "platform_name": render.PLATFORMS.get(e["platform"], {}).get("ad", e["platform"]),
            "status": e["status"], "progress": e["progress"], "error": e["error"], "size": e["size"],
            "title": meta.get("title", ""), "duration": meta.get("duration"), "created_at": e["created_at"],
            "download_url": f"/api/exports/{e['id']}/download" if e["status"] == "ready" else None,
        }

    def job_json(j: dict, detail: bool = False) -> dict:
        clips = db.select("clips", "job_id=?", (j["id"],), order="score DESC")
        out = {
            "id": j["id"], "kind": j["kind"], "title": j["title"], "query": j["query"], "status": j["status"],
            "stage": j["stage"], "progress": j["progress"], "message": j["message"], "error": j["error"],
            "warnings": j["warnings"] or [], "created_at": j["created_at"], "clip_count": len(clips),
            "thumb_url": next((clip_json(c)["thumb_url"] for c in clips if c.get("thumb_path")), None),
            "params": {k: v for k, v in (j["params"] or {}).items() if k in
                       ("count", "layout", "platform", "captions", "sources", "max_videos")},
        }
        if detail:
            vids = db.select("videos", "job_id=?", (j["id"],), order="created_at")
            vmap = {v["id"]: v for v in vids}
            out["clips"] = [clip_json(c, vmap.get(c["video_id"])) for c in clips]
            out["videos"] = [{"id": v["id"], "title": v["title"], "channel": v["channel"], "license": v["license"],
                              "url": v["url"], "duration": v["duration"], "status": v["status"], "kind": v["source"],
                              "thumbnail": v["thumbnail"],
                              "speakers": (v.get("meta") or {}).get("speakers"),
                              "provider": (v.get("meta") or {}).get("transcript_provider")} for v in vids]
        return out

    def _job_params(d: dict) -> dict:
        layout = d.get("layout") if d.get("layout") in render.LAYOUTS else "blur"
        platform = d.get("platform") if d.get("platform") in render.PLATFORMS else "tiktok"
        try:
            count = max(5, min(10, int(d.get("count", 8))))
        except (TypeError, ValueError):
            count = 8
        return {"count": count, "layout": layout, "platform": platform, "captions": bool(d.get("captions", True))}

    # ------------------------------------------------------------------ güvenlik
    @app.before_request
    def csrf_guard():
        if request.method in ("POST", "PUT", "PATCH", "DELETE") and request.path.startswith("/api/"):
            token = request.headers.get("X-CSRF-Token", "")
            if not session.get("csrf") or not secrets.compare_digest(token, session["csrf"]):
                return err("Güvenlik doğrulaması başarısız. Sayfayı yenileyin.", 403)

    @app.after_request
    def headers(resp):
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("Referrer-Policy", "same-origin")
        resp.headers.setdefault("X-Frame-Options", "DENY")
        return resp

    @app.errorhandler(RequestEntityTooLarge)
    def too_large(_e):
        return err(f"Dosya çok büyük (en fazla {settings.max_upload_mb} MB).", 413)

    @app.errorhandler(HTTPException)
    def http_err(e: HTTPException):
        if request.path.startswith(("/api/", "/media/")):
            names = {404: "Bulunamadı.", 405: "Yöntem desteklenmiyor.", 401: "Oturum gerekli."}
            return err(names.get(e.code or 500, e.description or "Hata."), e.code or 500)
        return e

    @app.errorhandler(Exception)
    def server_err(e):
        app.logger.exception("Sunucu hatası")
        return err("Sunucuda beklenmeyen bir hata oluştu.", 500)

    # ------------------------------------------------------------------ sayfa
    @app.get("/")
    def index():
        session.permanent = True
        session.setdefault("csrf", secrets.token_urlsafe(24))
        resp = app.make_response(render_template("index.html", csrf=session["csrf"]))
        resp.headers["Cache-Control"] = "no-store"
        return resp

    @app.get("/healthz")
    def healthz():
        return jsonify({"ok": True})

    # ------------------------------------------------------------------ kimlik doğrulama
    @app.get("/api/me")
    def api_me():
        u = me()
        session.permanent = True
        session.setdefault("csrf", secrets.token_urlsafe(24))
        return jsonify({
            "user": {"id": u["id"], "email": u["email"], "name": u["name"]} if u else None,
            "registration_open": settings.allow_registration, "csrf": session["csrf"],
        })

    def _throttle(key: str) -> bool:
        now = time.time()
        lst = [t for t in _FAILS.get(key, []) if now - t < 300]
        _FAILS[key] = lst
        return len(lst) >= 8

    @app.post("/api/auth/register")
    def register():
        if not settings.allow_registration:
            return err("Kayıtlar kapalı.", 403)
        d = request.get_json(silent=True) or {}
        email, pw, name = str(d.get("email", "")).strip().lower(), str(d.get("password", "")), str(d.get("name", "")).strip()
        if not _EMAIL.match(email):
            return err("Geçerli bir e-posta adresi girin.")
        if len(pw) < 8:
            return err("Parola en az 8 karakter olmalı.")
        if db.get_user_by_email(email):
            return err("Bu e-posta ile zaten bir hesap var.", 409)
        uid = db.insert("users", email=email, name=name[:60] or email.split("@")[0], pw_hash=generate_password_hash(pw))
        session.clear()
        session["uid"], session["csrf"] = uid, secrets.token_urlsafe(24)
        session.permanent = True
        return jsonify({"ok": True, "csrf": session["csrf"]})

    @app.post("/api/auth/login")
    def login():
        d = request.get_json(silent=True) or {}
        key = request.remote_addr or "?"
        if _throttle(key):
            return err("Çok fazla başarısız deneme. Birkaç dakika sonra tekrar deneyin.", 429)
        u = db.get_user_by_email(str(d.get("email", "")).strip())
        if not u or not check_password_hash(u["pw_hash"], str(d.get("password", ""))):
            _FAILS.setdefault(key, []).append(time.time())
            return err("E-posta veya parola hatalı.", 401)
        session.clear()
        session["uid"], session["csrf"] = u["id"], secrets.token_urlsafe(24)
        session.permanent = True
        return jsonify({"ok": True, "csrf": session["csrf"]})

    @app.post("/api/auth/logout")
    def logout():
        session.clear()
        session["csrf"] = secrets.token_urlsafe(24)
        return jsonify({"ok": True, "csrf": session["csrf"]})

    # ------------------------------------------------------------------ durum
    @app.get("/api/status")
    @login_required
    def status(_u):
        cap = capabilities()
        return jsonify({
            **cap,
            "platforms": {k: {"name": v["ad"], "max_s": v["max_s"]} for k, v in render.PLATFORMS.items()},
            "layouts": render.LAYOUTS, "colors": {k: {"name": v[0], "hex": v[1]} for k, v in render.COLORS.items()},
            "sources": {"youtube": bool(settings.youtube_api_key), "commons": True, "archive": True},
        })

    # ------------------------------------------------------------------ işler
    @app.post("/api/jobs/search")
    @login_required
    def job_search(u):
        d = request.get_json(silent=True) or {}
        q = str(d.get("query", "")).strip()
        if len(q) < 3:
            return err("Lütfen aramak istediğiniz konuyu yazın (en az 3 karakter).")
        sources = [s for s in (d.get("sources") or ["youtube", "commons", "archive"]) if s in search.SOURCE_LABEL]
        if not sources:
            return err("En az bir kaynak seçin.")
        raw = d.get("allowed_channels") or ""
        allowed = [c for c in (re.split(r"[\s,;]+", raw) if isinstance(raw, str) else raw) if c]
        if allowed and not d.get("attested"):
            return err("İzinli kanallar için içerik sahibi olduğunuzu ya da izin aldığınızı onaylamalısınız.")
        try:
            mv = max(1, min(5, int(d.get("max_videos", 3))))
        except (TypeError, ValueError):
            mv = 3
        params = {**_job_params(d), "sources": sources, "max_videos": mv, "allowed_channels": allowed,
                  "attested": bool(d.get("attested"))}
        jid = db.insert("jobs", user_id=u["id"], kind="search", title=q, query=q, status="queued", stage="arama",
                        progress=0, message="Sırada…", params=params, warnings=[])
        pipeline.submit(pipeline.run_job, jid)
        return jsonify({"id": jid}), 202

    @app.post("/api/jobs/url")
    @login_required
    def job_url(u):
        d = request.get_json(silent=True) or {}
        url = str(d.get("url", "")).strip()
        if not url:
            return err("Bir YouTube bağlantısı girin.")
        params = {**_job_params(d), "url": url, "attested": bool(d.get("attested"))}
        jid = db.insert("jobs", user_id=u["id"], kind="url", title=url[:80], query=str(d.get("query", "")).strip(),
                        status="queued", stage="arama", progress=0, message="Sırada…", params=params, warnings=[])
        pipeline.submit(pipeline.run_job, jid)
        return jsonify({"id": jid}), 202

    @app.post("/api/jobs/upload")
    @login_required
    def job_upload(u):
        f = request.files.get("video")
        if not f or not f.filename:
            return err("Bir video dosyası seçin.")
        if request.form.get("rights") != "1":
            return err("Videoyu işleme hakkınız olduğunu onaylamalısınız.")
        ext = Path(f.filename).suffix.lower()
        if ext not in VIDEO_EXT:
            return err("Desteklenen biçimler: " + ", ".join(sorted(VIDEO_EXT)))
        title = re.sub(r"[\\/:*?\"<>|]+", " ", Path(f.filename).stem).strip()[:80] or "Yüklenen video"
        params = _job_params(request.form)
        jid = db.insert("jobs", user_id=u["id"], kind="upload", title=title, query=request.form.get("query", "").strip(),
                        status="queued", stage="indirme", progress=0, message="Yükleniyor…", params=params, warnings=[])
        vid_id = db.new_id()
        wdir = settings.data_dir / "jobs" / jid / vid_id
        wdir.mkdir(parents=True, exist_ok=True)
        raw = wdir / f"upload{ext}"
        f.save(raw)
        try:
            probe(raw)
        except PipelineError as e:
            shutil.rmtree(settings.data_dir / "jobs" / jid, ignore_errors=True)
            db.delete("jobs", jid)
            return err(f"Dosya geçerli bir video değil: {e}")
        sub = request.files.get("subs")
        if sub and sub.filename:
            data = sub.read(5 * 1024 * 1024)
            (wdir / "subs.txt").write_bytes(data)
        db.insert("videos", id=vid_id, job_id=jid, source="upload", source_id="", title=title, channel="",
                  license="Kullanıcı yüklemesi", url="", path=str(raw), status="yuklendi", meta={})
        db.update("jobs", jid, params={**params, "video_id": vid_id})
        pipeline.submit(pipeline.run_job, jid)
        return jsonify({"id": jid}), 202

    @app.get("/api/jobs")
    @login_required
    def jobs_list(u):
        return jsonify([job_json(j) for j in db.select("jobs", "user_id=?", (u["id"],), order="created_at DESC", limit=100)])

    @app.get("/api/jobs/<jid>")
    @login_required
    def job_get(u, jid):
        return jsonify(job_json(owned_job(u, jid), detail=True))

    @app.delete("/api/jobs/<jid>")
    @login_required
    def job_delete(u, jid):
        j = owned_job(u, jid)
        if j["status"] in ("queued", "running"):
            return err("Çalışan bir proje silinemez; bitmesini bekleyin.", 409)
        for c in db.select("clips", "job_id=?", (jid,)):
            for e in db.select("exports", "clip_id=?", (c["id"],)):
                if e.get("path"):
                    Path(e["path"]).unlink(missing_ok=True)
            shutil.rmtree(settings.data_dir / "clips" / c["id"], ignore_errors=True)
        shutil.rmtree(settings.data_dir / "jobs" / jid, ignore_errors=True)
        db.delete("jobs", jid)
        return jsonify({"ok": True})

    # ------------------------------------------------------------------ klipler
    @app.get("/api/clips/<cid>")
    @login_required
    def clip_get(u, cid):
        c, v = owned_clip(u, cid)
        return jsonify(clip_json(c, v))

    @app.patch("/api/clips/<cid>")
    @login_required
    def clip_patch(u, cid):
        c, v = owned_clip(u, cid)
        if c["status"] == "rendering":
            return err("Önizleme hazırlanırken değişiklik yapılamaz.", 409)
        d = request.get_json(silent=True) or {}
        try:
            start = round(float(d.get("start", c["start"])), 2)
            end = round(float(d.get("end", c["end"])), 2)
        except (TypeError, ValueError):
            return err("Başlangıç/bitiş sayısal olmalı.")
        vdur = float(v.get("duration") or 0)
        if start < 0 or end <= start or (vdur and end > vdur + 0.5):
            return err("Geçersiz zaman aralığı.")
        if not 5 <= end - start <= 180:
            return err("Klip süresi 5–180 saniye arasında olmalı (önerilen 15–60 sn).")
        upd: dict = {}
        if "title" in d:
            upd["title"] = str(d["title"]).strip()[:120] or c["title"]
        if d.get("layout") in render.LAYOUTS:
            upd["layout"] = d["layout"]
        if isinstance(d.get("style"), dict):
            st = {**render.DEFAULT_STYLE, **(c["style"] or {})}
            ds = d["style"]
            if ds.get("color") in render.COLORS:
                st["color"] = ds["color"]
            for k in ("uppercase", "captions", "speaker_labels", "credit"):
                if k in ds:
                    st[k] = bool(ds[k])
            if "font_size" in ds:
                try:
                    st["font_size"] = max(40, min(110, int(ds["font_size"])))
                except (TypeError, ValueError):
                    pass
            upd["style"] = st
        meta = dict(c.get("meta") or {})
        if d.get("platform") in render.PLATFORMS:
            meta["platform"] = d["platform"]
        dur = end - start
        if isinstance(d.get("captions"), list):
            caps = []
            for cap in d["captions"][:400]:
                try:
                    a, b = float(cap["start"]), float(cap["end"])
                    text = str(cap["text"]).strip()
                except (KeyError, TypeError, ValueError):
                    continue
                if not text or b <= a or a >= dur + 1:
                    continue
                caps.append(highlights.retime_caption({
                    "start": round(max(0, a), 2), "end": round(min(b, dur), 2), "text": text[:120],
                    "speaker": cap.get("speaker"), "words": cap.get("words") or [],
                }))
            upd["captions"] = caps
        elif (start, end) != (c["start"], c["end"]) or d.get("rebuild_captions"):
            import json as _json
            segs = _json.loads(Path(v["transcript_path"]).read_text())["segments"]
            upd["captions"] = highlights.build_captions(segs, start, end)
        upd.update(start=start, end=end, meta=meta, status="rendering", error=None)
        db.update("clips", cid, **upd)
        pipeline.submit(pipeline.render_preview, cid)
        return jsonify(clip_json(db.get("clips", cid), v)), 202

    @app.post("/api/clips/<cid>/export")
    @login_required
    def clip_export(u, cid):
        c, _v = owned_clip(u, cid)
        d = request.get_json(silent=True) or {}
        platform = d.get("platform")
        if platform not in render.PLATFORMS:
            return err("Geçerli bir platform seçin (tiktok, reels, shorts).")
        if c["status"] != "ready":
            return err("Klip hazır olmadan dışa aktarılamaz.", 409)
        eid = db.insert("exports", clip_id=cid, user_id=u["id"], platform=platform, status="queued", progress=0,
                        meta={"title": c["title"], "duration": round(c["end"] - c["start"], 1)})
        pipeline.submit(pipeline.render_export, eid)
        return jsonify(export_json(db.get("exports", eid))), 202

    @app.get("/api/exports")
    @login_required
    def exports_list(u):
        return jsonify([export_json(e) for e in db.select("exports", "user_id=?", (u["id"],), order="created_at DESC", limit=100)])

    @app.get("/api/exports/<eid>")
    @login_required
    def export_get(u, eid):
        return jsonify(export_json(owned_export(u, eid)))

    @app.get("/api/exports/<eid>/download")
    @login_required
    def export_download(u, eid):
        e = owned_export(u, eid)
        if e["status"] != "ready" or not e.get("path") or not Path(e["path"]).exists():
            abort(404)
        name = f"{slugify((e.get('meta') or {}).get('title', 'klip'))}-{e['platform']}.mp4"
        return send_file(e["path"], mimetype="video/mp4", as_attachment=True, download_name=name)

    @app.delete("/api/exports/<eid>")
    @login_required
    def export_delete(u, eid):
        e = owned_export(u, eid)
        if e.get("path"):
            Path(e["path"]).unlink(missing_ok=True)
        db.delete("exports", eid)
        return jsonify({"ok": True})

    # ------------------------------------------------------------------ medya
    @app.get("/media/clips/<cid>/<name>")
    @login_required
    def media_clip(u, cid, name):
        c, _v = owned_clip(u, cid)
        if name == "preview.mp4":
            path, mime = c.get("preview_path"), "video/mp4"
        elif name == "thumb.jpg":
            path, mime = c.get("thumb_path"), "image/jpeg"
        else:
            abort(404)
        if not path or not Path(path).exists():
            abort(404)
        return send_file(path, mimetype=mime, conditional=True, max_age=0)

    return app
