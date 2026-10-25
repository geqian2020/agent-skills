---
name: media-analysis
description: >-
  Analyze full original content from Douyin/TikTok, Bilibili, YouTube, podcasts (including Xiaoyuzhou),
  webpages/blog posts, local videos/audio, and image galleries. Acquire all accessible media; transcribe
  entire audio with timestamped local Whisper checkpoints; OCR every image; preserve source citations.
  Use when user shares a media link, requests a transcript, asks to understand a video/post/podcast,
  or asks for detailed content analysis. Never infer the unseen content from a title or snippet.
---

# Media Analysis — acquire, verify, then analyze

This skill is **standalone**; it does not depend on, modify, or refer to any `video_rag` repository.
It is usable across projects in local code agents when installed at user scope.

## Contract

When given a video, audio, podcast, image-post or webpage URL:

1. Extract the URL from the full shared text; preserve the pasted caption separately from actual post body.
2. Determine media type: audio/video, gallery, article or mixed. A gallery must not be treated as a silent video or as a single thumbnail.
3. Attempt access with available built-in browser tools when appropriate, or invoke `scripts/media_analysis.py run --url ...` **only when local execution is available**. Never claim local commands ran when they did not.
4. Preserve the original source. Video and podcasts require entire accessible audio transcribed, not a platform summary, abstract, first 30 seconds, or screenshots. Image galleries require every obtainable original slide, ordered OCR and original caption. Articles require actual body text, not a snippet.
5. Check `manifest.json`: `status=complete` means *all locally discovered media* processed, **not** necessarily that every online gallery item was available. Check `remote_completeness_verified`, media counts, errors and the `content.md` source labels.
6. For responses, base claims on `content.md`, `evidence.jsonl`, and original files; cite [time] for ASR and [image index] for gallery OCR. Distinguish transcript assertions from own interpretation. If a specific clip, slide, claim or number could not be checked, say so clearly.
7. Do not substitute source guesses for access failures. State exactly what failed (DNS, authentication, extractor, download, ASR, OCR), and request the user-exported original as fallback.

## Local commands

From the skill folder:

```powershell
python scripts/media_analysis.py doctor
python scripts/media_analysis.py run --url "https://www.xiaoyuzhoufm.com/episode/XXXXXXXX" --asr-model "C:/models/whisper/small.pt"
python scripts/media_analysis.py run --url "https://v.douyin.com/XXXXXXXX/" --asr-model "C:/models/whisper/small.pt"
python scripts/media_analysis.py run --url "https://example.com/blog/article"
python scripts/media_analysis.py run --input "C:/media/saved-post" --asr-model "C:/models/whisper/small.pt"
```

A *complete offline ASR run* requires ffmpeg, ffprobe, `openai-whisper`, and a locally cached Whisper .pt. It **never silently downloads weights**. OCR requires `rapidocr-onnxruntime` and its packaged model weights. `yt-dlp` handles supported social/video sites. Public podcast media is resolved from page `og:audio`/HTML audio/episode RSS enclosure. Gallery links sometimes need platform-specific authentication or download tools; if automatic acquisition fails, use `--input` with originals saved from the platform app. Never bypass access controls or DRM.

Options: `--language zh` to force Chinese, `--output C:/media_archive` to specify output, `--cookies-from-browser chrome` **only with user's permission**, `--skip-ocr`/`--skip-asr` for partial acquisition (must label incomplete), `--download-only` to just save media (must label incomplete). `--input` also accepts .html/.htm local blog files.

## Evidence and completeness

Outputs for each source under a stable hash folder:
- `originals/`: original downloaded media, image files and extracted metadata; NEVER delete source material.
- `transcripts/*.jsonl` and `*.jsonl.windows/`: timestamped Whisper segments and restart checkpoints.
- `ocr.jsonl`: OCR lines with original image filenames and ordered image indices.
- `evidence.jsonl`: typed source evidence (web body / caption / image OCR / ASR segments).
- `content.md`: readable source text with timestamps and image indices.
- `manifest.json`: precise provenance, errors, completeness limits and SHA256 digests.

Output statuses: `complete` (all identified local assets processed), `partial` (some assets processed; required capability or extraction failed), `failed` (no usable source). `remote_completeness_verified` is false unless source gallery/media counts were independently checked.

## Analysis instructions

When analysis is requested, prefer exact topics and argument structure from the source. Start with a compact synthesis, then the strongest arguments, demonstrated evidence, disputed technical details and practical implications relevant to the user's question. For interviews, identify what the interviewer asks, whether answers are correct, and what is missing. For podcasts, mention what speakers actually said; do not invent quotes or attribute claims to guests based on chapter titles. Use only the actually acquired content and identify limits.

## Dependencies and setup

See `README.md` for Windows setup and user-scope installation. Do not install packages, download model weights, or use browser cookies silently. Reuse existing venv and cached models whenever possible.
