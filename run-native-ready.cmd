@echo off
setlocal
if "%~1"=="" exit /b 2
echo %~1
call "%~dp0run-native.cmd"
