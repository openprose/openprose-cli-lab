"""Lab prototype: bounded deterministic evidence, with no model calls."""
from __future__ import annotations
import hashlib
import json
import sqlite3
import os
import stat
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Evidence:
    identity: str
    payload: str
    observed_at: float
    valid_until: float
    gap: str | None = None


def pack(source, selector, value, now, ttl, gap=None):
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
    identity = hashlib.sha256(json.dumps([source, selector, payload, gap], ensure_ascii=False).encode()).hexdigest()
    return Evidence(identity, payload, now, now + ttl, gap)


def file_evidence(path, now, ttl=60, limit=65536):
    if type(limit) is not int or limit < 0:
        raise ValueError('limit must be a nonnegative integer')
    path = Path(path).resolve()
    try:
        if not stat.S_ISREG(path.stat().st_mode):
            raise ValueError('regular file required')
        # Nonblocking open also covers a replacement with a FIFO after stat.
        fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
        try:
            if not stat.S_ISREG(os.fstat(fd).st_mode):
                raise ValueError('regular file required')
            with os.fdopen(fd, 'rb', closefd=False) as stream:
                raw = stream.read(limit + 1)
        finally:
            os.close(fd)
        if len(raw) > limit:
            raise ValueError('oversize')
        value = raw.decode('utf-8')
        return pack(str(path), 'utf8-file-v1', value, now, ttl)
    except (OSError, UnicodeError, ValueError) as error:
        return pack(str(path), 'utf8-file-v1', None, now, ttl, type(error).__name__)


def query_evidence(path, query, params, now, ttl=60, row_limit=1000, byte_limit=65536, *, named_rows=False):
    if any(type(value) is not int or value < 0 for value in (row_limit, byte_limit)):
        raise ValueError('query limits must be nonnegative integers')
    path = Path(path).resolve()
    selector = ['sqlite-unordered-named-rows-v1' if named_rows else 'sqlite-unordered-rows-v1', query, params]
    try:
        # No create-if-missing behavior, no writes, no extension loading.
        with sqlite3.connect(path.as_uri() + '?mode=ro', uri=True) as db:
            db.execute('PRAGMA query_only=ON')
            ticks = [0]
            def stop():
                ticks[0] += 1
                return int(ticks[0] > 1000)
            db.set_progress_handler(stop, 1000)
            cursor = db.execute(query, params)
            if cursor.description is None:
                raise ValueError('not a result set')
            columns = [c[0] for c in cursor.description]
            rows = cursor.fetchmany(row_limit + 1)
            if len(rows) > row_limit:
                raise ValueError('row limit')
            # Lab convention: result is a multiset. Preserve duplicates, ignore order.
            # Order-sensitive contracts require an explicitly different selector.
            rows = sorted(rows, key=lambda row: json.dumps(row, ensure_ascii=False))
            if named_rows:
                if len(set(columns)) != len(columns):
                    raise ValueError('duplicate column names')
                rows = [dict(zip(columns, row)) for row in rows]
            value = {'columns': columns, 'rows': rows}
            if len(json.dumps(value, ensure_ascii=False).encode()) > byte_limit:
                raise ValueError('result byte limit')
            return pack(str(path), selector, value, now, ttl)
    except (sqlite3.Error, ValueError, TypeError) as error:
        return pack(str(path), selector, None, now, ttl, type(error).__name__)


def can_reuse(old, new, old_binding, current_binding, now):
    return (old_binding == current_binding and old.identity == new.identity
            and old.gap is None and new.gap is None
            and old.observed_at <= now < old.valid_until
            and new.observed_at <= now < new.valid_until)
