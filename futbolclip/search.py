"""Yapay zekâ video araştırmacısı: yalnızca lisanslı / izinli kaynaklarda arama.

Kaynaklar
- youtube : YouTube Data API v3, `videoLicense=creativeCommon` (CC BY). İzinli kanal listesi verilirse
            yalnızca o kanallarda (lisans filtresi olmadan) aranır — kullanıcı izin beyanı gerekir.
- commons : Wikimedia Commons (özgür lisanslı videolar) — anahtar gerekmez.
- archive : Internet Archive (Creative Commons / kamu malı) — anahtar gerekmez.

Instagram / TikTok / X için herkese açık arama+indirme API'si yoktur; desteklenmez.
"""
from __future__ import annotations

import html
import re
from dataclasses import asdict, dataclass, field
from typing import Optional

import requests

from .config import Settings
from .util import MissingService, ServiceError, tr_lower

STOP = {
    "maçı", "mac", "maç", "maçları", "açıklamaları", "açıklamalar", "açıklaması", "performansı", "performans",
    "değerlendirmesi", "değerlendirme", "yorumcuları", "yorumcular", "yorum", "yorumları", "sonu", "analizi",
    "analiz", "analizleri", "video", "videoları", "videolar", "son", "dakika", "hakkında", "ile", "ve", "bir",
    "bu", "için", "futbol", "özet", "özeti", "golleri",
}


@dataclass
class Found:
    source: str
    source_id: str
    title: str
    channel: str
    url: str
    duration: float = 0.0
    license: str = ""
    thumbnail: str = ""
    published: str = ""
    description: str = ""
    relevance: float = 0.0
    extra: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


def core_terms(query: str) -> list[str]:
    toks = re.findall(r"[\w']+", tr_lower(query))
    core = [t for t in toks if t not in STOP and len(t) > 1]
    return core or toks


def _relevance(f: Found, terms: list[str]) -> float:
    if not terms:
        return 0.0
    hay = tr_lower(f"{f.title} {f.channel} {f.description[:400]}")
    hits = sum(1 for t in terms if t[:5] in hay)
    return hits / len(terms)


def _get(url: str, params: Optional[dict], s: Settings, timeout=(6, 25)) -> requests.Response:
    try:
        return requests.get(url, params=params, headers={"User-Agent": s.user_agent}, timeout=timeout)
    except requests.RequestException as e:
        raise ServiceError(f"Ağ hatası ({url.split('/')[2]}): {e.__class__.__name__}") from e


def _strip_html(v: str) -> str:
    return html.unescape(re.sub(r"<[^>]+>", "", v or "")).strip()


