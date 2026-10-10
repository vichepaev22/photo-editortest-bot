"""Additive, transactional administration persistence; no Telegram/network effects."""

import json
import math
import re
import time
import uuid


class DomainError(Exception):
    pass


CATEGORIES = ("registration", "questions", "generation", "payment")
SNAPSHOT_FIELDS = (
    "balance", "reserved", "trial_granted", "trial_used", "trial_reserved",
    "manual_access", "refund_lock", "channel_bonus",
)


def _id(value):
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{32}", value):
        raise DomainError("invalid_admin_id")


def _actor(value):
    if type(value) is not int or not 0 < value < 2**52:
        raise DomainError("invalid_admin_actor")


class AdminStoreMixin:
    def _admin_init(self, c):
        statements = (
            """CREATE TABLE IF NOT EXISTS admin_credit_operations(
                id TEXT PRIMARY KEY,actor INTEGER NOT NULL,user_id INTEGER NOT NULL,
                mode TEXT NOT NULL,value INTEGER NOT NULL,reason TEXT NOT NULL,
                before_available INTEGER NOT NULL,reserved INTEGER NOT NULL,after_available INTEGER NOT NULL,
                snapshot TEXT NOT NULL,trial INTEGER NOT NULL,created REAL NOT NULL,expires REAL NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',applied_at REAL)""",
            """CREATE TABLE IF NOT EXISTS admin_sessions(
                actor INTEGER PRIMARY KEY,kind TEXT NOT NULL,data TEXT NOT NULL,expires REAL NOT NULL)""",
            """CREATE TABLE IF NOT EXISTS admin_settings(
                category TEXT PRIMARY KEY,enabled INTEGER NOT NULL CHECK(enabled IN (0,1)))""",
            """CREATE TABLE IF NOT EXISTS admin_outbox(
                id TEXT PRIMARY KEY,event_key TEXT UNIQUE NOT NULL,kind TEXT NOT NULL,payload TEXT NOT NULL,
                created REAL NOT NULL,status TEXT NOT NULL DEFAULT 'pending',attempts INTEGER NOT NULL DEFAULT 0,
                next_attempt REAL NOT NULL DEFAULT 0,message_id INTEGER,finished REAL)""",
            "CREATE INDEX IF NOT EXISTS admin_outbox_delivery ON admin_outbox(status,next_attempt,created)",
            """CREATE TABLE IF NOT EXISTS admin_question_limits(
                user_id INTEGER PRIMARY KEY,last_at REAL NOT NULL)""",
            """CREATE TABLE IF NOT EXISTS admin_job_shares(
                job_id TEXT PRIMARY KEY,user_id INTEGER NOT NULL,description TEXT NOT NULL,expires REAL NOT NULL)""",
        )
        for statement in statements:
            c.execute(statement)
        columns = {r["name"] for r in c.execute("PRAGMA table_info(users)")}
        if "share_enabled" not in columns:
            c.execute("ALTER TABLE users ADD COLUMN share_enabled INTEGER NOT NULL DEFAULT 0 "
                      "CHECK(share_enabled IN (0,1))")
        for category in ("enabled", *CATEGORIES):
            c.execute("INSERT OR IGNORE INTO admin_settings VALUES(?,1)", (category,))

    @staticmethod
    def _admin_wallet(u, trial):
        if trial and not u["manual_access"]:
            # The initial entitlement exists before consent/grant_trial, but must
            # never renew when the account becomes finite/manual.
            total = max(u["trial_reserved"], 1 - u["trial_used"])
            # Existing paid funds also survive promotion to manual access.
            available = u["balance"] - u["reserved"] + total - u["trial_reserved"]
        else:
            available = u["balance"] - u["reserved"]
        reserved = u["reserved"] + u["trial_reserved"]
        return available, reserved

    def admin_user(self, user, *, trial=False):
        _actor(user)
        if type(trial) is not bool:
            raise DomainError("invalid_admin_credit")
        with self.tx() as c:
            u = c.execute("SELECT * FROM users WHERE id=?", (user,)).fetchone()
            if u is None:
                raise DomainError("unknown_admin_user")
            available, reserved = self._admin_wallet(u, trial)
            generated = c.execute("SELECT COUNT(*) FROM jobs WHERE user_id=? AND status IN "
                                  "('generated','delivered')", (user,)).fetchone()[0]
            purchases = c.execute("SELECT COUNT(*) FROM payments WHERE user_id=? AND is_test=0",
                                  (user,)).fetchone()[0]
            revenue = c.execute("SELECT COALESCE(SUM(amount),0) FROM payments WHERE user_id=? AND is_test=0",
                                (user,)).fetchone()[0]
            base = max(0, 1 - u["trial_used"] - u["trial_reserved"]) if trial and not u["manual_access"] else 0
            return dict(u) | {"balance": available + reserved, "available": available,
                              "reserved": reserved, "generated_count": generated,
                              "purchase_count": purchases, "revenue_kopecks": revenue,
                              "paid_balance": u["balance"], "paid_available": u["balance"] - u["reserved"],
                              "paid_reserved": u["reserved"], "base_available": base,
                              "trial_remaining": base,
                              "active_available": base if trial and not u["manual_access"] else available,
                              "share_enabled": bool(u["share_enabled"])}

    def admin_search(self, query="", page=0, *, filter="all"):
        if (type(page) is not int or not 0 <= page <= 100000 or not isinstance(query, str)
                or len(query) > 64 or filter not in {"all", "visited", "paying"}):
            raise DomainError("invalid_admin_search")
        query = query.strip().removeprefix("@")
        if not query and filter == "all":
            return self.admin_stats(page)
        if query and not (query.isascii() and query.isdecimal()) and not re.fullmatch(r"[A-Za-z0-9_]{1,32}", query):
            raise DomainError("invalid_admin_search")
        result = self.admin_stats(0)
        clauses = ["u.id>0", "u.id<4503599627370496"]
        params = []
        if filter == "visited":
            clauses.append("u.first_seen IS NOT NULL")
        elif filter == "paying":
            clauses.append("EXISTS(SELECT 1 FROM payments p WHERE p.user_id=u.id AND p.is_test=0)")
        if query:
            if query.isdecimal():
                clauses.append("u.id=?")
                params.append(int(query) if len(query) < 16 else 0)
            else:
                clauses.append("u.username=? COLLATE NOCASE")
                params.append(query)
        where = " AND ".join(clauses)
        with self.tx() as c:
            count = c.execute(f"SELECT COUNT(*) FROM users u WHERE {where}", params).fetchone()[0]
            pages = max(1, (count + 9) // 10)
            page = min(page, pages - 1)
            rows = c.execute(f"SELECT u.id FROM users u WHERE {where} ORDER BY u.id LIMIT 10 OFFSET ?",
                             (*params, page * 10)).fetchall()
        return result | {"page": page, "pages": pages,
                         "matched_users": count, "filter": filter,
                         "users": [self.admin_user(r["id"]) for r in rows]}

    def admin_prepare_credit(self, actor, user, mode, value, reason, *, trial=False):
        _actor(actor)
        _actor(user)
        if actor == user:
            raise DomainError("owner_access_protected")
        if (mode not in {"add", "set"} or type(value) is not int or abs(value) > 100000
                or type(trial) is not bool or not isinstance(reason, str) or not 1 <= len(reason.strip()) <= 240):
            raise DomainError("invalid_admin_credit")
        now = time.time()
        with self.tx() as c:
            u = c.execute("SELECT * FROM users WHERE id=?", (user,)).fetchone()
            if u is None:
                raise DomainError("unknown_admin_user")
            if u["refund_lock"]:
                raise DomainError("refund_needs_support")
            if u["trial_reserved"]:
                # Wait for the original trial funding to settle before conversion.
                raise DomainError("already_active")
            before, reserved = self._admin_wallet(u, trial)
            after = value + before if mode == "add" else value
            if not 0 <= after <= 100000:
                raise DomainError("invalid_admin_credit")
            operation = uuid.uuid4().hex
            c.execute("INSERT INTO admin_credit_operations(id,actor,user_id,mode,value,reason,before_available,"
                      "reserved,after_available,snapshot,trial,created,expires) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                      (operation, actor, user, mode, value, reason.strip(), before, reserved, after,
                       json.dumps({k: u[k] for k in SNAPSHOT_FIELDS}, sort_keys=True), int(trial), now, now + 300))
            return dict(c.execute("SELECT * FROM admin_credit_operations WHERE id=?", (operation,)).fetchone())

    def admin_confirm_credit(self, actor, operation, *, trial=False):
        _actor(actor)
        _id(operation)
        with self.tx() as c:
            op = c.execute("SELECT * FROM admin_credit_operations WHERE id=?", (operation,)).fetchone()
            if op is None or op["actor"] != actor or type(trial) is not bool or bool(op["trial"]) != trial:
                raise DomainError("invalid_admin_confirmation")
            if op["user_id"] == actor:
                raise DomainError("owner_access_protected")
            if op["status"] == "applied":
                return dict(op) | {"applied": False}
            if op["status"] != "pending" or time.time() >= op["expires"]:
                raise DomainError("expired_admin_confirmation")
            u = c.execute("SELECT * FROM users WHERE id=?", (op["user_id"],)).fetchone()
            if u is None or {k: u[k] for k in SNAPSHOT_FIELDS} != json.loads(op["snapshot"]):
                raise DomainError("stale_admin_confirmation")
            if u["trial_reserved"]:
                raise DomainError("already_active")
            # Convert free availability only; paid reservations remain untouched.
            c.execute("UPDATE users SET balance=reserved+?,manual_access=1,trial_granted=1,demo_used=1 WHERE id=?",
                      (op["after_available"], op["user_id"]))
            c.execute("UPDATE admin_credit_operations SET status='applied',applied_at=? WHERE id=?",
                      (time.time(), operation))
            self._event(c, "admin_credit", operation)
            return dict(c.execute("SELECT * FROM admin_credit_operations WHERE id=?", (operation,)).fetchone()) | {"applied": True}

    def admin_cancel_credit(self, actor, operation):
        _actor(actor)
        _id(operation)
        with self.tx() as c:
            row = c.execute("SELECT actor,status FROM admin_credit_operations WHERE id=?", (operation,)).fetchone()
            if row is None or row["actor"] != actor or row["status"] == "applied":
                raise DomainError("invalid_admin_confirmation")
            c.execute("UPDATE admin_credit_operations SET status='canceled' WHERE id=?", (operation,))

    def admin_credit_history(self, user, limit=10):
        _actor(user)
        if type(limit) is not int or not 1 <= limit <= 100:
            raise DomainError("invalid_admin_page")
        with self.tx() as c:
            return [dict(r) for r in c.execute("SELECT * FROM admin_credit_operations WHERE user_id=? "
                                              "AND status='applied' ORDER BY applied_at DESC LIMIT ?", (user, limit))]

    def admin_session(self, actor):
        _actor(actor)
        with self.tx() as c:
            c.execute("DELETE FROM admin_sessions WHERE actor=? AND expires<=?", (actor, time.time()))
            row = c.execute("SELECT * FROM admin_sessions WHERE actor=?", (actor,)).fetchone()
            return {"kind": row["kind"], "data": json.loads(row["data"])} if row else None

    def admin_session_set(self, actor, kind, data):
        _actor(actor)
        if not isinstance(kind, str) or not 1 <= len(kind) <= 64 or not isinstance(data, dict):
            raise DomainError("invalid_admin_session")
        encoded = json.dumps(data, ensure_ascii=False)
        if len(encoded) > 8192:
            raise DomainError("invalid_admin_session")
        with self.tx() as c:
            c.execute("INSERT OR REPLACE INTO admin_sessions VALUES(?,?,?,?)", (actor, kind, encoded, time.time() + 600))

    def admin_session_clear(self, actor):
        _actor(actor)
        with self.tx() as c:
            c.execute("DELETE FROM admin_sessions WHERE actor=?", (actor,))

    def admin_settings(self):
        with self.tx() as c:
            return {r["category"]: bool(r["enabled"]) for r in c.execute("SELECT * FROM admin_settings")}

    def admin_configure(self, category, enabled):
        if category not in ("enabled", *CATEGORIES) or type(enabled) is not bool:
            raise DomainError("invalid_admin_setting")
        with self.tx() as c:
            c.execute("UPDATE admin_settings SET enabled=? WHERE category=?", (int(enabled), category))

    @staticmethod
    def _admin_row(row):
        return dict(row) | {"payload": json.loads(row["payload"])}

    def _admin_enqueue(self, c, key, kind, payload):
        if (not isinstance(key, str) or not 1 <= len(key) <= 240 or kind not in CATEGORIES
                or not isinstance(payload, dict)):
            raise DomainError("invalid_admin_event")
        encoded = json.dumps(payload, ensure_ascii=False, allow_nan=False)
        if len(encoded) > 16384:
            raise DomainError("invalid_admin_event")
        return c.execute("INSERT OR IGNORE INTO admin_outbox(id,event_key,kind,payload,created) VALUES(?,?,?,?,?)",
                         (uuid.uuid4().hex, key, kind, encoded, time.time())).rowcount == 1

    def _admin_safe_event(self, c, key, kind, payload):
        # A savepoint contains partial enqueue failures without touching quota/payment.
        c.execute("SAVEPOINT admin_notification")
        try:
            self._admin_enqueue(c, key, kind, payload)
        except Exception:
            c.execute("ROLLBACK TO admin_notification")
        finally:
            c.execute("RELEASE admin_notification")

    def _admin_generation_event(self, c, job, status, reserve_action):
        j = c.execute("SELECT * FROM jobs WHERE id=?", (job,)).fetchone()
        if j is None:
            return
        now = time.time()
        payload = {k: j[k] for k in ("user_id", "preset", "created", "cost", "trial", "quota_exempt")}
        if j["quota_exempt"]:
            reserve_action = "exempt"
        payload.update(job_id=job, status=status, occurred=now, duration=max(0, now - j["created"]),
                       reserve_action=reserve_action, error_category="provider_error" if status == "failed"
                       else "recovery" if status == "review" else None)
        self._admin_safe_event(c, f"generation:{job}:{status}", "generation", payload)

    def admin_event(self, key, kind, payload):
        with self.tx() as c:
            return self._admin_enqueue(c, key, kind, payload)

    def admin_events(self, page=0):
        if type(page) is not int or not 0 <= page <= 100000:
            raise DomainError("invalid_admin_page")
        with self.tx() as c:
            return [self._admin_row(r) for r in c.execute("SELECT * FROM admin_outbox ORDER BY created DESC,rowid DESC "
                                                        "LIMIT 10 OFFSET ?", (page * 10,))]

    def admin_event_detail(self, event_id):
        _id(event_id)
        with self.tx() as c:
            row = c.execute("SELECT * FROM admin_outbox WHERE id=?", (event_id,)).fetchone()
            if row is None:
                raise DomainError("unknown_admin_event")
            return self._admin_row(row)

    def admin_claim_event(self):
        with self.tx() as c:
            c.execute("DELETE FROM admin_job_shares WHERE expires<=?", (time.time(),))
            row = c.execute("""SELECT o.* FROM admin_outbox o WHERE o.status='pending' AND o.attempts<3
                AND o.next_attempt<=? AND o.kind IN ('registration','questions','generation','payment') AND
                (SELECT enabled FROM admin_settings WHERE category='enabled')=1 AND
                (SELECT enabled FROM admin_settings WHERE category=o.kind)=1
                ORDER BY o.created,o.rowid LIMIT 1""", (time.time(),)).fetchone()
            if row is None:
                return None
            c.execute("UPDATE admin_outbox SET status='running',attempts=attempts+1 WHERE id=?", (row["id"],))
            return self._admin_row(c.execute("SELECT * FROM admin_outbox WHERE id=?", (row["id"],)).fetchone())

    def admin_finish_event(self, event_id, message_id):
        _id(event_id)
        if type(message_id) is not int or message_id <= 0:
            raise DomainError("invalid_admin_message")
        with self.tx() as c:
            c.execute("UPDATE admin_outbox SET status='sent',message_id=?,finished=? WHERE id=? AND status='running'",
                      (message_id, time.time(), event_id))

    def admin_fail_event(self, event_id, *, uncertain=False, retry_after=None):
        _id(event_id)
        if (type(uncertain) is not bool or retry_after is not None and
                (type(retry_after) not in {int, float} or not math.isfinite(retry_after) or retry_after < 0)):
            raise DomainError("invalid_admin_retry")
        with self.tx() as c:
            row = c.execute("SELECT * FROM admin_outbox WHERE id=? AND status='running'", (event_id,)).fetchone()
            if row is None:
                return
            retry = not uncertain and retry_after is not None and row["attempts"] < 3
            status = "needs_review" if uncertain else "pending" if retry else "failed"
            delay = max(1, min(float(retry_after), 3600), 2 ** row["attempts"]) if retry else 0
            c.execute("UPDATE admin_outbox SET status=?,next_attempt=?,finished=? WHERE id=?",
                      (status, time.time() + delay, None if retry else time.time(), event_id))

    def admin_recover_events(self):
        with self.tx() as c:
            return c.execute("UPDATE admin_outbox SET status='needs_review',finished=? WHERE status='running'",
                             (time.time(),)).rowcount

    def admin_capture_question(self, user, text, message_id, chat_id):
        _actor(user)
        if (type(message_id) is not int or message_id <= 0 or type(chat_id) is not int or chat_id != user or not isinstance(text, str)
                or not text.strip()):
            return False
        now = time.time()
        with self.tx() as c:
            limit = c.execute("SELECT last_at FROM admin_question_limits WHERE user_id=?", (user,)).fetchone()
            if limit and now - limit["last_at"] < 60:
                return False
            u = self._user(c, user)
            accepted = self._admin_enqueue(c, f"question:{chat_id}:{message_id}", "questions",
                                          {"user_id": user, "username": u["username"], "text": text[:1000],
                                           "message_id": message_id, "chat_id": chat_id, "created": now})
            if accepted:
                c.execute("INSERT OR REPLACE INTO admin_question_limits VALUES(?,?)", (user, now))
            return accepted

    def admin_set_sharing(self, user, enabled):
        _actor(user)
        if type(enabled) is not bool:
            raise DomainError("invalid_admin_sharing")
        with self.tx() as c:
            self._user(c, user)
            c.execute("UPDATE users SET share_enabled=? WHERE id=?", (int(enabled), user))
            if not enabled:
                c.execute("DELETE FROM admin_job_shares WHERE user_id=?", (user,))

    def admin_share_enabled(self, user):
        _actor(user)
        with self.tx() as c:
            row = c.execute("SELECT share_enabled FROM users WHERE id=?", (user,)).fetchone()
            return bool(row and row["share_enabled"])

    def admin_capture_job(self, job, user, description):
        _id(job)
        _actor(user)
        if not isinstance(description, str):
            raise DomainError("invalid_job_description")
        with self.tx() as c:
            c.execute("DELETE FROM admin_job_shares WHERE expires<=?", (time.time(),))
            row = c.execute("SELECT j.created,u.share_enabled,u.consent FROM jobs j JOIN users u ON u.id=j.user_id "
                            "WHERE j.id=? AND j.user_id=?", (job, user)).fetchone()
            if row is None:
                raise DomainError("unknown_job")
            expires = row["created"] + 86400
            if not row["share_enabled"] or not row["consent"] or time.time() >= expires:
                return False
            c.execute("INSERT OR IGNORE INTO admin_job_shares VALUES(?,?,?,?)", (job, user, description[:2000], expires))
            return True

    def admin_job_detail(self, job):
        _id(job)
        with self.tx() as c:
            c.execute("DELETE FROM admin_job_shares WHERE expires<=?", (time.time(),))
            row = c.execute("SELECT j.*,u.share_enabled,u.consent FROM jobs j JOIN users u ON u.id=j.user_id WHERE j.id=?",
                            (job,)).fetchone()
            if row is None:
                raise DomainError("unknown_job")
            detail = {k: row[k] for k in ("id", "user_id", "preset", "status", "created", "cost", "trial", "quota_exempt")}
            shared = c.execute("SELECT * FROM admin_job_shares WHERE job_id=?", (job,)).fetchone()
            now = time.time()
            available = bool(row["share_enabled"] and row["consent"] and shared and
                             now < shared["expires"] and now < row["created"] + 86400)
            detail.update(share_enabled=bool(row["share_enabled"]), share_available=available,
                          expires=row["created"] + 86400)
            if available:
                detail.update(description=shared["description"], result=row["result"])
            return detail
