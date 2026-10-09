import hashlib
import hmac
import io
import json
import os
import time
import uuid
from pathlib import Path
from urllib.parse import urlencode

import httpx
import pytest
from PIL import Image

from image_studio.config import Settings
from image_studio.media import MAX_BYTES, Media
from image_studio.provider import MockProvider, ProviderError
from image_studio.service import Service
from image_studio.store import Store
from image_studio.studio import create_studio_app

TOKEN = "123456:offline-token-only"


async def test_owner_unlimited_is_authenticated_and_body_cannot_grant_it(studio):
    client, settings, store, media, provider, service, _ = studio
    settings.admin_user_id, settings.owner_unlimited_testing = 1, True
    service.unlimited_user_id = 1
    assert (await client.get("/api/me")).status_code == 401
    for user in (1, 2):
        headers = await auth(client, user)
        assert (await client.post("/api/consent", json={"accepted": True}, headers=headers)).status_code == 200
        result = (await client.get("/api/me", headers=headers)).json()
        assert result["unlimited"] is (user == 1) and result["available"] == 0
        upload = await client.post("/api/photos", content=image(), headers=headers)
        assert upload.status_code == 200
        if user == 1:
            for _ in range(4):
                response = await client.post("/api/jobs", json=body(upload.json()["id"]), headers=headers)
                assert response.status_code == 200
                job = response.json()["id"]
                assert store.job(job)["quota_exempt"] == 1
                assert await service.process(job)
            assert (await client.get("/api/me", headers=headers)).json()["unlimited"] is True
        else:
            forged = body(upload.json()["id"]) | {"unlimited": True, "quota_exempt": True, "user_id": 1}
            response = await client.post("/api/jobs", json=forged, headers=headers)
            assert response.status_code == 409 and store.jobs(2) == []
    assert store.wallet(1) == (0, 0) and store.wallet(2) == (0, 0) and provider.calls == 4


def signed(user=1, age=0, extra=None, user_data=None):
    fields = {
        "auth_date": str(int(time.time()) - age),
        "query_id": "offline-query",
        "user": json.dumps(user_data or {"id": user, "first_name": "Test"}),
        **(extra or {}),
    }
    data = "\n".join(f"{key}={value}" for key, value in sorted(fields.items()))
    secret = hmac.new(b"WebAppData", TOKEN.encode(), hashlib.sha256).digest()
    fields["hash"] = hmac.new(secret, data.encode(), hashlib.sha256).hexdigest()
    return urlencode(fields)


def image():
    buffer = io.BytesIO()
    source = Image.new("RGB", (32, 32), "purple")
    exif = source.getexif()
    exif[0x010E] = "private metadata"
    source.save(buffer, "JPEG", exif=exif)
    return buffer.getvalue()


@pytest.fixture
async def studio(tmp_path):
    settings = Settings(data_dir=tmp_path, bot_token=TOKEN, demo_credits=True)
    store, media, provider = Store(tmp_path / "db.sqlite3"), Media(tmp_path), MockProvider()
    provider.calls = 0
    service = Service(store, media, provider)
    app = create_studio_app(settings, store, media, service)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, client=("127.0.0.1", 4321)),
        base_url="http://127.0.0.1:8089",
    ) as client:
        yield client, settings, store, media, provider, service, app


async def auth(client, user=1):
    response = await client.post("/api/session", json={"init_data": signed(user)})
    assert response.status_code == 200
    return {"Authorization": "Bearer " + response.json()["token"]}


async def prepare(studio, user=1):
    client, _, store, *_ = studio
    headers = await auth(client, user)
    assert (await client.post("/api/consent", json={"accepted": True}, headers=headers)).status_code == 200
    store.grant_demo(user)
    upload = await client.post("/api/photos", content=image(), headers=headers)
    assert upload.status_code == 200
    return headers, upload.json()["id"]


def body(photo, preset="hair", key=None):
    return {
        "request_key": key or str(uuid.uuid4()), "preset": preset, "photos": [photo],
        "description": "Короткая стрижка", "confirmed": True,
    }


