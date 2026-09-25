# AI Drama Studio 启动脚本
Write-Host "Starting AI Drama Studio..." -ForegroundColor Green

# Check if backend is already running
$existing = netstat -ano | findstr ":8013" | findstr "LISTENING"
if ($existing) {
    Write-Host "Server already running on port 8013" -ForegroundColor Yellow
} else {
    # Start the FastAPI server
    cd "C:\Users\Administrator\Documents\ChatGPT\AI漫剧真人剧\ai-drama-studio\backend"
    Write-Host "Starting server on port 8013..." -ForegroundColor Cyan
    Start-Process python -ArgumentList "-m","uvicorn","app.main:app","--host","0.0.0.0","--port","8013" -WindowStyle Hidden
    Start-Sleep -Seconds 2
}

# Check if Agnes proxy is running
$agnes = netstat -ano | findstr ":57324" | findstr "LISTENING"
if (-not $agnes) {
    Write-Host "Starting Agnes proxy..." -ForegroundColor Cyan
    cd "C:\Users\Administrator\Documents\ChatGPT\微博热搜  腾讯热搜等热点项目"
    $env:AGNES_API_KEY = "sk-O3SeV8WTQ9NP6DrO0JcAXMirPrvYaI9nbzOelbqGRAe4QdrQ"
    Start-Process node -ArgumentList "agnes-proxy.js" -WindowStyle Hidden
    Start-Sleep -Seconds 1
}

# Check if Python proxy is running
$pyproxy = netstat -ano | findstr ":57322" | findstr "LISTENING"
if (-not $pyproxy) {
    Write-Host "Starting Agnes Python proxy..." -ForegroundColor Cyan
    cd "C:\Users\Administrator\Documents\ChatGPT\微博热搜  腾讯热搜等热点项目"
    $env:AGNES_API_KEY = "sk-O3SeV8WTQ9NP6DrO0JcAXMirPrvYaI9nbzOelbqGRAe4QdrQ"
    Start-Process python -ArgumentList "agnes-proxy.py" -WindowStyle Hidden
    Start-Sleep -Seconds 1
}

Write-Host ""
Write-Host "========================================" -ForegroundColor Green
Write-Host "AI Drama Studio is running!" -ForegroundColor Green
Write-Host "Open http://localhost:8013 in your browser" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Green
