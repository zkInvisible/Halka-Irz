@echo off
chcp 65001 >nul
echo ==========================================
echo ARZ PUSULASI BASLATILIYOR...
echo ==========================================
echo.
echo [1/2] Guncel veriler cekiliyor...
echo       Ilk calistirmada ~1 dakika surer; sonrasinda onbellek sayesinde ~10 saniye.
python backend/main.py refresh
echo.
echo [2/2] Web sunucusu baslatiliyor...
echo.
echo ==========================================
echo HAZIR! Tarayicinizda su adresi acin:
echo http://127.0.0.1:5050
echo.
echo Veri bayatladiginda sunucu kendini yeniler; "Yenile" dugmesi de
echo arka planda calisir, sayfa donmaz.
echo (Durdurmak icin bu pencereyi kapatabilirsiniz)
echo ==========================================
echo.
set WRITE_LOCAL_ARTIFACTS=1
python backend/app.py
pause
