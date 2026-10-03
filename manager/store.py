"""Private local workspace and image history. No client-controlled file paths."""
import json
from contextlib import contextmanager
import os
import re
import sqlite3
import time
from pathlib import Path
import config


class Store:
    def __init__(self):
        config.DATA_ROOT.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.path = config.DATA_ROOT / 'workspace.sqlite3'
        with self.connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS workspace (id TEXT PRIMARY KEY, kind TEXT, model TEXT, value TEXT, revision INTEGER, updated REAL)')
            db.execute('CREATE TABLE IF NOT EXISTS images (id TEXT PRIMARY KEY, value TEXT, updated REAL)')
        os.chmod(self.path, 0o600)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def index(self):
        with self.connect() as db:
            rows = db.execute('SELECT * FROM workspace ORDER BY updated DESC').fetchall()
        chats, drafts = [], []
        for row in rows:
            value = json.loads(row['value'])
            entry = dict(id=row['id'], model=row['model'], revision=row['revision'], updated=row['updated'])
            if row['kind'] == 'draft':
                drafts.append(dict(entry, value=value))
            else:
                chats.append(dict(entry, title=value['title'], count=len(value['messages'])))
        return dict(chats=chats, drafts=drafts)

    def get(self, key):
        with self.connect() as db:
            row = db.execute('SELECT * FROM workspace WHERE id=?', (key,)).fetchone()
        if not row:
            raise ValueError('Conversation not found')
        return dict(id=row['id'], model=row['model'], revision=row['revision'], value=json.loads(row['value']))

    def put(self, spec):
        key, kind, model, value = (spec.get(k) for k in ('id', 'kind', 'model', 'value'))
        revision = spec.get('revision', 0)
        if not isinstance(key, str) or not re.fullmatch(r'[a-zA-Z0-9_-]{1,100}', key):
            raise ValueError('Invalid workspace identifier')
        if kind not in ('chat', 'draft') or not isinstance(model, str) or len(model)>100 or not isinstance(value, dict):
            raise ValueError('Invalid workspace entry')
        if not isinstance(revision, int) or isinstance(revision, bool) or revision < 0:
            raise ValueError('Invalid revision')
        if kind == 'chat':
            if not isinstance(value.get('title'), str) or len(value['title'])>120:
                raise ValueError('Invalid conversation title')
            messages = value.get('messages')
            if not isinstance(messages, list) or len(messages)>100:
                raise ValueError('Conversation limit: 100 messages; start a new chat')
            for msg in messages:
                if not isinstance(msg, dict) or msg.get('role') not in ('user','assistant') or not isinstance(msg.get('content'), str) or not isinstance(msg.get('reasoning',''), str):
                    raise ValueError('Invalid conversation message')
        else:
            if not isinstance(value.get('prompt',''), str) or len(value.get('prompt',''))>100000:
                raise ValueError('Invalid draft')
        encoded = json.dumps(value, ensure_ascii=False)
        if len(encoded.encode()) > 1024*1024:
            raise ValueError('Workspace entry exceeds 1 MB')
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            old = db.execute('SELECT revision,kind,model FROM workspace WHERE id=?', (key,)).fetchone()
            if (old and (old['revision'] != revision or old['kind'] != kind or old['model'] != model)) or (not old and revision != 0):
                raise ValueError('History changed in another tab. Reload before saving; your draft remains in this tab.')
            revision += 1
            db.execute('INSERT OR REPLACE INTO workspace VALUES (?,?,?,?,?,?)', (key,kind,model,encoded,revision,time.time()))
        return dict(id=key, revision=revision)

    def image(self, job):
        with self.connect() as db:
            db.execute('INSERT OR REPLACE INTO images VALUES (?,?,?)', (job['id'],json.dumps(job),time.time()))

    def gallery(self):
        with self.connect() as db:
            jobs = {row['id']:json.loads(row['value']) for row in db.execute('SELECT * FROM images')}
        # Existing outputs stay visible, even if they predate parameter recording.
        for path in config.OUTPUT_ROOT.glob('*.png'):
            if re.fullmatch(r'[a-f0-9]{12}',path.stem) and path.stem not in jobs:
                jobs[path.stem] = dict(id=path.stem,state='done',started_at=None,legacy=True)
        result = []
        for job in jobs.values():
            path = config.OUTPUT_ROOT / (job['id']+'.png')
            if job.get('state') == 'done' and path.is_file():
                result.append(dict(job, saved_at=path.stat().st_mtime))
        return sorted(result,key=lambda x:x['saved_at'],reverse=True)

    def output(self, key):
        if not isinstance(key,str) or not re.fullmatch(r'[a-f0-9]{12}',key):
            raise ValueError('Invalid image identifier')
        path = config.OUTPUT_ROOT / (key+'.png')
        if not path.is_file() or path.resolve().parent != config.OUTPUT_ROOT.resolve():
            raise ValueError('Image not found')
        return path
