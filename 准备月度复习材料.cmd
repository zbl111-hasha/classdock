@echo off
chcp 65001 >nul
set PYTHONIOENCODING=utf-8
set /p course_month=输入月份，如 2026-10:
call "%~dp0run.cmd" course_assistant.py month --month "%course_month%"
pause