async def test_signed_signature_is_in_hmac_and_telegram_receives_no_automatic_credits(studio):
    client, _, store, *_ = studio
    response = await client.post("/api/session", json={"init_data": signed(extra={"signature": "signed-value"})})
    assert response.status_code == 200
    assert response.json()["user"]["id"] == 1
    assert len(response.json()["token"]) >= 43
    assert store.wallet(1) == (0, 0)
    assert response.headers["cache-control"] == "no-store"
    forged = signed(extra={"signature": "signed-value"}).replace("signed-value", "changed")
    assert (await client.post("/api/session", json={"init_data": forged})).status_code == 401


@pytest.mark.parametrize("data", [
    lambda: signed(age=3601), lambda: signed(age=-31), lambda: signed() + "&auth_date=1",
    lambda: signed(user_data={"id": True, "first_name": "Test"}),
    lambda: signed(user=0), lambda: signed(user=2**52), lambda: signed() + "&user=%ZZ",
    lambda: "invalid", lambda: "a" * 9000,
])
async def test_auth_rejects_forged_expired_duplicate_or_invalid_identity(studio, data):
    response = await studio[0].post("/api/session", json={"init_data": data()})
    assert response.status_code in {400, 401, 413}
    assert set(response.json()) == {"error"}


async def test_body_and_origin_host_guards(studio):
    client = studio[0]
    assert (await client.get("/api/health", headers={"Host": "attacker.example"})).status_code == 403
    assert (await client.post("/api/demo-session", json={"consent": True},
                              headers={"Origin": "https://attacker.example"})).status_code == 403
    assert (await client.post("/api/session", content=b"x" * 17000)).status_code == 413
    assert (await client.post("/api/session", content=b"not-json")).status_code == 400
    assert (await client.get("/api/me")).status_code == 401
    assert "access-control-allow-origin" not in (await client.get("/api/health")).headers


async def test_demo_requires_explicit_consent_mock_flag_and_loopback_peer(studio):
    client, settings, store, *_, app = studio
    assert (await client.post("/api/demo-session", json={"consent": False})).status_code == 400
    response = await client.post("/api/demo-session", json={"consent": True})
    assert response.status_code == 200 and response.json()["user"]["id"] == 0
    await client.post("/api/demo-session", json={"consent": True})
    assert store.wallet(0) == (3, 0)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app, client=("203.0.113.7", 1)),
                                base_url="http://127.0.0.1:8089") as remote:
        assert (await remote.post("/api/demo-session", json={"consent": True})).status_code == 403
        assert (await remote.get("/api/me", headers={"Authorization": "Bearer " + response.json()["token"]}
                                 )).status_code == 403
    settings.image_provider = "openai"
    assert (await client.post("/api/demo-session", json={"consent": True})).status_code == 403
    settings.image_provider = "mock"
    settings.demo_credits = False
    assert (await client.post("/api/demo-session", json={"consent": True})).status_code == 403


async def test_consent_explicit_demo_grant_and_live_gate(studio):
    client, settings, store, *_ = studio
    headers = await auth(client)
    assert (await client.post("/api/photos", content=image(), headers=headers)).status_code == 403
    assert (await client.post("/api/consent", json={"accepted": 1}, headers=headers)).status_code == 400
    await client.post("/api/consent", json={"accepted": True}, headers=headers)
    assert (await client.post("/api/demo-credits", headers=headers)).json() == {"granted": True}
    assert (await client.post("/api/demo-credits", headers=headers)).json() == {"granted": False}
    assert (await client.get("/api/me", headers=headers)).json()["available"] == 3
    settings.image_provider = "openai"
    assert (await client.post("/api/demo-credits", headers=headers)).status_code == 403
    assert store.wallet(1) == (3, 0)


