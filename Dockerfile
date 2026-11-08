FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg fonts-inter libgl1 libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
ENV FUTBOLCLIP_DATA=/data HOST=0.0.0.0 PORT=8080 COOKIE_SECURE=true
VOLUME /data
EXPOSE 8080
CMD ["sh","-c","gunicorn -w 1 --threads 8 --timeout 0 -b 0.0.0.0:${PORT} app:app"]
