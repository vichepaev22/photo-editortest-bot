"""Persistent five-day free pool and notification outbox, separate from paid funds."""

import asyncio
import logging
import time
import uuid
from datetime import datetime, timedelta, timezone

INTERVAL = 5 * 86400
CAP = 3
RULE_TEXT = ('🎁 Каждые 5 дней вам начисляется одна бесплатная генерация. '
             'Можно накопить максимум 3, включая неиспользованную стартовую. '
             'Заходите почаще и пробуйте новые образы!')
log = logging.getLogger(__name__)


class FreeStoreMixin:
    def _free_init(self, c):
        columns = {row['name'] for row in c.execute('PRAGMA table_info(users)')}
        for name, definition in {
            'free_balance': 'INTEGER NOT NULL DEFAULT 0 CHECK(free_balance BETWEEN 0 AND 3)',
            'free_reserved': 'INTEGER NOT NULL DEFAULT 0 CHECK(free_reserved>=0 AND free_reserved<=free_balance)',
            'free_next_due': 'REAL',
            'free_active': 'INTEGER NOT NULL DEFAULT 0',
            'free_blocked': 'INTEGER NOT NULL DEFAULT 0',
        }.items():
            if name not in columns:
                c.execute(f'ALTER TABLE users ADD COLUMN {name} {definition}')
        jobs = {row['name'] for row in c.execute('PRAGMA table_info(jobs)')}
        if 'free_credit' not in jobs:
            c.execute('ALTER TABLE jobs ADD COLUMN free_credit INTEGER NOT NULL DEFAULT 0 CHECK(free_credit IN (0,1))')
        c.execute('CREATE TABLE IF NOT EXISTS free_policy(id INTEGER PRIMARY KEY CHECK(id=1), '
                  'activated REAL NOT NULL, trial INTEGER NOT NULL, exempt INTEGER NOT NULL)')
        c.execute('CREATE INDEX IF NOT EXISTS free_due ON users(free_active,free_next_due)')
        c.execute('''CREATE TABLE IF NOT EXISTS free_notices(
            id TEXT PRIMARY KEY,user_id INTEGER NOT NULL,cycle REAL NOT NULL,
            added INTEGER NOT NULL,remaining INTEGER NOT NULL,next_due REAL NOT NULL,
            status TEXT NOT NULL DEFAULT 'queued',next_attempt REAL NOT NULL DEFAULT 0,
            attempts INTEGER NOT NULL DEFAULT 0,message_id TEXT,UNIQUE(user_id,cycle))''')

    def enable_recurring_free(self, *, trial=False, exempt=0):
        now = time.time()
        with self.tx() as c:
            c.execute('INSERT OR IGNORE INTO free_policy VALUES(1,?,?,?)', (now, int(trial), exempt))
            # Reconfiguration does not reset the activation date or anyone's schedule.
            c.execute('UPDATE free_policy SET trial=?,exempt=? WHERE id=1', (int(trial), exempt))
            rows = c.execute('SELECT * FROM users WHERE id>0 AND id<4503599627370496 '
                             'AND (first_seen IS NOT NULL OR consent=1)').fetchall()
            for u in rows:
                self._free_enroll(c, u, now)

    @staticmethod
    def _free_enroll(c, u, now):
        policy = c.execute('SELECT * FROM free_policy WHERE id=1').fetchone()
        if policy is None or not 0 < u['id'] < 2**52 or u['id'] == policy['exempt']:
            return
        if u['free_next_due'] is None:
            c.execute('UPDATE users SET free_next_due=?,free_active=? WHERE id=?',
                      (now + INTERVAL, int(bool(u['consent'])), u['id']))

    def _free_consent(self, c, u, now):
        self._free_enroll(c, u, now)
        if not u['consent']:
            # First consent keeps the future first-visit date. Reactivation cannot catch up a pause.
            c.execute('UPDATE users SET free_active=1,free_next_due=CASE '
                      'WHEN free_next_due<=? THEN ? ELSE free_next_due END WHERE id=?',
                      (now, now + INTERVAL, u['id']))

    @staticmethod
    def _free_start(u, policy):
        return max(u['trial_reserved'], 1 - u['trial_used']) if policy and policy['trial'] and not u['manual_access'] else 0

    def advance_free_due(self, *, limit=100):
        now, processed = time.time(), 0
        with self.tx() as c:
            policy = c.execute('SELECT * FROM free_policy WHERE id=1').fetchone()
            if policy is None:
                return 0
            for u in c.execute('SELECT * FROM users WHERE free_active=1 AND consent=1 '
                               'AND free_next_due<=? AND id!=? ORDER BY free_next_due LIMIT ?',
                               (now, policy['exempt'], limit)).fetchall():
                periods = int((now - u['free_next_due']) // INTERVAL) + 1
                start = self._free_start(u, policy)
                added = min(periods, max(0, CAP - start - u['free_balance']))
                next_due = u['free_next_due'] + periods * INTERVAL
                c.execute('UPDATE users SET free_balance=free_balance+?,free_next_due=? WHERE id=?',
                          (added, next_due, u['id']))
                # One current notice after downtime; no obsolete batch of catch-up messages.
                c.execute("UPDATE free_notices SET status='skipped' WHERE user_id=? AND status='queued'", (u['id'],))
                if not u['free_blocked']:
                    c.execute('INSERT OR IGNORE INTO free_notices(id,user_id,cycle,added,remaining,next_due) '
                              'VALUES(?,?,?,?,?,?)', (uuid.uuid4().hex, u['id'], u['free_next_due'],
                                                     added, start + u['free_balance'] + added, next_due))
                self._event(c, 'free_refill', str(u['id']))
                processed += 1
        return processed

    def free_info(self, user):
        with self.tx() as c:
            u = self._user(c, user)
            policy = c.execute('SELECT * FROM free_policy WHERE id=1').fetchone()
            start = self._free_start(u, policy)
            return {'enabled': policy is not None, 'free_available': start + u['free_balance']
                    - u['trial_reserved'] - u['free_reserved'],
                    'free_reserved': u['trial_reserved'] + u['free_reserved'],
                    'extra_available': u['balance'] - u['reserved'],
                    'next_due': u['free_next_due'] if u['consent'] and u['free_active'] else None,
                    'cap': CAP, 'interval_days': 5}

    def recover_free_notices(self):
        with self.tx() as c:
            c.execute("UPDATE free_notices SET status='unknown' WHERE status='running'")

    def claim_free_notice(self):
        with self.tx() as c:
            c.execute("UPDATE free_notices SET status='skipped' WHERE status='queued' AND user_id IN "
                      '(SELECT id FROM users WHERE consent=0 OR free_active=0 OR free_blocked=1)')
            row = c.execute("SELECT * FROM free_notices WHERE status='queued' AND next_attempt<=? "
                            'ORDER BY cycle,user_id LIMIT 1', (time.time(),)).fetchone()
            if row is None:
                return None
            c.execute("UPDATE free_notices SET status='running',attempts=attempts+1 WHERE id=?", (row['id'],))
            return dict(row)

    def free_notice_result(self, notice, status, *, message_id=None, retry_after=0):
        if status not in {'sent', 'failed', 'unknown', 'blocked', 'queued', 'skipped'}:
            raise ValueError('invalid_free_notice_status')
        with self.tx() as c:
            row = c.execute("SELECT * FROM free_notices WHERE id=? AND status='running'", (notice,)).fetchone()
            if row is None:
                return
            if status == 'queued' and row['attempts'] >= 5:
                status = 'failed'
            c.execute('UPDATE free_notices SET status=?,message_id=?,next_attempt=? WHERE id=?',
                      (status, str(message_id) if message_id is not None else None,
                       time.time() + max(1, retry_after), notice))
            if status == 'blocked':
                c.execute('UPDATE users SET free_blocked=1 WHERE id=?', (row['user_id'],))


def balance_text(info):
    next_due = info.get('next_due')
    suffix = ('Следующее пополнение: ' + datetime.fromtimestamp(
        next_due, timezone(timedelta(hours=5))).strftime('%d.%m в %H:%M (МСК+2)')
        if next_due else 'Пополнения доступны после согласия в /start.')
    return (f"🎁 Бесплатные: {info['free_available']} из 3\n"
            f"💎 Купленные и дополнительные: {info['extra_available']}\n"
            f"Каждые 5 дней — +1 бесплатно.\n{suffix}")


def notice_text(notice, info):
    if notice['added']:
        intro = (f"🎁 Начислено бесплатных генераций: {notice['added']}."
                 if notice['added'] > 1 else '🎁 Вам начислена одна бесплатная генерация!')
    else:
        intro = '🎁 На момент пополнения у вас уже был полный запас — 3 бесплатные генерации.'
    return intro + '\n\n' + balance_text(info) + '\n\nЗаходите почаще и пробуйте новые образы!'


class FreeDeliveryError(Exception):
    def __init__(self, status, retry_after=0):
        self.status, self.retry_after = status, retry_after


class FreeDelivery:
    def __init__(self, store, send):
        self.store, self.send = store, send

    async def once(self):
        self.store.advance_free_due()
        notice = self.store.claim_free_notice()
        if notice is None:
            return False
        info = self.store.free_info(notice['user_id'])
        if info['next_due'] is None:
            self.store.free_notice_result(notice['id'], 'skipped')
            return True
        try:
            message_id = await self.send(notice['user_id'], notice_text(notice, info))
        except FreeDeliveryError as error:
            self.store.free_notice_result(notice['id'], error.status, retry_after=error.retry_after)
        except Exception:
            # May have reached the messenger; an automatic replay could duplicate it.
            self.store.free_notice_result(notice['id'], 'unknown')
            log.warning('free_notice_unknown')
        else:
            self.store.free_notice_result(notice['id'], 'sent', message_id=message_id)
        return True

    async def worker(self):
        self.store.recover_free_notices()
        while True:
            try:
                active = await self.once()
            except Exception:
                log.exception('free_worker_failed')
                active = False
            await asyncio.sleep(1 if active else 30)
