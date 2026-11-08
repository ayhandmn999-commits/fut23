"""Bölüm seçimi: transkript + ses enerjisi + Türkçe futbol sözlüğü ile en dikkat çekici 15–60 sn'lik pencereler.

Puan bileşenleri (0..1): heyecan, anahtar kelime yoğunluğu, konuşma temposu, vurgu (!/?), açılış kancası,
konu ilgisi, sınır temizliği (cümle başı/sonu), süre uygunluğu. İsteğe bağlı olarak Claude ile yeniden puanlanır.
"""
from __future__ import annotations

import json
import math
import re
from collections import Counter
from typing import Optional

import numpy as np
import requests

from .audio import HOP
from .config import Settings
from .search import core_terms
from .util import ServiceError, tr_capital, tr_lower, tr_upper

# (kategori, ağırlık, kalıplar) — tek kelimeler önek olarak (ek alabilir), boşluklu olanlar ifade olarak aranır.
LEXICON: list[tuple[str, float, list[str]]] = [
    ("Gol", 3.0, ["gol", "ağları salladı", "röveşata", "frikik", "kafa vuruşu", "öne geçti", "beraberliği buldu",
                  "skoru değiştirdi", "dakikada attı", "golü attı"]),
    ("Penaltı / VAR", 2.8, ["penaltı", "ofsayt", "var kaydı", "var hakemi", "var inceleme", "var odası", "var kararı",
                            "el ile oynama", "hakem hatası", "pozisyon yok"]),
    ("Kart", 2.6, ["kırmızı kart", "sarı kart", "ikinci sarı", "oyundan atıldı", "kart gördü", "ihraç"]),
    ("Hakem", 1.6, ["hakem", "faul", "düdük", "skandal", "haksız"]),
    ("Heyecan", 2.0, ["inanılmaz", "müthiş", "muhteşem", "harika", "olağanüstü", "şok", "tarihi", "efsane", "bomba",
                      "korkunç", "utanç", "rezil", "rezalet", "çılgın", "mucize", "dehşet", "hayal kırıklığı",
                      "gurur", "büyük başarı", "kabus", "çok büyük"]),
    ("Transfer", 2.0, ["transfer", "bonservis", "imza attı", "sözleşme", "milyon euro", "teklif", "kiralık", "ayrılık"]),
    ("Şampiyonluk", 2.0, ["şampiyon", "kupa", "derbi", "averaj", "zirve", "play-off", "lider", "puan farkı",
                          "finale", "elendi", "avrupa"]),
    ("Tartışma", 1.4, ["açıklama", "itiraf", "tepki", "eleştiri", "suçladı", "istifa", "kriz", "kavga", "gerginlik",
                       "iddia", "gerçek şu ki", "açık konuşayım", "kesinlikle", "asla", "yanlış", "ayıp"]),
    ("Performans", 1.3, ["performans", "form", "yıldız", "asist", "çalım", "şut", "kurtardı", "kaleci", "sakatlık",
                         "sakatlandı", "direkten", "tempo", "oyun zekası", "yetenek"]),
    ("Taktik", 1.0, ["taktik", "diziliş", "pres", "savunma", "hücum", "orta saha", "forvet", "hoca"]),
]

ENTITIES = [
    "galatasaray", "fenerbahçe", "beşiktaş", "trabzonspor", "başakşehir", "real madrid", "barcelona", "manchester",
    "liverpool", "bayern", "arda güler", "hakan çalhanoğlu", "kenan yıldız", "icardi", "mourinho", "okan buruk",
    "ronaldo", "messi", "mbappé", "haaland", "milli takım", "süper lig", "şampiyonlar ligi", "avrupa ligi", "tff",
    "osimhen", "montella", "tedesco",
]

_SINGLE: list[tuple[str, str, float]] = []
_PHRASE: list[tuple[str, str, float]] = []
for _cat, _w, _pats in LEXICON:
    for _p in _pats:
        _p = tr_lower(_p)
        (_PHRASE if " " in _p else _SINGLE).append((_p, _cat, _w))
