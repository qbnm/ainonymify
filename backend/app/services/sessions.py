"""Single-process temporary store. Locks serialize PDF/OCR jobs and mutations."""
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import replace
from fastapi import HTTPException
from pathlib import Path
from threading import RLock
import shutil
import tempfile
import time
from ..models import Session
from .accounts import owner


class Store:
    def __init__(self, ttl=1800, root=None, maximum=20, per_owner=3, guest_per_peer=6):
        self.root = Path(tempfile.mkdtemp(prefix='privacy-', dir=root))
        self.root.chmod(0o700)
        self.ttl, self.maximum = ttl, maximum
        self.per_owner, self.guest_per_peer = per_owner, guest_per_peer
        self.peers = {}
        self.creation_attempts = {}
        self.items = {}
        self.owners = {}
        self.lock = RLock()

    def cleanup(self):
        with self.lock:
            for sid, (_, touched) in list(self.items.items()):
                if time.monotonic() - touched > self.ttl:
                    self.delete(sid, internal=True)

    def create(self, peer=None):
        with self.lock:
            self.cleanup()
            identity = owner.get()
            if sum(value == identity for value in self.owners.values()) >= self.per_owner:
                raise HTTPException(429, 'Your active session limit is reached; delete an unused session')
            guest = identity is not None and identity.startswith('g:')
            if guest and peer is not None and sum(value == peer for value in self.peers.values()) >= self.guest_per_peer:
                raise HTTPException(429, 'Guest session limit for this address is reached; delete an unused session or sign in')
            now = time.monotonic()
            self.creation_attempts = {key: [v for v in values if v > now-60]
                for key, values in self.creation_attempts.items() if values[-1] > now-60}
            keys = [('owner', identity)]
            if guest and peer is not None:
                keys.append(('guest-peer', peer))
            for key in keys:
                if len(self.creation_attempts.get(key, [])) >= (20 if key[0] == 'guest-peer' else 10):
                    raise HTTPException(429, 'Too many new sessions; try again in a minute', headers={'Retry-After': '60'})
            if len(self.creation_attempts) + len(keys) > 10000:
                raise HTTPException(429, 'Session creation is busy; try again in a minute')
            if len(self.items) >= self.maximum:
                raise ValueError('Session limit reached; delete an unused session')
            for key in keys:
                self.creation_attempts.setdefault(key, []).append(now)
            session = Session()
            (self.root / session.id).mkdir(mode=0o700)
            self.items[session.id] = (session, time.monotonic())
            self.owners[session.id] = identity
            if guest and peer is not None:
                self.peers[session.id] = peer
            return session

    @contextmanager
    def access(self, sid):
        with self.lock:
            self.cleanup()
            if sid not in self.items or self.owners.get(sid) != owner.get():
                raise KeyError('Session not found or expired')
            session, _ = self.items[sid]
            self.items[sid] = (session, time.monotonic())
            yield session, self.root / sid
            # Long OCR operations count as activity, not idle time.
            if sid in self.items:
                self.items[sid] = (self.items[sid][0], time.monotonic())

    @contextmanager
    def review(self, sid):
        with self.access(sid) as (original, root):
            if original.status not in ('review', 'verification_warning', 'complete'):
                raise ValueError('Complete analysis before editing detections')
            # Source pages stay immutable during review; clone only mutable review
            # state, avoiding a second copy of every source glyph on large PDFs.
            draft = replace(original, entities=deepcopy(original.entities),
                detections=deepcopy(original.detections), warnings=[],
                status='review', includes_mapping=False)
            yield draft, root
            self.items[sid] = (draft, time.monotonic())

    def delete(self, sid, internal=False):
        with self.lock:
            # Never build a filesystem deletion path from an unknown caller ID.
            if sid not in self.items:
                return
            if not internal and self.owners.get(sid) != owner.get():
                raise KeyError('Session not found')
            self.owners.pop(sid, None)
            self.peers.pop(sid, None)
            self.items.pop(sid, None)
            shutil.rmtree(self.root / sid, ignore_errors=True)

    def delete_owned(self, identity):
        with self.lock:
            for sid in list(self.items):
                if self.owners.get(sid) == identity:
                    self.delete(sid, internal=True)

    def close(self):
        with self.lock:
            shutil.rmtree(self.root, ignore_errors=True)
            self.items.clear()
            self.owners.clear()
            self.peers.clear()
            self.creation_attempts.clear()


def invalidate(session, path):
    session.status = 'review'
    session.includes_mapping = False
    session.warnings = []
    (path / 'result.zip').unlink(missing_ok=True)
    (path / 'mapping.json').unlink(missing_ok=True)
