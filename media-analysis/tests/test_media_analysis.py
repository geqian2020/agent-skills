"""Offline regression tests: NEVER need public websites or model downloads."""
import importlib.util
import json
from pathlib import Path
import subprocess
import shutil
import sys
from argparse import Namespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / 'scripts' / 'media_analysis.py'
spec = importlib.util.spec_from_file_location('media_analysis', SCRIPT)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


def args(tmp_path, **kwargs):
    d = dict(url=None, input=None, output=str(tmp_path / 'results'),
             asr_model='small', language='auto', window_seconds=30,
             episode_title=None, cookies_from_browser=None, skip_asr=False,
             skip_ocr=False, download_only=False)
    d.update(kwargs)
    return Namespace(**d)


def photo(tmp_path, name='01.png'):
    from PIL import Image
    p = tmp_path / name
    p.parent.mkdir(parents=True, exist_ok=True)
    Image.new('RGB', (28, 28), '#f8f8f8').save(p)
    return p


def test_share_text_extracts_url_without_trailing_chinese_punctuation():
    x = m.get_url('复制打开抖音极速版 https://v.douyin.com/stMG1PSghXk/。#话题')
    assert x == 'https://v.douyin.com/stMG1PSghXk/'
    with pytest.raises(m.MediaError):
        m.get_url('没提供网址')


def test_podcast_html_audio_is_not_shownotes():
    content = '''<html><head><meta property="og:audio" content="https://cdn.example.com/a.mp3">
      <meta property="og:description" content="这里只是摘要"></head><body>节目介绍</body></html>'''
    assert m.find_audio_url(content, 'https://www.xiaoyuzhoufm.com/episode/123') == 'https://cdn.example.com/a.mp3'
    assert m.PageMeta() is not None


def test_podcast_missing_audio_must_fail_not_invent_article(tmp_path):
    def fetch(_):
        return (b'<html><body><article><p>' + b'a' * 200 + b'</p></article></body></html>',
                'https://www.xiaoyuzhoufm.com/episode/123', 'text/html')
    with pytest.raises(m.MediaError, match='播客页面未解析到完整音频'):
        m.work(args(tmp_path, url='https://www.xiaoyuzhoufm.com/episode/123'), fetch_page=fetch)
    manifest = json.loads((tmp_path / 'results' / m.stable_id('https://www.xiaoyuzhoufm.com/episode/123') / 'manifest.json').read_text())
    assert manifest['status'] == 'failed'


def test_rss_single_episode_and_multi_ambiguity():
    rss = b'<rss><channel><item><title>A</title><enclosure url="https://cdn.test/a.mp3"/></item><item><title>B</title><enclosure url="https://cdn.test/b.mp3"/></item></channel></rss>'
    assert m.rss_audio(rss, 'B') == ('https://cdn.test/b.mp3', 'B')
    with pytest.raises(m.MediaError, match='匹配到 2 集'):
        m.rss_audio(rss)


def test_blog_local_uses_actual_body_not_preview(tmp_path):
    p = tmp_path / 'article.html'
    p.write_text('<html><head><title>测试文章</title></head><body><nav>菜单菜单菜单</nav>'
                 '<article><h2>背景</h2><p>' + '这是一篇真实文章的正文，用于测试提取，不使用任何预览或摘要。' * 5
                 + '</p></article></body></html>', encoding='utf-8')
    dest = m.work(args(tmp_path, input=str(p)))
    content = (dest / 'content.md').read_text(encoding='utf-8')
    assert '真实文章的正文' in content
    assert '菜单菜单' not in content
    state = json.loads((dest / 'manifest.json').read_text(encoding='utf-8'))
    assert state['status'] == 'complete'
    assert json.loads((dest / 'evidence.jsonl').read_text(encoding='utf-8').splitlines()[0])['modality'] == 'web_article'


def test_gallery_full_ocr_preserves_image_order_and_caption(tmp_path):
    root = tmp_path / 'gallery'; root.mkdir()
    photo(root, '02.png'); photo(root, '01.png')
    (root / 'description.txt').write_text('作者原始配文', encoding='utf-8')
    class OCR:
        def __call__(self, filename):
            return [[[[0, 0], [1, 0], [1, 1], [0, 1]], Path(filename).stem + '内容', .99]], None
    dest = m.work(args(tmp_path, input=str(root)), ocr_loader=lambda: OCR())
    body = (dest / 'content.md').read_text(encoding='utf-8')
    assert body.find('图 1：01.png') < body.find('图 2：02.png')
    assert '01内容' in body and '02内容' in body
    assert '作者原始配文' in body
    state = json.loads((dest / 'manifest.json').read_text())
    assert state['status'] == 'complete' and not state['remote_completeness_verified']
    evidence = [json.loads(x) for x in (dest / 'evidence.jsonl').read_text(encoding='utf-8').splitlines()]
    assert sum(e['modality'] == 'image_ocr' for e in evidence) == 2


def test_gallery_no_ocr_not_pretend_success(tmp_path):
    root = tmp_path / 'photos'; root.mkdir(); photo(root)
    dest = m.work(args(tmp_path, input=str(root)), ocr_loader=lambda: (_ for _ in ()).throw(m.MediaError('ocr', 'OCR 未安装')))
    state = json.loads((dest / 'manifest.json').read_text())
    assert state['status'] == 'partial'
    assert state['errors'][0]['stage'] == 'ocr'
    assert '图 1' not in (dest / 'content.md').read_text()


