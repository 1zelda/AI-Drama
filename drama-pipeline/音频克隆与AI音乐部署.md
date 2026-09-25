# 音频克隆与 AI 音乐 — 部署指南

> **2026-09-10 重大更新：本机 GPU 已升级为 RTX 5060 Ti 16GB**（驱动 610.47，torch 2.8+cu128 实测可用）——所有模型都能本机 GPU 跑，不再需要租卡/远程机。ComfyUI（SDXL/Wan2.2）本地化也重新可行。

## 一、声音克隆 TTS：GPT-SoVITS（推荐首选）

**为什么选它**：MIT 协议、5-15 秒参考音频零样本克隆（不用训练）、中文效果好、有 Windows 整合包、自带 api_v2 HTTP 接口。
备选：IndexTTS-2（情绪控制最强，商用需联系 B站）、CosyVoice3（Apache-2.0，有 vLLM 部署）。

### 本机部署（RTX 5060 Ti → 必须用 nvidia50 专用包）
1. 整合包下载：`GPT-SoVITS-v2pro-20250604-nvidia50.7z`（8.8GB，50 系专用构建；标准包的 torch 不支持 Blackwell 架构）
   - 镜像：hf-mirror.com/lj1995/GPT-SoVITS-windows-package（ModelScope 无此仓库）
   - 一键脚本（下载完执行）：`bash drama-pipeline/scripts/deploy_gptsovits_local.sh`（解压 → 拉起 api_v2 → 健康检查）
2. **安装路径必须纯英文**（GPT-SoVITS 对中文路径不友好）：脚本装到 `E:\AITools\GPT-SoVITS`
3. 音色库已就绪：`backend/data/voices/` 里「元首_暴怒 / 元首_沉怒」两条参考音频，德语文字稿已用 faster-whisper 转写填好（small 模型；追求更准可换 large-v3 重转）
4. `.env` 加 `GPTSOVITS_URL=http://127.0.0.1:9880`，配音台选音色即用
5. 16GB 显存余量大，可用元首素材批量切 20-50 条做底模微调（30 分钟），克隆更稳

### 提升克隆质量
- 参考音频：干声（无 BGM/混响）、5-15 秒、情绪与目标台词匹配
- 文字稿逐字对应，标点也对
- 愤怒台词用「元首_暴怒」参考，低沉台词用「元首_沉怒」

## 二、AI 音乐：ACE-Step（作曲+歌词演唱）

**16GB 显存本机可跑**（XL-4B 约需 10-12GB；1.5 版本更低）。
```bash
cd _upgrade/ACE-Step
pip install -r requirements.txt        # 与本机 Python 3.13 + torch 2.8 兼容
# 权重走 hf-mirror 下载：HF_ENDPOINT=https://hf-mirror.com huggingface-cli download ACE-Step/ACE-Step-v1-3.5B
python infer-api.py --port 8001        # REST API
```
`.env` 加 `ACESTEP_URL=http://127.0.0.1:8001`。

### 让元首来唱（歌声换声 = RVC）
ACE-Step 生成模型默认音色的演唱 → 在 RVC（`_upgrade/Retrieval-based-Voice-Conversion-WebUI`，元首干声 30-50 条、10 分钟训练）做声音转换。16GB 显存训练+推理都没问题。

## 三、平台内的入口

- **配音音乐台**（首页「配音音乐」）：音色管理 / 克隆 TTS 配音 / AI 写词+出曲，顶部自动探测两服务在线状态
- **API**（可被流水线节点调用）：
  - `POST /api/audio/tts` {text, engine: edge|gptsovits, voice_name} → 配音文件
  - `POST /api/audio/lyrics` {theme, style_tags, duration} → 歌词
  - `POST /api/audio/song` {lyrics, style_tags, duration} → 歌曲文件
- 元首参考音频：`backend/data/voices/ref_audios/元首_暴怒.wav`、`元首_沉怒.wav`（德语文字稿已填）

## 四、成片流程里的位置

```
剧情工坊定稿 → 台词逐句克隆 TTS（元首音色，本机 GPU）→ 关键帧/视频生成（云 API）→
配音对轨 → BGM/主题曲（ACE-Step 本机 + RVC 元首献唱）→ LaMa 去水印（本机 GPU）→ 剪映草稿精剪
```

## 五、GPU 加速现状（RTX 5060 Ti 16GB）

| 环节 | 状态 |
|---|---|
| LaMa 去水印 | ✅ 已切 GPU（watermark.py 自动探测，整镜 47s） |
| GPT-SoVITS 克隆 TTS | 🔄 nvidia50 整合包下载中 → 本机 GPU 推理/微调 |
| ACE-Step 音乐 | ⏳ 依赖与 torch 2.8 兼容，权重下载后本机 GPU 跑 |
| ComfyUI（SDXL/Wan2.2） | 🟢 重新可行（之前因 MX110 2GB 判死）——路由里的 comfyui 节点可考虑本地化 |
