@echo off
rem Double-click: updates this LiveBridge folder to the newest version, then runs the installer.
rem Quit Claude Desktop first (it runs the MCP server from this folder). See installers\update.py.
rem One line on purpose: the update may replace this file while it runs; cmd never re-reads it.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0installers\install.ps1" --update %* & pause & exit /b
