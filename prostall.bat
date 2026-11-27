@echo off
rem prostall: one-click ArcGIS Pro workstation check.
rem
rem Double-click this file to check the workstation. It writes the report as
rem text, JSON and a web page, opens the web page, and changes nothing else.
rem Keep it in the same folder as prostall.py, and run it as the user whose
rem ArcGIS Pro is slow, with Pro closed, and not as administrator.
setlocal

rem -- Settings ---------------------------------------------------------------
rem Where the reports go. Outside OneDrive, so the report itself is not synced.
set "REPORT_DIR=%LOCALAPPDATA%\prostall\reports"
rem Extra options for every run, such as: --sde-server gisdb01 --portal gis.example.org
set "PROSTALL_ARGS="
rem ---------------------------------------------------------------------------

title prostall: ArcGIS Pro workstation check
set "SCRIPT=%~dp0prostall.py"
if not exist "%SCRIPT%" goto :noscript

rem Find Python. ArcGIS Pro ships its own, so try that first: it is on every
rem machine this is meant for, and needs nothing installed.
set "PYARG="
set "PYEXE=%ProgramFiles%\ArcGIS\Pro\bin\Python\envs\arcgispro-py3\python.exe"
if exist "%PYEXE%" goto :run
set "PYEXE=%LOCALAPPDATA%\Programs\ArcGIS\Pro\bin\Python\envs\arcgispro-py3\python.exe"
if exist "%PYEXE%" goto :run
set "PYEXE=py"
set "PYARG=-3"
py -3 --version >nul 2>&1
if not errorlevel 1 goto :run
rem The Microsoft Store stand-in for python.exe fails this test, so it is
rem never mistaken for a real Python.
set "PYEXE=python"
set "PYARG="
python --version >nul 2>&1
if not errorlevel 1 goto :run
goto :nopython

:run
echo Checking this workstation. This takes a minute or two and changes nothing.
echo.
"%PYEXE%" %PYARG% "%SCRIPT%" --report "%REPORT_DIR%" %PROSTALL_ARGS% %*
set "CODE=%ERRORLEVEL%"
echo.
if "%CODE%"=="0" echo Result: nothing needs fixing.
if "%CODE%"=="1" echo Result: some things are slowing ArcGIS Pro down. See the report.
if "%CODE%"=="2" echo Result: at least one problem can cause lost edits. See the report.
if "%CODE%"=="3" echo Result: almost nothing could be checked. See the report.
if "%CODE%"=="64" echo Result: prostall could not run as asked. See the message above.
echo Reports are saved in %REPORT_DIR%
echo.
pause
exit /b %CODE%

:noscript
echo prostall.py was not found next to this file:
echo   %SCRIPT%
echo Keep prostall.bat and prostall.py in the same folder.
echo.
pause
exit /b 64

:nopython
echo No Python was found. ArcGIS Pro's own Python was not in its usual place,
echo and neither the py launcher nor python is installed.
echo Install ArcGIS Pro or Python 3, or ask IT, then run this again.
echo.
pause
exit /b 64
