@echo off
setlocal EnableExtensions DisableDelayedExpansion
chcp 65001 >nul 2>&1
pushd "%~dp0"

rem Read-only workstation diagnostic. Do not add --apply here.
rem Optional argument: a local output folder for the reports.
set "SCRIPT=%~dp0arcgis_pro_performance_analyser.py"
set "RENDERER=%~dp0html_report.py"
set "OUT=%~1"
if not defined OUT set "OUT=%LOCALAPPDATA%\ArcGISPro_PerformanceAnalyser\Rapports"
if not defined LOCALAPPDATA set "OUT=%~dp0Rapports"

if not exist "%SCRIPT%" goto MISSING_FILES
if not exist "%RENDERER%" goto MISSING_FILES

set "PYTHON="
if exist "%ProgramFiles%\ArcGIS\Pro\bin\Python\envs\arcgispro-py3\python.exe" (
    set "PYTHON=%ProgramFiles%\ArcGIS\Pro\bin\Python\envs\arcgispro-py3\python.exe"
)
if not defined PYTHON (
    where py >nul 2>nul
    if not errorlevel 1 set "PYTHON=py -3"
)
if not defined PYTHON (
    where python >nul 2>nul
    if not errorlevel 1 set "PYTHON=python"
)
if not defined PYTHON goto NO_PYTHON

echo.
echo === Diagnostic ArcGIS Pro - lecture seule ===
echo Pour les controles de projets recents, ArcGIS Pro doit etre ferme.
echo Les add-ins sont inventories sans charger leur code.
echo Rapports : %OUT%
echo.

%PYTHON% "%SCRIPT%" --report "%OUT%" --html --verbose
set "RC=%ERRORLEVEL%"

if %RC% LEQ 3 goto FIND_REPORT
echo.
echo Le diagnostic n'a pas pu produire tous les rapports. Code : %RC%
echo Lisez les messages ci-dessus et le dossier de sortie.
goto FINISH

:FIND_REPORT
set "LATEST="
for /f "delims=" %%F in ('dir /b /a-d /o-d "%OUT%\arcgis_pro_performance_analyser_*.html" 2^>nul') do (
    set "LATEST=%%~fF"
    goto OPEN_REPORT
)
goto REPORT_DONE

:OPEN_REPORT
if defined LATEST start "" "%LATEST%"

:REPORT_DONE
echo.
echo Diagnostic termine. Codes 0 a 3 : rapports produits ; 1/2 : constats a lire.
echo HTML : lecture. TXT : ticket support. JSON : comparaison.
echo Dossier : %OUT%

:FINISH
echo.
pause
popd
endlocal & exit /b %RC%

:MISSING_FILES
echo.
echo Fichiers requis absents. Conservez ensemble :
echo   Diagnostiquer_ArcGIS_Pro.bat
echo   arcgis_pro_performance_analyser.py
echo   html_report.py
set "RC=64"
goto FINISH

:NO_PYTHON
echo.
echo Python 3.9+ est introuvable. Le Python d'ArcGIS Pro est recherche dans :
echo %ProgramFiles%\ArcGIS\Pro\bin\Python\envs\arcgispro-py3\python.exe
echo Installez ou reparez Python/ArcGIS Pro, puis relancez ce fichier.
set "RC=64"
goto FINISH
