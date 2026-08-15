@echo off
setlocal EnableExtensions

if "%~1"=="" goto :usage
if "%~2"=="" goto :usage

set "MHSRC=%~f1"
set "QTINCLUDE=%~f2"
set "OUTPUT=%~f3"
if not defined OUTPUT set "OUTPUT=%~dp0..\router"
for %%I in ("%OUTPUT%") do set "OUTPUT=%%~fI"

if not exist "%MHSRC%\include\MinHook.h" (
  echo MinHook source tree not found: "%MHSRC%"
  exit /b 2
)
if not exist "%QTINCLUDE%\QtCore\QByteArray" (
  echo Qt 5.15.2 headers not found: "%QTINCLUDE%"
  exit /b 2
)

set "VSWHERE=%ProgramFiles(x86)%\Microsoft Visual Studio\Installer\vswhere.exe"
if not exist "%VSWHERE%" (
  echo Visual Studio Installer vswhere.exe was not found.
  exit /b 2
)
for /f "usebackq delims=" %%I in (`"%VSWHERE%" -latest -products * -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath`) do set "VSROOT=%%I"
if not defined VSROOT (
  echo Visual Studio C++ x86 build tools were not found.
  exit /b 2
)
if not exist "%VSROOT%\VC\Auxiliary\Build\vcvars32.bat" (
  echo vcvars32.bat was not found below "%VSROOT%".
  exit /b 2
)

call "%VSROOT%\VC\Auxiliary\Build\vcvars32.bat" >nul || exit /b 1
if not exist "%~dp0build\x86" mkdir "%~dp0build\x86" || exit /b 1
if not exist "%OUTPUT%" mkdir "%OUTPUT%" || exit /b 1
pushd "%~dp0build\x86" || exit /b 1

cl /nologo /O2 /MT /W3 /DUNICODE /D_UNICODE /I"%MHSRC%\include" /I"%MHSRC%\src" /c ^
  "%MHSRC%\src\buffer.c" "%MHSRC%\src\hook.c" "%MHSRC%\src\trampoline.c" "%MHSRC%\src\hde\hde32.c" || goto :fail

cl /nologo /O2 /MT /W4 /EHsc /DUNICODE /D_UNICODE /I"%MHSRC%\include" /c "%~dp0ipcroute.cpp" || goto :fail
link /nologo /dll /out:"%OUTPUT%\ipcroute.dll" /implib:ipcroute.lib ipcroute.obj buffer.obj hook.obj trampoline.obj hde32.obj ws2_32.lib iphlpapi.lib || goto :fail

cl /nologo /O2 /MT /W4 /EHsc /DUNICODE /D_UNICODE "%~dp0netinject.cpp" /Fe:"%OUTPUT%\netinject.exe" || goto :fail
cl /nologo /O2 /MT /W4 /EHsc /DUNICODE /D_UNICODE "%~dp0guardlaunch.cpp" /Fe:"%OUTPUT%\guardlaunch.exe" || goto :fail

lib /nologo /machine:x86 /def:"%~dp0qt5core_min.def" /out:qt5core_min.lib || goto :fail
lib /nologo /machine:x86 /def:"%~dp0qt5network_min.def" /out:qt5network_min.lib || goto :fail
cl /nologo /O2 /MT /W4 /EHsc /DUNICODE /D_UNICODE /DQT_NO_VERSION_TAGGING ^
  /I"%MHSRC%\include" /I"%QTINCLUDE%" /I"%QTINCLUDE%\QtCore" /I"%QTINCLUDE%\QtNetwork" ^
  /c "%~dp0versionguard.cpp" || goto :fail
link /nologo /dll /out:"%OUTPUT%\versionguard.dll" /implib:versionguard.lib versionguard.obj buffer.obj hook.obj trampoline.obj hde32.obj ^
  qt5core_min.lib qt5network_min.lib version.lib || goto :fail

cl /nologo /O2 /MT /W4 /EHsc /DUNICODE /D_UNICODE "%~dp0ipcroute_test.cpp" /Fe:ipcroute_test.exe /link ws2_32.lib || goto :fail
cl /nologo /O2 /MT /W4 /EHsc /DUNICODE /D_UNICODE "%~dp0version_endpoint_test.cpp" /Fe:version_endpoint_test.exe || goto :fail
cl /nologo /O2 /MT /W4 /EHsc /DUNICODE /D_UNICODE "%~dp0guardlaunch_policy_test.cpp" /Fe:guardlaunch_policy_test.exe || goto :fail

popd
echo Built x86 runtime files in "%OUTPUT%".
echo Native tests are in "%~dp0build\x86".
exit /b 0

:usage
echo Usage: build_native.cmd MINHOOK_ROOT QT_5_15_2_INCLUDE [OUTPUT_DIRECTORY]
echo Example: build_native.cmd C:\src\minhook C:\Qt\5.15.2\msvc2019\include
exit /b 2

:fail
set "BUILDERR=%ERRORLEVEL%"
popd
exit /b %BUILDERR%
