"""Install this reusable Agent Skill at user scope, without Git access."""
import argparse
from pathlib import Path
import shutil
import time


def targets(home: Path, agents: list[str]):
    lookup = {"codex": home / '.agents' / 'skills' / 'media-analysis',
              "claude": home / '.claude' / 'skills' / 'media-analysis'}
    return [lookup[a] for a in agents]


def install(source: Path, home: Path, agents: list[str]):
    if not (source / 'SKILL.md').is_file() or not (source / 'scripts' / 'media_analysis.py').is_file():
        raise FileNotFoundError('Skill 来源文件不完整；请先解压完整的 ZIP')
    records = []
    for target in targets(home, agents):
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            backup = target.with_name(target.name + '.backup-' + time.strftime('%Y%m%d-%H%M%S'))
            counter = 0
            while backup.exists():
                counter += 1
                backup = target.with_name(target.name + '.backup-' + time.strftime('%Y%m%d-%H%M%S') + f'-{counter}')
            target.rename(backup)
        shutil.copytree(source, target, ignore=shutil.ignore_patterns('__pycache__', '.pytest_cache', '*.pyc'))
        records.append(target)
    return records


def main():
    p = argparse.ArgumentParser(description='安装独立的 media-analysis 用户级 Agent Skill')
    p.add_argument('--agent', choices=['codex', 'claude'], action='append', required=True,
                   help='可重复；例如 --agent codex --agent claude')
    p.add_argument('--home', type=Path, default=Path.home(),
                   help='仅供测试或高级用户指定不同 HOME')
    args = p.parse_args()
    source = Path(__file__).resolve().parent
    for path in install(source, args.home.expanduser().resolve(), list(dict.fromkeys(args.agent))):
        print('已安装:', path / 'SKILL.md')
    print('提示：这只作用于本地 Agent 的会话；ChatGPT 网页端不自动注册 Skill。')


if __name__ == '__main__':
    main()
