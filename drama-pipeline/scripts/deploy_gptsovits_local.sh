#!/usr/bin/env bash
# GPT-SoVITS 一键部署（Windows 整合包，RTX 50 系专用版）
# 用法: bash deploy_gptsovits_local.sh
# 前置: 整合包 7z 已下载到 /e/AITools/（由下载脚本完成）
set -e

PKG="/e/AITools/GPT-SoVITS-v2pro-20250604-nvidia50.7z"
DEST="/e/AITools/GPT-SoVITS"
EXPECTED=8835144925

size=$(stat -c%s "$PKG" 2>/dev/null || echo 0)
if [ "$size" -lt "$EXPECTED" ]; then
  echo "✗ 整合包未下载完（$size / $EXPECTED），等下载脚本跑完再执行"
  exit 1
fi

if [ ! -d "$DEST/runtime" ]; then
  echo "[1/3] 解压中（约 5-15 分钟）..."
  python - <<'EOF'
import py7zr, os
os.makedirs(r"E:\AITools", exist_ok=True)
with py7zr.SevenZipFile(r"E:\AITools\GPT-SoVITS-v2pro-20250604-nvidia50.7z") as z:
    z.extractall(r"E:\AITools")
EOF
  echo "    解压完成"
else
  echo "[1/3] 已解压，跳过"
fi

# 找 runtime 与 api 脚本
echo "[2/3] 目录结构:"
ls "$DEST" | head -10

echo "[3/3] 启动 api_v2（GPU 模式，监听 127.0.0.1:9880）"
# nvidia50 包的启动脚本名可能是 go-api.bat；直接用 runtime python 拉起 api_v2 更可控
cd "$DEST"
if [ -f "runtime/python.exe" ]; then
  ./runtime/python.exe api_v2.py -a 127.0.0.1 -p 9880 -c GPT_SoVITS/configs/tts_infer.yaml > /e/AITools/api_v2.log 2>&1 &
  echo "api_v2 已后台启动，日志: E:/AITools/api_v2.log"
else
  echo "未找到 runtime/python.exe，请查看上面的目录结构，手动运行 go-api.bat"
  exit 1
fi

sleep 25
curl -s -m 5 "http://127.0.0.1:9880/" -o /dev/null -w "api_v2 http: %{http_code}\n" || echo "api_v2 尚未就绪，查看日志"
