@echo off
setlocal
pushd "%~dp0"
pythonw.exe "%~dp0run_patch.py"
if errorlevel 1 (
  python.exe "%~dp0run_patch.py"
  if errorlevel 1 pause
)
popd