async def test_upload_normalizes_limits_and_owner_scoping(studio):
    client, _, _, media, *_ = studio
    headers, photo = await prepare(studio)
    fetched = await client.get("/api/photos/" + photo, headers=headers)
    assert fetched.status_code == 200
    with Image.open(io.BytesIO(fetched.content)) as normalized:
        assert not normalized.getexif() and normalized.format == "JPEG"
    other = await auth(client, 2)
    assert (await client.get("/api/photos/" + photo, headers=other)).status_code == 404
    assert (await client.get("/api/photos/not-a-uuid", headers=headers)).status_code == 404
    assert (await client.post("/api/photos", content=b"invalid", headers=headers)).status_code == 400
    assert (await client.post("/api/photos", content=b"x" * (MAX_BYTES + 1), headers=headers)).status_code == 413
    assert (await client.post("/api/photos", content=image(), headers=headers | {"Content-Type": "text/plain"}
                              )).status_code == 415
    os.utime(media.path(f"1/{photo}.jpg"), (time.time() - 86401,) * 2)
    assert (await client.get("/api/photos/" + photo, headers=headers)).status_code == 404


async def test_upload_count_and_stream_limit(studio):
    client, _, _, media, *_ = studio
    headers, _ = await prepare(studio)
    for _ in range(23):
        media.save(1, image())
    assert (await client.post("/api/photos", content=image(), headers=headers)).status_code == 409
    async def chunks():
        for _ in range(11):
            yield b"x" * (1024 * 1024)
    response = await client.post("/api/photos", content=chunks(), headers=headers)
    assert response.status_code == 413


async def test_confirm_idempotent_queue_balance_and_owner_results(studio, monkeypatch):
    client, _, store, _, provider, service, _ = studio
    headers, photo = await prepare(studio)
    submit_calls = []
    original = service.submit
    def counted(*args):
        submit_calls.append(args)
        return original(*args)
    monkeypatch.setattr(service, "submit", counted)
    request = body(photo)
    assert (await client.post("/api/jobs", json=request | {"confirmed": False}, headers=headers)
            ).status_code == 400
    accepted = await client.post("/api/jobs", json=request, headers=headers)
    assert accepted.status_code == 200
    job = accepted.json()["id"]
    assert provider.calls == 0 and store.wallet(1) == (3, 1)
    assert (await client.post("/api/jobs", json=request, headers=headers)).json()["id"] == job
    assert len(submit_calls) == 1
    assert (await client.post("/api/jobs", json=request | {"preset": "background"}, headers=headers)
            ).status_code == 409
    other = await auth(client, 2)
    assert (await client.get(f"/api/jobs/{job}", headers=other)).status_code == 404
    assert (await client.get(f"/api/jobs/{job}/result", headers=other)).status_code == 404
    blocked_delete = await client.post("/api/delete", json={"confirmed": True}, headers=headers)
    assert blocked_delete.status_code == 409 and blocked_delete.json() == {"error": "already_active"}
    assert await service.process(job)
    result = await client.get(f"/api/jobs/{job}/result", headers=headers)
    assert result.status_code == 200 and result.headers["content-type"] == "image/jpeg"
    assert "attachment" in result.headers["content-disposition"]
    assert (await client.get("/api/jobs", headers=headers)).json()["jobs"][0]["has_result"]
    assert provider.calls == 1 and store.wallet(1) == (2, 0)
    assert (await client.post("/api/delete", json={"confirmed": True}, headers=headers)).status_code == 200
    assert (await client.get("/api/me", headers=headers)).status_code == 401
    assert store.wallet(1) == (2, 0) and not store.has_consent(1)


async def test_job_input_owner_merge_cost_and_insufficient_credit(studio):
    client, _, store, *_ = studio
    headers, photo = await prepare(studio)
    other = await auth(client, 2)
    store.consent(2)
    store.grant_demo(2)
    assert (await client.post("/api/jobs", json=body(photo), headers=other)).status_code == 404
    assert (await client.post("/api/jobs", json=body(photo, "merge"), headers=headers)).status_code == 400
    second = (await client.post("/api/photos", content=image(), headers=headers)).json()["id"]
    merged = await client.post("/api/jobs", json=body(photo, "merge") | {"photos": [photo, second]},
                               headers=headers)
    assert merged.status_code == 200 and store.wallet(1) == (3, 2)
    no_credit = await auth(client, 3)
    store.consent(3)
    third = (await client.post("/api/photos", content=image(), headers=no_credit)).json()["id"]
    response = await client.post("/api/jobs", json=body(third), headers=no_credit)
    assert response.status_code == 409 and response.json() == {"error": "insufficient_credits"}


