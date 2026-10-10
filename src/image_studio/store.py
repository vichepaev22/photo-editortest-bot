import json
import re
import sqlite3
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

from .admin_store import AdminStoreMixin, DomainError

TRIAL_LIMIT = 1


def validated_username(username):
    if isinstance(username, str) and re.fullmatch(r"[A-Za-z0-9_]{1,32}", username, re.ASCII):
        return username
    return None


class Store(AdminStoreMixin):
    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        self._enable_wal()
        with self.tx() as c:
            # Keep the historical <=3 constraints so existing counters and reservations remain valid.
            c.executescript("""
                CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY, balance INTEGER NOT NULL DEFAULT 0 CHECK(balance>=0),
                    reserved INTEGER NOT NULL DEFAULT 0 CHECK(reserved>=0 AND reserved<=balance),
                    consent INTEGER NOT NULL DEFAULT 0, demo_used INTEGER NOT NULL DEFAULT 0,
                    spent INTEGER NOT NULL DEFAULT 0, refund_lock INTEGER NOT NULL DEFAULT 0,
                    trial_granted INTEGER NOT NULL DEFAULT 0 CHECK(trial_granted IN (0,1)),
                    trial_used INTEGER NOT NULL DEFAULT 0 CHECK(trial_used>=0 AND trial_used<=3),
                    trial_reserved INTEGER NOT NULL DEFAULT 0
                        CHECK(trial_reserved>=0 AND trial_reserved<=3-trial_used),
                    username TEXT, first_seen REAL, last_seen REAL,
                    manual_access INTEGER NOT NULL DEFAULT 0 CHECK(manual_access IN (0,1)),
                    channel_bonus INTEGER NOT NULL DEFAULT 0 CHECK(channel_bonus IN (0,1)));
                CREATE TABLE IF NOT EXISTS invoices (
                    id TEXT PRIMARY KEY, user_id INTEGER NOT NULL, pack TEXT NOT NULL,
                    amount INTEGER NOT NULL CHECK(amount>0), credits INTEGER NOT NULL CHECK(credits>0),
                    created REAL NOT NULL, status TEXT NOT NULL DEFAULT 'new',
                    spent_baseline INTEGER NOT NULL, provider_id TEXT UNIQUE, confirmation_url TEXT,
                    billing_body TEXT, refund_id TEXT, refund_started REAL);
                CREATE TABLE IF NOT EXISTS payments (
                    charge_id TEXT PRIMARY KEY, order_id TEXT UNIQUE NOT NULL,
                    user_id INTEGER NOT NULL, amount INTEGER NOT NULL,
                    is_test INTEGER NOT NULL DEFAULT 1 CHECK(is_test IN (0,1)));
                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY, user_id INTEGER NOT NULL, request_key TEXT NOT NULL,
                    preset TEXT NOT NULL, cost INTEGER NOT NULL CHECK(cost>0),
                    status TEXT NOT NULL DEFAULT 'queued', created REAL NOT NULL,
                    result TEXT, usage TEXT, request_id TEXT, error TEXT,
                    trial INTEGER NOT NULL DEFAULT 0 CHECK(trial IN (0,1)),
                    quota_exempt INTEGER NOT NULL DEFAULT 0 CHECK(quota_exempt IN (0,1)),
                    UNIQUE(user_id, request_key));
                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY, created REAL NOT NULL, kind TEXT NOT NULL, ref TEXT NOT NULL);
            """)
            # executescript commits the tx's initial BEGIN. Serialize all migration checks
            # and additive ALTERs so concurrent process initialization is idempotent too.
            c.execute("BEGIN IMMEDIATE")
            columns = {row["name"] for row in c.execute("PRAGMA table_info(invoices)")}
            if "refund_started" not in columns:
                c.execute("ALTER TABLE invoices ADD COLUMN refund_started REAL")
            user_columns = {row["name"] for row in c.execute("PRAGMA table_info(users)")}
            trial_columns = {
                "trial_granted": "INTEGER NOT NULL DEFAULT 0 CHECK(trial_granted IN (0,1))",
                "trial_used": "INTEGER NOT NULL DEFAULT 0 CHECK(trial_used>=0 AND trial_used<=3)",
                "trial_reserved": (
                    "INTEGER NOT NULL DEFAULT 0 CHECK(trial_reserved>=0 AND trial_reserved<=3-trial_used)"
                ),
            }
            for name, definition in trial_columns.items():
                if name not in user_columns:
                    c.execute(f"ALTER TABLE users ADD COLUMN {name} {definition}")
            for name, definition in {"username": "TEXT", "first_seen": "REAL", "last_seen": "REAL"}.items():
                if name not in user_columns:
                    c.execute(f"ALTER TABLE users ADD COLUMN {name} {definition}")
            if "manual_access" not in user_columns:
                c.execute("ALTER TABLE users ADD COLUMN manual_access INTEGER NOT NULL DEFAULT 0 "
                          "CHECK(manual_access IN (0,1))")
            if "channel_bonus" not in user_columns:
                c.execute("ALTER TABLE users ADD COLUMN channel_bonus INTEGER NOT NULL DEFAULT 0 "
                          "CHECK(channel_bonus IN (0,1))")
            payment_columns = {row["name"] for row in c.execute("PRAGMA table_info(payments)")}
            if "is_test" not in payment_columns:
                c.execute(
                    "ALTER TABLE payments ADD COLUMN is_test INTEGER NOT NULL DEFAULT 1 "
                    "CHECK(is_test IN (0,1))"
                )
            job_columns = {row["name"] for row in c.execute("PRAGMA table_info(jobs)")}
            if "trial" not in job_columns:
                c.execute("ALTER TABLE jobs ADD COLUMN trial INTEGER NOT NULL DEFAULT 0 CHECK(trial IN (0,1))")
            if "quota_exempt" not in job_columns:
                c.execute(
                    "ALTER TABLE jobs ADD COLUMN quota_exempt INTEGER NOT NULL DEFAULT 0 "
                    "CHECK(quota_exempt IN (0,1))"
                )
            self._admin_init(c)

    def _enable_wal(self):
        # Journal changes need an autocommit connection, outside BEGIN IMMEDIATE.
        # SQLite can report BUSY immediately for lock upgrades despite busy_timeout.
        # Five attempts with a 1s SQLite timeout + four 50ms pauses bound this step.
        connection = sqlite3.connect(self.path, timeout=1)
        try:
            for attempt in range(5):
                try:
                    mode = connection.execute("PRAGMA journal_mode=WAL").fetchone()[0]
                    if mode != "wal":
                        raise sqlite3.OperationalError("wal_unavailable")
                    return
                except sqlite3.OperationalError as error:
                    code = getattr(error, "sqlite_errorcode", 0) & 0xFF
                    if code not in {sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED} or attempt == 4:
                        raise
                    time.sleep(0.05)
        finally:
            connection.close()

    @contextmanager
    def tx(self):
        c = sqlite3.connect(self.path, timeout=20)
        c.row_factory = sqlite3.Row
        try:
            c.execute("BEGIN IMMEDIATE")
            yield c
            c.commit()
        except Exception:
            c.rollback()
            raise
        finally:
            c.close()

    @staticmethod
    def _user(c, user):
        c.execute("INSERT OR IGNORE INTO users(id) VALUES(?)", (user,))
        return c.execute("SELECT * FROM users WHERE id=?", (user,)).fetchone()

    @staticmethod
    def _event(c, kind, ref):
        c.execute("INSERT INTO events(created,kind,ref) VALUES(?,?,?)", (time.time(), kind, ref))

    def record_visit(self, user, username=None):
        if type(user) is not int or not 0 < user < 2**52:
            return False
        now = time.time()
        with self.tx() as c:
            u = self._user(c, user)
            first_visit = u["first_seen"] is None
            c.execute(
                "UPDATE users SET username=?,first_seen=COALESCE(first_seen,?),last_seen=? WHERE id=?",
                (validated_username(username), now, now, user),
            )
            if first_visit:
                self._admin_safe_event(c, f"registration:{user}", "registration",
                                       {"user_id": user, "username": validated_username(username),
                                        "created": now, "consent": bool(u["consent"])})
        return True

    def admin_stats(self, page=0, page_size=10):
        if type(page) is not int or page < 0 or type(page_size) is not int or not 1 <= page_size <= 100:
            raise DomainError("invalid_admin_page")
        with self.tx() as c:
            totals = c.execute("""
                SELECT
                    (SELECT COUNT(*) FROM users WHERE id>0 AND id<4503599627370496) AS total_users,
                    (SELECT COUNT(*) FROM users WHERE id>0 AND id<4503599627370496
                        AND first_seen IS NOT NULL) AS visited_users,
                    (SELECT COUNT(DISTINCT user_id) FROM payments WHERE is_test=0 AND user_id IN
                        (SELECT id FROM users WHERE id>0 AND id<4503599627370496)) AS paying_users,
                    (SELECT COUNT(*) FROM jobs WHERE status IN ('generated','delivered') AND user_id IN
                        (SELECT id FROM users WHERE id>0 AND id<4503599627370496)) AS generated_count,
                    (SELECT COALESCE(SUM(amount),0) FROM payments WHERE is_test=0 AND user_id IN
                        (SELECT id FROM users WHERE id>0 AND id<4503599627370496)) AS revenue_kopecks
            """).fetchone()
            pages = max(1, (totals["total_users"] + page_size - 1) // page_size)
            page = min(page, pages - 1)
            rows = c.execute("""
                WITH generated AS (
                    SELECT user_id,COUNT(*) AS generated_count FROM jobs
                    WHERE status IN ('generated','delivered') GROUP BY user_id
                ), purchased AS (
                    SELECT user_id,COUNT(*) AS purchase_count FROM payments
                    WHERE is_test=0 GROUP BY user_id
                )
                SELECT users.id,users.username,COALESCE(generated.generated_count,0) AS generated_count,
                    COALESCE(purchased.purchase_count,0) AS purchase_count
                FROM users LEFT JOIN generated ON generated.user_id=users.id
                    LEFT JOIN purchased ON purchased.user_id=users.id
                WHERE users.id>0 AND users.id<4503599627370496
                ORDER BY users.id LIMIT ? OFFSET ?
            """, (page_size, page * page_size)).fetchall()
            return dict(totals) | {"page": page, "pages": pages, "users": [dict(row) for row in rows]}

    def consent(self, user):
        with self.tx() as c:
            self._user(c, user)
            c.execute("UPDATE users SET consent=1 WHERE id=?", (user,))

    def has_consent(self, user):
        with self.tx() as c:
            return bool(self._user(c, user)["consent"])

    def wallet(self, user, *, trial=False):
        with self.tx() as c:
            u = self._user(c, user)
            if trial and not u["manual_access"]:
                # Keep legacy reservations visible while clamping exhausted availability to zero.
                total = max(u["trial_reserved"], TRIAL_LIMIT - u["trial_used"]) if u["trial_granted"] else 0
                return total, u["trial_reserved"]
            return u["balance"], u["reserved"]

    def has_manual_access(self, user):
        with self.tx() as c:
            return bool(self._user(c, user)["manual_access"])

    def has_channel_bonus(self, user):
        with self.tx() as c:
            return bool(self._user(c, user)["channel_bonus"])

    def claim_channel_bonus_offer(self, user, job):
        with self.tx() as c:
            u = self._user(c, user)
            j = c.execute("SELECT * FROM jobs WHERE id=? AND user_id=?", (job, user)).fetchone()
            if (j is None or j["status"] != "delivered" or not j["trial"] or j["quota_exempt"]
                    or not u["consent"] or u["manual_access"] or u["channel_bonus"]
                    or u["trial_used"] != TRIAL_LIMIT):
                return False
            if c.execute("SELECT 1 FROM events WHERE kind='channel_bonus_offer' AND ref=?",
                         (str(user),)).fetchone():
                return False
            self._event(c, "channel_bonus_offer", str(user))
            return True

    def grant_channel_bonus(self, user):
        self._trial_user(user)
        with self.tx() as c:
            u = self._user(c, user)
            if u["channel_bonus"]:
                return False
            if not u["consent"]:
                raise DomainError("consent_required")
            if u["reserved"] or u["trial_reserved"]:
                raise DomainError("already_active")
            base = max(0, TRIAL_LIMIT - u["trial_used"]) if not u["manual_access"] else 0
            c.execute("UPDATE users SET balance=balance+?,manual_access=1,channel_bonus=1,"
                      "trial_granted=1,demo_used=1 WHERE id=?", (base + 1, user))
            self._event(c, "channel_bonus", str(user))
            return True

    def grant_manual(self, user, amount, grant_id):
        if type(user) is not int or not 0 < user < 2**52:
            raise DomainError("invalid_manual_user")
        if type(amount) is not int or not 1 <= amount <= 10:
            raise DomainError("invalid_manual_amount")
        if not isinstance(grant_id, str) or not re.fullmatch(r"[0-9a-f]{32}", grant_id):
            raise DomainError("invalid_manual_grant")
        reference = f"{grant_id}:{user}:{amount}"
        with self.tx() as c:
            previous = c.execute(
                "SELECT ref FROM events WHERE kind='manual_grant' AND substr(ref,1,33)=?",
                (grant_id + ":",),
            ).fetchone()
            if previous:
                if previous["ref"] != reference:
                    raise DomainError("manual_grant_mismatch")
                return False
            u = self._user(c, user)
            if not u["manual_access"] and (u["reserved"] or u["trial_reserved"]):
                raise DomainError("already_active")
            # Allocation can precede first visit; reserve still requires consent.
            base = max(0, TRIAL_LIMIT - u["trial_used"]) if not u["manual_access"] else 0
            c.execute("UPDATE users SET balance=balance+?,manual_access=1,trial_granted=1,demo_used=1 WHERE id=?",
                      (amount + base, user))
            self._event(c, "manual_grant", reference)
            return True

    @staticmethod
    def _trial_user(user):
        if type(user) is not int or not 0 < user < 2**52:
            raise DomainError("invalid_trial_user")

    def grant_trial(self, user):
        self._trial_user(user)
        with self.tx() as c:
            u = self._user(c, user)
            if not u["consent"]:
                raise DomainError("consent_required")
            if u["trial_granted"] or u["manual_access"]:
                return False
            c.execute("UPDATE users SET trial_granted=1 WHERE id=?", (user,))
            self._event(c, "trial_grant", str(user))
            return True

    def grant_demo(self, user, amount=3):
        if not isinstance(amount, int) or not 1 <= amount <= 10:
            raise DomainError("invalid_demo")
        with self.tx() as c:
            u = self._user(c, user)
            if u["demo_used"] or not u["consent"]:
                return False
            c.execute("UPDATE users SET demo_used=1,balance=balance+? WHERE id=?", (amount, user))
            self._event(c, "demo", str(user))
            return True

    def invoice(self, user, pack, amount, credits):
        if amount <= 0 or credits <= 0:
            raise DomainError("invalid_invoice")
        with self.tx() as c:
            u = self._user(c, user)
            if not u["consent"]:
                raise DomainError("consent_required")
            order = uuid.uuid4().hex
            c.execute(
                "INSERT INTO invoices(id,user_id,pack,amount,credits,created,spent_baseline) "
                "VALUES(?,?,?,?,?,?,?)",
                (order, user, pack, amount, credits, time.time(), u["spent"]),
            )
            return order

    def grant_pilot(self, user, amount):
        if not isinstance(amount, int) or not 1 <= amount <= 10:
            raise DomainError("invalid_pilot_amount")
        with self.tx() as c:
            u = self._user(c, user)
            if not u["consent"]:
                raise DomainError("consent_required")
            c.execute("UPDATE users SET balance=balance+? WHERE id=?", (amount, user))
            self._event(c, "pilot_grant", f"{user}:{amount}")

    def get_invoice(self, order):
        with self.tx() as c:
            row = c.execute("SELECT * FROM invoices WHERE id=?", (order,)).fetchone()
            if row is None:
                raise DomainError("unknown_order")
            return dict(row)

    def precheck(self, user, order, currency, amount):
        try:
            o = self.get_invoice(order)
            return (
                o["user_id"] == user
                and currency == "RUB"
                and o["amount"] == amount
                and o["status"] == "new"
                and time.time() - o["created"] < 1800
                and self.has_consent(user)
            )
        except DomainError:
            return False

    def billing_body(self, order, body):
        with self.tx() as c:
            row = c.execute("SELECT billing_body FROM invoices WHERE id=?", (order,)).fetchone()
            if row is None:
                raise DomainError("unknown_order")
            if not row["billing_body"]:
                c.execute("UPDATE invoices SET billing_body=? WHERE id=?", (json.dumps(body), order))
            else:
                body = json.loads(row["billing_body"])
            return body

    def bind_payment(self, order, provider_id, url=None):
        with self.tx() as c:
            row = c.execute("SELECT provider_id FROM invoices WHERE id=?", (order,)).fetchone()
            if row is None or (row["provider_id"] and row["provider_id"] != provider_id):
                raise DomainError("payment_identity_mismatch")
            c.execute(
                "UPDATE invoices SET provider_id=?,confirmation_url=COALESCE(?,confirmation_url) WHERE id=?",
                (provider_id, url, order),
            )

    def payment(self, user, order, currency, amount, charge, *, is_test=True):
        with self.tx() as c:
            o = c.execute("SELECT * FROM invoices WHERE id=?", (order,)).fetchone()
            if (
                o is None
                or o["user_id"] != user
                or currency != "RUB"
                or o["amount"] != amount
                or not isinstance(charge, str)
                or not charge
                or type(is_test) is not bool
            ):
                raise DomainError("invalid_payment")
            previous = c.execute("SELECT * FROM payments WHERE charge_id=?", (charge,)).fetchone()
            if previous:
                if previous["order_id"] != order or previous["user_id"] != user:
                    raise DomainError("payment_identity_mismatch")
                return False
            if o["status"] != "new" or (o["provider_id"] and o["provider_id"] != charge):
                raise DomainError("order_already_paid")
            c.execute(
                "INSERT INTO payments(charge_id,order_id,user_id,amount,is_test) VALUES(?,?,?,?,?)",
                (charge, order, user, amount, int(is_test)),
            )
            u = self._user(c, user)
            c.execute(
                "UPDATE invoices SET status='paid',provider_id=?,spent_baseline=? WHERE id=?",
                (charge, u["spent"], order),
            )
            c.execute("UPDATE users SET balance=balance+? WHERE id=?", (o["credits"], user))
            self._event(c, "payment", order)
            if not is_test:
                self._admin_safe_event(c, f"payment:{order}", "payment",
                                       {"user_id": user, "order_id": order, "amount": amount,
                                        "currency": currency, "charge_id": charge, "credits": o["credits"],
                                        "created": time.time()})
            return True

    def reserve(self, user, key, preset, cost, *, trial=False, quota_exempt=False):
        if (type(cost) is not int or cost <= 0 or not key or type(trial) is not bool
                or type(quota_exempt) is not bool or (trial and cost != 1)):
            raise DomainError("invalid_job")
        if trial or quota_exempt:
            self._trial_user(user)
        with self.tx() as c:
            u = self._user(c, user)
            old = c.execute("SELECT * FROM jobs WHERE user_id=? AND request_key=?", (user, key)).fetchone()
            if old:
                if old["preset"] != preset or old["cost"] != cost:
                    raise DomainError("request_mismatch")
                if bool(old["trial"]) != trial:
                    if trial and u["manual_access"] and not old["trial"] and not quota_exempt:
                        raise DomainError("manual_access_required")
                    raise DomainError("request_mismatch")
                return old["id"]
            # The unused trial was moved into balance by a concurrent grant.
            # Choose the funding mode again instead of reserving that same use twice.
            if trial and u["manual_access"] and not quota_exempt:
                raise DomainError("manual_access_required")
            if not u["consent"]:
                raise DomainError("consent_required")
            if (
                u["refund_lock"]
                or c.execute(
                    "SELECT 1 FROM jobs WHERE user_id=? AND status IN ('queued','running','review')", (user,)
                ).fetchone()
            ):
                raise DomainError("already_active")
            if not quota_exempt and trial:
                if not u["trial_granted"]:
                    raise DomainError("trial_not_granted")
                if TRIAL_LIMIT - u["trial_used"] - u["trial_reserved"] < 1:
                    raise DomainError("trial_exhausted")
            elif not quota_exempt and u["balance"] - u["reserved"] < cost:
                raise DomainError("insufficient_credits")
            job = uuid.uuid4().hex
            if not quota_exempt and trial:
                c.execute("UPDATE users SET trial_reserved=trial_reserved+1 WHERE id=?", (user,))
            elif not quota_exempt:
                c.execute("UPDATE users SET reserved=reserved+? WHERE id=?", (cost, user))
            c.execute(
                "INSERT INTO jobs(id,user_id,request_key,preset,cost,created,trial,quota_exempt) "
                "VALUES(?,?,?,?,?,?,?,?)",
                (job, user, key, preset, cost, time.time(), int(trial), int(quota_exempt)),
            )
            self._event(c, "reserve", job)
            return job

    def job(self, job):
        with self.tx() as c:
            row = c.execute("SELECT * FROM jobs WHERE id=?", (job,)).fetchone()
            if row is None:
                raise DomainError("unknown_job")
            return dict(row)

    def job_for_request(self, user, key):
        with self.tx() as c:
            row = c.execute("SELECT * FROM jobs WHERE user_id=? AND request_key=?", (user, key)).fetchone()
            return dict(row) if row else None

    def jobs(self, user=None):
        with self.tx() as c:
            rows = c.execute(
                "SELECT * FROM jobs" + (" WHERE user_id=?" if user is not None else ""),
                (user,) if user is not None else (),
            ).fetchall()
            return [dict(row) for row in rows]

    def claim(self, job):
        with self.tx() as c:
            claimed = (
                c.execute("UPDATE jobs SET status='running' WHERE id=? AND status='queued'", (job,)).rowcount
                == 1
            )
            if claimed:
                self._admin_generation_event(c, job, "running", "reserved")
            return claimed

    def finish(self, job, result, usage, request_id):
        with self.tx() as c:
            j = c.execute("SELECT * FROM jobs WHERE id=?", (job,)).fetchone()
            if j is None:
                raise DomainError("unknown_job")
            if j["status"] in {"generated", "delivered"}:
                return
            if j["status"] != "running":
                raise DomainError("invalid_job_state")
            if not j["quota_exempt"] and j["trial"]:
                c.execute(
                    "UPDATE users SET trial_reserved=trial_reserved-1,trial_used=trial_used+1 WHERE id=?",
                    (j["user_id"],),
                )
            elif not j["quota_exempt"]:
                c.execute(
                    "UPDATE users SET balance=balance-?,reserved=reserved-?,spent=spent+? WHERE id=?",
                    (j["cost"], j["cost"], j["cost"], j["user_id"]),
                )
            c.execute(
                "UPDATE jobs SET status='generated',result=?,usage=?,request_id=? WHERE id=?",
                (result, json.dumps(usage), request_id, job),
            )
            self._event(c, "finish", job)
            self._admin_generation_event(c, job, "generated", "spent")

    def fail(self, job, reason):
        with self.tx() as c:
            j = c.execute("SELECT * FROM jobs WHERE id=?", (job,)).fetchone()
            if j is None or j["status"] not in {"queued", "running", "review"}:
                return
            if not j["quota_exempt"] and j["trial"]:
                c.execute("UPDATE users SET trial_reserved=trial_reserved-1 WHERE id=?", (j["user_id"],))
            elif not j["quota_exempt"]:
                c.execute("UPDATE users SET reserved=reserved-? WHERE id=?", (j["cost"], j["user_id"]))
            c.execute("UPDATE jobs SET status='failed',error=? WHERE id=?", (reason[:60], job))
            self._event(c, "release", job)
            self._admin_generation_event(c, job, "failed", "released")

    def delivered(self, job):
        with self.tx() as c:
            c.execute("UPDATE jobs SET status='delivered' WHERE id=? AND status='generated'", (job,))

    def result(self, user, job):
        j = self.job(job)
        if j["user_id"] != user or j["status"] not in {"generated", "delivered"}:
            raise DomainError("result_unavailable")
        return j

    def recover(self):
        with self.tx() as c:
            interrupted = [r["id"] for r in c.execute("SELECT id FROM jobs WHERE status='running'")]
            c.execute("UPDATE jobs SET status='review',error='interrupted_provider' WHERE status='running'")
            for job in interrupted:
                self._admin_generation_event(c, job, "review", "retained")

    def revoke_consent(self, user):
        with self.tx() as c:
            if c.execute(
                "SELECT 1 FROM jobs WHERE user_id=? AND status IN ('queued','running','review')", (user,)
            ).fetchone():
                raise DomainError("already_active")
            c.execute("UPDATE users SET consent=0,share_enabled=0 WHERE id=?", (user,))
            c.execute("DELETE FROM admin_job_shares WHERE user_id=?", (user,))

    def begin_refund(self, order):
        with self.tx() as c:
            o = c.execute("SELECT * FROM invoices WHERE id=?", (order,)).fetchone()
            if o is None:
                raise DomainError("unknown_order")
            u = self._user(c, o["user_id"])
            if o["status"] == "refunding":
                return dict(o) | {"charge_id": o["provider_id"]}
            if (
                o["status"] != "paid"
                or u["spent"] != o["spent_baseline"]
                or u["reserved"]
                or u["refund_lock"]
                or u["balance"] < o["credits"]
            ):
                raise DomainError("refund_needs_support")
            c.execute("UPDATE users SET refund_lock=1 WHERE id=?", (u["id"],))
            started = time.time()
            c.execute("UPDATE invoices SET status='refunding',refund_started=? WHERE id=?", (started, order))
            self._event(c, "refund_pending", order)
            return dict(o) | {"charge_id": o["provider_id"], "refund_started": started}

    def bind_refund(self, order, refund_id):
        with self.tx() as c:
            o = c.execute("SELECT * FROM invoices WHERE id=?", (order,)).fetchone()
            if o is None or o["status"] != "refunding" or (o["refund_id"] and o["refund_id"] != refund_id):
                raise DomainError("invalid_refund_identity")
            c.execute("UPDATE invoices SET refund_id=? WHERE id=? AND status='refunding'", (refund_id, order))

    def cancel_refund(self, order):
        with self.tx() as c:
            o = c.execute("SELECT * FROM invoices WHERE id=?", (order,)).fetchone()
            if o is None or o["status"] != "refunding":
                raise DomainError("invalid_refund_state")
            c.execute("UPDATE users SET refund_lock=0 WHERE id=?", (o["user_id"],))
            c.execute("UPDATE invoices SET status='refund_canceled' WHERE id=?", (order,))
            self._event(c, "refund_canceled", order)

    def finish_refund(self, order):
        with self.tx() as c:
            o = c.execute("SELECT * FROM invoices WHERE id=?", (order,)).fetchone()
            if o is None or o["status"] != "refunding":
                raise DomainError("invalid_refund_state")
            c.execute(
                "UPDATE users SET balance=balance-?,refund_lock=0 WHERE id=?", (o["credits"], o["user_id"])
            )
            c.execute("UPDATE invoices SET status='refunded' WHERE id=?", (order,))
            self._event(c, "refund", order)
