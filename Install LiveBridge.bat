@echo off
rem Double-click: installs LiveBridge (Remote Script, Max devices, MCP server, skill) from this folder.
rem One line on purpose: cmd reads a .bat while it runs, so the rest is never re-read.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0installers\install.ps1" %* & pause & exit /b