async def test_session_expiry_and_capacity_are_bounded(studio, monkeypatch):
    import image_studio.studio as module
    client = studio[0]
    headers = await auth(client)
    now = time.time()
    monkeypatch.setattr(module.time, "time", lambda: now + 3601)
    assert (await client.get("/api/me", headers=headers)).status_code == 401
    monkeypatch.setattr(module.time, "time", lambda: now)
    # Expired sessions are purged; deterministic bound is enforced without evicting valid users.
    for _ in range(1000):
        response = await client.post("/api/session", json={"init_data": signed()})
        if response.status_code == 503:
            break
    assert (await client.post("/api/session", json={"init_data": signed()})).status_code == 503


async def test_internal_errors_are_sanitized(studio, monkeypatch):
    client, _, _, _, _, service, _ = studio
    headers, photo = await prepare(studio)
    def failed(*args):
        raise OSError("secret-provider-and-private-path")
    monkeypatch.setattr(service, "submit", failed)
    response = await client.post("/api/jobs", json=body(photo), headers=headers)
    assert response.status_code == 500 and response.json() == {"error": "internal_error"}


async def test_configured_https_origin_has_exact_bearer_cors_only(studio):
    _, settings, store, media, _, service, _ = studio
    settings.mini_app_url = "https://example.github.io/obraz/"
    app = create_studio_app(settings, store, media, service)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app, client=("127.0.0.1", 1)),
                                base_url="http://127.0.0.1:8089") as client:
        origin = "https://example.github.io"
        response = await client.options("/api/jobs", headers={
            "Origin": origin, "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "authorization,content-type",
        })
        assert response.status_code == 204
        assert response.headers["access-control-allow-origin"] == origin
        assert "access-control-allow-credentials" not in response.headers
        assert "Origin" in response.headers["vary"]
        headers = await auth(client)
        fetched = await client.get("/api/me", headers=headers | {"Origin": origin})
        assert fetched.headers["access-control-allow-origin"] == origin
        assert (await client.options("/api/jobs", headers={"Origin": "https://evil.github.io",
                "Access-Control-Request-Method": "POST"})).status_code == 403
        assert (await client.options("/api/jobs", headers={"Origin": origin,
                "Access-Control-Request-Method": "DELETE"})).status_code == 403
        assert (await client.options("/api/jobs", headers={"Origin": origin,
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "x-admin-token"})).status_code == 403
        assert (await client.post("/api/demo-session", json={"consent": True},
                                 headers={"Origin": origin})).status_code == 200


async def test_result_ttl_errors_and_no_global_media_mount(studio):
    client, _, store, media, _, service, _ = studio
    headers, photo = await prepare(studio)
    job = (await client.post("/api/jobs", json=body(photo), headers=headers)).json()["id"]
    assert (await client.get(f"/api/jobs/{job}/result", headers=headers)).status_code == 404
    assert await service.process(job)
    result = media.path(store.job(job)["result"])
    os.utime(result, (time.time() - 86401,) * 2)
    assert (await client.get(f"/api/jobs/{job}/result", headers=headers)).status_code == 404
    assert not (await client.get(f"/api/jobs/{job}", headers=headers)).json()["has_result"]
    assert (await client.get(f"/media/1/{photo}.jpg")).status_code == 404
    bad = store.reserve(1, "bad-code", "hair", 1)
    store.fail(bad, "private-provider-payload")
    assert (await client.get(f"/api/jobs/{bad}", headers=headers)).json()["error"] == "processing_error"
    assert (await client.post("/api/delete", json={"confirmed": 1}, headers=headers)).status_code == 400


async def test_revoked_consent_during_upload_cannot_recreate_private_media(studio):
    client, _, _, media, _, service, _ = studio
    headers, _ = await prepare(studio)
    async def delayed_upload():
        yield image()
        service.delete(1)
    response = await client.post("/api/photos", content=delayed_upload(), headers=headers)
    assert response.status_code == 403
    assert not media.path("1").exists()


