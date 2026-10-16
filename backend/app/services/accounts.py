"""Local optional identities and exact-text preferences; never stores documents."""
from contextvars import ContextVar
from pathlib import Path
from threading import RLock
import hashlib
import hmac
import secrets
import sqlite3
import time
import unicodedata
from fastapi import HTTPException
from .text_matching import text_key

owner = ContextVar('document_owner', default=None)


class Accounts:
    def __init__(self, root):
        root = Path(root)
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        root.chmod(0o700)
        self.db = sqlite3.connect(root / 'accounts.sqlite3', check_same_thread=False)
        (root / 'accounts.sqlite3').chmod(0o600)
        self.db.row_factory = sqlite3.Row
        self.lock = RLock()
        self.attempts = {}
        with self.db:
            self.db.executescript('''
                PRAGMA foreign_keys=ON;
                PRAGMA secure_delete=ON;
                CREATE TABLE IF NOT EXISTS users (
                    id TEXT PRIMARY KEY, username TEXT NOT NULL UNIQUE,
                    salt BLOB NOT NULL, password_hash BLOB NOT NULL);
                CREATE TABLE IF NOT EXISTS logins (
                    token_hash TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    expires REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS rules (
                    id TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    text TEXT NOT NULL, text_key TEXT NOT NULL, type TEXT NOT NULL,
                    label TEXT NOT NULL, action TEXT NOT NULL,
                    UNIQUE(user_id, text_key));
            ''')

    def close(self):
        self.db.close()

    def throttle(self, peer, username='', register=False):
        now = time.monotonic()
        username = unicodedata.normalize('NFKC', username).strip().casefold()
        name = hashlib.sha256(username.encode()).hexdigest()
        pair, address = ('login', peer, name), ('login-peer', peer)
        limits = [(('register-peer', peer), 20)] if register else [(pair, 20), (address, 200)]
        with self.lock:
            self.attempts = {key: [v for v in values if v > now-900]
                for key, values in self.attempts.items() if values[-1] > now-900}
            if any(len(self.attempts.get(key, [])) >= limit for key, limit in limits):
                raise HTTPException(429, 'Too many account attempts; try again in 15 minutes',
                                    headers={'Retry-After': '900'})
            new_keys = sum(key not in self.attempts for key, _ in limits)
            if len(self.attempts) + new_keys > 10000:
                raise HTTPException(429, 'Account authentication is busy; try again later')
            # Reserve attempts before hashing, bounding simultaneous requests too.
            for key, _ in limits:
                self.attempts.setdefault(key, []).append(now)
            return (pair, address, now) if not register else None

    def authentication_succeeded(self, ticket):
        if ticket is None:
            return  # Registration has its own address budget, even on success.
        pair, address, stamp = ticket
        with self.lock:
            self.attempts.pop(pair, None)
            values = self.attempts.get(address, [])
            if stamp in values:
                values.remove(stamp)
            if not values:
                self.attempts.pop(address, None)

    @staticmethod
    def password_hash(password, salt):
        return hashlib.pbkdf2_hmac('sha256', password.encode(), salt, 600000)

    def authenticate(self, username, password, register=False):
        username = unicodedata.normalize('NFKC', username).strip().casefold()
        if not (3 <= len(username) <= 64) or not all(c.isalnum() or c in '._-' for c in username):
            raise ValueError('Use 3–64 letters, digits, dots, underscores or hyphens for your username')
        if not (12 <= len(password) <= 256):
            raise ValueError('Use a password with 12–256 characters')
        with self.lock, self.db:
            user = self.db.execute('SELECT * FROM users WHERE username=?', (username,)).fetchone()
            if register:
                if user:
                    raise ValueError('This username is unavailable')
                if self.db.execute('SELECT COUNT(*) FROM users').fetchone()[0] >= 1000:
                    raise ValueError('Local account limit reached')
                salt, user_id = secrets.token_bytes(32), secrets.token_hex(16)
                self.db.execute('INSERT INTO users VALUES (?,?,?,?)',
                    (user_id, username, salt, self.password_hash(password, salt)))
            else:
                salt = user['salt'] if user else bytes(32)
                digest = self.password_hash(password, salt)
                if not user or not hmac.compare_digest(digest, user['password_hash']):
                    raise HTTPException(401, 'Incorrect username or password')
                user_id = user['id']
            token = secrets.token_urlsafe(32)
            self.db.execute('DELETE FROM logins WHERE expires<?', (time.time(),))
            # Bound stored browser sessions per account.
            self.db.execute('DELETE FROM logins WHERE user_id=? AND token_hash NOT IN '
                '(SELECT token_hash FROM logins WHERE user_id=? ORDER BY expires DESC LIMIT 9)', (user_id, user_id))
            self.db.execute('INSERT INTO logins VALUES (?,?,?)',
                (hashlib.sha256(token.encode()).hexdigest(), user_id, time.time()+30*86400))
            return {'id': user_id, 'username': username}, token

    def identity(self, token):
        with self.lock:
            row = self.db.execute('SELECT users.id,username FROM users JOIN logins ON users.id=logins.user_id '
                'WHERE token_hash=? AND expires>?', (hashlib.sha256(token.encode()).hexdigest(), time.time())).fetchone()
            return dict(row) if row else None

    def logout(self, token):
        with self.lock, self.db:
            self.db.execute('DELETE FROM logins WHERE token_hash=?', (hashlib.sha256(token.encode()).hexdigest(),))

    def list_rules(self, user_id):
        with self.lock:
            return [dict(row) for row in self.db.execute(
                'SELECT id,text,type,label,action FROM rules WHERE user_id=? ORDER BY rowid DESC', (user_id,))]

    def save_rule(self, user_id, text, kind, label, action):
        self.save_rules(user_id, [(text, kind, label, action)])

    def save_rules(self, user_id, values):
        # One explicit bulk decision either saves all preferences or none.
        with self.lock, self.db:
            for text, kind, label, action in values:
                text, label = text.strip(), label.strip()
                key = text_key(text)
                if not key or not any(c.isalnum() for c in key) or len(text) > 4096:
                    raise ValueError('Enter text containing letters or digits (up to 4096 characters)')
                if len(label) > 80:
                    raise ValueError('Keep the personal label within 80 characters')
                existing = self.db.execute('SELECT id,label FROM rules WHERE user_id=? AND text_key=?', (user_id, key)).fetchone()
                if not existing and self.db.execute('SELECT COUNT(*) FROM rules WHERE user_id=?', (user_id,)).fetchone()[0] >= 200:
                    raise ValueError('Saved rule limit reached (200); remove an unused rule first or turn off Remember')
                label = label or (existing['label'] if existing else kind)
                self.db.execute('INSERT INTO rules VALUES (?,?,?,?,?,?,?) ON CONFLICT(user_id,text_key) '
                    'DO UPDATE SET text=excluded.text,type=excluded.type,label=excluded.label,action=excluded.action',
                    (secrets.token_hex(16), user_id, text, key, kind, label, action))

    def delete_rule(self, user_id, rule_id):
        with self.lock, self.db:
            if not self.db.execute('DELETE FROM rules WHERE user_id=? AND id=?', (user_id, rule_id)).rowcount:
                raise KeyError('Rule not found')

    def delete_account(self, user_id):
        with self.lock, self.db:
            self.db.execute('DELETE FROM users WHERE id=?', (user_id,))
