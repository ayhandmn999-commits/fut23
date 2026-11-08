"""Uçtan uca duman testi (Flask test client + gerçek FFmpeg).

Kullanım: python tests/smoke_test.py <ornek_klasoru>
(önce: python tests/make_sample.py <ornek_klasoru>)
"""
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
SAMPLE = Path(sys.argv[1] if len(sys.argv) > 1 else ROOT / "sample")
os.environ["FUTBOLCLIP_DATA"] = tempfile.mkdtemp(prefix="fc_smoke_")
for k in ("YOUTUBE_API_KEY", "OPENAI_API_KEY", "GROQ_API_KEY", "ANTHROPIC_API_KEY"):
    os.environ.pop(k, None)

from futbolclip.web import create_app  # noqa: E402

app = create_app()
c = app.test_client()
H = {}


def step(msg):
    print(f"\n▶ {msg}")


def ok(cond, msg):
    print(("  ✔ " if cond else "  ✘ ") + msg)
    if not cond:
        sys.exit(1)


def call(method, url, **kw):
    r = getattr(c, method)(url, headers=H, **kw)
    return r, (r.get_json(silent=True) if r.is_json else None)


def wait(url, key="status", done=("done", "ready", "error"), timeout=600):
    t0 = time.time()
    last = ""
    while time.time() - t0 < timeout:
        _, j = call("get", url)
        msg = f"{j.get('stage', '')} %{j.get('progress', 0)} {j.get('message', '')}"
        if msg != last and "stage" in j:
            print("   ", msg[:110])
            last = msg
        if j[key] in done:
            return j
        time.sleep(1.5)
    raise TimeoutError(url)


step("Oturum / CSRF")
_, j = call("get", "/api/me")
H["X-CSRF-Token"] = j["csrf"]
r, j = call("post", "/api/auth/register", json={"email": "test@example.com", "password": "parola1234", "name": "Test"})
ok(r.status_code == 200, "kayıt başarılı")
H["X-CSRF-Token"] = j["csrf"]
r = c.post("/api/jobs/search", json={"query": "x"}, headers={"X-CSRF-Token": "yanlis"})
ok(r.status_code == 403, "yanlış CSRF reddedildi")

step("Servis durumu")
r, j = call("get", "/api/status")
for h in j["hizmetler"]:
    print(f"   [{h['durum']:>14}] {h['ad']}")
ok(any(h["id"] == "ffmpeg" and h["hazir"] for h in j["hizmetler"]), "FFmpeg hazır")

step("Arama işi (anahtarsız kaynaklar; ağ olmayabilir — çökmemeli)")
r, j = call("post", "/api/jobs/search", json={"query": "Galatasaray Fenerbahçe maçı", "sources": ["youtube", "commons", "archive"]})
ok(r.status_code == 202, "arama işi kuyruğa alındı")
job = wait(f"/api/jobs/{j['id']}", timeout=120)
print("   durum:", job["status"], "|", (job["error"] or "")[:160])
for w in job["warnings"]:
    print("   uyarı:", w[:150])
ok(job["status"] in ("error", "done"), "arama işi çökmeden sonlandı")

step("Video yükleme + SRT ile analiz")
with open(SAMPLE / "ornek_mac_yorumu.mp4", "rb") as v, open(SAMPLE / "ornek_altyazi.srt", "rb") as s:
    r, j = call("post", "/api/jobs/upload", data={
        "video": (v, "ornek_mac_yorumu.mp4"), "subs": (s, "ornek.srt"), "rights": "1", "count": "6",
        "layout": "blur", "platform": "tiktok", "query": "gol penaltı derbi",
    }, content_type="multipart/form-data")
ok(r.status_code == 202, f"yükleme kabul edildi {j}")
job = wait(f"/api/jobs/{j['id']}", timeout=900)
ok(job["status"] == "done", f"iş tamamlandı ({job.get('error')})")
job_id = job["id"]
_, job = call("get", f"/api/jobs/{job_id}")
clips = job["clips"]
print(f"   {len(clips)} klip, kaynak: {job['videos'][0]['provider']}, konuşmacı: {job['videos'][0]['speakers']}")
for cl in clips:
    print(f"   • %{cl['score']:>5} {cl['duration']:>5.1f}s [{cl['start']:.1f}-{cl['end']:.1f}] {cl['title'][:60]} | {cl['tags']}")
ok(len(clips) >= 3, "en az 3 klip üretildi")
ok(all(15 <= cl["duration"] <= 62 for cl in clips), "tüm klipler 15–60 sn aralığında")
ok(all(cl["status"] == "ready" and cl["preview_url"] for cl in clips), "tüm önizlemeler hazır")
starts = sorted((cl["start"], cl["end"]) for cl in clips)
ok(all(b[0] >= a[1] - 0.5 for a, b in zip(starts, starts[1:])), "klipler çakışmıyor")
ok(any("Gol" in cl["tags"] or "Penaltı / VAR" in cl["tags"] for cl in clips[:3]), "en yüksek puanlılar gol/penaltı bölümlerinden")

