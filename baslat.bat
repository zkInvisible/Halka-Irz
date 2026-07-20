@echo off
echo ==========================================
echo ARZ PUSULASI BASLATILIYOR...
echo ==========================================
echo.
echo [1/2] Guncel veriler internetten cekiliyor (Lutfen bekleyin)...
python backend/main.py refresh --force-market-refresh
echo.
echo [2/2] Web sunucusu baslatiliyor...
echo.
echo ==========================================
echo HER SEY HAZIR! Tarayicinizda su adresi acin:
echo http://127.0.0.1:5050
echo.
echo (Sunucuyu durdurmak icin bu pencereyi kapatabilirsiniz)
echo ==========================================
echo.
python backend/app.py
pause
