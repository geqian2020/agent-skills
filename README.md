# Agent Skills | 可复用 Agent 技能库

这个仓库独立于所有业务项目，用于管理**可在本地 Agent 全局安装的 Skills**。

## Skills 目录

| Skill | 用途 | 状态 |
|---|---|---|
| [`media-analysis`](media-analysis/SKILL.md) | 抖音、B站、YouTube、播客、博客、图文和本地媒体的完整素材提取与证据化分析 | v1（离线测试通过，真实抖音链接仍需验收） |

## 快速安装（Windows，不需要 Git）

1. 在 GitHub 页面使用 **Code → Download ZIP** 下载整个仓库，然后解压。
2. 双击仓库根目录的 **`安装到Codex和ClaudeCode.cmd`**。
3. 重新打开 Codex / Claude Code，在任何本地项目会话里使用 `media-analysis`。
4. 首次使用前，运行 `检查现有依赖.cmd`。需要下载/识别的功能依赖所需工具与已有本地模型；不要在公司电脑上擅自安装未经批准的依赖。

手动安装：

```powershell
py media-analysis/setup_global.py --agent codex --agent claude
```

安装位置：`%USERPROFILE%\.agents\skills\media-analysis` 和 `%USERPROFILE%\.claude\skills\media-analysis`。

> 这不是 ChatGPT 网页插件，**不会**自动让 ChatGPT 网页端的新 Session 获取本地文件/执行视频下载。可先用本地工具导出 `content.md` 和 `evidence.jsonl`，再上传到 ChatGPT 做分析。

## 检查和测试

```powershell
py media-analysis/scripts/media_analysis.py doctor
py -m pytest -q media-analysis/tests/test_media_analysis.py
```

## 数据和完整性要求

- 不凭短链标题、节目介绍或平台摘要猜测完整内容。
- 图文尽力获取全部图片和原始配文；未核实在线完整性时明确标记。
- 音视频转录保留时间戳、每个窗口的检查点及证据追溯；不悄悄下载 Whisper 模型。
- 只访问有权访问的内容；尊重平台登录限制、版权及隐私。
- 原始媒体及私有转录**不提交 Git**。默认输出至用户主目录下的 `MediaAnalysis`。

具体说明见 [`media-analysis/README.md`](media-analysis/README.md)。
