@echo off
setlocal
chcp 65001 >nul
set PYTHONIOENCODING=utf-8
set "task_python=python"
if exist "%USERPROFILE%\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe" set "task_python=%USERPROFILE%\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"
if defined CLASSDOCK_PYTHON set "task_python=%CLASSDOCK_PYTHON%"
if exist "%~dp0.venv\Scripts\python.exe" set "task_python=%~dp0.venv\Scripts\python.exe"
pushd "%~dp0"
"%task_python%" %*
set "task_exit=%errorlevel%"
popd
exit /b %task_exit%
