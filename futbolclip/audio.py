"""Ses analizi: heyecan eğrisi (ses enerjisi + parlaklık) ve hafif konuşmacı algılama."""
from __future__ import annotations

import subprocess
from typing import Optional

import numpy as np

SR = 16000
HOP = 0.5  # saniye


def load_pcm(path: str, sr: int = SR, duration: Optional[float] = None) -> np.ndarray:
    cmd = ["ffmpeg", "-v", "error", "-i", str(path), "-vn", "-ac", "1", "-ar", str(sr)]
    if duration:
        cmd += ["-t", f"{duration:.2f}"]
    cmd += ["-f", "s16le", "-"]
    p = subprocess.run(cmd, capture_output=True)
    if p.returncode != 0 or not p.stdout:
        return np.zeros(0, dtype=np.float32)
    return np.frombuffer(p.stdout, dtype=np.int16).astype(np.float32) / 32768.0


def _z(x: np.ndarray, floor: float) -> np.ndarray:
    return (x - np.median(x)) / max(float(np.std(x)), floor)


def excitement_curve(y: np.ndarray, sr: int = SR, hop: float = HOP) -> np.ndarray:
    """0.5 sn'lik çerçeveler için heyecan skoru (z-skor benzeri; ~ -2 .. +4).

    Yorumcu heyecanlandığında hem ses şiddeti hem de 1–4 kHz bandı payı artar; ikisi birleştirilir.
    """
    frame = int(sr * hop)
    n = len(y) // frame
    if n < 4:
        return np.zeros(max(n, 1), dtype=np.float32)
    X = y[: n * frame].reshape(n, frame)
    rms = np.sqrt((X ** 2).mean(axis=1) + 1e-10)
    db = 20 * np.log10(rms + 1e-8)
    spec = np.abs(np.fft.rfft(X * np.hanning(frame), axis=1)) ** 2
    freqs = np.fft.rfftfreq(frame, 1 / sr)
    band = (freqs >= 1000) & (freqs < 4000)
    hf = spec[:, band].sum(axis=1) / (spec.sum(axis=1) + 1e-9)

    # Yerel (≈60 sn) taban çizgisine göre: programın sesi genel olarak yüksek olsa bile anlık yükselişler öne çıkar.
    from scipy.ndimage import median_filter

    base = median_filter(db, size=121, mode="nearest")
    local = (db - base) / max(float(np.std(db)), 3.0)
    glob = _z(db, 3.0)
    e = 0.5 * local + 0.3 * glob + 0.2 * _z(hf, 0.05)
    e[db < -52] = -2.0  # sessizlik
    return np.clip(e, -2.0, 4.0).astype(np.float32)


# --------------------------------------------------------------------------- #
# Konuşmacı algılama (hafif, yaklaşık)
# --------------------------------------------------------------------------- #
_MEL: dict[tuple, np.ndarray] = {}


