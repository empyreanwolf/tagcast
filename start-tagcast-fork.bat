@echo off
rem Start this fork of Tagcast (with Artist cleanup) on its own port, 8913, so it never
rem reuses an official Tagcast that is already running on 8912. Same settings folder and sign-in.
call "%~dp0start-tagcast.bat" --port 8913
