"""Local Mini App transport over the bot's existing service and ledger.

No provider, queue worker, Telegram client or external network lifecycle belongs here.
"""

import hashlib
import hmac
import ipaddress
import json
import re
import secrets
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import parse_qsl, urlsplit

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from starlette.exceptions import HTTPException

from .catalog import PRESETS
from .media import MAX_BYTES, normalize
from .store import DomainError

JSON_LIMIT = 16384
INIT_DATA_LIMIT = 8192
SESSION_TTL = 3600
SESSION_LIMIT = 1000
MEDIA_TTL = 86400
UUID_PATTERN = re.compile(r"(?:[0-9a-fA-F]{32}|[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12})")
SAFE_JOB_ERRORS = {
    "provider_connection_or_timeout", "provider_authentication", "provider_quota", "provider_rate_limit",
    "provider_rejected", "provider_unavailable", "invalid_provider_response", "missing_output",
    "processing_error", "storage_error", "interrupted_provider",
}
DOMAIN_CODES = {
    "invalid_inputs": 400, "invalid_job": 400, "invalid_prompt": 400,
    "consent_required": 403, "request_mismatch": 409, "already_active": 409,
    "insufficient_credits": 409, "storage_error": 500,
    "trial_exhausted": 409, "trial_not_granted": 403, "invalid_trial_user": 403,
}


class APIError(Exception):
    def __init__(self, code, status=400):
        self.code, self.status = code, status


@dataclass(frozen=True)
class Session:
    user: dict
    expires: float


def _telegram_user(init_data, token):
    if not isinstance(init_data, str) or len(init_data) > INIT_DATA_LIMIT:
        raise APIError("invalid_init_data", 401)
    if not token or not init_data or re.search(r"%(?![0-9a-fA-F]{2})", init_data):
        raise APIError("invalid_init_data", 401)
    try:
        if len(init_data.encode("utf-8")) > INIT_DATA_LIMIT:
            raise ValueError
        pairs = parse_qsl(init_data, strict_parsing=True, errors="strict", max_num_fields=64)
        fields = dict(pairs)
        if len(fields) != len(pairs):
            raise ValueError
        digest = fields.pop("hash")
        if not re.fullmatch(r"[0-9a-fA-F]{64}", digest):
            raise ValueError
        # Telegram HMAC includes every received field except hash, including signature.
        check = "\n".join(f"{key}={value}" for key, value in sorted(fields.items()))
        secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
        expected = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, digest.lower()):
            raise ValueError
        if not re.fullmatch(r"[0-9]{1,12}", fields["auth_date"]):
            raise ValueError
        age = time.time() - int(fields["auth_date"])
        if not -30 <= age <= SESSION_TTL:
            raise ValueError
        user = json.loads(fields["user"], object_pairs_hook=_unique_object)
        if not isinstance(user, dict) or type(user.get("id")) is not int or not 0 < user["id"] < 2**52:
            raise ValueError
        first_name = user.get("first_name", "")
        if not isinstance(first_name, str) or len(first_name) > 128:
            raise ValueError
        identity = {"id": user["id"], "first_name": first_name}
        username = user.get("username")
        if isinstance(username, str) and re.fullmatch(r"[A-Za-z0-9_]{1,32}", username):
            identity["username"] = username
        return identity
    except (ValueError, KeyError, TypeError, UnicodeError, RecursionError):
        raise APIError("invalid_init_data", 401) from None


def _unique_object(pairs):
    result = dict(pairs)
    if len(result) != len(pairs):
        raise ValueError("duplicate_json_field")
    return result


async def _bounded_body(request, limit):
    length = request.headers.get("content-length")
    if length is not None:
        if not length.isdigit() or len(length) > 12:
            raise APIError("invalid_body")
        if int(length) > limit:
            raise APIError("body_too_large", 413)
    body = bytearray()
    async for chunk in request.stream():
        if len(body) + len(chunk) > limit:
            raise APIError("body_too_large", 413)
        body.extend(chunk)
    return bytes(body)


