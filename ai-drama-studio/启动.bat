@echo off
setlocal
chcp 65001 >nul
title AI Drama Studio

rem ==== locate node (skip if already on PATH) ====
where node >nul 2>nul || set "PATH=C:\Users\Administrator\.workbuddy\binaries\node\versions\22.22.2-3;%PATH%"

rem ==== backend: FastAPI on 8013 ====
netstat -ano | findstr ":8013 " | findstr LISTENING >nul
if errorlevel 1 (
    pushd "%~dp0backend"
    echo [启动] 后端 http://localhost:8013 ...
    start "ai-drama-backend" /min cmd /c "python -m uvicorn app.main:app --host 127.0.0.1 --port 8013"
    popd
) else echo [跳过] 后端已在 8013 运行

rem ==== frontend: Next.js dev on 3000 ====
netstat -ano | findstr ":3000 " | findstr LISTENING >nul
if errorlevel 1 (
    pushd "%~dp0frontend"
    if not exist node_modules (
        echo [安装] 前端依赖首次安装中，请稍候...
        call npm install --no-audit --no-fund
    )
    echo [启动] 前端 http://localhost:3000 ...
    start "ai-drama-frontend" /min cmd /c "npm run dev"
    popd
) else echo [跳过] 前端已在 3000 运行

timeout /t 6 /nobreak >nul
start http://localhost:3000
echo ============================================
echo  AI Drama Studio 已启动: http://localhost:3000
echo ============================================
endlocal
