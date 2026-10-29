#!/usr/bin/env python3
"""JSON CLI used by WorkBuddy and the Rust desktop (no shell interpolation)."""
import argparse
import json
import os
import sys
from contextlib import contextmanager
from pathlib import Path


@contextmanager
def _skill_maintenance_lock():
    """Share the updater lock so CLI imports never observe half-replaced code."""
    lock_dir = Path(__file__).resolve().parents[1] / '.update'
    lock_dir.mkdir(exist_ok=True)
    handle = (lock_dir / 'lock').open('a+b')
    try:
        if os.name == 'nt':
            import msvcrt
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except (OSError, IOError) as exc:
        handle.close()
        raise RuntimeError('Skill 正在更新或回退，请稍后重试。') from exc
    try:
        yield
    finally:
        try:
            if os.name == 'nt':
                import msvcrt
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            handle.close()


def main():
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
        sys.stderr.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser(description='HR 文件夹简历池 / Excel 台账')
    parser.add_argument('--config', help='工作配置 JSON；默认读取 Skill 根目录的 workspace.local.json')
    parser.add_argument('--root', help='本机工作副本目录；覆盖配置中的 root')
    parser.add_argument('--actor', help='实际操作人姓名；覆盖配置中的 actor')
    parser.add_argument('command', choices=['init', 'snapshot', 'doctor', 'jd-put', 'candidate-add', 'candidate-update', 'application-add', 'application-update', 'review-queue', 'review-submit', 'ranking', 'interview-add', 'ledger-export', 'ledger-import', 'sync', 'sync-status', 'sync-resolve'])
    inputs = parser.add_mutually_exclusive_group()
    inputs.add_argument('--payload', help='JSON 输入')
    inputs.add_argument('--input', help='UTF-8 JSON 文件；适合长文本')
    args = parser.parse_args()
    try:
        config_path = Path(args.config) if args.config else Path(__file__).resolve().parents[1] / 'workspace.local.json'
        config = {}
        if args.config or config_path.exists():
            config = json.loads(config_path.read_text('utf-8-sig'))
            if not isinstance(config, dict):
                raise ValueError('工作配置必须是 JSON 对象')
        root = args.root if args.root is not None else config.get('root')
        actor = args.actor if args.actor is not None else config.get('actor')
        if not isinstance(root, str) or not root.strip() or not isinstance(actor, str) or not actor.strip():
            raise ValueError('缺少本地工作目录或操作人。请先运行 scripts/setup.ps1，或同时提供 --root 和 --actor。')
        raw = Path(args.input).read_text('utf-8-sig') if args.input else args.payload or '{}'
        data = json.loads(raw)
        if not isinstance(data, dict):
            raise ValueError('命令输入必须是 JSON 对象')
        if args.command in ('sync', 'sync-resolve') and 'remote_root' not in data:
            remote = config.get('remote_root')
            if isinstance(remote, str) and remote.strip():
                data['remote_root'] = remote
        with _skill_maintenance_lock():
            from hr_resume.service import Service
            result = Service(root, actor).execute(args.command, data)
        print(json.dumps(dict(ok=True, data=result), ensure_ascii=False, allow_nan=False))
        return 0
    except (ValueError, OSError, KeyError, TypeError, ImportError, RuntimeError) as exc:
        print(json.dumps(dict(ok=False, error=str(exc)), ensure_ascii=False))
        return 1


if __name__ == '__main__':
    sys.exit(main())
