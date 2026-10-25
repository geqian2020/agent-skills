#!/usr/bin/env python3
"""Standalone media acquisition and evidence extraction for local Agent Skills.

No video_rag imports, cloud LLM calls, automatic model downloads or shell execution.
Python >=3.10. Optional yt-dlp, openai-whisper, rapidocr-onnxruntime, BeautifulSoup.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
from html.parser import HTMLParser
import importlib.util
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
from urllib.parse import urljoin, urlparse
from urllib.request import Request, urlopen
import xml.etree.ElementTree as ET

AUDIO = {'.mp3', '.m4a', '.wav', '.ogg', '.opus', '.flac', '.aac', '.wma'}
VIDEO = {'.mp4', '.mkv', '.webm', '.mov', '.avi', '.m4v'}
IMAGES = {'.jpg', '.jpeg', '.png', '.webp', '.bmp', '.tif', '.tiff'}
HTML = {'.html', '.htm'}
UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/130 Safari/537.36'
URL_RE = re.compile(r'https?://[^\s<>\[\]"\'，。！？；（）]+', re.I)
MAX_HTML = 7 * 1024 * 1024
MAX_MEDIA = 2 * 1024 * 1024 * 1024


class MediaError(Exception):
    def __init__(self, stage: str, message: str):
        super().__init__(message)
        self.stage = stage


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def atomic_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile('w', encoding='utf-8', dir=path.parent,
                                     prefix='.write-', delete=False) as f:
        temp = Path(f.name)
        try:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        except BaseException:
            temp.unlink(missing_ok=True)
            raise
    os.replace(temp, path)


def write_json(path: Path, obj) -> None:
    atomic_text(path, json.dumps(obj, ensure_ascii=False, indent=2) + '\n')


def write_jsonl(path: Path, rows: list[dict]) -> None:
    atomic_text(path, ''.join(json.dumps(x, ensure_ascii=False) + '\n' for x in rows))


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open('rb') as f:
        for block in iter(lambda: f.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def stable_id(value: str) -> str:
    return hashlib.sha256(value.encode('utf-8')).hexdigest()[:18]


def get_url(value: str) -> str:
    m = URL_RE.search(value or '')
    if not m:
        raise MediaError('input', '未找到 http(s) 分享链接；也可以用 --input 处理本地媒体。')
    return m.group().rstrip('.,;!?)）>')


def http_read(url: str, limit: int = MAX_HTML) -> tuple[bytes, str, str]:
    try:
        with urlopen(Request(url, headers={'User-Agent': UA}), timeout=35) as r:
            mime = (r.headers.get('Content-Type') or '').lower().split(';')[0]
            data = r.read(limit + 1)
            if len(data) > limit:
                raise MediaError('network', f'网页超过 {limit} 字节上限；可能不是 HTML。')
            return data, r.geturl(), mime
    except MediaError:
        raise
    except Exception as exc:
        raise MediaError('network', f'无法访问 {url}：{type(exc).__name__}: {exc}') from exc


def download_file(url: str, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = Path(str(path) + '.part')
    # Never re-use a partial whose Range validity cannot be verified.
    if path.is_file() and path.stat().st_size > 0:
        return path
    try:
        with urlopen(Request(url, headers={'User-Agent': UA}), timeout=60) as response:
            advertised = response.headers.get('Content-Length', '')
            if advertised.isdigit() and int(advertised) > MAX_MEDIA:
                raise MediaError('download', '源媒体超过 2 GiB 安全上限。')
            total = 0
            with partial.open('wb') as out:
                for chunk in iter(lambda: response.read(1024 * 1024), b''):
                    total += len(chunk)
                    if total > MAX_MEDIA:
                        raise MediaError('download', '下载超过 2 GiB 安全上限。')
                    out.write(chunk)
            if not total:
                raise MediaError('download', '服务器返回零字节文件。')
            if advertised.isdigit() and total != int(advertised):
                raise MediaError('download', f'文件不完整：收到 {total}/{advertised} 字节。')
            os.replace(partial, path)
    except MediaError:
        raise
    except Exception as exc:
        raise MediaError('download', f'媒体下载失败：{type(exc).__name__}: {exc}') from exc
    return path


class PageMeta(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.metas: dict[str, str] = {}
        self.audio_urls: list[str] = []
        self.title_parts: list[str] = []
        self._title = False

    def handle_starttag(self, tag: str, attrs):
        a = dict(attrs)
        if tag == 'meta':
            key = (a.get('property') or a.get('name') or '').lower()
            value = a.get('content') or ''
            if key and value:
                self.metas.setdefault(key, value)
        elif tag in ('audio', 'source'):
            source = a.get('src') or ''
            if source:
                self.audio_urls.append(source)
        elif tag == 'title':
            self._title = True

    def handle_endtag(self, tag: str):
        if tag == 'title':
            self._title = False

    def handle_data(self, data: str):
        if self._title:
            self.title_parts.append(data)


def decode_html(data: bytes) -> str:
    # Most supported sites serve UTF-8; a caller may specify character sets later.
    return data.decode('utf-8-sig', errors='replace')


def find_audio_url(html: str, base: str) -> str | None:
    p = PageMeta()
    p.feed(html)
    for k in ('og:audio', 'og:audio:url', 'og:audio:secure_url', 'twitter:player:stream'):
        if p.metas.get(k):
            return urljoin(base, p.metas[k])
    for item in p.audio_urls:
        return urljoin(base, item)
    # Xiaoyuzhou pages may expose an enclosure URL in serialized page data.
    for pattern in [r'"enclosure"\s*:\s*\{[^{}]{0,1500}?"url"\s*:\s*"(https?[^"\\]*(?:\\.[^"\\]*)*)"',
                    r'"audioUrl"\s*:\s*"(https?[^"\\]*(?:\\.[^"\\]*)*)"']:
        m = re.search(pattern, html)
        if m:
            return m.group(1).replace(r'\u002F', '/').replace(r'\/', '/').replace(r'\u0026', '&')
    return None


def rss_audio(data: bytes, title_match: str | None = None) -> tuple[str, str]:
    try:
        root = ET.fromstring(data)
    except ET.ParseError as exc:
        raise MediaError('podcast', f'RSS XML 非法：{exc}') from exc
    items = root.findall('.//item')
    if title_match:
        items = [x for x in items if title_match.casefold() in (x.findtext('title') or '').casefold()]
    if len(items) != 1:
        raise MediaError('podcast', f'RSS 匹配到 {len(items)} 集；请提供单集页面或 --episode-title 精确筛选。')
    enclosure = items[0].find('enclosure')
    if enclosure is None or not enclosure.get('url'):
        raise MediaError('podcast', '该 RSS 单集缺少音频 enclosure 地址。')
    return enclosure.get('url'), (items[0].findtext('title') or 'podcast')


def page_body(html: str) -> tuple[str, str]:
    meta = PageMeta()
    meta.feed(html)
    title = (meta.metas.get('og:title') or ''.join(meta.title_parts)).strip()
    try:
        from bs4 import BeautifulSoup
    except ImportError as exc:
        raise MediaError('article', '提取文章正文需要 beautifulsoup4：py -m pip install beautifulsoup4。') from exc
    soup = BeautifulSoup(html, 'html.parser')
    for tag in soup(['script', 'style', 'nav', 'footer', 'header', 'aside', 'noscript', 'form', 'iframe']):
        tag.decompose()
    main = soup.find('article') or soup.find('main') or soup.find(attrs={'role': 'main'}) or soup.body
    if not main:
        raise MediaError('article', 'HTML 中没有可识别正文区域。')
    blocks = []
    for t in main.find_all(['h1', 'h2', 'h3', 'h4', 'p', 'li', 'blockquote', 'pre']):
        if t.find_parent(['p', 'li', 'blockquote', 'pre']):
            continue
        s = re.sub(r'\s+', ' ', t.get_text(' ', strip=True)).strip()
        if s and (len(s) >= 4 or t.name.startswith('h')):
            blocks.append(s)
    if not blocks:
        text = re.sub(r'\s+', ' ', main.get_text(' ', strip=True))
        blocks = [text] if text else []
    body = '\n\n'.join(dict.fromkeys(blocks)).strip()
    if len(body) < 80:
        raise MediaError('article', '正文过短，可能是登录墙、动态渲染或反爬页面；不能视为完整博客。')
    return title or '网页文章', body


def list_media(root: Path) -> tuple[list[Path], list[Path]]:
    if not root.is_dir():
        raise MediaError('input', f'素材目录不存在：{root}')
    imgs, av = [], []
    for i, file in enumerate(sorted(root.rglob('*'), key=lambda p: p.as_posix().lower())):
        if i > 10000:
            raise MediaError('input', '文件过多：请仅提供单个作品的媒体目录。')
        if not file.is_file() or file.is_symlink():
            continue
        if file.suffix.lower() in IMAGES:
            imgs.append(file)
        elif file.suffix.lower() in AUDIO | VIDEO:
            av.append(file)
    return imgs, av


def ytdlp_download(url: str, root: Path, cookies: str | None = None) -> tuple[dict, list[Path]]:
    if importlib.util.find_spec('yt_dlp') is None:
        raise MediaError('download', '未找到 yt-dlp；安装后重试，图文可能仍需导出原图。')
    root.mkdir(parents=True, exist_ok=True)
    cmd = [sys.executable, '-m', 'yt_dlp', '--no-playlist', '--no-overwrites',
           '--write-info-json', '--no-progress',
           '-o', str(root / '%(id)s.%(ext)s')]
    if cookies:
        cmd += ['--cookies-from-browser', cookies]
    cmd += [url]
    try:
        run = subprocess.run(cmd, capture_output=True, text=True,
                             encoding='utf-8', errors='replace', timeout=1800)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise MediaError('download', f'yt-dlp 无法执行或超时：{exc}') from exc
    imgs, av = list_media(root)
    if run.returncode or not imgs and not av:
        err = (run.stderr or run.stdout or '').strip()[-1600:]
        raise MediaError('download', '平台下载失败；可能是短链、访问权限或提取器不支持图文。' + ('\n' + err if err else ''))
    infos = list(root.glob('*.info.json'))
    metadata = {}
    if infos:
        try:
            metadata = json.loads(infos[0].read_text(encoding='utf-8'))
        except (ValueError, OSError):
            pass
    return metadata, imgs + av


def media_info(path: Path) -> dict:
    ffprobe = shutil.which('ffprobe')
    if not ffprobe:
        raise MediaError('probe', '没有 ffprobe；需要安装 FFmpeg 并加入 PATH。')
    try:
        x = subprocess.run([ffprobe, '-v', 'error', '-show_format', '-show_streams',
                            '-of', 'json', str(path)], capture_output=True, text=True,
                           check=True, timeout=100)
        d = json.loads(x.stdout)
        duration = float(d.get('format', {}).get('duration', 0))
        if not math.isfinite(duration) or duration <= 0:
            raise ValueError('duration missing or invalid')
        return {'duration': duration,
                'audio_streams': sum(s.get('codec_type') == 'audio' for s in d.get('streams', [])),
                'video_streams': sum(s.get('codec_type') == 'video' for s in d.get('streams', []))}
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, ValueError, OSError) as exc:
        raise MediaError('probe', f'无法读取完整媒体时长或音轨：{path.name}: {exc}') from exc


def check_whisper_model(value: str) -> str:
    candidate = Path(value).expanduser()
    if candidate.is_file():
        if candidate.suffix.lower() != '.pt':
            raise MediaError('asr', f'Whisper 模型必须是可信的 .pt 文件：{candidate}')
        return str(candidate.resolve())
    if any(sep in value for sep in '/\\') or value.endswith('.pt'):
        raise MediaError('asr', f'模型文件不存在：{candidate}。不会自动下载模型。')
    cache = Path(os.getenv('XDG_CACHE_HOME', str(Path.home() / '.cache'))) / 'whisper' / (value + '.pt')
    if not cache.is_file():
        raise MediaError('asr', f'本地没有缓存的 Whisper {value} 模型：{cache}。不会自动下载模型；请先准备可信模型。')
    return str(cache)


def asr(media: Path, out: Path, model_name: str, language: str,
        seconds: float = 30, *, loader=None, transcribe=None) -> list[dict]:
    info = media_info(media)
    if info['audio_streams'] == 0:
        raise MediaError('asr', '视频不包含音轨，无法转录语音；仍需用视觉处理确认画面内容。')
    model_path = check_whisper_model(model_name) if loader is None else model_name
    if loader is None:
        try:
            import whisper
        except ImportError as exc:
            raise MediaError('asr', 'openai-whisper 未安装。请复用现有环境或先安装。') from exc
        loader = lambda: whisper.load_model(model_path)
    duration = info['duration']
    count = math.ceil(duration / seconds)
    ident = sha256_file(media)
    model_id = str(model_path) if loader is None else model_name
    fingerprint = stable_id(json.dumps({'v': 1, 'source': ident, 'model': model_id,
                                        'language': language, 'window': seconds}, sort_keys=True))
    checkpoint_dir = Path(str(out) + '.windows')
    checkpoint_dir.mkdir(parents=True, exist_ok=True)
    caches: list[dict | None] = []
    for i in range(count):
        f = checkpoint_dir / f'{i:06d}.json'
        cached = None
        if f.is_file():
            try:
                d = json.loads(f.read_text(encoding='utf-8'))
                if d.get('fingerprint') == fingerprint and d.get('index') == i and isinstance(d.get('rows'), list):
                    c = d['rows']
                    correct = all(isinstance(r, dict) and r.get('start', -1) >= i * seconds
                                  and r.get('end', float('inf')) <= min(duration, (i + 1) * seconds) + .001
                                  and r.get('end', -1) > r.get('start', 0)
                                  and isinstance(r.get('text'), str) for r in c)
                    digest = hashlib.sha256(json.dumps(c, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
                    if correct and digest == d.get('digest'):
                        cached = d
            except (OSError, ValueError, TypeError, KeyError):
                pass
        caches.append(cached)
    whisper_model = loader() if any(c is None for c in caches) else None
    ffmpeg = shutil.which('ffmpeg')
    if any(c is None for c in caches) and not ffmpeg:
        raise MediaError('asr', '缺少 ffmpeg；无法按时间窗提取完整音轨。')
    for i in range(count):
        if caches[i] is not None:
            print(f'[ASR] 复用窗口 {i+1}/{count}', flush=True)
            continue
        start = i * seconds
        end = min(duration, start + seconds)
        with tempfile.TemporaryDirectory(prefix='media-asr-') as t:
            wav = Path(t) / 'window.wav'
            try:
                subprocess.run([ffmpeg, '-nostdin', '-loglevel', 'error', '-y',
                                '-ss', str(start), '-i', str(media), '-t', str(end-start),
                                '-vn', '-ar', '16000', '-ac', '1', '-c:a', 'pcm_s16le', str(wav)],
                               capture_output=True, check=True, timeout=300)
            except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError) as exc:
                raise MediaError('asr', f'窗口 {i+1}/{count} 提取音频失败：{exc}') from exc
            try:
                if transcribe is not None:
                    results = transcribe(wav, language)
                else:
                    kwargs = {'verbose': False, 'fp16': False, 'condition_on_previous_text': False}
                    if language != 'auto':
                        kwargs['language'] = language
                    results = whisper_model.transcribe(str(wav), **kwargs)
            except Exception as exc:
                raise MediaError('asr', f'Whisper 第 {i+1} 窗口识别失败：{exc}') from exc
        rows = []
        for s in results.get('segments', []):
            text = str(s.get('text') or '').strip()
            if not text:
                continue
            a, b = float(s['start']), float(s['end'])
            a, b = start + max(0., min(a, end - start)), start + max(0., min(b, end - start))
            if b > a:
                rows.append({'start': round(a, 3), 'end': round(b, 3), 'text': text,
                             'source_file': media.name, 'method': 'whisper_asr'})
        digest = hashlib.sha256(json.dumps(rows, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
        cp = {'index': i, 'fingerprint': fingerprint, 'start': start, 'end': end,
              'rows': rows, 'digest': digest}
        write_json(checkpoint_dir / f'{i:06d}.json', cp)
        caches[i] = cp
        print(f'[ASR] 完成窗口 {i+1}/{count}，{len(rows)} 条，断点已保存', flush=True)
    rows = [r for c in caches for r in c['rows']]
    write_jsonl(out, rows)
    write_json(Path(str(out) + '.audit.json'),
               {'source_sha256': ident, 'model': model_name, 'language': language,
                'duration_seconds': duration, 'windows_total': count, 'windows_completed': len(caches),
                'audio_streams': info['audio_streams'],
                'transcript_sha256': sha256_file(out), 'finished_at': now(),
                'note': 'Whisper ASR, not human-reviewed; errors and hallucinations are possible.'})
    return rows


def ocr_image(img: Path, engine) -> list[dict]:
    from PIL import Image
    with Image.open(img) as im:
        im.verify()
    try:
        result, _ = engine(str(img))
    except Exception as exc:
        raise MediaError('ocr', f'图片 OCR 失败：{img.name}: {exc}') from exc
    lines = []
    for rec in result or []:
        if not isinstance(rec, (tuple, list)) or len(rec) < 3:
            continue
        box, text, confidence = rec[:3]
        if str(text).strip():
            lines.append({'text': str(text), 'confidence': float(confidence), 'box': box})
    return lines


def load_ocr():
    if importlib.util.find_spec('rapidocr_onnxruntime') is None:
        raise MediaError('ocr', '没有 rapidocr-onnxruntime；安装经批准的 OCR 包和本地模型。')
    if importlib.util.find_spec('PIL') is None:
        raise MediaError('ocr', '没有 Pillow；需要安装图像校验依赖。')
    from rapidocr_onnxruntime import RapidOCR
    return RapidOCR()


def original_description(root: Path, ytdlp_info: dict) -> tuple[str, str]:
    title = str(ytdlp_info.get('title') or '')
    description = str(ytdlp_info.get('description') or '')
    for f in sorted(root.glob('*.description')) + sorted(root.glob('description.txt')):
        if not description and f.stat().st_size < 1024 * 1024:
            description = f.read_text(encoding='utf-8-sig', errors='replace').strip()
    for f in sorted(root.glob('*.info.json')):
        if title and description:
            break
        try:
            j = json.loads(f.read_text(encoding='utf-8'))
            title = title or str(j.get('title') or '')
            description = description or str(j.get('description') or '')
        except (ValueError, OSError):
            pass
    return title, description


def expected_gallery_count(info: dict) -> int | None:
    candidates = [info.get('image_post_info'), info.get('aweme_detail'), info]
    for obj in candidates:
        if isinstance(obj, dict):
            images = obj.get('images')
            if isinstance(images, list) and images:
                return len(images)
    return None


def read_status(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except (ValueError, OSError):
        return None


def build_content(url: str, title: str, caption: str, article: str,
                  image_rows: list[dict], transcripts: list[dict], root: Path,
                  completeness: str) -> str:
    lines = ['# 媒体原文归档', '', f'来源：{url or "本地文件"}',
             f'状态说明：{completeness}', '']
    if title:
        lines.extend(['## 标题', title, ''])
    if caption:
        lines.extend(['## 发布者配文（不是完整转录）', caption, ''])
    if article:
        lines.extend(['## 网页文章原文（正文提取）', article, ''])
    for x in image_rows:
        lines.extend([f'## 图 {x["index"]}：{x["source_file"]}',
                      '\n'.join(row['text'] for row in x['lines']) or '（未检测到可识别文字）', ''])
    for item in transcripts:
        lines.extend([f'## 音轨转录：{item["source_file"]}',
                      '注意：以下为 Whisper 自动转录，未人工核对。', ''])
        for seg in item['rows']:
            a, b = seg['start'], seg['end']
            lines.append(f'[{int(a//60):02d}:{a%60:05.2f}–{int(b//60):02d}:{b%60:05.2f}] {seg["text"]}')
        lines.append('')
    return '\n'.join(lines).rstrip() + '\n'


def work(args, *, ocr_loader=None, transcriber=None, fetch_page=http_read,
         video_downloader=ytdlp_download) -> Path:
    if not args.url and not args.input:
        raise MediaError('input', '至少指定 --url 或 --input。')
    source = get_url(args.url) if args.url else ''
    given = Path(args.input).expanduser().resolve() if args.input else None
    if given and not given.exists():
        raise MediaError('input', f'本地源不存在：{given}')
    key = stable_id(source or str(given))
    out = Path(args.output).expanduser().resolve() / key
    out.mkdir(parents=True, exist_ok=True)
    original = out / 'originals'
    manifest_path = out / 'manifest.json'
    state = {'version': 1, 'source': source or str(given), 'key': key,
             'created_at': now(), 'status': 'started', 'remote_completeness_verified': False,
             'assets': [], 'errors': [], 'limitations': [], 'processing': {}}
    write_json(manifest_path, state)
    evidence: list[dict] = []
    article = ''; title = ''; caption = ''
    images: list[Path] = []; media: list[Path] = []
    ytdlp_info = {}
    local_root = given if given and given.is_dir() else given.parent if given else original
    try:
        if given:
            if given.is_file() and given.suffix.lower() in HTML:
                article_html = given.read_text(encoding='utf-8-sig')
                title, article = page_body(article_html)
            elif given.is_file() and given.suffix.lower() in IMAGES:
                images = [given]
            elif given.is_file() and given.suffix.lower() in AUDIO | VIDEO:
                media = [given]
            elif given.is_dir():
                images, media = list_media(given)
                title, caption = original_description(given, {})
                for html_file in sorted(given.glob('*.html'))[:1]:
                    try:
                        title, article = page_body(html_file.read_text(encoding='utf-8-sig'))
                    except MediaError as exc:
                        state['limitations'].append(f'本地 HTML 未解析：{exc}')
                if not images and not media and not article:
                    raise MediaError('input', '目录中没有支持的媒体文件或有效 HTML 正文。')
            else:
                raise MediaError('input', f'暂不支持此本地文件类型：{given.suffix}')
        else:
            u = urlparse(source)
            # Direct media files: download the bytes rather than interpreting as a page.
            ext = Path(u.path).suffix.lower()
            if ext in AUDIO | VIDEO | IMAGES:
                path = download_file(source, original / ('source' + ext))
                images = [path] if ext in IMAGES else []
                media = [path] if ext in AUDIO | VIDEO else []
            elif any(domain in u.netloc.lower() for domain in ('douyin.com', 'tiktok.com', 'youtube.com', 'youtu.be', 'bilibili.com', 'b23.tv')):
                ytdlp_info, _ = video_downloader(source, original, args.cookies_from_browser)
                images, media = list_media(original)
                title, caption = original_description(original, ytdlp_info)
                if 'douyin.com' in u.netloc.lower():
                    state['limitations'].append('抖音图文平台页面可能不被 yt-dlp 支持；如缺图或下载失败，请导出原始图集离线导入。')
            else:
                data, resolved, mime = fetch_page(source)
                if mime in ('audio/mpeg', 'audio/mp4', 'audio/wav', 'video/mp4'):
                    ext = '.mp3' if mime == 'audio/mpeg' else '.m4a' if mime == 'audio/mp4' else '.wav' if mime == 'audio/wav' else '.mp4'
                    path = download_file(resolved, original / ('source' + ext))
                    media = [path]
                else:
                    html = decode_html(data)
                    original.mkdir(parents=True, exist_ok=True)
                    atomic_text(original / 'webpage.html', html)
                    if mime in ('text/xml', 'application/rss+xml', 'application/xml') or html.lstrip().startswith('<?xml'):
                        audio_url, title = rss_audio(data, args.episode_title)
                        media = [download_file(urljoin(resolved, audio_url), original / 'episode.mp3')]
                    else:
                        p = PageMeta(); p.feed(html)
                        title = (p.metas.get('og:title') or ''.join(p.title_parts)).strip()
                        audio_url = find_audio_url(html, resolved)
                        if audio_url:
                            remote_name = Path(urlparse(audio_url).path).name
                            suffix = Path(remote_name).suffix.lower()
                            if suffix not in AUDIO | VIDEO:
                                remote_name = 'episode.mp3'  # ffprobe verifies actual media afterward
                            media = [download_file(audio_url, original / remote_name)]
                            caption = p.metas.get('og:description') or ''
                        elif any(d in u.netloc.lower() for d in ('xiaoyuzhoufm.com', 'fuyoutiandi.com', 'podcasts.apple.com')):
                            raise MediaError('podcast', '播客页面未解析到完整音频地址；不能把节目简介当成完整转录。可尝试官方 RSS 或保存原始 MP3。')
                        else:
                            title, article = page_body(html)
        if not images and not media and not article:
            raise MediaError('extract', '没有实际获取任何正文、音频、视频或图片；不能用标题或摘要替代原文。')
        if article:
            evidence.append({'modality': 'web_article', 'source': source or str(given),
                             'text': article, 'extraction': 'main_content', 'reviewed': False})
        if caption:
            evidence.append({'modality': 'publisher_caption', 'source': source or str(given),
                             'text': caption, 'is_transcript': False})
        count = expected_gallery_count(ytdlp_info)
        gallery_hinted = bool(re.search(r'图文作品|图文合集|图集作品|photo\s*post', args.url or '', re.I)) or count is not None
        state['gallery_expected'] = gallery_hinted
        if gallery_hinted and not images:
            state['errors'].append({'stage': 'download', 'error': '作品显示为图文，但未获取到任何原始图片；不能将视频/封面视为图文完整下载。'})
        state['remote_image_count'] = count
        state['acquired_image_count'] = len(images)
        state['remote_completeness_verified'] = bool(count is not None and len(images) == count)
        if count is not None and len(images) != count:
            state['errors'].append({'stage': 'download', 'error': f'图集数量不符：预期 {count} 张，获取 {len(images)} 张。'})
        original_page = original / 'webpage.html'
        source_assets = images + media + ([original_page] if original_page.is_file() else [])
        state['assets'] = [{'file': str(x), 'bytes': x.stat().st_size, 'sha256': sha256_file(x)}
                           for x in source_assets]
        write_json(manifest_path, state)
        image_rows = []; transcript_blocks = []
        if images:
            if args.download_only or args.skip_ocr:
                state['errors'].append({'stage': 'ocr', 'error': f'{len(images)} 张图片未执行 OCR（显式跳过）。'})
            else:
                try:
                    engine = (ocr_loader or load_ocr)()
                except Exception as exc:
                    state['errors'].append({'stage': getattr(exc, 'stage', 'ocr'), 'error': str(exc)})
                    engine = None
                for idx, img in enumerate(images, 1):
                    if engine is None:
                        break
                    try:
                        lines = ocr_image(img, engine)
                    except Exception as exc:
                        state['errors'].append({'stage': 'ocr', 'file': str(img), 'error': str(exc)})
                        continue
                    if not lines:
                        state['limitations'].append(f'第 {idx} 张图未检测到文字；OCR 不等于理解图片画面。')
                    image_rows.append({'index': idx, 'source_file': img.name, 'path': str(img), 'lines': lines})
                    for line in lines:
                        evidence.append({'modality': 'image_ocr', 'source': source or str(given),
                                         'image_index': idx, 'file': str(img), 'text': line['text'],
                                         'confidence': line['confidence'], 'box': line['box']})
                    write_jsonl(out / 'ocr.jsonl', image_rows)
                    state['processing']['images_processed'] = len(image_rows)
                    write_json(manifest_path, state)
        if media:
            if args.download_only or args.skip_asr:
                state['errors'].append({'stage': 'asr', 'error': f'{len(media)} 个音视频文件未执行 ASR（显式跳过）。'})
            else:
                for idx, item in enumerate(media, 1):
                    sub = out / 'transcripts' / f'{stable_id(str(item))}.jsonl'
                    try:
                        rows = (transcriber or asr)(item, sub, args.asr_model, args.language,
                                                    args.window_seconds)
                    except Exception as exc:
                        state['errors'].append({'stage': getattr(exc, 'stage', 'asr'),
                                                'file': str(item), 'error': str(exc)})
                        continue
                    transcript_blocks.append({'source_file': item.name, 'rows': rows,
                                              'file': str(sub)})
                    for row in rows:
                        evidence.append({'modality': 'audio_asr', 'source': source or str(given),
                                         'file': str(item), 'start': row['start'], 'end': row['end'],
                                         'text': row['text'], 'reviewed': False})
                    state['processing']['media_transcribed'] = len(transcript_blocks)
                    write_json(manifest_path, state)
        if article or caption or image_rows or transcript_blocks:
            state['status'] = 'partial' if state['errors'] else 'complete'
        else:
            state['status'] = 'partial' if images or media else 'failed'
        if media and not transcript_blocks and not state['errors']:
            # A media file without an audio stream cannot yield a speech transcript;
            # we did not understand its visual content.
            state['status'] = 'partial'
            state['limitations'].append('视频缺少音轨或没有可转录语音；仅凭音频流程不能理解视觉内容。')
        if not state['remote_completeness_verified'] and source and (images or 'douyin.com' in urlparse(source).netloc.lower()):
            state['limitations'].append('远端图集总张数未验证：处理了已发现图片，不保证线上全部页面均已获取。')
        if media:
            state['limitations'].append('ASR 是机器转录，不代表对视频画面进行逐帧理解或人工逐字审校。')
        state['finished_at'] = now()
        write_jsonl(out / 'evidence.jsonl', evidence)
        write_json(out / 'manifest.json', state)
        atomic_text(out / 'content.md', build_content(source or str(given), title, caption, article,
                       image_rows, transcript_blocks, out,
                       f'本地处理={state["status"]}；远端图集完整性={"已核对张数" if state["remote_completeness_verified"] else "未核实"}。'))
        return out
    except Exception as exc:
        state['status'] = 'failed'
        state['errors'].append({'stage': getattr(exc, 'stage', 'internal'), 'error': str(exc)})
        state['finished_at'] = now()
        write_json(manifest_path, state)
        raise


def doctor() -> dict:
    return {'python': sys.version.split()[0], 'ffmpeg': shutil.which('ffmpeg'),
            'ffprobe': shutil.which('ffprobe'),
            'yt_dlp': importlib.util.find_spec('yt_dlp') is not None,
            'openai_whisper': importlib.util.find_spec('whisper') is not None,
            'rapidocr_onnxruntime': importlib.util.find_spec('rapidocr_onnxruntime') is not None,
            'Pillow': importlib.util.find_spec('PIL') is not None,
            'beautifulsoup4': importlib.util.find_spec('bs4') is not None}


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest='command', required=True)
    sub.add_parser('doctor', help='检查现有环境，不下载安装任何东西')
    run = sub.add_parser('run', help='获取/导入并解析完整原始内容')
    src = run.add_mutually_exclusive_group(required=True)
    src.add_argument('--url', help='URL 或整段包含 URL 的分享文案')
    src.add_argument('--input', help='已保存的图片目录、音视频文件或网页 HTML 文件')
    run.add_argument('--output', default=str(Path.home() / 'MediaAnalysis'))
    run.add_argument('--asr-model', default='small', help='本地 Whisper .pt 路径或已有缓存的模型名称')
    run.add_argument('--language', default='auto', help='auto/zh/en 等语言代码')
    run.add_argument('--window-seconds', type=float, default=30., help='每个 Whisper 音轨窗口的秒数')
    run.add_argument('--episode-title', default=None, help='若输入是含多集的 RSS，用此标题子串选择单集')
    run.add_argument('--cookies-from-browser', default=None,
                     help='仅经用户明确许可，向 yt-dlp 授权读取指定浏览器的 cookie')
    run.add_argument('--skip-ocr', action='store_true')
    run.add_argument('--skip-asr', action='store_true')
    run.add_argument('--download-only', action='store_true')
    args = p.parse_args(argv)
    if args.command == 'doctor':
        print(json.dumps(doctor(), ensure_ascii=False, indent=2))
        return 0
    if not 5 <= args.window_seconds <= 60 or not math.isfinite(args.window_seconds):
        p.error('--window-seconds 必须在 5 到 60 秒之间')
    try:
        folder = work(args)
    except MediaError as exc:
        print(f'[FAIL:{exc.stage}] {exc}', file=sys.stderr)
        return 2
    except Exception as exc:
        print(f'[FAIL:internal] {type(exc).__name__}: {exc}', file=sys.stderr)
        return 3
    info = read_status(folder / 'manifest.json') or {}
    print(f'[STATE] {info.get("status", "unknown")}')
    for problem in info.get('errors', []):
        print(f'[WARN:{problem["stage"]}] {problem["error"]}', file=sys.stderr)
    print(f'[CONTENT] {folder / "content.md"}')
    print(f'[EVIDENCE] {folder / "evidence.jsonl"}')
    return 0 if info.get('status') == 'complete' else 2


if __name__ == '__main__':
    raise SystemExit(main())
