@echo off
chcp 65001 >nul
set PYTHONIOENCODING=utf-8
call "%~dp0run.cmd" course_assistant.py today
pause
