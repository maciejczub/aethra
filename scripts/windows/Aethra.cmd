@echo off
rem Aethra launcher.
rem
rem   Aethra.cmd                      run the show (show.yaml next to this file)
rem   Aethra.cmd other.yaml           run another show file
rem   Aethra.cmd show.yaml --test-mode
rem   Aethra.cmd check show.yaml      any other aethra command runs once:
rem                                   check, verify, devices, monitor, bench, selftest,
rem                                   rehearse, report, send
rem
rem The show is restarted automatically if it ever crashes. A restarted
rem controller reconnects to the video players that are still running, so a
rem crash does not interrupt the picture.
rem
rem No parenthesised blocks below on purpose: a ")" in the folder path would
rem break them. "call" keeps this script in control even if python is a wrapper script.
setlocal EnableExtensions
cd /d "%~dp0"
chcp 65001 >nul

rem Python: the bundled one, else a virtual environment, else the py launcher.
rem PYCMD keeps its own quotes so that paths with spaces work.
if exist "%~dp0python\python.exe" goto :bundled
if exist "%~dp0.venv\Scripts\python.exe" goto :venv
set PYCMD=py -3
set "PYTHONPATH=%~dp0src"
goto :dispatch
:bundled
set PYCMD="%~dp0python\python.exe"
goto :dispatch
:venv
set PYCMD="%~dp0.venv\Scripts\python.exe"
set "PYTHONPATH=%~dp0src"

:dispatch
for %%c in (check verify devices monitor bench selftest rehearse report send) do if /i "%~1"=="%%c" goto :command
goto :show

:command
call %PYCMD% -X utf8 -m aethra %*
exit /b %ERRORLEVEL%

:show
title Aethra
set "CONFIG=%~1"
if "%CONFIG%"=="" set "CONFIG=show.yaml"
set "EXTRA=%2 %3 %4 %5"
if "%AETHRA_START_DELAY%"=="" goto :loop
echo Starting in %AETHRA_START_DELAY% seconds...
timeout /t %AETHRA_START_DELAY% /nobreak >nul

:loop
call %PYCMD% -X utf8 -m aethra run "%CONFIG%" %EXTRA%
set "CODE=%ERRORLEVEL%"
if "%CODE%"=="0" goto :end
if "%CODE%"=="2" goto :config_error
if "%CODE%"=="3" goto :already_running
echo.
echo Aethra stopped unexpectedly, exit code %CODE%. Restarting in 2 seconds...
echo Close this window to stop.
timeout /t 2 /nobreak >nul
goto :loop

:config_error
echo.
echo Aethra cannot start: fix the problem shown above and start again.
echo Tip: "Aethra.cmd check" lists every problem in the show file.
pause
goto :end

:already_running
echo.
echo Aethra is already running.
timeout /t 5 >nul

:end
endlocal
