@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo FutbolClip AI baslatiliyor...
docker version >nul 2>&1
if errorlevel 1 (
  echo.
  echo Docker Desktop acik degil veya kurulu degil.
  echo 1^) docker.com adresinden Docker Desktop'i kurun
  echo 2^) Docker Desktop'i acin, hazir olmasini bekleyin
  echo 3^) Bu dosyayi tekrar calistirin
  pause
  exit /b
)
if not exist .env copy .env.example .env >nul
docker image inspect futbolclip >nul 2>&1
if errorlevel 1 (
  echo Ilk kurulum yapiliyor, 10 dakika kadar surebilir. Pencereyi kapatmayin.
  docker build -t futbolclip .
  if errorlevel 1 ( echo Kurulum basarisiz oldu. Yukaridaki hatayi bana gonderin. & pause & exit /b )
)
start "" cmd /c "timeout /t 6 >nul & start http://localhost:8080"
echo.
echo Site: http://localhost:8080   (Durdurmak icin bu pencereyi kapatin)
docker run --rm -p 8080:8080 -v futbolclip_data:/data --env-file .env -e COOKIE_SECURE=false futbolclip
pause