async def _json_body(request):
    try:
        body = json.loads(await _bounded_body(request, JSON_LIMIT), object_pairs_hook=_unique_object)
        if not isinstance(body, dict):
            raise ValueError
        return body
    except (ValueError, UnicodeError, RecursionError):
        raise APIError("invalid_body") from None


def _uuid(value, status=400):
    if not isinstance(value, str) or not UUID_PATTERN.fullmatch(value):
        raise APIError("invalid_id" if status == 400 else "not_found", status)
    return uuid.UUID(value).hex


def create_studio_app(settings, store, media, service):
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    sessions = {}
    assets = Path(__file__).parent / "web"
    local_hosts = {"localhost", "127.0.0.1", "::1"}
    configured = urlsplit(getattr(settings, "mini_app_url", "") or "")
    allowed_hosts = local_hosts | ({configured.hostname} if configured.hostname else set())
    cors_origin = (f"{configured.scheme}://{configured.netloc}"
                   if configured.scheme == "https" and configured.hostname
                   and not configured.username and not configured.password else None)

    def demo_enabled():
        return settings.image_provider == "mock" and settings.demo_credits

    def parsed_host(request):
        raw = request.headers.get("host", "")
        try:
            parsed = urlsplit("http://" + raw)
            port = parsed.port
            if (not raw or parsed.username or parsed.password or parsed.path or parsed.query
                    or parsed.fragment or parsed.hostname not in allowed_hosts
                    or (port is not None and not 1 <= port <= 65535)):
                raise ValueError
            return parsed.hostname
        except ValueError:
            raise APIError("host_forbidden", 403) from None

    def require_local(request):
        if not demo_enabled() or parsed_host(request) not in local_hosts or request.client is None:
            raise APIError("demo_unavailable", 403)
        try:
            peer = ipaddress.ip_address(request.client.host)
        except ValueError:
            raise APIError("demo_unavailable", 403) from None
        if not peer.is_loopback:
            raise APIError("demo_unavailable", 403)

    def purge_sessions():
        now = time.time()
        for token in list(sessions):
            if sessions[token].expires <= now:
                del sessions[token]

    def session_response(user):
        if store.has_consent(user["id"]):
            service.wallet(user["id"])
        purge_sessions()
        if len(sessions) >= SESSION_LIMIT:
            raise APIError("session_limit", 503)
        token = secrets.token_urlsafe(32)
        sessions[token] = Session(user, time.time() + SESSION_TTL)
        return {"token": token, "user": user, "consent": store.has_consent(user["id"])}

    def identity(request, consent=False):
        purge_sessions()
        authorization = request.headers.get("authorization", "")
        if not re.fullmatch(r"Bearer [A-Za-z0-9_-]{43}", authorization):
            raise APIError("unauthorized", 401)
        session = sessions.get(authorization[7:])
        if session is None:
            raise APIError("unauthorized", 401)
        if session.user["id"] == 0:
            require_local(request)
        if consent and not store.has_consent(session.user["id"]):
            raise APIError("consent_required", 403)
        return session.user

    def own_file(user, relative):
        try:
            path = media.path(relative)
            if (path.parent != media.path(str(user)) or not path.is_file()
                    or time.time() - path.stat().st_mtime > MEDIA_TTL):
                raise ValueError
            return path
        except (ValueError, OSError, TypeError):
            raise APIError("not_found", 404) from None

    def own_job(user, job):
        try:
            record = store.job(_uuid(job, 404))
            if record["user_id"] != user:
                raise APIError("not_found", 404)
            return record
        except DomainError:
            raise APIError("not_found", 404) from None

    def job_view(record):
        available = False
        if record["status"] in {"generated", "delivered"} and record["result"]:
            try:
                own_file(record["user_id"], record["result"])
                available = True
            except APIError:
                pass
        preset = PRESETS.get(record["preset"])
        error = record["error"]
        return {
            "id": record["id"], "preset": record["preset"],
            "label": preset.label if preset else "Обработка", "cost": record["cost"],
            "status": record["status"], "created": record["created"], "has_result": available,
            "error": error if error in SAFE_JOB_ERRORS else ("processing_error" if error else None),
        }

    @app.middleware("http")
    async def security(request, call_next):
        origin = request.headers.get("origin")
        try:
            parsed_host(request)
            if request.method not in {"GET", "HEAD"} and origin is not None:
                allowed_origins = {f"{request.url.scheme}://{request.headers['host']}"}
                if cors_origin:
                    allowed_origins.add(cors_origin)
                if origin not in allowed_origins:
                    raise APIError("origin_forbidden", 403)
            if request.method == "OPTIONS" and "access-control-request-method" in request.headers:
                requested_headers = {
                    field.strip().lower()
                    for field in request.headers.get("access-control-request-headers", "").split(",")
                    if field.strip()
                }
                if (origin != cors_origin or not cors_origin
                        or request.headers["access-control-request-method"] not in {"GET", "POST"}
                        or not requested_headers <= {"authorization", "content-type"}):
                    raise APIError("origin_forbidden", 403)
                response = Response(status_code=204, headers={
                    "Access-Control-Allow-Methods": "GET, POST",
                    "Access-Control-Allow-Headers": "Authorization, Content-Type",
                    "Access-Control-Max-Age": "600",
                })
            else:
                response = await call_next(request)
        except APIError as error:
            response = JSONResponse({"error": error.code}, status_code=error.status)
        except DomainError as error:
            code = str(error)
            response = JSONResponse({"error": code if code in DOMAIN_CODES else "internal_error"},
                                    status_code=DOMAIN_CODES.get(code, 500))
        except Exception:
            # Never expose or log provider payloads, prompts, auth data, credentials or filesystem paths.
            response = JSONResponse({"error": "internal_error"}, status_code=500)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        if cors_origin and origin == cors_origin:
            response.headers["Access-Control-Allow-Origin"] = cors_origin
            response.headers["Access-Control-Expose-Headers"] = "Content-Disposition"
            response.headers["Vary"] = "Origin"
        return response

    @app.exception_handler(HTTPException)
    async def http_error(request, error):
        return JSONResponse({"error": "not_found" if error.status_code == 404 else "invalid_request"},
                            status_code=error.status_code)

    @app.get("/api/health")
    async def health():
        return {"ok": True, "mode": settings.image_provider, "local_demo": demo_enabled()}

    @app.get("/api/catalog")
    async def catalog():
        return {
            "presets": [{"id": key, "label": preset.label, "inputs": preset.inputs, "credits": service.cost(key)}
                        for key, preset in PRESETS.items() if key != "document_original"],
            "mode": settings.image_provider, "local_demo": demo_enabled(),
            "support_contact": settings.support_contact,
            "trial_access": service.trial_access,
        }

    @app.post("/api/session")
    async def session(request: Request):
        data = await _json_body(request)
        user = _telegram_user(data.get("init_data"), settings.bot_token)
        response = session_response(user)
        store.record_visit(user["id"], user.get("username"))
        return response

    @app.post("/api/demo-session")
    async def demo_session(request: Request):
        require_local(request)
        data = await _json_body(request)
        if data.get("consent") is not True:
            raise APIError("consent_required")
        store.consent(0)
        store.grant_demo(0, 3)
        return session_response({"id": 0, "first_name": "Локальное демо"})

    @app.get("/api/me")
    async def me(request: Request):
        user = identity(request)
        balance, reserved = service.wallet(user["id"])
        return {"user": user, "consent": store.has_consent(user["id"]),
                "available": balance - reserved, "reserved": reserved, "mode": settings.image_provider,
                "trial_access": service.trial_access}

    @app.post("/api/consent")
    async def consent(request: Request):
        identity(request)
        data = await _json_body(request)
        user = identity(request)
        if data.get("accepted") is not True:
            raise APIError("consent_required")
        store.consent(user["id"])
        service.wallet(user["id"])
        return {"ok": True}

    @app.post("/api/demo-credits")
    async def demo_credits(request: Request):
        user = identity(request, consent=True)
        if service.trial_access:
            return {"granted": store.grant_trial(user["id"])}
        if not demo_enabled():
            raise APIError("demo_unavailable", 403)
        return {"granted": store.grant_demo(user["id"], 3)}

    @app.post("/api/photos")
    async def upload(request: Request):
        user = identity(request, consent=True)["id"]
        content_type = request.headers.get("content-type", "application/octet-stream").split(";")[0]
        if content_type != "application/octet-stream" and not content_type.startswith("image/"):
            raise APIError("unsupported_media", 415)
        body = await _bounded_body(request, MAX_BYTES)
        # Streaming yields: deletion/revocation in another request must prevent saving afterward.
        identity(request, consent=True)
        directory = media.user_dir(user)
        if sum(1 for file in directory.glob("*.jpg") if file.is_file()) >= 24:
            raise APIError("photo_limit", 409)
        try:
            normalized = normalize(body)
        except ValueError:
            raise APIError("invalid_image") from None
        photo = uuid.uuid4().hex
        media.save(user, normalized, photo + ".jpg")
        return {"id": photo}

    @app.get("/api/photos/{photo}")
    async def photo(request: Request, photo: str):
        user = identity(request)["id"]
        path = own_file(user, f"{user}/{_uuid(photo, 404)}.jpg")
        return FileResponse(path, media_type="image/jpeg")

    @app.post("/api/jobs")
    async def submit(request: Request):
        identity(request, consent=True)
        data = await _json_body(request)
        user = identity(request, consent=True)["id"]
        if data.get("confirmed") is not True:
            raise APIError("confirmation_required")
        key = _uuid(data.get("request_key"))
        preset = data.get("preset")
        if not isinstance(preset, str) or preset not in PRESETS:
            raise APIError("invalid_inputs")
        description, photos = data.get("description"), data.get("photos")
        if (not isinstance(description, str) or not 1 <= len(description.strip()) <= 1500
                or len(description) > 1500 or not isinstance(photos, list)
                or len(photos) != PRESETS[preset].inputs):
            raise APIError("invalid_inputs")
        ids = [_uuid(photo) for photo in photos]
        if len(set(ids)) != len(ids):
            raise APIError("invalid_inputs")
        # Replays return the existing ledger job even after input TTL cleanup, without resubmission.
        for record in store.jobs(user):
            if record["request_key"] == key:
                if (record["preset"] != preset or bool(record["trial"]) != service.trial_access
                        or record["cost"] != service.cost(preset)):
                    raise APIError("request_mismatch", 409)
                return {"id": record["id"], "status": record["status"]}
        inputs = [f"{user}/{photo}.jpg" for photo in ids]
        for relative in inputs:
            own_file(user, relative)
        job = service.submit(user, key, preset, inputs, description)
        return {"id": job, "status": store.job(job)["status"]}

    @app.get("/api/jobs")
    async def jobs(request: Request):
        user = identity(request)["id"]
        records = sorted(store.jobs(user), key=lambda record: record["created"], reverse=True)[:20]
        return {"jobs": [job_view(record) for record in records]}

    @app.get("/api/jobs/{job}")
    async def job(request: Request, job: str):
        return job_view(own_job(identity(request)["id"], job))

    @app.get("/api/jobs/{job}/result")
    async def result(request: Request, job: str):
        user = identity(request)["id"]
        record = own_job(user, job)
        if record["status"] not in {"generated", "delivered"} or not record["result"]:
            raise APIError("not_found", 404)
        path = own_file(user, record["result"])
        png = record["preset"] in {"document", "document_original"}
        extension = ".png" if png else ".jpg"
        return FileResponse(path, media_type="image/png" if png else "image/jpeg",
                            filename="obraz-" + record["id"] + extension)

    @app.post("/api/delete")
    async def delete(request: Request):
        identity(request)
        data = await _json_body(request)
        user = identity(request)["id"]
        if data.get("confirmed") is not True:
            raise APIError("confirmation_required")
        service.delete(user)
        for token in list(sessions):
            if sessions[token].user["id"] == user:
                del sessions[token]
        return {"ok": True}

    async def asset(name):
        path = assets / name
        if not path.is_file():
            raise APIError("not_found", 404)
        return FileResponse(path)

    @app.get("/")
    async def index():
        return await asset("index.html")

    @app.get("/app.css")
    async def stylesheet():
        return await asset("app.css")

    @app.get("/app.js")
    async def javascript():
        return await asset("app.js")

    @app.get("/config.js")
    async def configuration():
        return await asset("config.js")

    return app