step("Önizleme medyası (Range desteği)")
r = c.get(clips[0]["preview_url"], headers={**H, "Range": "bytes=0-99"})
ok(r.status_code == 206, "Range isteği 206 döndü")
r = c.get(clips[0]["thumb_url"], headers=H)
ok(r.status_code == 200 and r.data[:2] == b"\xff\xd8", "kapak JPEG")
out = Path(os.environ["FUTBOLCLIP_DATA"]) / "clips" / clips[0]["id"] / "preview.mp4"
p = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_name,width,height", "-of", "compact", str(out)],
                   capture_output=True, text=True).stdout
print("  ", p.strip().replace("\n", " | "))
ok("width=540" in p and "height=960" in p, "önizleme 540x960 (9:16)")

step("Klip düzenleme (kırpma + altyazı + düzen)")
cl = clips[0]
body = {"start": round(cl["start"] + 1.0, 2), "end": round(cl["end"] - 1.0, 2), "layout": "smart", "title": "Düzenlenmiş başlık ğüşiöç İI",
        "style": {"color": "yesil", "speaker_labels": True}, "platform": "reels"}
r, j = call("patch", f"/api/clips/{cl['id']}", json=body)
ok(r.status_code == 202, "PATCH kabul edildi")
j = wait(f"/api/clips/{cl['id']}", done=("ready", "error"), timeout=300)
ok(j["status"] == "ready", f"yeniden render tamam ({j.get('error')})")
ok(j["layout"] == "smart" and j["style"]["color"] == "yesil", "düzen ve stil kaydedildi")
caps = j["captions"]
ok(len(caps) > 3, f"altyazılar yeniden üretildi ({len(caps)} blok)")
caps[0]["text"] = "Özel düzenlenmiş altyazı"
r, j = call("patch", f"/api/clips/{cl['id']}", json={"captions": caps})
ok(r.status_code == 202, "altyazı metni düzenlendi")
j = wait(f"/api/clips/{cl['id']}", done=("ready", "error"), timeout=300)
ok(j["status"] == "ready" and j["captions"][0]["text"] == "Özel düzenlenmiş altyazı", "düzenlenen altyazı kalıcı")
r, _ = call("patch", f"/api/clips/{cl['id']}", json={"start": 5, "end": 6})
ok(r.status_code == 400, "çok kısa süre reddedildi")
ok(call("get", f"/api/clips/{cl['id']}")[1]["status"] == "ready", "reddedilen düzenleme klibi bozmadı")

step("MP4 dışa aktarma (3 platform)")
for plat in ("tiktok", "reels", "shorts"):
    r, j = call("post", f"/api/clips/{cl['id']}/export", json={"platform": plat})
    ok(r.status_code == 202, f"{plat} dışa aktarma kuyruğa alındı")
    j = wait(f"/api/exports/{j['id']}", done=("ready", "error"), timeout=600)
    ok(j["status"] == "ready", f"{plat} hazır ({j.get('error')})")
    r = c.get(j["download_url"], headers=H)
    ok(r.status_code == 200 and "attachment" in r.headers.get("Content-Disposition", ""), "indirme başlığı doğru")
    f = Path(tempfile.mkdtemp()) / f"{plat}.mp4"
    f.write_bytes(r.data)
    info = json.loads(subprocess.run(
        ["ffprobe", "-v", "error", "-print_format", "json", "-show_streams", "-show_format", str(f)],
        capture_output=True, text=True).stdout)
    v = next(s for s in info["streams"] if s["codec_type"] == "video")
    a = next(s for s in info["streams"] if s["codec_type"] == "audio")
    dur = float(info["format"]["duration"])
    print(f"   {plat}: {v['codec_name']} {v['width']}x{v['height']} {v['r_frame_rate']} | {a['codec_name']} | {dur:.1f}s | {f.stat().st_size / 1e6:.1f} MB")
    ok((v["width"], v["height"]) == (1080, 1920) and v["codec_name"] == "h264" and a["codec_name"] == "aac", "1080x1920 H.264/AAC")
    ok(abs(dur - (j["duration"])) < 1.0, "süre beklenenle uyumlu")
    (SAMPLE / f"export_{plat}.mp4").write_bytes(r.data)

step("Yetkisiz erişim")
c2 = app.test_client()
r = c2.get(clips[0]["preview_url"])
ok(r.status_code == 401, "oturumsuz medya erişimi reddedildi")

print("\n✅ Tüm testler geçti")
