"""Immutable JSON revisions with a single atomic catalog commit point.

Use one real SMB share. Replicated/offline sync folders cannot provide this lock.
No stale-lock timeout: an interrupted writer must be investigated by an operator.
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import socket
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

KINDS = ('candidates', 'jobs', 'applications', 'reviews', 'interviews')
PREFIX = dict(zip(KINDS, ('C', 'J', 'A', 'R', 'I')))


def now():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def digest(data):
    return hashlib.sha256(data).hexdigest()


def encode(value):
    return (json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n').encode('utf-8')


class Store:
    def __init__(self, root, actor):
        self.root = Path(root).expanduser().resolve()
        self.actor = str(actor).strip()
        if not self.actor:
            raise ValueError('请填写操作人 --actor')

    def safe(self, relative):
        path = (self.root / relative).resolve()
        if not path.is_relative_to(self.root) or path == self.root:
            raise ValueError('资料路径超出数据根目录')
        return path

    @contextmanager
    def lock(self):
        lock = self.safe('.write.lock')
        try:
            lock.mkdir()
        except FileExistsError:
            raise ValueError('共享目录正在写入或上次写入异常中断；请稍后重试，勿直接删除锁') from None
        try:
            (lock / 'owner.json').write_bytes(encode(dict(actor=self.actor, host=socket.gethostname(), pid=os.getpid(), started_at=now())))
            yield
        finally:
            (lock / 'owner.json').unlink(missing_ok=True)
            lock.rmdir()

    def atomic(self, path, content):
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
        try:
            with tmp.open('xb') as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp, path)
        finally:
            tmp.unlink(missing_ok=True)

    def init(self):
        self.root.mkdir(parents=True, exist_ok=True)
        with self.lock():
            if self.safe('catalog.json').exists():
                return self.snapshot()
            remaining = [p for p in self.root.iterdir() if p.name != '.write.lock']
            if remaining:
                raise ValueError('初始化请选择空目录，避免混入其他资料')
            catalog = dict(schema_version=1, revision=0, updated_at=now(), updated_by=self.actor, records={k: {} for k in KINDS})
            self.atomic(self.safe('catalog.json'), encode(catalog))
        return self.snapshot()

    def catalog(self):
        try:
            value = json.loads(self.safe('catalog.json').read_text('utf-8'))
        except FileNotFoundError:
            raise ValueError('数据目录尚未初始化，请先执行 init') from None
        if value.get('schema_version') != 1 or set(value.get('records', {})) != set(KINDS):
            raise ValueError('数据目录格式不兼容或损坏，请先恢复备份')
        return value

    def snapshot(self):
        catalog = self.catalog()
        result = dict(revision=catalog['revision'], updated_at=catalog['updated_at'])
        for kind in KINDS:
            result[kind] = []
            for ident, ref in catalog['records'][kind].items():
                data = self.safe(ref['path']).read_bytes()
                if digest(data) != ref['sha256']:
                    raise ValueError(f'记录校验失败：{ident}，请从备份恢复，不要直接改 JSON')
                row = json.loads(data)
                if row['id'] != ident or row['version'] != ref['version']:
                    raise ValueError(f'记录索引不一致：{ident}')
                result[kind].append(row)
        return result

    @contextmanager
    def transaction(self):
        # Opening a wrong path must never create a new pool implicitly.
        self.catalog()
        with self.lock():
            tx = Transaction(self, self.catalog(), self.snapshot())
            yield tx
            tx.commit()

    def artifact(self, relative):
        path = self.safe(relative)
        if not path.is_file():
            raise ValueError(f'原件丢失：{relative}')
        return path


class Transaction:
    def __init__(self, store, catalog, snapshot):
        self.store, self.catalog, self.snapshot = store, catalog, snapshot
        self.pending = {}

    def get(self, kind, ident):
        row = next((r for r in self.snapshot[kind] if r['id'] == ident), None)
        if row is None:
            raise ValueError(f'找不到{kind}记录：{ident}')
        return copy.deepcopy(row)

    def put(self, kind, value, expected_version=None):
        row = copy.deepcopy(value)
        ident = row.get('id')
        if ident:
            old = self.get(kind, ident)
            if type(expected_version) is not int or expected_version != old['version']:
                raise ValueError(f'版本冲突：{ident}，当前版本 {old["version"]}；请刷新后重新维护')
            version = old['version'] + 1
        else:
            ident, version = PREFIX[kind] + '-' + uuid.uuid4().hex[:16], 1
        row.update(id=ident, version=version, updated_at=now(), updated_by=self.store.actor)
        self.snapshot[kind] = [r for r in self.snapshot[kind] if r['id'] != ident] + [row]
        self.pending[(kind, ident)] = row
        return copy.deepcopy(row)

    def import_file(self, source, folder):
        source = Path(source).expanduser().resolve()
        if not source.is_file() or source.stat().st_size > 25 * 1024 * 1024:
            raise ValueError('资料必须是存在的文件，且不超过 25 MB')
        suffix = source.suffix.lower()
        if suffix not in {'.pdf', '.doc', '.docx', '.txt', '.md', '.xlsx', '.png', '.jpg', '.jpeg'}:
            raise ValueError('只支持 PDF、Word、文本、Excel、PNG/JPG 资料')
        content = source.read_bytes()
        sha = digest(content)
        relative = f'{folder}/{sha}{suffix}'
        target = self.store.safe(relative)
        if target.exists():
            if digest(target.read_bytes()) != sha:
                raise ValueError('原件校验失败，禁止覆盖')
        else:
            self.store.atomic(target, content)
        return dict(path=relative, sha256=sha, original_name=source.name)

    def commit(self):
        if not self.pending:
            return
        catalog = copy.deepcopy(self.catalog)
        for (kind, ident), row in self.pending.items():
            # UUID suffix makes abandoned pre-commit versions harmless after a crash.
            relative = f'records/{kind}/{ident}/{row["version"]}-{uuid.uuid4().hex}.json'
            content = encode(row)
            self.store.atomic(self.store.safe(relative), content)
            catalog['records'][kind][ident] = dict(path=relative, sha256=digest(content), version=row['version'])
        catalog.update(revision=catalog['revision'] + 1, updated_at=now(), updated_by=self.store.actor)
        history = f'history/{catalog["revision"]}-{uuid.uuid4().hex}.json'
        self.store.atomic(self.store.safe(history), encode(catalog))
        self.store.atomic(self.store.safe('catalog.json'), encode(catalog))
