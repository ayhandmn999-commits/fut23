"""Yapılandırma ve servis durumu tespiti."""
from __future__ import annotations

import importlib.util
import os
import secrets
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


_load_dotenv(ROOT / ".env")


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _int(name: str, default: int) -> int:
    try:
        return int(_env(name, str(default)))
    except ValueError:
        return default


def _bool(name: str, default: bool) -> bool:
    value = _env(name)
    if not value:
        return default
    return value.lower() in {"1", "true", "evet", "yes", "on"}


def has_module(name: str) -> bool:
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError):
        return False


class Settings:
    def __init__(self) -> None:
        self.root = ROOT
        self.data_dir = Path(_env("FUTBOLCLIP_DATA", str(ROOT / "data"))).resolve()
        for sub in ("jobs", "clips", "exports", "tmp"):
            (self.data_dir / sub).mkdir(parents=True, exist_ok=True)
        self.db_path = self.data_dir / "futbolclip.db"
        self.secret_key = self._load_secret()

        # Harici servisler
        self.youtube_api_key = _env("YOUTUBE_API_KEY")
        self.openai_api_key = _env("OPENAI_API_KEY")
        self.groq_api_key = _env("GROQ_API_KEY")
        self.anthropic_api_key = _env("ANTHROPIC_API_KEY")
        self.llm_model = _env("LLM_MODEL", "claude-sonnet-5-5")

        # Transkripsiyon
        self.transcribe_provider = _env("TRANSCRIBE_PROVIDER", "auto").lower()
        self.whisper_model = _env("WHISPER_MODEL", "small")
        self.openai_transcribe_model = _env("OPENAI_TRANSCRIBE_MODEL", "whisper-1")
        self.groq_transcribe_model = _env("GROQ_TRANSCRIBE_MODEL", "whisper-large-v3-turbo")

        # Sınırlar
        self.max_analyze_minutes = _int("MAX_ANALYZE_MINUTES", 45)
        self.max_source_height = _int("MAX_SOURCE_HEIGHT", 720)
        self.max_download_mb = _int("MAX_DOWNLOAD_MB", 1500)
        self.max_upload_mb = _int("MAX_UPLOAD_MB", 2048)
        self.workers = max(1, _int("WORKERS", 2))
        self.allow_registration = _bool("ALLOW_REGISTRATION", True)
        self.cookie_secure = _bool("COOKIE_SECURE", False)
        self.caption_font = _env("CAPTION_FONT", "Inter")
        self.user_agent = _env(
            "USER_AGENT", "FutbolClipAI/1.0 (self-hosted; lisansli video arastirma)"
        )
        self.clip_min = 15
        self.clip_max = 60

    @property
    def max_analyze_s(self) -> float:
        return self.max_analyze_minutes * 60.0

    def _load_secret(self) -> str:
        from_env = _env("SECRET_KEY")
        if from_env:
            return from_env
        path = self.data_dir / "secret.key"
        if path.exists():
            return path.read_text().strip()
        key = secrets.token_hex(32)
        path.write_text(key)
        try:
            path.chmod(0o600)
        except OSError:
            pass
        return key


settings = Settings()


# --------------------------------------------------------------------------- #
# Servis durumu
# --------------------------------------------------------------------------- #
_ASS_CACHE: dict[str, bool] = {}


def ffmpeg_has_ass() -> bool:
    if "v" not in _ASS_CACHE:
        try:
            out = subprocess.run(
                ["ffmpeg", "-hide_banner", "-filters"], capture_output=True, text=True, timeout=20
            ).stdout
            _ASS_CACHE["v"] = " ass " in out
        except Exception:
            _ASS_CACHE["v"] = False
    return _ASS_CACHE["v"]


def has_ytdlp() -> bool:
    return bool(shutil.which("yt-dlp")) or has_module("yt_dlp")


def has_face_cascade() -> bool:
    try:
        import cv2  # type: ignore

        return (Path(cv2.data.haarcascades) / "haarcascade_frontalface_default.xml").exists()
    except Exception:
        return False


def transcription_providers(s: Settings = settings) -> dict[str, bool]:
    return {
        "faster_whisper": has_module("faster_whisper"),
        "groq": bool(s.groq_api_key),
        "openai": bool(s.openai_api_key),
    }