async def test_only_fixed_config_asset_is_public(studio):
    import image_studio.studio as module
    client = studio[0]
    response = await client.get("/config.js")
    assert response.status_code == 200
    assert response.content == (Path(module.__file__).parent / "web" / "config.js").read_bytes()
    assert "javascript" in response.headers["content-type"]
    assert (await client.get("/web/config.js")).status_code == 404
    assert (await client.get("/config.js/private.json")).status_code == 404


@pytest.mark.parametrize("endpoint", ["/api/consent", "/api/jobs", "/api/delete"])
@pytest.mark.parametrize("race", ["session_expiry", "session_invalidation"])
async def test_json_writes_recheck_identity_after_stream(studio, monkeypatch, endpoint, race):
    import image_studio.studio as module
    client, _, store, media, _, _, _ = studio
    headers, photo = await prepare(studio)
    payload = {"/api/consent": {"accepted": True}, "/api/jobs": body(photo),
               "/api/delete": {"confirmed": True}}[endpoint]
    wallet = store.wallet(1)
    now = time.time()
    async def delayed_body():
        yield json.dumps(payload).encode()
        if race == "session_expiry":
            monkeypatch.setattr(module.time, "time", lambda: now + 3601)
        else:
            deletion = await client.post("/api/delete", json={"confirmed": True}, headers=headers)
            assert deletion.status_code == 200
    response = await client.post(endpoint, content=delayed_body(), headers=headers)
    assert response.status_code == 401 and response.json() == {"error": "unauthorized"}
    assert store.wallet(1) == wallet and store.jobs(1) == []
    if race == "session_invalidation":
        assert not store.has_consent(1) and not media.path("1").exists()
    else:
        assert store.has_consent(1) and media.path(f"1/{photo}.jpg").is_file()


@pytest.fixture
async def trial_studio(tmp_path):
    settings = Settings(data_dir=tmp_path, bot_token=TOKEN, image_provider="openai", trial_access=True)
    store, media, provider = Store(tmp_path / "db.sqlite3"), Media(tmp_path), MockProvider()
    service = Service(store, media, provider, trial_access=True)
    app = create_studio_app(settings, store, media, service)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app, client=("127.0.0.1", 1)),
                                base_url="http://127.0.0.1:8089") as client:
        yield client, settings, store, media, provider, service, app


async def test_trial_signed_consent_auto_one_no_anonymous_or_repeat_grant(trial_studio):
    client, _, store, _, _, service, _ = trial_studio
    headers = await auth(client)
    assert store.wallet(1, trial=True) == (0, 0)
    assert (await client.get("/api/me", headers=headers)).json()["available"] == 0
    assert (await client.post("/api/demo-credits", headers=headers)).status_code == 403
    assert (await client.post("/api/demo-session", json={"consent": True})).status_code == 403
    await client.post("/api/consent", json={"accepted": True}, headers=headers)
    me = (await client.get("/api/me", headers=headers)).json()
    assert me["available"] == 1 and me["trial_access"] is True
    assert (await client.post("/api/demo-credits", headers=headers)).json() == {"granted": False}
    await client.post("/api/consent", json={"accepted": True}, headers=headers)
    await auth(client)
    assert service.wallet(1) == (1, 0) and store.wallet(1) == (0, 0)
    other = await auth(client, 2)
    await client.post("/api/consent", json={"accepted": True}, headers=other)
    assert (await client.get("/api/me", headers=other)).json()["available"] == 1
    catalog = (await client.get("/api/catalog")).json()
    assert catalog["trial_access"] is True and all(p["credits"] == 1 for p in catalog["presets"])


async def test_trial_signed_one_merge_generation_second_blocked_delete_no_reset(trial_studio):
    client, _, store, _, provider, service, _ = trial_studio
    headers = await auth(client)
    await client.post("/api/consent", json={"accepted": True}, headers=headers)
    photos = [(await client.post("/api/photos", content=image(), headers=headers)).json()["id"]
              for _ in range(2)]
    request = body(photos[0], "merge") | {"photos": photos}
    accepted = await client.post("/api/jobs", json=request, headers=headers)
    assert accepted.status_code == 200
    job = accepted.json()["id"]
    assert (await client.post("/api/jobs", json=request, headers=headers)).json()["id"] == job
    assert service.wallet(1) == (1, 1) and store.wallet(1) == (0, 0)
    assert store.job(job)["cost"] == 1
    assert await service.process(job)
    denied = await client.post("/api/jobs", json=body(photos[0]), headers=headers)
    assert denied.status_code == 409 and denied.json() == {"error": "trial_exhausted"}
    assert provider.calls == 1 and len(store.jobs(1)) == 1
    await client.post("/api/delete", json={"confirmed": True}, headers=headers)
    headers = await auth(client)
    await client.post("/api/consent", json={"accepted": True}, headers=headers)
    assert (await client.get("/api/me", headers=headers)).json()["available"] == 0
    assert (await client.post("/api/demo-credits", headers=headers)).json() == {"granted": False}


