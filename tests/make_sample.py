"""Test için örnek video + Türkçe SRT üretir (gerçek konuşma değil; hattı doğrulamak içindir).

Kullanım: python tests/make_sample.py [çıktı_klasörü]
- 5 dakikalık 1280x720 video; 60–95, 150–185 ve 220–250. saniyelerde yüksek enerjili "heyecan" bölümleri
- 20 sn'lik bloklarla değişen iki farklı ses perdesi (iki konuşmacı simülasyonu)
- Heyecan bölümleriyle eşleşen Türkçe futbol yorumu altyazıları
"""
import subprocess
import sys
from pathlib import Path

OUT = Path(sys.argv[1] if len(sys.argv) > 1 else "sample")
OUT.mkdir(parents=True, exist_ok=True)
DUR = 300

EXC = "(between(t,60,95)+between(t,150,185)+between(t,220,250))"
F0 = "if(lt(mod(t,40),20),120,210)"
AUDIO = (
    f"aevalsrc='(0.10+0.42*{EXC})*(0.55+0.45*sin(2*PI*4*t))*"
    f"(sin(2*PI*{F0}*t)+0.5*sin(4*PI*{F0}*t)+0.25*sin(6*PI*{F0}*t))*0.5':s=16000:d={DUR}"
)

subprocess.run([
    "ffmpeg", "-y", "-v", "error",
    "-f", "lavfi", "-i", f"testsrc2=size=1280x720:rate=25:duration={DUR}",
    "-f", "lavfi", "-i", AUDIO,
    "-vf", "hue=h=t*12,drawtext=text='FutbolClip AI test':x=(w-tw)/2:y=40:fontsize=42:fontcolor=white:box=1:boxcolor=black@0.5",
    "-c:v", "libx264", "-preset", "veryfast", "-crf", "28", "-pix_fmt", "yuv420p",
    "-c:a", "aac", "-b:a", "96k", "-shortest", str(OUT / "ornek_mac_yorumu.mp4"),
], check=True)

CUES = [
    "Merhaba arkadaşlar, derbinin ardından değerlendirmelerimize başlıyoruz.",
    "İlk yarıda iki takım da temkinli başladı, orta sahada mücadele vardı.",
    "Teknik direktörün diziliş tercihi savunmada boşluk bıraktı.",
    "Pas organizasyonlarında tempo düşüktü, hücum hattı etkisizdi.",
    "Kanat oyuncuları sürekli içeri kat ederek yardım istedi.",
    "Orta sahada top kayıpları maalesef çok fazlaydı.",
    "Savunma arkasına atılan toplarda sürekli zorlandılar.",
    "Rakip takım ise kontra ataklarla bulduğu pozisyonlara odaklandı.",
    "Şimdi maçın kırılma anına geliyoruz, çünkü orada her şey değişti!",
    "GOL! Ağları salladı, inanılmaz bir gol attı, müthiş bir vuruş!",
    "Kaleci hiçbir şey yapamadı, tam köşeye gitti, muhteşem bir gol!",
    "Stat inliyor, tribünler çılgına döndü, bu golü uzun süre konuşacağız!",
    "Ama dur, hakem VAR kaydını inceliyor, ofsayt var mı bakıyoruz.",
    "Hayır ofsayt yok, gol geçerli, skoru değiştirdi, bu tarihi bir an!",
    "Hemen ardından penaltı! Hakem penaltı noktasını gösterdi, skandal bir pozisyon!",
    "Kırmızı kart çıktı, oyundan atıldı, bu karar olay yaratacak!",
    "Büyük tepki var, teknik heyet hakeme koşuyor, gerginlik çok yüksek!",
    "Penaltıyı kaleci kurtardı! İnanılmaz bir kurtarış, harika bir performans!",
    "İkinci yarıda oyun biraz sakinleşti ve tempo düştü.",
    "Orta sahada top çevirme ağırlıklıydı, pozisyon üretemediler.",
    "Savunma hattı geri çekilip alan savunmasına geçti.",
    "Teknik direktör oyuncu değişikliğiyle hücuma güç katmak istedi.",
    "Yeni giren oyuncu pas trafiğine ritim kattı ama yeterli olmadı.",
    "Rakip takım zaman geçirmeye başladı, oyun sürekli kesildi.",
    "Sakatlık nedeniyle oyuna ara verildi, sağlık ekibi sahaya girdi.",
    "Sonra bir anda kaleye şut! Direkten döndü, inanılmaz bir şans!",
    "Bir daha yakaladılar, bu sefer kafa vuruşu, gol! Ağları salladı!",
    "Beraberliği buldu, şampiyonluk yarışında çok büyük bir gol bu!",
    "Derbide son dakikalar, tribünler ayakta, müthiş bir atmosfer!",
    "Hakem son düdüğü çaldı, maç berabere bitti, puan farkı korundu.",
    "Şampiyonluk açısından bu skor ne anlama geliyor, onu konuşalım.",
    "Lider ekip averajla önde, kupa yarışı çok kızışacak gibi.",
    "Transfer döneminde bu bölgeye mutlaka takviye gerekiyor.",
    "Yönetimin açıklamaları da bu yönde, bonservis görüşmeleri sürüyor.",
    "Arda Güler'in performansı ise gerçekten dikkat çekiciydi.",
    "Asist yapan oyuncunun formu yükseliyor, milli takım için de umut verici.",
    "Genel olarak savunmada hata sayısı fazlaydı, düzeltilmesi gerek.",
    "Haftaya yeni bir derbi var, hazırlık için çok az zaman kalıyor.",
    "Takımın moral motivasyonu bu sonuçtan sonra yüksek olacaktır.",
    "Evet arkadaşlar, bugünlük bu kadar, bir sonraki programda görüşmek üzere.",
]


def ts(t):
    return f"{int(t // 3600):02d}:{int(t // 60 % 60):02d}:{int(t % 60):02d},{int(round((t % 1) * 1000)):03d}"


lines = []
step = (DUR - 6) / len(CUES)
for i, text in enumerate(CUES):
    a = 3 + i * step
    b = a + step - 0.35
    lines += [str(i + 1), f"{ts(a)} --> {ts(b)}", text, ""]
(OUT / "ornek_altyazi.srt").write_text("\n".join(lines), encoding="utf-8")
print("Hazır:", OUT / "ornek_mac_yorumu.mp4", OUT / "ornek_altyazi.srt")