def capabilities(s: Settings = settings) -> dict:
    """Arayüzdeki 'Servis Durumu' sayfası için tüm bileşenlerin durumu."""
    tp = transcription_providers(s)
    ffm = bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))
    ytdlp = has_ytdlp()
    items: list[dict] = []

    def add(id_, grup, ad, hazir, aciklama, nasil="", zorunlu=False, desteklenmiyor=False):
        if desteklenmiyor:
            durum = "desteklenmiyor"
        elif hazir:
            durum = "hazir"
        else:
            durum = "eksik" if zorunlu else "istege_bagli"
        items.append(
            dict(id=id_, grup=grup, ad=ad, durum=durum, hazir=bool(hazir), aciklama=aciklama, nasil=nasil)
        )

    add("ffmpeg", "Temel", "FFmpeg + FFprobe", ffm,
        "Video kırpma, 9:16 dönüştürme, altyazı yakma ve MP4 dışa aktarma.",
        "Sunucuya FFmpeg kurun (ör. apt install ffmpeg).", zorunlu=True)
    add("libass", "Temel", "Altyazı yakma (libass)", ffm and ffmpeg_has_ass(),
        "Türkçe altyazıların videoya gömülmesi.", "libass destekli bir FFmpeg derlemesi kullanın.", zorunlu=True)

    add("youtube_api", "Video kaynakları", "YouTube Data API v3", bool(s.youtube_api_key),
        "Creative Commons lisanslı YouTube videolarını arar (resmî API).",
        "YOUTUBE_API_KEY ortam değişkenini tanımlayın (Google Cloud → YouTube Data API v3).")
    add("ytdlp", "Video kaynakları", "yt-dlp (YouTube indirme)", ytdlp,
        "Bulunan YouTube videosunu analiz için indirir. YouTube kullanım koşulları gereği yalnızca "
        "lisanslı/izinli içerikte kullanın.", "pip install yt-dlp")
    add("commons", "Video kaynakları", "Wikimedia Commons", True,
        "Özgür lisanslı videolar (anahtar gerekmez).")
    add("archive", "Video kaynakları", "Internet Archive", True,
        "Creative Commons / kamu malı videolar (anahtar gerekmez).")
    add("social", "Video kaynakları", "Instagram / TikTok / X araması", False,
        "Bu platformlar başkalarının videolarını arayıp indirmek için herkese açık bir API sunmuyor; "
        "kullanım koşulları da buna izin vermiyor. Kendi içeriğinizi 'Video yükle' ile ekleyin.",
        desteklenmiyor=True)

    add("fw", "Konuşma tanıma (Türkçe)", "faster-whisper (yerel)", tp["faster_whisper"],
        "Ücretsiz, çevrimdışı, kelime zamanlı Türkçe transkripsiyon.",
        "pip install faster-whisper (WHISPER_MODEL=small|medium|large-v3)")
    add("groq", "Konuşma tanıma (Türkçe)", "Groq Whisper API", tp["groq"],
        "Hızlı bulut transkripsiyonu (whisper-large-v3-turbo).", "GROQ_API_KEY tanımlayın.")
    add("openai", "Konuşma tanıma (Türkçe)", "OpenAI Whisper API", tp["openai"],
        "Bulut transkripsiyonu (whisper-1, kelime zamanlı).", "OPENAI_API_KEY tanımlayın.")
    add("subs", "Konuşma tanıma (Türkçe)", "SRT / VTT içe aktarma", True,
        "Hazır altyazı dosyanız varsa transkripsiyon servisine gerek kalmaz (yükleme ekranından eklenir).")

    add("llm", "Yapay zekâ", "Claude API (başlık ve viral puanı)", bool(s.anthropic_api_key),
        "Aday bölümleri yeniden puanlar, başlık ve hashtag üretir. Yoksa yerleşik sezgisel puanlama çalışır.",
        "ANTHROPIC_API_KEY tanımlayın (LLM_MODEL ile model seçilir).")
    add("diar", "Yapay zekâ", "Konuşmacı algılama (ses tabanlı)", has_module("sklearn") and has_module("scipy"),
        "MFCC + perde özellikleriyle konuşmacıları kümeler. Yaklaşık sonuç verir.",
        "pip install scikit-learn scipy", zorunlu=False)
    add("framing", "Yapay zekâ", "Akıllı kadraj (hareket/doku takibi)", has_module("numpy"),
        "16:9 görüntüde 9:16 kırpma penceresini hareketli bölgeye göre kaydırır.", "pip install numpy")
    add("face", "Yapay zekâ", "Yüz tabanlı kadraj (OpenCV)", has_face_cascade(),
        "Yüz algılama ile kadrajı konuşan kişiye kilitler.", "pip install opencv-python (cascade dosyalarıyla)")

    transkripsiyon = any(tp.values())
    ozet = {
        "arama_hazir": bool(s.youtube_api_key) or True,  # anahtarsız kaynaklar her zaman var
        "youtube_arama": bool(s.youtube_api_key),
        "youtube_indirme": ytdlp,
        "transkripsiyon": transkripsiyon,
        "llm": bool(s.anthropic_api_key),
        "tam_otomatik_youtube": bool(s.youtube_api_key) and ytdlp and transkripsiyon,
    }
    return {"hizmetler": items, "ozet": ozet}