def _mel_fb(n_mels: int, n_fft: int, sr: int) -> np.ndarray:
    key = (n_mels, n_fft, sr)
    if key in _MEL:
        return _MEL[key]
    f2m = lambda f: 2595 * np.log10(1 + f / 700)
    m2f = lambda m: 700 * (10 ** (m / 2595) - 1)
    pts = m2f(np.linspace(f2m(60), f2m(sr / 2), n_mels + 2))
    bins = np.floor((n_fft + 1) * pts / sr).astype(int)
    fb = np.zeros((n_mels, n_fft // 2 + 1))
    for i in range(1, n_mels + 1):
        l, c, r = bins[i - 1], bins[i], bins[i + 1]
        for k in range(l, c):
            fb[i - 1, k] = (k - l) / max(c - l, 1)
        for k in range(c, r):
            fb[i - 1, k] = (r - k) / max(r - c, 1)
    _MEL[key] = fb
    return fb


def _f0(frames: np.ndarray, sr: int) -> float:
    """Otokorelasyonla basit perde tahmini (medyan, Hz). Bulunamazsa 0."""
    vals = []
    lo, hi = int(sr / 400), int(sr / 70)
    for fr in frames:
        fr = fr - fr.mean()
        if np.max(np.abs(fr)) < 0.01:
            continue
        ac = np.correlate(fr, fr, mode="full")[len(fr) - 1:]
        if ac[0] <= 0:
            continue
        seg = ac[lo:hi]
        lag = int(np.argmax(seg)) + lo
        if ac[lag] / ac[0] > 0.3:
            vals.append(sr / lag)
    return float(np.median(vals)) if vals else 0.0


def _features(x: np.ndarray, sr: int) -> Optional[np.ndarray]:
    from scipy.fft import dct

    if len(x) < sr * 0.6:
        return None
    n_fft, hop = 512, 160
    x = np.append(x[0], x[1:] - 0.97 * x[:-1])
    n = 1 + (len(x) - n_fft) // hop
    if n < 10:
        return None
    idx = np.arange(n_fft)[None, :] + hop * np.arange(n)[:, None]
    fr = x[idx] * np.hanning(n_fft)
    power = (np.abs(np.fft.rfft(fr, axis=1)) ** 2) / n_fft
    energy = power.sum(axis=1)
    keep = energy > np.percentile(energy, 40)
    if keep.sum() < 8:
        return None
    logmel = np.log(power[keep] @ _mel_fb(26, n_fft, sr).T + 1e-10)
    mfcc = dct(logmel, type=2, axis=1, norm="ortho")[:, 1:14]
    voiced = x[idx][keep][:: max(1, int(keep.sum() // 40))]
    f0 = _f0(voiced[:40], sr)
    return np.concatenate([mfcc.mean(axis=0), mfcc.std(axis=0), [np.log(f0 + 1.0) * 2.0]])


def diarize(y: np.ndarray, segments: list[dict], sr: int = SR, max_speakers: int = 4) -> list[int]:
    """Her segment için konuşmacı indeksi (0..k-1). Kesin değil; MFCC + perde kümelemesi."""
    n = len(segments)
    zeros = [0] * n
    try:
        from sklearn.cluster import AgglomerativeClustering  # type: ignore
        from sklearn.metrics import silhouette_score  # type: ignore
    except Exception:
        return zeros
    feats: list[Optional[np.ndarray]] = []
    for g in segments:
        a, b = int(g["start"] * sr), int(g["end"] * sr)
        feats.append(_features(y[a:b], sr) if b - a >= sr * 0.9 else None)
    ids = [i for i, f in enumerate(feats) if f is not None]
    if len(ids) < 6:
        return zeros
    X = np.stack([feats[i] for i in ids])  # type: ignore[misc]
    X = (X - X.mean(axis=0)) / (X.std(axis=0) + 1e-6)
    best_k, best_s, best_lab = 1, 0.0, None
    for k in range(2, min(max_speakers, len(ids) // 3) + 1):
        lab = AgglomerativeClustering(n_clusters=k, linkage="ward").fit_predict(X)
        counts = np.bincount(lab, minlength=k)
        if counts.min() < max(3, 0.08 * len(ids)):
            continue
        sc = float(silhouette_score(X, lab))
        if sc > best_s:
            best_k, best_s, best_lab = k, sc, lab
    if best_lab is None or best_s < 0.14:
        return zeros
    cent = np.stack([X[best_lab == c].mean(axis=0) for c in range(best_k)])
    labels = [-1] * n
    for pos, i in enumerate(ids):
        labels[i] = int(best_lab[pos])
    for i in range(n):  # kısa/özelliksiz segmentler: en yakın komşunun etiketi
        if labels[i] == -1:
            left = next((labels[j] for j in range(i - 1, -1, -1) if labels[j] != -1), None)
            right = next((labels[j] for j in range(i + 1, n) if labels[j] != -1), None)
            labels[i] = left if left is not None else (right if right is not None else 0)
    # Tek başına kalmış kısa dalgalanmaları düzelt (A B A -> A A A)
    for i in range(1, n - 1):
        if labels[i - 1] == labels[i + 1] != labels[i] and (segments[i]["end"] - segments[i]["start"]) < 1.6:
            labels[i] = labels[i - 1]
    # İlk görünme sırasına göre yeniden numarala
    remap: dict[int, int] = {}
    out = []
    for l in labels:
        remap.setdefault(l, len(remap))
        out.append(remap[l])
    return out
