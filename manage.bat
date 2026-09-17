@echo off
setlocal
cd /d "%~dp0"

if "%~1"=="" (
  echo.
  echo AcademicTranslate 项目管理
  echo Usage: manage.bat [setup^|start^|stop^|status^|restart] [all^|backend^|frontend]
  echo.
  echo Examples:
  echo   manage.bat setup
  echo   manage.bat start
  echo   manage.bat status
  echo   manage.bat stop
  echo   manage.bat restart backend
  echo.
  exit /b 0
)

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\manage.ps1" %*
exit /b %ERRORLEVEL%
