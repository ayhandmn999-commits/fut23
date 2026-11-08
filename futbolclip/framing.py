"""Sahne kesimi tespiti ve akıllı 9:16 kadraj (hareket + doku tabanlı sanal kamera)."""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

import numpy as np

from .config import has_face_cascade


def scene_cuts(video: str | Path, start: float, end: float, threshold: float = 0.32) -> list[float]:
    """[start, end] aralığındaki sahne kesimlerinin mutlak zamanları (sn)."""
    cmd = [
        "ffmpeg", "-hide_banner", "-nostats", "-ss", f"{start:.2f}", "-t", f"{end - start:.2f}", "-i", str(video),
        "-an", "-vf", f"scale=320:-2,select='gt(scene,{threshold})',showinfo", "-f", "null", "-",
    ]
    p = subprocess.run(cmd, capture_output=True, text=True)
    return [start + float(m) for m in re.findall(r"pts_time:([\d.]+)", p.stderr)]


def snap_to_cuts(start: float, end: float, cuts: list[float]) -> tuple[float, float]:
    """Başlangıç/bitişi yakındaki bir sahne kesimine hizalar (sahne ortasında başlamayı önler)."""
    for c in cuts:
        if start - 0.8 <= c <= start + 1.4:
            start = c
            break
    for c in reversed(cuts):
        if end - 1.4 <= c <= end + 0.8 and c - start >= 12:
            end = c
            break
    return start, end


def crop_window(src_w: int, src_h: int, aspect: float = 9 / 16) -> tuple[int, int]:
    """Kaynaktan alınacak (genişlik, yükseklik) — çift sayılar."""
    cw = min(src_w, int(src_h * aspect))
    return cw - cw % 2, src_h - src_h % 2


def _smooth(a: np.ndarray, sigma: float) -> np.ndarray:
    r = int(3 * sigma) + 1
    k = np.exp(-0.5 * (np.arange(-r, r + 1) / sigma) ** 2)
    return np.convolve(a, k / k.sum(), mode="same")


def crop_trajectory(
    video: str | Path, start: float, dur: float, src_w: int, src_h: int, step: float = 1.0
) -> list[tuple[float, float]]:
    """Kırpma penceresinin sol kenarı x(t) için (t, x) anahtar noktaları.

    16:9 görüntüde hareket ve kenar yoğunluğu en fazla olan bölgeyi izler; kamera yalnızca hedef belirgin
    biçimde kaydığında yavaşça hareket eder (sanal kameraman). Kaynak zaten dikey/dar ise sabit döner.
    """
    cw, _ = crop_window(src_w, src_h)
    max_x = max(0, src_w - cw)
    if max_x <= 2:
        return [(0.0, 0.0)]
    fps = 3
    W = 192
    H = int(round(W * src_h / src_w / 2) * 2)
    cmd = [
        "ffmpeg", "-v", "error", "-ss", f"{start:.2f}", "-t", f"{dur:.2f}", "-i", str(video), "-an",
        "-vf", f"fps={fps},scale={W}:{H},format=gray", "-f", "rawvideo", "-pix_fmt", "gray", "-",
    ]
    raw = subprocess.run(cmd, capture_output=True).stdout
    n = len(raw) // (W * H)
    if n < 3:
        return [(0.0, max_x / 2)]
    fr = np.frombuffer(raw[: n * W * H], dtype=np.uint8).reshape(n, H, W).astype(np.float32)

    cw_small = cw * W / src_w
    centers = []
    prior = np.exp(-0.5 * ((np.arange(W) - W / 2) / (0.45 * W)) ** 2)
    for t in range(n):
        motion = np.abs(fr[t] - fr[t - 1]).mean(axis=0) if t > 0 else np.zeros(W)
        edges = np.abs(np.diff(fr[t], axis=1)).mean(axis=0)
        edges = np.append(edges, edges[-1])
        col = _smooth(motion, 7) * 1.0 + _smooth(edges, 7) * 0.25
        col = col / (col.max() + 1e-6)
        # pencere içindeki toplam ilgiye göre en iyi merkezi seç
        win = np.convolve(col * (0.6 + 0.4 * prior), np.ones(int(cw_small)), mode="same")
        centers.append(float(np.argmax(win)))
    centers_px = np.array(centers) * src_w / W

    cam = src_w / 2
    xs = []
    dead = 0.12 * cw
    for c in centers_px:
        if abs(c - cam) > dead:
            cam += 0.12 * (c - cam)
        xs.append(float(np.clip(cam - cw / 2, 0, max_x)))
    t_axis = np.arange(n) / fps
    key_t = np.arange(0, dur + step, step)
    key_x = np.interp(key_t, t_axis, xs)
    key_x = _smooth(key_x, 1.2) if len(key_x) > 4 else key_x
    # Çok sık anahtar noktası ifadeyi şişirir; en fazla ~40 nokta
    stride = max(1, int(np.ceil(len(key_t) / 40)))
    return [(float(t), float(np.clip(x, 0, max_x))) for t, x in zip(key_t[::stride], key_x[::stride])]


def x_expression(points: list[tuple[float, float]]) -> str:
    """FFmpeg crop `x` ifadesi: anahtar noktalar arasında doğrusal geçiş."""
    if len(points) == 1:
        return f"{points[0][1]:.1f}"
    expr = f"{points[-1][1]:.1f}"
    for (t0, x0), (t1, x1) in reversed(list(zip(points, points[1:]))):
        if t1 <= t0:
            continue
        expr = f"if(lt(t,{t1:.2f}),{x0:.1f}+({x1 - x0:.2f})*(t-{t0:.2f})/{t1 - t0:.2f},{expr})"
    return expr


def face_support() -> bool:
    return has_face_cascade()
