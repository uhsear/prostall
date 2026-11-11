@echo off
setlocal EnableExtensions DisableDelayedExpansion
chcp 65001 >nul 2>&1
pushd "%~dp0"

rem No argument: the interactive menu, whose first choice is the read-only
rem diagnostic. Mutating modes still need an explicit choice or argument.
set "SCRIPT=%~dp0arcgis_pro_performance_analyser.py"
set "RENDERER=%~dp0html_report.py"
set "MODE=diagnose"
set "OUT="
set "PROJECT="
set "REPAIR_MODE=views"
set "PACKAGE="
set "MANIFEST="
set "MACHINE_FLAG="
set "SHOW_HELP="
set "REPORT_PATTERN=arcgis_pro_performance_analyser_*.html"

if "%~1"=="" goto MENU_MODE
if /i "%~1"=="--repair-aprx" (
    set "MODE=repair"
    set "PROJECT=%~2"
    set "REPORT_PATTERN=arcgis_pro_project_analysis_*.html"
    if not "%~3"=="" set "REPAIR_MODE=%~3"
    if not "%~4"=="" goto USAGE
    goto DEFAULTS
)
if /i "%~1"=="--quarantine-addin" (
    set "MODE=quarantine"
    set "PACKAGE=%~2"
    if /i "%~3"=="--confirm-machine-wide" set "MACHINE_FLAG=--confirm-machine-wide"
    if not "%~3"=="" if not defined MACHINE_FLAG goto USAGE
    if not "%~4"=="" goto USAGE
    goto DEFAULTS
)
if /i "%~1"=="--restore-addin" (
    set "MODE=restore"
    set "MANIFEST=%~2"
    if not "%~3"=="" goto USAGE
    goto DEFAULTS
)
if /i "%~1"=="--apply-safe-fixes" (
    set "MODE=apply"
    if not "%~2"=="" goto USAGE
    goto DEFAULTS
)
if /i "%~1"=="--help" (
    set "SHOW_HELP=1"
    goto USAGE
)
if /i "%~1"=="--menu" if not "%~2"=="" goto USAGE
if /i "%~1"=="--menu" goto MENU_MODE
set "OUT=%~1"
if not "%~2"=="" goto USAGE
goto DEFAULTS

:MENU_MODE
set "MODE=menu"
goto DEFAULTS

:DEFAULTS
if not defined OUT set "OUT=%LOCALAPPDATA%\ArcGISPro_PerformanceAnalyser\Rapports"
if not defined LOCALAPPDATA set "OUT=%~dp0Rapports"
if not exist "%SCRIPT%" goto MISSING_FILES
if not exist "%RENDERER%" goto MISSING_FILES

set "PYTHON="
set "PYTHON_ARGS="
if exist "%ProgramFiles%\ArcGIS\Pro\bin\Python\envs\arcgispro-py3\python.exe" (
    set "PYTHON=%ProgramFiles%\ArcGIS\Pro\bin\Python\envs\arcgispro-py3\python.exe"
)
if not defined PYTHON (
    where py >nul 2>nul
    if not errorlevel 1 (
        set "PYTHON=py"
        set "PYTHON_ARGS=-3"
    )
)
if not defined PYTHON (
    where python >nul 2>nul
    if not errorlevel 1 set "PYTHON=python"
)
if not defined PYTHON goto NO_PYTHON

if /i "%MODE%"=="menu" goto RUN_MENU
if /i "%MODE%"=="repair" goto REPAIR_PROJECT
if /i "%MODE%"=="quarantine" goto QUARANTINE_ADDIN
if /i "%MODE%"=="restore" goto RESTORE_ADDIN
goto RUN_DIAGNOSTIC

:RUN_MENU
echo.
echo === Assistant ArcGIS Pro : menu interactif ===
echo 1 diagnostic, 2 correctif AP-01, 3 copie de projet, 4 quarantaine, 5 restauration,
echo 6 test sans add-ins deja effectue, 7 aide.
echo ArcGIS Pro peut rester ouvert pour le diagnostic ; fermez-le avant une action.
echo Rapports : "%OUT%"
echo.
"%PYTHON%" %PYTHON_ARGS% "%SCRIPT%" --menu --report "%OUT%" --html
set "RC=%ERRORLEVEL%"
goto MENU_DONE

:RUN_DIAGNOSTIC
set "ACTION_FLAG="
if /i "%MODE%"=="apply" set "ACTION_FLAG=--apply"
echo.
if /i "%MODE%"=="apply" (
    echo === Diagnostic et correctif sur reversible AP-01 ===
    echo ArcGIS Pro doit etre ferme. Aucune preference GPU n'est modifiee.
) else (
    echo === Diagnostic ArcGIS Pro - lecture seule ===
)
echo Pour les controles de projets recents, ArcGIS Pro doit etre ferme.
echo Les add-ins sont inventories sans charger leur code.
echo Rapports : "%OUT%"
echo.
"%PYTHON%" %PYTHON_ARGS% "%SCRIPT%" --report "%OUT%" --html --verbose %ACTION_FLAG%
set "RC=%ERRORLEVEL%"
goto AFTER_REPORT

