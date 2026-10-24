"""Ограниченный owner-only пилот. Транзакции общие с очередью и балансом."""

import json
import time
import uuid

from .admin_store import DomainError
from .bonus_catalog import PILOT_OWNER, REWARDS, TEMPLATES


class BonusStoreMixin:
    @staticmethod
    def _bonus_init(c):
        # Только добавление таблиц: старые кошельки/резервы не пересчитываются.
        statements = (
            "CREATE TABLE IF NOT EXISTS bonus_accounts(user_id INTEGER PRIMARY KEY, "
            "points INTEGER NOT NULL DEFAULT 0 CHECK(points>=0), xp INTEGER NOT NULL DEFAULT 0 CHECK(xp>=0))",
            "CREATE TABLE IF NOT EXISTS bonus_campaigns(id TEXT PRIMARY KEY, user_id INTEGER UNIQUE NOT NULL, "
            "created REAL NOT NULL, version INTEGER NOT NULL DEFAULT 1)",
            "CREATE TABLE IF NOT EXISTS bonus_missions(id TEXT PRIMARY KEY, campaign_id TEXT NOT NULL, "
            "slot INTEGER NOT NULL, variant INTEGER NOT NULL, title TEXT NOT NULL, summary TEXT NOT NULL, "
            "reward INTEGER NOT NULL, done INTEGER NOT NULL DEFAULT 0, UNIQUE(campaign_id,slot))",
            "CREATE TABLE IF NOT EXISTS bonus_steps(id TEXT PRIMARY KEY, mission_id TEXT NOT NULL, "
            "position INTEGER NOT NULL, definition TEXT NOT NULL, job_id TEXT UNIQUE, "
            "done INTEGER NOT NULL DEFAULT 0, UNIQUE(mission_id,position))",
            "CREATE TABLE IF NOT EXISTS bonus_jobs(job_id TEXT PRIMARY KEY, step_id TEXT NOT NULL)",
            "CREATE TABLE IF NOT EXISTS bonus_ledger(id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL, "
            "ref TEXT UNIQUE NOT NULL, kind TEXT NOT NULL, title TEXT NOT NULL, points INTEGER NOT NULL, "
            "generations INTEGER NOT NULL DEFAULT 0, created REAL NOT NULL)",
            "CREATE TABLE IF NOT EXISTS bonus_checkouts(id TEXT PRIMARY KEY, user_id INTEGER NOT NULL, "
            "quantity INTEGER NOT NULL CHECK(quantity IN (1,3,5)), created REAL NOT NULL, "
            "confirmed INTEGER NOT NULL DEFAULT 0, cancelled INTEGER NOT NULL DEFAULT 0)",
            "CREATE TABLE IF NOT EXISTS bonus_sessions(user_id INTEGER PRIMARY KEY, step_id TEXT NOT NULL, "
            "input_path TEXT, token TEXT NOT NULL, panel_id INTEGER NOT NULL, created REAL NOT NULL)",
        )
        for statement in statements:
            c.execute(statement)

    def _bonus_guard(self, c, user):
        if type(user) is not int or user != PILOT_OWNER:
            raise DomainError("bonus_unavailable")
        u = self._user(c, user)
        if not u["consent"]:
            raise DomainError("consent_required")
        return u

    @staticmethod
    def _bonus_steps(c, mission, variant):
        title, summary, steps = TEMPLATES[mission["slot"]][variant]
        c.execute("UPDATE bonus_missions SET variant=?,title=?,summary=? WHERE id=?",
                  (variant, title, summary, mission["id"]))
        for position, definition in enumerate(steps):
            c.execute("INSERT INTO bonus_steps(id,mission_id,position,definition) VALUES(?,?,?,?)",
                      (uuid.uuid4().hex[:16], mission["id"], position, json.dumps(definition, ensure_ascii=False)))

    def bonus_snapshot(self, user):
        with self.tx() as c:
            self._bonus_guard(c, user)
            c.execute("INSERT OR IGNORE INTO bonus_accounts(user_id) VALUES(?)", (user,))
            campaign = c.execute("SELECT * FROM bonus_campaigns WHERE user_id=?", (user,)).fetchone()
            if campaign is None:
                campaign_id = uuid.uuid4().hex
                c.execute("INSERT INTO bonus_campaigns(id,user_id,created) VALUES(?,?,?)",
                          (campaign_id, user, time.time()))
                for slot, reward in enumerate(REWARDS):
                    mission = {"id": uuid.uuid4().hex[:16], "slot": slot}
                    c.execute("INSERT INTO bonus_missions(id,campaign_id,slot,variant,title,summary,reward) "
                              "VALUES(?,?,?,0,'','',?)", (mission["id"], campaign_id, slot, reward))
                    seen = {json.loads(row["definition"])["title"] for row in c.execute(
                        "SELECT s.definition FROM bonus_steps s JOIN bonus_missions m ON m.id=s.mission_id "
                        "WHERE m.campaign_id=?", (campaign_id,))}
                    overlap = [sum(step["title"] in seen for step in template[2]) for template in TEMPLATES[slot]]
                    candidates = [i for i, score in enumerate(overlap) if score == min(overlap)]
                    variant = candidates[int(campaign_id[slot * 2:slot * 2 + 2], 16) % len(candidates)]
                    self._bonus_steps(c, mission, variant)
                campaign = c.execute("SELECT * FROM bonus_campaigns WHERE user_id=?", (user,)).fetchone()
            result = dict(c.execute("SELECT * FROM bonus_accounts WHERE user_id=?", (user,)).fetchone())
            result["missions"] = []
            for row in c.execute("SELECT * FROM bonus_missions WHERE campaign_id=? ORDER BY slot",
                                 (campaign["id"],)).fetchall():
                mission = dict(row)
                mission["steps"] = [dict(s) | json.loads(s["definition"]) for s in c.execute(
                    "SELECT * FROM bonus_steps WHERE mission_id=? ORDER BY position", (row["id"],))]
                result["missions"].append(mission)
            result["history"] = [dict(row) for row in c.execute(
                "SELECT * FROM bonus_ledger WHERE user_id=? ORDER BY id DESC", (user,))]
            result["earned"] = sum(max(0, row["points"]) for row in result["history"])
            result["redeemed"] = sum(row["generations"] for row in result["history"])
            return result

    def bonus_step(self, user, step_id):
        with self.tx() as c:
            self._bonus_guard(c, user)
            row = c.execute("SELECT s.* FROM bonus_steps s JOIN bonus_missions m ON m.id=s.mission_id "
                            "JOIN bonus_campaigns p ON p.id=m.campaign_id WHERE s.id=? AND p.user_id=?",
                            (step_id, user)).fetchone()
            if row is None:
                raise DomainError("bonus_stale")
            return dict(row) | json.loads(row["definition"])

    def bonus_swap(self, user, mission_id):
        with self.tx() as c:
            self._bonus_guard(c, user)
            mission = c.execute("SELECT m.* FROM bonus_missions m JOIN bonus_campaigns p "
                                "ON p.id=m.campaign_id WHERE m.id=? AND p.user_id=?", (mission_id, user)).fetchone()
            if mission is None:
                raise DomainError("bonus_stale")
            if c.execute("SELECT 1 FROM bonus_jobs b JOIN bonus_steps s ON s.id=b.step_id "
                         "WHERE s.mission_id=?", (mission_id,)).fetchone():
                raise DomainError("bonus_started")
            c.execute("DELETE FROM bonus_sessions WHERE step_id IN "
                      "(SELECT id FROM bonus_steps WHERE mission_id=?)", (mission_id,))
            c.execute("DELETE FROM bonus_steps WHERE mission_id=?", (mission_id,))
            self._bonus_steps(c, mission, (mission["variant"] + 1) % len(TEMPLATES[mission["slot"]]))
        return next(m for m in self.bonus_snapshot(user)["missions"] if m["id"] == mission_id)

    def _bonus_bind(self, c, user, step_id, preset, job):
        self._bonus_guard(c, user)
        row = c.execute("SELECT s.* FROM bonus_steps s JOIN bonus_missions m ON m.id=s.mission_id "
                        "JOIN bonus_campaigns p ON p.id=m.campaign_id WHERE s.id=? AND p.user_id=?",
                        (step_id, user)).fetchone()
        if row is None or row["done"] or row["job_id"]:
            raise DomainError("bonus_stale")
        if json.loads(row["definition"])["preset"] != preset:
            raise DomainError("bonus_step_mismatch")
        if c.execute("SELECT 1 FROM bonus_steps WHERE mission_id=? AND position<? AND done=0",
                     (row["mission_id"], row["position"])).fetchone():
            raise DomainError("bonus_step_order")
        c.execute("UPDATE bonus_steps SET job_id=? WHERE id=?", (job, step_id))
        c.execute("INSERT INTO bonus_jobs(job_id,step_id) VALUES(?,?)", (job, step_id))

    @staticmethod
    def _bonus_award(c, user, ref, kind, title, points):
        if c.execute("INSERT OR IGNORE INTO bonus_ledger(user_id,ref,kind,title,points,created) "
                     "VALUES(?,?,?,?,?,?)", (user, ref, kind, title, points, time.time())).rowcount:
            c.execute("UPDATE bonus_accounts SET points=points+? WHERE user_id=?", (points, user))

    def _bonus_delivered(self, c, job):
        # Unique job binding, not words in the prompt, proves a particular step was run.
        row = c.execute("SELECT s.*,m.reward,m.title,m.campaign_id,p.user_id FROM bonus_steps s "
                        "JOIN bonus_jobs b ON b.step_id=s.id AND b.job_id=s.job_id "
                        "JOIN bonus_missions m ON m.id=s.mission_id "
                        "JOIN bonus_campaigns p ON p.id=m.campaign_id WHERE b.job_id=?", (job,)).fetchone()
        if row is None or row["done"] or row["user_id"] != PILOT_OWNER:
            return
        c.execute("UPDATE bonus_steps SET done=1 WHERE id=?", (row["id"],))
        c.execute("UPDATE bonus_accounts SET xp=xp+10 WHERE user_id=?", (row["user_id"],))
        if c.execute("SELECT 1 FROM bonus_steps WHERE mission_id=? AND done=0",
                     (row["mission_id"],)).fetchone():
            return
        c.execute("UPDATE bonus_missions SET done=1 WHERE id=?", (row["mission_id"],))
        self._bonus_award(c, row["user_id"], "mission:" + row["mission_id"], "mission",
                          row["title"], row["reward"])
        if not c.execute("SELECT 1 FROM bonus_missions WHERE campaign_id=? AND done=0",
                         (row["campaign_id"],)).fetchone():
            self._bonus_award(c, row["user_id"], "complete:" + row["campaign_id"], "completion",
                              "Вся программа завершена", 50)

    def bonus_quote(self, user, quantity):
        if type(quantity) is not int or quantity not in {1, 3, 5}:
            raise DomainError("bonus_invalid_quantity")
        with self.tx() as c:
            self._bonus_guard(c, user)
            account = c.execute("SELECT * FROM bonus_accounts WHERE user_id=?", (user,)).fetchone()
            if not account or account["points"] < quantity * 100:
                raise DomainError("bonus_insufficient")
            token = uuid.uuid4().hex[:16]
            c.execute("INSERT INTO bonus_checkouts(id,user_id,quantity,created) VALUES(?,?,?,?)",
                      (token, user, quantity, time.time()))
            return {"id": token, "quantity": quantity, "points": account["points"]}

    def bonus_exchange(self, user, token):
        with self.tx() as c:
            u = self._bonus_guard(c, user)
            checkout = c.execute("SELECT * FROM bonus_checkouts WHERE id=? AND user_id=?",
                                 (token, user)).fetchone()
            if checkout is None or checkout["cancelled"]:
                raise DomainError("bonus_stale")
            if checkout["confirmed"]:
                return False
            if time.time() - checkout["created"] > 1800:
                raise DomainError("bonus_stale")
            if u["refund_lock"] or (not u["manual_access"] and u["trial_reserved"]):
                raise DomainError("already_active")
            cost = checkout["quantity"] * 100
            if not c.execute("UPDATE bonus_accounts SET points=points-? WHERE user_id=? AND points>=?",
                             (cost, user, cost)).rowcount:
                raise DomainError("bonus_insufficient")
            # Same access conversion as manual crediting, preserving an actually granted
            # unused first attempt. Otherwise trial mode would hide redeemed credits.
            initial = max(0, 1 - u["trial_used"]) if u["trial_granted"] and not u["manual_access"] else 0
            c.execute("UPDATE users SET balance=balance+?,manual_access=1,trial_granted=1,demo_used=1 WHERE id=?",
                      (checkout["quantity"] + initial, user))
            c.execute("INSERT INTO bonus_ledger(user_id,ref,kind,title,points,generations,created) "
                      "VALUES(?,?,'exchange','Обмен на генерации',?,?,?)",
                      (user, "exchange:" + token, -cost, checkout["quantity"], time.time()))
            c.execute("UPDATE bonus_checkouts SET confirmed=1 WHERE id=?", (token,))
            self._event(c, "bonus_exchange", token)
            return True

    def bonus_cancel_quote(self, user, token):
        with self.tx() as c:
            self._bonus_guard(c, user)
            c.execute("UPDATE bonus_checkouts SET cancelled=1 WHERE id=? AND user_id=? AND confirmed=0",
                      (token, user))

    def bonus_session(self, user, *, step_id=None, panel_id=0, input_path=None, clear=False):
        if user != PILOT_OWNER:
            return None
        with self.tx() as c:
            if clear:
                c.execute("DELETE FROM bonus_sessions WHERE user_id=?", (user,))
                return None
            if step_id is not None:
                self._bonus_guard(c, user)
                c.execute("INSERT OR REPLACE INTO bonus_sessions VALUES(?,?,?,?,?,?)",
                          (user, step_id, input_path, uuid.uuid4().hex[:16], panel_id, time.time()))
            row = c.execute("SELECT * FROM bonus_sessions WHERE user_id=?", (user,)).fetchone()
            if row and time.time() - row["created"] <= 1800:
                return dict(row)
            return None