_TOKEN = re.compile(r"\w+", re.U)
_CONJ = {"ve", "ama", "yani", "işte", "çünkü", "ki", "de", "da", "fakat", "ancak", "ya", "hem", "veya", "ise"}
_END = re.compile(r"[.!?…][\"')\]]*$")


def lex_hits(text: str) -> tuple[float, dict[str, list[str]]]:
    t = tr_lower(text)
    cnt: Counter[tuple[str, str, float]] = Counter()
    for p, cat, w in _PHRASE:
        if p in t:
            cnt[(p, cat, w)] += 1
    for tok in _TOKEN.findall(t):
        for p, cat, w in _SINGLE:
            if tok.startswith(p) and len(tok) - len(p) <= 4:
                cnt[(p, cat, w)] += 1
                break
    total, hits = 0.0, {}
    for (p, cat, w), n in cnt.items():
        total += w * min(n, 3)
        hits.setdefault(cat, []).append(p)
    return total, hits


def entities_in(text: str) -> list[str]:
    t = tr_lower(text)
    return [e for e in ENTITIES if e in t]


def _hashtag(ent: str) -> str:
    return "#" + "".join(tr_capital(w) for w in ent.split())


# --------------------------------------------------------------------------- #
# Aday pencereler
# --------------------------------------------------------------------------- #
def _sigmoid01(x: float, mid: float, scale: float) -> float:
    return 1 / (1 + math.exp(-(x - mid) / scale))


