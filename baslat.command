#!/bin/bash
cd "$(dirname "$0")"
if ! docker version >/dev/null 2>&1; then
  echo "Docker Desktop açık değil veya kurulu değil. docker.com'dan kurun, açın ve bu dosyayı tekrar çalıştırın."
  read -p "Kapatmak için Enter"; exit 1
fi
[ -f .env ] || cp .env.example .env
if ! docker image inspect futbolclip >/dev/null 2>&1; then
  echo "İlk kurulum yapılıyor, 10 dakika kadar sürebilir. Pencereyi kapatmayın."
  docker build -t futbolclip . || { echo "Kurulum başarısız."; read -p "Enter"; exit 1; }
fi
(sleep 6; open http://localhost:8080) &
echo "Site: http://localhost:8080  (Durdurmak için bu pencereyi kapatın)"
docker run --rm -p 8080:8080 -v futbolclip_data:/data --env-file .env -e COOKIE_SECURE=false futbolclip
