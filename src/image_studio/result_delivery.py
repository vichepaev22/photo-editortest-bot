"""User-result delivery receipts; never call the image provider or repeat uncertain sends."""

import time
from pathlib import Path

from aiogram.exceptions import TelegramBadRequest, TelegramRetryAfter
from aiogram.types import FSInputFile

from .store import DomainError

RESULT_TTL = 86400


def now():
    return time.time()


class ResultDeliveryDeferred(DomainError):
    def __init__(self, retry_at):
        super().__init__("delivery_deferred")
        self.retry_at = retry_at


def _available(store, user, job, path):
    record = store.result(user, job)
    if not store.has_consent(user):
        raise DomainError("consent_required")
    relative = Path(record["result"])
    path = Path(path)
    if path.parts[-len(relative.parts):] != relative.parts:
        raise DomainError("result_unavailable")
    if not path.is_file() or now() - path.stat().st_mtime > RESULT_TTL:
        raise DomainError("expired_result")
    return record, path.stat().st_mtime + RESULT_TTL


def _receipt(store, bot_id, user, job, result):
    with store.tx() as connection:
        connection.execute("""
            CREATE TABLE IF NOT EXISTS result_documents (
                bot_id INTEGER NOT NULL CHECK(bot_id>0),
                user_id INTEGER NOT NULL CHECK(user_id>0),
                job_id TEXT NOT NULL, result TEXT NOT NULL,
                file_id TEXT, retry_at REAL NOT NULL DEFAULT 0,
                expires_at REAL NOT NULL,
                PRIMARY KEY(bot_id,user_id,job_id))
        """)
        connection.execute("DELETE FROM result_documents WHERE expires_at<=?", (now(),))
        row = connection.execute("""
            SELECT file_id,retry_at FROM result_documents
            WHERE bot_id=? AND user_id=? AND job_id=? AND result=?
        """, (bot_id, user, job, result)).fetchone()
        return dict(row) if row else {"file_id": None, "retry_at": 0}


def _save(store, bot_id, user, job, result, expires_at, file_id, retry_at=0):
    with store.tx() as connection:
        connection.execute("""
            INSERT INTO result_documents(bot_id,user_id,job_id,result,file_id,retry_at,expires_at)
            VALUES(?,?,?,?,?,?,?)
            ON CONFLICT(bot_id,user_id,job_id) DO UPDATE SET
                result=excluded.result,file_id=excluded.file_id,
                retry_at=excluded.retry_at,expires_at=excluded.expires_at
        """, (bot_id, user, job, result, file_id, retry_at, expires_at))


def _invalid_file_id(error):
    # Telegram's messages are not a stable enum; fail closed on unrelated Bad Request errors.
    message = error.message.lower()
    return any(marker in message for marker in (
        "wrong file identifier", "wrong remote file identifier", "file_id not found",
        "invalid file id", "file reference expired", "file_reference_expired",
    ))


async def send_result_document(bot, store, user, job, path, *, filename, **kwargs):
    record, expires_at = _available(store, user, job, path)
    bot_id = bot.id
    if type(bot_id) is not int or bot_id <= 0:
        raise DomainError("invalid_bot_identity")
    receipt = _receipt(store, bot_id, user, job, record["result"])
    if receipt["retry_at"] > now():
        raise ResultDeliveryDeferred(receipt["retry_at"])
    file_id = receipt["file_id"]
    try:
        try:
            message = await bot.send_document(
                user, file_id or FSInputFile(path, filename=filename), **kwargs,
            )
        except TelegramBadRequest as error:
            if not file_id or not _invalid_file_id(error):
                raise
            # Only a definite rejection permits one upload, with fresh ownership/TTL checks.
            file_id = None
            _save(store, bot_id, user, job, record["result"], expires_at, None)
            _available(store, user, job, path)
            message = await bot.send_document(user, FSInputFile(path, filename=filename), **kwargs)
    except TelegramRetryAfter as error:
        retry_at = now() + max(1, error.retry_after)
        _save(store, bot_id, user, job, record["result"], expires_at, file_id, retry_at)
        raise ResultDeliveryDeferred(retry_at) from None
    # No catch/retry for an uncertain network outcome. Missing documents in offline adapters
    # are tolerated; only actual non-empty strings become reusable Telegram receipts.
    document = getattr(message, "document", None)
    received = getattr(document, "file_id", None)
    if isinstance(received, str) and received:
        file_id = received
    _save(store, bot_id, user, job, record["result"], expires_at, file_id)
    return message