:REPAIR_PROJECT
if not defined PROJECT goto USAGE
echo.
echo === Reparation d'un projet ArcGIS Pro sur une nouvelle copie ===
echo Mode : "%REPAIR_MODE%". L'original n'est jamais reecrit.
echo Rapports : "%OUT%"
echo.
"%PYTHON%" %PYTHON_ARGS% "%SCRIPT%" --project "%PROJECT%" --project-repair "%REPAIR_MODE%" --report "%OUT%" --html --verbose
set "RC=%ERRORLEVEL%"
goto AFTER_REPORT

:QUARANTINE_ADDIN
if not defined PACKAGE goto USAGE
echo.
echo === Quarantaine reversible d'un package ArcGIS Pro ===
echo Le package exact est retire du chargement, copie avec SHA-256 et conserve.
echo ArcGIS Pro doit etre ferme. Aucun package n'est choisi automatiquement.
echo.
"%PYTHON%" %PYTHON_ARGS% "%SCRIPT%" --quarantine-addin "%PACKAGE%" %MACHINE_FLAG%
set "RC=%ERRORLEVEL%"
if not "%RC%"=="0" goto ACTION_FAILED
echo.
echo Quarantaine terminee. Le diagnostic de controle va maintenant s'executer.
set "MODE=diagnose"
goto RUN_DIAGNOSTIC

:RESTORE_ADDIN
if not defined MANIFEST goto USAGE
echo.
echo === Restauration reversible d'un package ArcGIS Pro ===
echo Aucun fichier existant ne sera remplace. ArcGIS Pro doit etre ferme.
echo.
"%PYTHON%" %PYTHON_ARGS% "%SCRIPT%" --restore-addin "%MANIFEST%"
set "RC=%ERRORLEVEL%"
if not "%RC%"=="0" goto ACTION_FAILED
echo.
echo Restauration terminee. Le diagnostic de controle va maintenant s'executer.
set "MODE=diagnose"
goto RUN_DIAGNOSTIC

:AFTER_REPORT
if %RC% LEQ 3 goto FIND_REPORT
echo.
echo L'operation ou l'ecriture des rapports a echoue. Code : %RC%
echo Lisez les messages ci-dessus. Un manifeste de quarantaine affiche reste valable.
goto FINISH

:FIND_REPORT
set "LATEST="
for /f "delims=" %%F in ('dir /b /a-d /o-d "%OUT%\%REPORT_PATTERN%" 2^>nul') do (
    set "LATEST=%%~fF"
    goto OPEN_REPORT
)
goto REPORT_DONE

:OPEN_REPORT
if defined LATEST start "" "%LATEST%"

:REPORT_DONE
echo.
echo Operation terminee. Codes 0 a 3 : rapports produits ; 1/2 : constats a lire.
echo HTML : lecture. TXT : ticket support. JSON : comparaison.
echo Dossier : "%OUT%"
goto FINISH

:MENU_DONE
if %RC% LEQ 3 goto MENU_OK
echo.
echo L'assistant s'est arrete sur le code %RC%. Lisez le message ci-dessus.
echo Aucune autre action n'a ete lancee.
goto FINISH

:MENU_OK
echo.
echo Assistant termine. Code %RC%. Rapports eventuels : "%OUT%"
echo 0 rien a signaler ; 1/2 constats a lire ; 64 action refusee.
echo Pour ne pas passer par le menu, les options restent disponibles : voir --help.
goto FINISH

:ACTION_FAILED
echo.
echo Action refusee ou incomplete. Code : %RC%. Lisez le message ci-dessus.
echo En cas de doute, ne supprimez rien manuellement et utilisez le manifeste.
goto FINISH

:USAGE
echo.
echo Usage :
echo   Diagnostiquer_ArcGIS_Pro.bat                  menu interactif
echo   Diagnostiquer_ArcGIS_Pro.bat --menu
echo   Diagnostiquer_ArcGIS_Pro.bat [dossier_rapports]
echo   Diagnostiquer_ArcGIS_Pro.bat --apply-safe-fixes
echo   Diagnostiquer_ArcGIS_Pro.bat --repair-aprx "projet.aprx" [views^|views_history^|repack^|none]
echo   Diagnostiquer_ArcGIS_Pro.bat --quarantine-addin "package.esriAddinX" [--confirm-machine-wide]
echo   Diagnostiquer_ArcGIS_Pro.bat --restore-addin "quarantine.json"
echo.
echo.
echo Sans argument : menu numerote. Choix 1 = diagnostic en lecture seule,
echo et c'est le choix pris par Entree. Les options ci-dessus restent la
echo voie des scripts et des taches planifiees.
echo La quarantaine retire un add-in du chargement sans l'effacer.
if defined SHOW_HELP (set "RC=0") else (set "RC=64")
goto FINISH

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
echo "%ProgramFiles%\ArcGIS\Pro\bin\Python\envs\arcgispro-py3\python.exe"
echo Installez ou reparez Python/ArcGIS Pro, puis relancez ce fichier.
set "RC=64"
goto FINISH

:FINISH
echo.
pause
popd
endlocal & exit /b %RC%