def test_douyin_mock_acquired_gallery_count_checked(tmp_path):
    def downloader(url, output, cookies):
        assert cookies is None and 'v.douyin.com' in url
        output.mkdir(parents=True, exist_ok=True)
        photo(output, '01.png')
        photo(output, '02.png')
        return {'title':'示例', 'description':'分享配文',
                'image_post_info': {'images':[{'x':1}, {'x':2}]}}, []
    class Engine:
        def __call__(self, *_):
            return [], None
    dest = m.work(args(tmp_path, url='https://v.douyin.com/stMG1PSghXk/'),
                  video_downloader=downloader, ocr_loader=lambda: Engine())
    state = json.loads((dest/'manifest.json').read_text())
    assert state['status'] == 'complete'
    assert state['remote_completeness_verified'] is True
    assert state['acquired_image_count'] == 2


def test_douyin_missing_one_photo_is_partial(tmp_path):
    def downloader(url, output, cookies):
        output.mkdir(parents=True, exist_ok=True)
        photo(output)
        return {'image_post_info': {'images': [1, 2]}}, []
    class Engine:
        def __call__(self, *_):
            return [], None
    dest = m.work(args(tmp_path, url='https://v.douyin.com/abc/'),
                  video_downloader=downloader, ocr_loader=lambda: Engine())
    state = json.loads((dest/'manifest.json').read_text())
    assert state['status'] == 'partial'
    assert '数量不符' in str(state['errors'])


def test_media_asr_missing_weights_yields_partial(tmp_path):
    if not shutil.which('ffmpeg') or not shutil.which('ffprobe'):
        pytest.skip('FFmpeg unavailable')
    media = tmp_path / 'sound.wav'
    subprocess.run(['ffmpeg', '-nostdin', '-v', 'error', '-f', 'lavfi',
                    '-i', 'sine=frequency=300:duration=1', '-y', str(media)], check=True)
    dest = m.work(args(tmp_path, input=str(media), asr_model=str(tmp_path / 'missing.pt')))
    state = json.loads((dest/'manifest.json').read_text())
    assert state['status'] == 'partial'
    assert any('不会自动下载' in x['error'] for x in state['errors'])
    assert not (dest/'transcripts').exists() or not list((dest/'transcripts').glob('*.jsonl'))


def test_asr_checkpoint_resume_and_digest(tmp_path):
    if not shutil.which('ffmpeg') or not shutil.which('ffprobe'):
        pytest.skip('FFmpeg unavailable')
    media = tmp_path / 'sample.wav'
    subprocess.run(['ffmpeg', '-nostdin', '-v', 'error', '-f', 'lavfi',
                    '-i', 'sine=frequency=500:duration=2.5', '-y', str(media)], check=True)
    out = tmp_path / 'transcript.jsonl'
    calls = []
    def fake_transcribe(wav, lang):
        calls.append(Path(wav).stat().st_size)
        return {'segments':[{'start':.1, 'end':.6, 'text':'测试音频'}]}
    rows = m.asr(media, out, 'fake-model', 'zh', 1, loader=lambda: object(), transcribe=fake_transcribe)
    assert len(rows) == 3 and len(calls) == 3
    second = m.asr(media, out, 'fake-model', 'zh', 1,
                   loader=lambda: (_ for _ in ()).throw(Exception('must not reload model')),
                   transcribe=fake_transcribe)
    assert second == rows and len(calls) == 3
    audit = json.loads(Path(str(out)+'.audit.json').read_text())
    assert audit['windows_total'] == 3 == audit['windows_completed']
    assert audit['transcript_sha256'] == m.sha256_file(out)


def test_download_only_explicitly_partial(tmp_path):
    p = photo(tmp_path, 'some.png')
    dest = m.work(args(tmp_path, input=str(p), download_only=True))
    state = json.loads((dest/'manifest.json').read_text())
    assert state['status'] == 'partial'
    assert state['errors'][0]['stage'] == 'ocr'


def test_global_install_two_agents_works_without_git(tmp_path):
    spec2 = importlib.util.spec_from_file_location('setup_global', ROOT / 'setup_global.py')
    inst = importlib.util.module_from_spec(spec2)
    spec2.loader.exec_module(inst)
    files = inst.install(ROOT, tmp_path / 'home', ['codex', 'claude'])
    assert len(files) == 2
    assert all((f / 'SKILL.md').exists() for f in files)
    assert all((f / 'scripts' / 'media_analysis.py').exists() for f in files)


def test_share_text_says_gallery_but_no_images_is_not_success(tmp_path):
    def downloader(url, output, cookies):
        output.mkdir(parents=True, exist_ok=True)
        # Fake platform extractor only downloaded an MP4 (no gallery originals).
        # Use a real, tiny sound clip so ASR can be explicitly skipped.
        if not shutil.which('ffmpeg'):
            pytest.skip('FFmpeg unavailable')
        subprocess.run(['ffmpeg', '-nostdin', '-v', 'error', '-f', 'lavfi',
                        '-i', 'sine=frequency=400:duration=0.7', '-y', str(output/'video.mp4')],
                       check=True)
        return {'title': '图文示例'}, []
    share = '复制打开抖音，看看这篇图文作品 https://v.douyin.com/galleryfake/'
    dest = m.work(args(tmp_path, url=share, skip_asr=True), video_downloader=downloader)
    state = json.loads((dest / 'manifest.json').read_text())
    assert state['gallery_expected'] is True
    assert state['status'] == 'partial'
    assert any('未获取到任何原始图片' in x['error'] for x in state['errors'])
