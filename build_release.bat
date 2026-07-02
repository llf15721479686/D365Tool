@echo off
setlocal

set PYTHON_EXE=%~dp0.venv\Scripts\python.exe
if not exist "%PYTHON_EXE%" (
  set PYTHON_EXE=python
)

echo [1/4] Installing dependencies...
"%PYTHON_EXE%" -m pip install -r "%~dp0requirements.txt"
"%PYTHON_EXE%" -m pip install pyinstaller

echo [2/4] Building executable...
"%PYTHON_EXE%" -m PyInstaller ^
  --noconfirm ^
  --clean ^
  --onefile ^
  --windowed ^
  --name D365FieldCreator ^
  "%~dp0d365_field_creator.py"

echo [3/4] Preparing release folder...
if exist "%~dp0release" rmdir /s /q "%~dp0release"
mkdir "%~dp0release"
copy "%~dp0dist\D365FieldCreator.exe" "%~dp0release\D365FieldCreator.exe" >nul
if exist "%~dp0config.example.json" copy "%~dp0config.example.json" "%~dp0release\config.json" >nul
if exist "%~dp0config.example.json" copy "%~dp0config.example.json" "%~dp0release\config.example.json" >nul
if exist "%~dp0schema.json" copy "%~dp0schema.json" "%~dp0release\schema.json" >nul

echo [4/4] Done.
echo Release output: %~dp0release
endlocal
