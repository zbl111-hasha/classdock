@echo off
chcp 65001 >nul
set PYTHONIOENCODING=utf-8
echo 输入本批资料上课日期，如 2026-10-09。文件名有日期时优先采用文件名。
set /p course_day=上课日期:
call "%~dp0run.cmd" course_assistant.py archive --date "%course_day%"
pause
