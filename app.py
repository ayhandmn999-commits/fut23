"""FutbolClip AI — giriş noktası.

Geliştirme:  python app.py            (http://localhost:8080)
Üretim:      gunicorn -w 1 --threads 8 -b 0.0.0.0:8080 "app:app"
"""
import logging
import os

from futbolclip.web import create_app

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
app = create_app()

if __name__ == "__main__":
    app.run(host=os.environ.get("HOST", "127.0.0.1"), port=int(os.environ.get("PORT", "8080")), threaded=True)