def find_candidates(
    segs: list[dict], exc: np.ndarray, query: str = "", *, min_s: float = 15, max_s: float = 60,
    per_video: int = 12, hop: float = HOP,
) -> list[dict]:
    n = len(segs)
    if n == 0:
        return []
    qstems = [t[:5] for t in core_terms(query)] if query else []
    F = []
    for g in segs:
        text = g["text"]
        lx, hits = lex_hits(text)
        toks = {t[:5] for t in _TOKEN.findall(tr_lower(text))}
        F.append({
            "lex": lx, "hits": hits, "words": len(text.split()), "excl": text.count("!"), "q": text.count("?"),
            "qmask": sum(1 << i for i, s in enumerate(qstems) if s in toks), "dur": g["end"] - g["start"],
        })
    pre = {k: np.concatenate([[0.0], np.cumsum([f[k] for f in F])]) for k in ("lex", "words", "excl", "q", "dur")}

    cands: list[dict] = []
    for i in range(n):
        for j in range(i, n):
            start, end = segs[i]["start"], segs[j]["end"]
            dur = end - start
            if dur > max_s:
                break
            if dur < min_s:
                continue
            if j > i and any(segs[k + 1]["start"] - segs[k]["end"] > 5.0 for k in range(i, j)):
                continue
            speech = pre["dur"][j + 1] - pre["dur"][i]
            if speech / dur < 0.55:
                continue
            a, b = int(start / hop), max(int(start / hop) + 1, int(math.ceil(end / hop)))
            e = exc[a:b] if a < len(exc) else np.zeros(1)
            if len(e) == 0:
                e = np.zeros(1)
            top = float(np.mean(np.sort(e)[-max(1, len(e) // 4):]))
            lex = pre["lex"][j + 1] - pre["lex"][i]
            words = pre["words"][j + 1] - pre["words"][i]
            emph = (pre["excl"][j + 1] - pre["excl"][i]) + 0.5 * (pre["q"][j + 1] - pre["q"][i])
            # Açılış kancası: ilk 4 sn
            hook_lex, hook_q = 0.0, 0
            for k in range(i, j + 1):
                if segs[k]["start"] > start + 4:
                    break
                hook_lex += F[k]["lex"]
                hook_q += F[k]["q"] + F[k]["excl"]
            mask = 0
            for k in range(i, j + 1):
                mask |= F[k]["qmask"]
            rel = (bin(mask).count("1") / len(qstems)) if qstems else 0.0
            first = segs[i]["text"].strip()
            first_tok = tr_lower(first.split()[0]) if first.split() else ""
            prev_gap = (start - segs[i - 1]["end"]) if i > 0 else 1.0
            clean_start = 0.5 if (prev_gap >= 0.35 or (i > 0 and _END.search(segs[i - 1]["text"].strip()))) and \
                first_tok not in _CONJ else (0.2 if first_tok not in _CONJ else 0.0)
            clean_end = 0.5 if _END.search(segs[j]["text"].strip()) else 0.15

            comp = {
                "heyecan": float(np.clip((top + 0.4) / 3.2, 0, 1)),
                "kelime": 1 - math.exp(-(lex / (dur / 10)) / 4.0),
                "tempo": math.exp(-(((words / dur) - 3.2) / 1.4) ** 2),
                "vurgu": 1 - math.exp(-(emph / (dur / 10)) / 1.5),
                "kanca": min(1.0, hook_lex / 3.0 + 0.3 * min(hook_q, 2)),
                "ilgi": rel,
                "sinir": clean_start + clean_end,
                "sure": math.exp(-((dur - 32) / 18) ** 2),
            }
            w = {"heyecan": .24, "kelime": .24, "tempo": .08, "vurgu": .07, "kanca": .10, "ilgi": .11, "sinir": .08,
                 "sure": .08}
            if not qstems:
                w.pop("ilgi")
            tot = sum(w.values())
            score = 100 * sum(comp[k] * v for k, v in w.items()) / tot
            cands.append({"i": i, "j": j, "start": start, "end": end, "score": score, "components": comp})

    # Çakışmayanları seç (non-max suppression)
    cands.sort(key=lambda c: -c["score"])
    chosen: list[dict] = []
    for c in cands:
        ok = True
        for d in chosen:
            overlap = min(c["end"], d["end"]) - max(c["start"], d["start"])
            if overlap > 0.25 * min(c["end"] - c["start"], d["end"] - d["start"]):
                ok = False
                break
        if ok:
            chosen.append(c)
        if len(chosen) >= per_video:
            break
    return [_finish(c, segs, F, query) for c in chosen]


def _finish(c: dict, segs: list[dict], F: list[dict], query: str) -> dict:
    i, j = c["i"], c["j"]
    window = segs[i: j + 1]
    words = [w for g in window for w in (g.get("words") or [])]
    start = max(0.0, (words[0]["s"] if words else window[0]["start"]) - 0.2)
    end = (words[-1]["e"] if words else window[-1]["end"]) + 0.4
    text = " ".join(g["text"] for g in window)

    cat_score: Counter[str] = Counter()
    for k in range(i, j + 1):
        for cat, terms in F[k]["hits"].items():
            cat_score[cat] += len(terms)
    tags = [cat for cat, _ in cat_score.most_common(3)]
    terms = [t for k in range(i, j + 1) for ts in F[k]["hits"].values() for t in ts]
    ents = entities_in(text)

    # Başlık: penceredeki en çarpıcı cümle
    best = max(range(i, j + 1), key=lambda k: F[k]["lex"] + 0.6 * F[k]["excl"] + 0.3 * F[k]["q"])
    title = _title(segs[best]["text"] if F[best]["lex"] or F[best]["excl"] else segs[i]["text"])

    comp = c["components"]
    why = []
    if comp["heyecan"] > 0.55:
        why.append("Ses enerjisi yüksek (heyecanlı anlatım)")
    if terms:
        why.append("Kritik kelimeler: " + ", ".join(dict.fromkeys(terms[:4])))
    if comp["kanca"] > 0.45:
        why.append("güçlü açılış")
    if comp["ilgi"] > 0.5:
        why.append("aranan konuyla doğrudan ilgili")
    if comp["tempo"] > 0.75:
        why.append("tempolu konuşma")
    reason = " • ".join(why) or "Dengeli, bütünlüklü bir konuşma bölümü"

    hashtags = ["#futbol"] + [_hashtag(e) for e in ents[:3]]
    if any(e in ENTITIES[:5] + ["süper lig", "tff"] for e in ents):
        hashtags.insert(1, "#SüperLig")
    speakers = sorted({g["speaker"] for g in window if g.get("speaker") is not None})
    return {
        "start": round(start, 2), "end": round(end, 2), "score": round(c["score"], 1), "components": comp,
        "tags": tags, "hashtags": list(dict.fromkeys(hashtags)), "reason": reason, "title": title, "text": text,
        "speakers": speakers,
    }


def _title(s: str, maxlen: int = 72) -> str:
    s = s.strip()
    toks = s.split()
    if toks and tr_lower(toks[0]) in _CONJ:
        toks = toks[1:]
    s = " ".join(toks).strip(" ,;:-")
    s = re.sub(r"[.…]+$", "", s)
    if len(s) > maxlen:
        s = s[:maxlen].rsplit(" ", 1)[0].rstrip(",;:- ") + "…"
    return tr_capital(s) or "Öne çıkan bölüm"


# --------------------------------------------------------------------------- #
# Seçim ve çeşitlilik
# --------------------------------------------------------------------------- #
def select_final(cands: list[dict], count: int, per_video_cap: Optional[int] = None) -> list[dict]:
    cands = sorted(cands, key=lambda c: -c["score"])
    videos = {c.get("video_id") for c in cands}
    cap = per_video_cap or (count if len(videos) <= 1 else max(2, math.ceil(count * 0.6)))
    # Aynı videodan çakışan klip olmasın. Sahne hizalaması uçları ≤0,8 sn kaydırabildiğinden 1,7 sn boşluk bırakılır.
    gap = 1.7

    def clashes(c: dict, chosen: list[dict]) -> bool:
        return any(
            o.get("video_id") == c.get("video_id") and c["start"] < o["end"] + gap and c["end"] + gap > o["start"]
            for o in chosen
        )

    out, per = [], Counter()
    for c in cands:
        if per[c.get("video_id")] >= cap or clashes(c, out):
            continue
        out.append(c)
        per[c.get("video_id")] += 1
        if len(out) >= count:
            break
    if len(out) < count:  # video başına kota dolduysa kalanlarla tamamla (yine çakışmadan)
        for c in cands:
            if c not in out and not clashes(c, out):
                out.append(c)
            if len(out) >= count:
                break
    return out


# --------------------------------------------------------------------------- #
# Claude ile yeniden puanlama (isteğe bağlı)
# --------------------------------------------------------------------------- #
def llm_refine(cands: list[dict], query: str, s: Settings) -> None:
    """Başlık, viral puanı ve hashtag üretir; cands yerinde güncellenir. Hata → ServiceError."""
    if not s.anthropic_api_key or not cands:
        return
    items = [{"id": i, "sure_sn": round(c["end"] - c["start"]), "sezgisel_puan": round(c["score"]),
              "metin": c["text"][:700]} for i, c in enumerate(cands)]
    prompt = (
        "Sen Türkçe futbol içerikleri için çalışan deneyimli bir sosyal medya video editörüsün. "
        f"Kullanıcının aradığı konu: «{query or 'belirtilmedi'}».\n"
        "Aşağıda bir video transkriptinden çıkarılmış aday kısa video bölümleri var. Her biri için TikTok/Reels/Shorts "
        "performansı açısından 0-100 arası 'viral_skoru' ver (güçlü kanca, duygu, tartışma/merak, bütünlük, konu ilgisi). "
        "Ayrıca en fazla 70 karakterlik, merak uyandıran ama YANILTMAYAN bir Türkçe 'baslik' yaz; başlık yalnızca "
        "metinde gerçekten söylenenlere dayansın, uydurma bilgi ekleme. 'neden' alanına tek cümlelik gerekçe, "
        "'etiketler' alanına 3-5 hashtag yaz.\n"
        "YALNIZCA JSON dizisi döndür: [{\"id\":0,\"viral_skoru\":0,\"baslik\":\"\",\"neden\":\"\",\"etiketler\":[\"#...\"]}]\n\n"
        + json.dumps(items, ensure_ascii=False)
    )
    try:
        r = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={"x-api-key": s.anthropic_api_key, "anthropic-version": "2023-06-01", "content-type": "application/json"},
            json={"model": s.llm_model, "max_tokens": 4000, "messages": [{"role": "user", "content": prompt}]},
            timeout=(10, 120),
        )
    except requests.RequestException as e:
        raise ServiceError(f"Claude API'ye ulaşılamadı ({e.__class__.__name__}).") from e
    if r.status_code != 200:
        raise ServiceError(f"Claude API hatası ({r.status_code}): {r.text[:140]}")
    text = "".join(b.get("text", "") for b in r.json().get("content", []) if b.get("type") == "text")
    m = re.search(r"\[.*\]", text, re.S)
    if not m:
        raise ServiceError("Claude yanıtı JSON olarak okunamadı.")
    try:
        data = json.loads(m.group(0))
    except ValueError as e:
        raise ServiceError("Claude yanıtı geçersiz JSON.") from e
    for d in data:
        try:
            c = cands[int(d["id"])]
        except (KeyError, ValueError, IndexError):
            continue
        v = d.get("viral_skoru")
        if isinstance(v, (int, float)):
            c["score"] = round(0.5 * c["score"] + 0.5 * float(max(0, min(100, v))), 1)
        if d.get("baslik"):
            c["title"] = str(d["baslik"]).strip()[:90]
        if d.get("neden"):
            c["reason"] = str(d["neden"]).strip()
        tags = [t if t.startswith("#") else "#" + t for t in d.get("etiketler", []) if isinstance(t, str) and t.strip()]
        if tags:
            c["hashtags"] = list(dict.fromkeys(["#futbol", *tags]))[:6]


# --------------------------------------------------------------------------- #
# Altyazı (caption) üretimi
# --------------------------------------------------------------------------- #
def build_captions(segs: list[dict], start: float, end: float, *, max_words: int = 3, max_chars: int = 20) -> list[dict]:
    """Klip aralığındaki kelimelerden kısa altyazı blokları (klip başlangıcına göre saniye)."""
    words = []
    for g in segs:
        if g["end"] < start or g["start"] > end:
            continue
        for w in g.get("words") or []:
            if w["s"] >= start - 0.05 and w["e"] <= end + 0.3:
                words.append({**w, "sp": g.get("speaker")})
    caps: list[dict] = []
    cur: list[dict] = []

    def flush():
        if not cur:
            return
        sp = Counter(w["sp"] for w in cur).most_common(1)[0][0]
        caps.append({
            "start": round(max(0.0, cur[0]["s"] - start), 2), "end": round(max(0.1, cur[-1]["e"] - start), 2),
            "text": " ".join(w["t"] for w in cur), "speaker": sp,
            "words": [{"s": round(w["s"] - start, 2), "e": round(w["e"] - start, 2), "t": w["t"]} for w in cur],
        })
        cur.clear()

    for w in words:
        if cur:
            gap = w["s"] - cur[-1]["e"]
            chars = sum(len(x["t"]) + 1 for x in cur) + len(w["t"])
            if len(cur) >= max_words or chars > max_chars or gap > 0.7 or w["sp"] != cur[-1]["sp"] or \
                    (len(cur) >= 2 and re.search(r"[.!?…,;:]$", cur[-1]["t"])):
                flush()
        cur.append(w)
    flush()
    for a, b in zip(caps, caps[1:]):  # küçük boşlukları kapat, çakışmayı önle
        if b["start"] - a["end"] < 0.35:
            a["end"] = max(a["end"], round(b["start"], 2))
        a["end"] = min(a["end"], b["start"])
    return caps


def retime_caption(cap: dict) -> dict:
    """Kullanıcı metni düzenlediyse kelime zamanlarını yeniden dağıtır."""
    from .transcribe import synth_words

    old = " ".join(w["t"] for w in cap.get("words", []))
    if old == cap["text"].strip() and cap.get("words"):
        return cap
    cap = dict(cap)
    cap["text"] = re.sub(r"\s+", " ", cap["text"]).strip()
    cap["words"] = synth_words(cap["start"], cap["end"], cap["text"])
    return cap
