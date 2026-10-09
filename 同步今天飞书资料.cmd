@echo off
chcp 65001 >nul
set PYTHONIOENCODING=utf-8
call "%~dp0run.cmd" sync_feishu.py
pause
