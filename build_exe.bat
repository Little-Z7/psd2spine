@echo off
REM 打包独立版为单个 exe(依赖全内置、独立运行)。
REM 需先: pip install -r requirements.txt pyinstaller
chcp 65001 >nul
echo === 打包 psd2spine 独立版 ===
pyinstaller --noconfirm --onefile --windowed --name psd2spine ^
  --icon logo.ico ^
  --add-data "pose_landmarker.task;." ^
  --collect-all psd_tools ^
  --collect-all webview ^
  --collect-all mediapipe ^
  --collect-submodules clr_loader ^
  --hidden-import ml_rig ^
  psd2spine_app.py
echo.
echo 完成后 exe 在 dist\psd2spine.exe
pause
