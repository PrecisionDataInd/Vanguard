@echo off
cd /d "%~dp0.."
call npm install
call npm test
call npm run dist
echo.
echo Installer is in the dist folder.
pause
