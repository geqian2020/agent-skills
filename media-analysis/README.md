# Media Analysis Skill / 跨项目媒体分析

**独立于 `video_rag` 的媒体获取、OCR 与完整音频转录工具。** 适用于抖音图文/视频、B站、YouTube、播客（包括小宇宙）、网页博客，以及已下载的本地文件。若平台接口/权限导致无法获取，绝不把摘要冒充全文。

## 1. 安装为本地 Agent 的全局 Skill（Windows）

先解压 ZIP，打开 PowerShell，进入 `media-analysis` 所在目录，运行：

```powershell
py media-analysis/setup_global.py --agent codex --agent claude
```

若已打开 Codex 或 Claude Code，重新启动或执行其 Skill 刷新操作。安装到：

- Codex: `%USERPROFILE%\.agents\skills\media-analysis\SKILL.md`
- Claude Code: `%USERPROFILE%\.claude\skills\media-analysis\SKILL.md`

这在**同一台电脑上跨 Codex/Claude Code 项目和会话可用**，但**不会自动安装到 ChatGPT 网站的所有对话**。ChatGPT 网页端当前没有可用的直接注册本地 Skill 的工具；可以在会话中上传产出的 `content.md` 与 `evidence.jsonl` 进行分析。

> 安装不依赖 Git。更新时重跑安装命令，会先备份已安装旧版（如有）。

## 2. 依赖检测

```powershell
py media-analysis/scripts/media_analysis.py doctor
```

至少需要 Python 3.10+；视频/音频处理需要 ffmpeg 和 ffprobe。安装功能时可复用已有环境，不要重复下载模型。

```powershell
py -m pip install yt-dlp beautifulsoup4 Pillow
# 需要 OCR 才安装，并确认 wheel 已具备 ONNX 模型：
py -m pip install rapidocr-onnxruntime==1.4.4
# 需要 Whisper 音频转录才安装，并指定本地权重：
py -m pip install openai-whisper==20250625
```

首次安装 Python 包可能联网；严格离线/公司电脑应使用批准 wheelhouse。**不自动下载 Whisper 权重或从陌生来源执行代码**。

## 3. 命令范例

```powershell
# 小宇宙 / 播客完整录音转文字：
py media-analysis/scripts/media_analysis.py run --url "https://www.xiaoyuzhoufm.com/episode/6a1fa171b30e1571ae9fce67" --asr-model "C:/models/whisper/small.pt" --language zh

# 抖音（支持直接粘贴含短链的整段分享文案）：
py media-analysis/scripts/media_analysis.py run --url "3.38 复制打开抖音 https://v.douyin.com/stMG1PSghXk/" --asr-model "C:/models/whisper/small.pt" --language zh

# 普通博客，下载网页正文：
py media-analysis/scripts/media_analysis.py run --url "https://example.com/article"

# 当抖音图文短链无法获取时，从官方客户端保存全部图片和配文，再离线导入：
py media-analysis/scripts/media_analysis.py run --input "C:/Downloads/douyin-post"

# 想先下载/存档，不进行转录或 OCR：
py media-analysis/scripts/media_analysis.py run --url "https://example.com/watch" --download-only
```

**抖音图文注意**：通用下载器 `yt-dlp` 可能不支持图文或遇到平台限制。脚本不会仅凭封面推断“已抓取全部图片”。如果未下载到全部素材，使用 `--input` 处理你从官方客户端导出的全部图片。即使程序状态 `complete`，也表示本地识别到的所有文件已处理，不能证明远端没有遗漏；查看 `manifest.json` 的 `remote_completeness_verified`。

博客仅抓可直接访问的网页正文；需要登录/JS 渲染的页面可能无法取全文，需使用页面导出的 HTML/PDF/文本或允许 Agent 浏览器访问。音频字幕不会从网页简介或节目摘要自动补写。

## 4. 输出和错误

默认输出 `~/MediaAnalysis/`，每篇作品一份稳定目录：

```text
MediaAnalysis/<hash>/
  originals/                  原始媒体（原文件不被删除）
  transcripts/xxx.jsonl       带时间戳的全部转录文本
  transcripts/xxx.jsonl.windows/  每个窗口的重启断点
  ocr.jsonl                   图片 OCR 原文及图片顺序
  evidence.jsonl              用于分析的可追溯证据
  content.md                  供人阅读/可直接上传 ChatGPT
  manifest.json               处理状态、文件列表、校验值、错误及边界
```

若 `manifest.json` 状态为 `partial` 或 `failed`，不能把结果当作完整内容。依赖或模型不足时只会报错，不会悄悄改用标题、简介或第三方摘要。

## 5. 其他 Session 如何使用

- **本地 Codex/Claude Code 新 Session**：安装完成后直接发送媒体链接并要求“使用 media-analysis 完整读取后分析”。代理能执行代码时会调用本地工具。
- **ChatGPT 网页的新 Session**：本地 Skill 不自动随账户安装。可将 `content.md`（以及需要时的图片、视频、JSONL）上传，再让 ChatGPT 基于它分析；不能声称自动跨会话调用。

## 6. 安全原则

仅对有权访问的内容操作；尊重平台限制和版权。Cookie 需明确授权才使用 `--cookies-from-browser`。分析以证据为准，原始素材留在本地。音轨可能含隐私，谨慎分享。调用外部下载工具时不执行 `shell=True`。