async def test_trial_signed_preexisting_consent_session_grants_once_financial_wallet_preserved(trial_studio):
    client, _, store, _, _, service, _ = trial_studio
    store.consent(7)
    store.grant_demo(7)
    await auth(client, 7)
    assert store.wallet(7, trial=True) == (1, 0)
    assert store.wallet(7) == (3, 0)
    await auth(client, 7)
    assert service.wallet(7) == (1, 0)


async def test_trial_api_replay_cannot_change_reservation_mode(studio):
    client, _, store, _, _, service, _ = studio
    headers, photo = await prepare(studio)
    request = body(photo)
    original = await client.post("/api/jobs", json=request, headers=headers)
    assert original.status_code == 200
    service.trial_access = True
    replay = await client.post("/api/jobs", json=request, headers=headers)
    assert replay.status_code == 409 and replay.json() == {"error": "request_mismatch"}
    assert len(store.jobs(1)) == 1 and store.wallet(1) == (3, 1)


async def test_trial_api_failure_restores_free_quota_without_financial_changes(trial_studio, monkeypatch):
    client, _, store, _, provider, service, _ = trial_studio
    headers = await auth(client)
    await client.post("/api/consent", json={"accepted": True}, headers=headers)
    photo = (await client.post("/api/photos", content=image(), headers=headers)).json()["id"]
    accepted = await client.post("/api/jobs", json=body(photo), headers=headers)
    assert accepted.status_code == 200
    assert (await client.get("/api/me", headers=headers)).json()["available"] == 0

    async def rejected(*args):
        raise ProviderError("provider_rejected")

    monkeypatch.setattr(provider, "edit", rejected)
    assert not await service.process(accepted.json()["id"])
    me = (await client.get("/api/me", headers=headers)).json()
    assert me["available"] == 1 and me["reserved"] == 0 and store.wallet(1) == (0, 0)


async def test_authenticated_miniapp_visit_persists_only_verified_public_username(studio):
    client, _, store, *_ = studio
    user = {"id": 42, "first_name": "Do not persist this name", "username": "studio_visitor"}
    accepted = await client.post("/api/session", json={"init_data": signed(user_data=user)})
    assert accepted.status_code == 200
    rows = store.admin_stats()["users"]
    assert next(row for row in rows if row["id"] == 42)["username"] == "studio_visitor"
    forged = signed(user_data={"id": 43, "first_name": "Other", "username": "forged_handle"})
    forged = forged.replace("forged_handle", "hacked_handle")
    assert (await client.post("/api/session", json={"init_data": forged})).status_code == 401
    assert all(row["id"] != 43 for row in store.admin_stats()["users"])


async def test_original_document_result_download_is_png_without_provider_call(studio):
    client, _, store, _, provider, service, _ = studio
    headers, photo = await prepare(studio)
    request = body(photo, "document_original") | {"description": "Подготовить четыре фото 35×45 мм"}
    accepted = await client.post("/api/jobs", json=request, headers=headers)
    assert accepted.status_code == 200
    job = accepted.json()["id"]
    assert await service.process(job) and provider.calls == 0
    for _ in range(2):
        result = await client.get(f"/api/jobs/{job}/result", headers=headers)
        assert result.status_code == 200 and result.headers["content-type"] == "image/png"
        assert ".png" in result.headers["content-disposition"]
        with Image.open(io.BytesIO(result.content)) as sheet:
            assert sheet.size == (826, 1062)
    assert store.admin_stats()["generated_count"] == 1
