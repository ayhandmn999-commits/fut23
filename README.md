# FutbolClip AI

Uzun futbol yorumu / analiz videolarından, Türkçe altyazılı **9:16 kısa videolar** üreten web uygulaması.
Konu yazın (ör. *"Arda Güler performansı"*); sistem lisanslı videoları arar, analiz eder, en heyecanlı 5–10 bölümü seçer ve MP4 olarak dışa aktarır.

## Kurulum

```bash
pip install -r requirements.txt
sudo apt install ffmpeg           # libass destekli olmalı
cp .env.example .env              # anahtarları doldurun
python app.py                     # http://127.0.0.1:8080
# üretim: gunicorn -w 1 --threads 8 -b 0.0.0.0:8080 "app:app"
```

Arayüzdeki **Servis Durumu** sayfası, hangi bileşenin hazır/eksik olduğunu ve nasıl kurulacağını gösterir.

## Hat (pipeline)

1. **Arama** — YouTube Data API v3 (`videoLicense=creativeCommon`), Wikimedia Commons, Internet Archive
2. **İndirme** — yt-dlp (yalnızca bulunan lisanslı video) / doğrudan dosya
3. **Ses enerjisi analizi** — coşku eğrisi (ses yükselmesi, tempo)
4. **Türkçe transkripsiyon** — faster-whisper (yerel), Groq veya OpenAI Whisper; ya da yüklenen SRT/VTT
5. **Konuşmacı algılama** — MFCC + perde özellikleriyle kümeleme (yaklaşık)
6. **Bölüm seçimi** — sezgisel puan (coşku, anahtar kelimeler, konu ilgisi) + isteğe bağlı Claude ile başlık/viral puanı
7. **Kadraj** — sahne kesimi algılama, 9:16 için bulanık arka plan / akıllı / merkez kırpma
8. **Render** — FFmpeg ile altyazı yakma (libass), 1080×1920 H.264/AAC MP4 (TikTok, Reels, Shorts)

## Telif ve içerik izinleri

- Arama yalnızca **CC lisanslı / özgür lisanslı** videoları getirir. Her kliple kaynak, kanal ve lisans bilgisi saklanır ve videoya kredi olarak eklenebilir.
- CC olmayan videolar yalnızca **sahibi olduğunuzu veya izin aldığınızı beyan ettiğinizde** (kanal izin listesi / bağlantı / yükleme) işlenir.
- **Instagram, TikTok ve X'te arama yapılmaz**: bu platformlar başkalarının videolarını aramak/indirmek için herkese açık API sunmuyor ve kullanım koşulları buna izin vermiyor. Kendi içeriğinizi "Video yükle" ile ekleyin.
- Yayın öncesi lisans koşullarını (atıf gerekliliği vb.) kontrol etmek kullanıcının sorumluluğundadır.

## Eksik servisler (bu ortamda kurulu değildi)

| Servis | Etkisi | Çözüm |
|---|---|---|
| `YOUTUBE_API_KEY` | YouTube araması çalışmaz | Google Cloud'da YouTube Data API v3 anahtarı |
| `yt-dlp` | YouTube videosu indirilemez | `pip install yt-dlp` |
| Konuşma tanıma | Otomatik Türkçe altyazı çıkmaz (SRT/VTT yüklenirse çalışır) | `pip install faster-whisper` veya `GROQ_API_KEY` / `OPENAI_API_KEY` |
| `ANTHROPIC_API_KEY` | Claude ile başlık/puan yok; yerleşik sezgisel puanlama kullanılır | İsteğe bağlı |
| OpenCV | Yüz tabanlı kadraj yok; hareket tabanlı kadraj kullanılır | İsteğe bağlı |

## Test

```bash
python tests/make_sample.py sample      # sentetik örnek video + SRT
python tests/smoke_test.py sample       # uçtan uca (kayıt, yükleme, analiz, düzenleme, 3 platform MP4)
```

## Docker ile çalıştırma (önerilen)

```bash
docker build -t futbolclip .
docker run -d -p 8080:8080 -v futbolclip_data:/data --env-file .env -e COOKIE_SECURE=false futbolclip
# http://localhost:8080
```

Sunucuda (Render/Railway/Fly.io/VPS): Dockerfile'ı kullanın, `/data` için kalıcı disk bağlayın,
ortam değişkenlerine `.env.example` içindeki anahtarları girin (HTTPS arkasında `COOKIE_SECURE=true`).
Not: Worker sayısı 1 olmalı (iş kuyruğu bellek içidir). Video işleme CPU yoğundur; en az 2 vCPU / 4 GB RAM önerilir.
