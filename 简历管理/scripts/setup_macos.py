#!/usr/bin/env python3
"""Install a private HR Skill on macOS; writes no configuration inside the app."""
import argparse
import json
import os
import platform
import shutil
import subprocess
import sys
import time
from pathlib import Path


PROJECT = Path(__file__).resolve().parents[1]
SOURCE = PROJECT / 'skills/manage-hr-resumes'


def run(argv, *, json_result=False, timeout=300):
    result = subprocess.run([str(arg) for arg in argv], capture_output=True,
                            encoding='utf-8', errors='replace', timeout=timeout,
                            env={**os.environ, 'PYTHONUTF8': '1'})
    if result.returncode:
        raise RuntimeError((result.stderr + '\n' + result.stdout).strip())
    if not json_result:
        return result.stdout
    value = json.loads(result.stdout)
    if not value.get('ok'):
        raise RuntimeError(value.get('error', str(value)))
    return value.get('data', value)


def atomic_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + f'.{os.getpid()}.tmp')
    try:
        temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def overlaps(first, second):
    return first == second or first in second.parents or second in first.parents


def validate_paths(skill, local, remote, config, use_existing):
    if overlaps(skill, SOURCE.resolve()):
        raise ValueError('Skill 安装目录必须独立于源码目录。')
    if overlaps(skill, local):
        raise ValueError('Skill 安装目录与业务工作副本不能互相包含。')
    if remote and (overlaps(remote, local) or overlaps(remote, skill)):
        raise ValueError('NAS Hub、Skill 和本地工作副本必须是相互独立的目录。')
    if local == config.parent or local in config.parents:
        raise ValueError('客户端配置不能放进业务工作副本。')
    if any(p.suffix == '.app' for p in [config, *config.parents]):
        raise ValueError('客户端配置不能写入 .app 应用包。')
    if use_existing:
        for relative in ('SKILL.md', 'scripts/hr.py', '.venv/bin/python'):
            if not (skill / relative).is_file():
                raise ValueError('复用目录不是完整的已安装 Skill：' + relative)
    elif skill.exists():
        raise ValueError('目标安装目录已存在；请选择新的版本目录，或使用 --use-existing-skill。')


def install(args):
    if sys.platform != 'darwin':
        raise ValueError('本脚本用于 macOS；Windows 请运行 scripts/setup.ps1。')
    if platform.machine().lower() not in ('arm64', 'aarch64'):
        raise ValueError('目前仅支持 Apple 芯片 Mac；Intel Mac 暂不支持。请使用原生 arm64 Python。')
    if sys.version_info < (3, 11):
        raise ValueError('需要 Python 3.11 或更新版本。')
    actor = args.actor.strip()
    if not actor:
        raise ValueError('请提供实际使用者姓名。')
    skill = Path(args.skill_target).expanduser().resolve()
    local = Path(args.local_root).expanduser().resolve()
    remote = Path(args.remote_root).expanduser().resolve() if args.remote_root else None
    config_path = Path(args.config).expanduser().resolve()
    if args.initialize_remote and not remote:
        raise ValueError('--initialize-remote 需要 --remote-root。')
    validate_paths(skill, local, remote, config_path, args.use_existing_skill)
    app_exe = PROJECT / '招聘看板.app/Contents/MacOS/招聘看板'
    if app_exe.is_file():
        run([app_exe, '--check-runtime'], json_result=True, timeout=15)
    wheelhouse = Path(args.wheelhouse).expanduser().resolve() if args.wheelhouse else PROJECT / 'wheelhouse'
    if args.wheelhouse and not wheelhouse.is_dir():
        raise ValueError('指定的 wheelhouse 不存在。')
    if not args.use_existing_skill:
        # All preflight checks complete before creating the target installation.
        run([sys.executable, '-c', 'import venv,pip; assert tuple(int(x) for x in pip.__version__.split(".")[:2]) >= (22,3)'])
        shutil.copytree(SOURCE, skill, ignore=shutil.ignore_patterns(
            '__pycache__', '*.pyc', '.venv', '.update', 'workspace.local.json'))
        run([sys.executable, '-m', 'venv', '--without-pip', skill / '.venv'])
        dependency_args = [sys.executable, '-m', 'pip', '--python', skill / '.venv/bin/python',
                           'install', '--disable-pip-version-check']
        if wheelhouse.is_dir():
            dependency_args.extend(['--no-index', '--find-links', wheelhouse])
        run([*dependency_args, '-r', skill / 'requirements.txt'])
    python = skill / '.venv/bin/python'
    entry = skill / 'scripts/hr.py'
    updater = skill / 'scripts/hr_update.py'
    run([python, '-c', 'import openpyxl,pypdf'])

    def hr(command, payload=None):
        return run([python, entry, '--root', local, '--actor', actor, command,
                    '--payload', json.dumps(payload or {}, ensure_ascii=False)], json_result=True)

    hr('init')
    health = hr('doctor')
    if not health.get('healthy'):
        raise ValueError('本地工作副本检查失败：' + str(health.get('issues')))
    run([python, updater, 'init-state', '--skill-root', skill], json_result=True)
    config = dict(python=str(python), script=str(entry), root=str(local), actor=actor,
                  remote_root=str(remote) if remote else '', port=0, config_path=str(config_path))
    if config_path.exists():
        backup = config_path.with_name(f'client-config.{time.time_ns()}.backup.json')
        shutil.copy2(config_path, backup)
    atomic_json(skill / 'workspace.local.json', config)
    atomic_json(config_path, config)
    import_result = run([python, updater, 'refresh-package', '--skill-root', skill], json_result=True)
    client = dict(executable=str(app_exe), client_ready=app_exe.is_file())
    if not app_exe.is_file() and not args.skip_client_download:
        downloaded = run([python, skill / 'scripts/hr_client.py', 'download', '--skill-root', skill], json_result=True)
        client = dict(executable='', dmg=downloaded['dmg'], client_ready=False, need_install=True)
    sync = 'offline'
    if remote and not args.skip_sync:
        result = hr('sync', dict(remote_root=str(remote), initialize_remote=args.initialize_remote))
        if not result.get('synced'):
            raise ValueError('已保存配置，但存在同步冲突；请让 Agent 比较两个版本。没有覆盖数据。')
        sync = 'synced'
    receipt_path = config_path.parent / 'setup-result.json'
    receipt = dict(ok=True, skill=str(skill), local_root=str(local),
                   remote_root=str(remote) if remote else '', sync=sync,
                   workspace_config=str(skill / 'workspace.local.json'), web_config=str(config_path),
                   workbuddy_package=import_result['package'], **client,
                   receipt=str(receipt_path))
    atomic_json(receipt_path, receipt)
    return receipt


def main():
    parser = argparse.ArgumentParser(description='安装 macOS 招聘工作台的 Skill 和个人配置')
    parser.add_argument('--skill-target', required=True)
    parser.add_argument('--local-root', required=True)
    parser.add_argument('--actor', required=True)
    parser.add_argument('--remote-root', default='')
    parser.add_argument('--wheelhouse', default='')
    parser.add_argument('--config', default=str(Path.home() / 'Library/Application Support/HRResume/client-config.json'))
    parser.add_argument('--use-existing-skill', action='store_true')
    parser.add_argument('--initialize-remote', action='store_true')
    parser.add_argument('--skip-sync', action='store_true')
    parser.add_argument('--skip-client-download', action='store_true')
    try:
        receipt = install(parser.parse_args())
        print(json.dumps(receipt, ensure_ascii=False))
        return 0
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        print(json.dumps(dict(ok=False, error=str(exc)), ensure_ascii=False))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