# --------------------------------------------------------------------------- #
# YouTube
# --------------------------------------------------------------------------- #
_ISO = re.compile(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?")


def _iso_seconds(v: str) -> float:
    m = _ISO.fullmatch(v or "")
    if not m:
        return 0.0
    h, mi, se = (int(x or 0) for x in m.groups())
    return h * 3600 + mi * 60 + se


def _yt_error(r: requests.Response) -> ServiceError:
    try:
        err = r.json()["error"]
        reason = (err.get("errors") or [{}])[0].get("reason", "")
        msg = err.get("message", "")
    except Exception:
        reason, msg = "", r.text[:120]
    if reason in {"quotaExceeded", "dailyLimitExceeded"}:
        return ServiceError("YouTube API günlük kotası doldu (yarın yeniden deneyin ya da kotayı artırın).")
    if reason in {"keyInvalid", "badRequest"} or r.status_code in (400, 403):
        return ServiceError(f"YouTube API reddetti: {msg or reason}")
    return ServiceError(f"YouTube API hatası ({r.status_code}).")


def youtube_search(query: str, s: Settings, limit: int, allowed_channels: Optional[list[str]] = None) -> list[Found]:
    if not s.youtube_api_key:
        raise MissingService("YouTube Data API", "YOUTUBE_API_KEY tanımlı değil.")
    allowed = [c.strip() for c in (allowed_channels or []) if c.strip()]
    ids: list[str] = []
    base = {
        "part": "snippet", "type": "video", "q": query, "maxResults": 25,
        "relevanceLanguage": "tr", "regionCode": "TR", "order": "relevance", "key": s.youtube_api_key,
    }
    if allowed:
        for ch in allowed[:6]:
            r = _get("https://www.googleapis.com/youtube/v3/search", {**base, "channelId": ch, "maxResults": 10}, s)
            if r.status_code != 200:
                raise _yt_error(r)
            ids += [i["id"]["videoId"] for i in r.json().get("items", []) if i.get("id", {}).get("videoId")]
    else:
        r = _get("https://www.googleapis.com/youtube/v3/search", {**base, "videoLicense": "creativeCommon"}, s)
        if r.status_code != 200:
            raise _yt_error(r)
        ids = [i["id"]["videoId"] for i in r.json().get("items", []) if i.get("id", {}).get("videoId")]
    if not ids:
        return []
    r = _get(
        "https://www.googleapis.com/youtube/v3/videos",
        {"part": "snippet,contentDetails,status,statistics", "id": ",".join(dict.fromkeys(ids)), "key": s.youtube_api_key},
        s,
    )
    if r.status_code != 200:
        raise _yt_error(r)
    out: list[Found] = []
    for it in r.json().get("items", []):
        sn, st = it.get("snippet", {}), it.get("status", {})
        lic = st.get("license", "")
        by_allow = bool(allowed) and sn.get("channelId") in allowed
        if lic != "creativeCommon" and not by_allow:
            continue  # lisans doğrulanamayan içerik asla alınmaz
        if sn.get("liveBroadcastContent", "none") != "none":
            continue
        dur = _iso_seconds(it.get("contentDetails", {}).get("duration", ""))
        if dur < 45:
            continue
        thumbs = sn.get("thumbnails", {})
        thumb = (thumbs.get("high") or thumbs.get("medium") or thumbs.get("default") or {}).get("url", "")
        out.append(Found(
            source="youtube", source_id=it["id"], title=sn.get("title", ""), channel=sn.get("channelTitle", ""),
            url=f"https://www.youtube.com/watch?v={it['id']}", duration=dur,
            license="CC BY (YouTube)" if lic == "creativeCommon" else "Kanal sahibi izni (kullanıcı beyanı)",
            thumbnail=thumb, published=sn.get("publishedAt", "")[:10], description=sn.get("description", ""),
            extra={"views": int(it.get("statistics", {}).get("viewCount", 0) or 0), "channel_id": sn.get("channelId")},
        ))
    return out


def youtube_video_info(video_id: str, s: Settings) -> Optional[Found]:
    """Tek bir video için lisans ve bilgi (URL ile eklenen videoların lisans kontrolü)."""
    if not s.youtube_api_key:
        return None
    r = _get(
        "https://www.googleapis.com/youtube/v3/videos",
        {"part": "snippet,contentDetails,status", "id": video_id, "key": s.youtube_api_key}, s,
    )
    if r.status_code != 200 or not r.json().get("items"):
        return None
    it = r.json()["items"][0]
    sn = it["snippet"]
    return Found(
        source="youtube", source_id=video_id, title=sn.get("title", ""), channel=sn.get("channelTitle", ""),
        url=f"https://www.youtube.com/watch?v={video_id}",
        duration=_iso_seconds(it.get("contentDetails", {}).get("duration", "")),
        license="CC BY (YouTube)" if it.get("status", {}).get("license") == "creativeCommon" else "Standart YouTube Lisansı",
        thumbnail=(sn.get("thumbnails", {}).get("high") or {}).get("url", ""),
        extra={"channel_id": sn.get("channelId")},
    )


# --------------------------------------------------------------------------- #
# Wikimedia Commons
# --------------------------------------------------------------------------- #
def commons_search(query: str, s: Settings, limit: int) -> list[Found]:
    r = _get("https://commons.wikimedia.org/w/api.php", {
        "action": "query", "format": "json", "generator": "search", "gsrnamespace": 6,
        "gsrsearch": f"{query} filetype:video", "gsrlimit": min(30, limit * 3),
        "prop": "imageinfo", "iiprop": "url|size|mime|extmetadata|mediatype", "iiurlwidth": 640,
        "iiextmetadatafilter": "LicenseShortName|Artist|ImageDescription|DateTimeOriginal",
    }, s)
    if r.status_code != 200:
        raise ServiceError(f"Wikimedia Commons hatası ({r.status_code}).")
    pages = (r.json().get("query") or {}).get("pages") or {}
    out: list[Found] = []
    for p in sorted(pages.values(), key=lambda x: x.get("index", 0)):
        ii = (p.get("imageinfo") or [{}])[0]
        if ii.get("mediatype") not in {"VIDEO", None} and not str(ii.get("mime", "")).startswith("video"):
            continue
        md = ii.get("extmetadata", {})
        dur = float(ii.get("duration") or 0)
        if dur and dur < 20:
            continue
        title = p.get("title", "").removeprefix("File:").rsplit(".", 1)[0].replace("_", " ")
        out.append(Found(
            source="commons", source_id=str(p.get("pageid")), title=title,
            channel=_strip_html(md.get("Artist", {}).get("value", "")) or "Wikimedia Commons",
            url=ii.get("descriptionurl") or ii.get("url", ""), duration=dur,
            license=_strip_html(md.get("LicenseShortName", {}).get("value", "")) or "Özgür lisans",
            thumbnail=ii.get("thumburl", ""), description=_strip_html(md.get("ImageDescription", {}).get("value", "")),
            extra={"file_url": ii.get("url"), "size": ii.get("size", 0)},
        ))
    return out


# --------------------------------------------------------------------------- #
# Internet Archive
# --------------------------------------------------------------------------- #
def archive_search(query: str, s: Settings, limit: int) -> list[Found]:
    terms = " AND ".join(core_terms(query)[:4])
    q = (
        f"({terms}) AND mediatype:(movies) AND "
        "(licenseurl:(*creativecommons.org*) OR licenseurl:(*publicdomain*))"
    )
    r = _get("https://archive.org/advancedsearch.php", {
        "q": q, "fl[]": ["identifier", "title", "creator", "licenseurl", "description"],
        "rows": min(30, limit * 2), "output": "json",
    }, s)
    if r.status_code != 200:
        raise ServiceError(f"Internet Archive hatası ({r.status_code}).")
    out: list[Found] = []
    for d in (r.json().get("response") or {}).get("docs", []):
        lic = d.get("licenseurl", "")
        if isinstance(lic, list):
            lic = lic[0] if lic else ""
        creator = d.get("creator", "")
        if isinstance(creator, list):
            creator = ", ".join(creator)
        desc = d.get("description", "")
        if isinstance(desc, list):
            desc = " ".join(desc)
        out.append(Found(
            source="archive", source_id=d["identifier"], title=_strip_html(str(d.get("title", d["identifier"]))),
            channel=creator or "Internet Archive", url=f"https://archive.org/details/{d['identifier']}",
            license=("Kamu malı" if "publicdomain" in lic else "Creative Commons"),
            thumbnail=f"https://archive.org/services/img/{d['identifier']}", description=_strip_html(str(desc)),
            extra={"license_url": lic},
        ))
    return out


# --------------------------------------------------------------------------- #
# Toplu arama
# --------------------------------------------------------------------------- #
SOURCE_LABEL = {"youtube": "YouTube", "commons": "Wikimedia Commons", "archive": "Internet Archive"}
_PRIORITY = {"youtube": 0, "archive": 1, "commons": 2}


def search_all(
    query: str, s: Settings, *, sources: list[str], limit: int = 6, allowed_channels: Optional[list[str]] = None,
) -> tuple[list[Found], list[str]]:
    terms = core_terms(query)
    warnings: list[str] = []
    pool: dict[tuple[str, str], Found] = {}

    def run(name: str, fn):
        try:
            res = fn()
            if not res and name != "youtube":
                # daha geniş: yalnızca çekirdek terimler
                core = " ".join(terms)
                if core and core != tr_lower(query):
                    res = fn_alt(name, core)
        except MissingService as e:
            warnings.append(f"{SOURCE_LABEL.get(name, name)} atlandı: {e.hint}")
            return
        except ServiceError as e:
            warnings.append(f"{SOURCE_LABEL.get(name, name)}: {e}")
            return
        if not res:
            warnings.append(f"{SOURCE_LABEL.get(name, name)}: lisanslı sonuç bulunamadı.")
        for f in res:
            pool.setdefault((f.source, f.source_id), f)

    def fn_alt(name: str, q: str) -> list[Found]:
        if name == "commons":
            return commons_search(q, s, limit)
        if name == "archive":
            return archive_search(q, s, limit)
        return []

    for name in sources:
        if name == "youtube":
            run(name, lambda: youtube_search(query, s, limit, allowed_channels))
        elif name == "commons":
            run(name, lambda: commons_search(query, s, limit))
        elif name == "archive":
            run(name, lambda: archive_search(query, s, limit))

    found = list(pool.values())
    for f in found:
        f.relevance = _relevance(f, terms)
    found.sort(key=lambda f: (-(f.relevance > 0), _PRIORITY.get(f.source, 9), -f.relevance))
    return found[: max(limit, 1)], warnings
