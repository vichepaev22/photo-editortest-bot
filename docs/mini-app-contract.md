# Локальная Mini App: контракт реализации

Дата 2026-10-07. Принятый срез: локальный интерфейс и API, а также публичный предпросмотр интерфейса на GitHub Pages. Обработка в Telegram через Mini App требует последующего размещения API на VPS с HTTPS.

## API

`create_studio_app(settings, store, media, service)` exports FastAPI app, does not create worker/provider/polling. Root starts uvicorn 127.0.0.1:8089 in bot.run with access logs disabled. No new backend dependency. Static assets live in src/image_studio/web/. Root extends Settings with studio_enabled=True, studio_port=8089, mini_app_url=''.

All API JSON errors `{error: safe_code}`. No raw provider payloads/prompts/keys. Responses containing personal data/media are no-store. Bearer sessions random >=32 bytes, RAM-only, 1h TTL, at most 1000; CORS only exact configured MINI_APP_URL HTTPS origin, GET/POST, Authorization/Content-Type, no cookies or wildcard. Validate Host and write Origin (if present); localhost demo additionally requires actual loopback peer and Host. No raw filesystem paths in API. Photo ID is a UUID filename stem and mapped to authenticated owner directory. Auth/JSON body bounded, upload streamed limit10MiB, normalized existing Media.normalize; max24 upload files per owner until TTL cleanup. No global media static mount.

Public:
- GET `/api/health` -> `{ok:true, mode:'mock'|'openai', local_demo:bool}`; no identities/secrets.
- GET `/api/catalog` -> `{presets:[{id,label,inputs,credits}], mode, local_demo, support_contact, trial_access}`. In trial all preset costs are 1; commercial merge stays 2.
- POST `/api/session` JSON `{init_data:string}` -> `{token, user:{id,first_name}, consent:bool}`. Verify Telegram initData with bot-token HMAC, all fields except hash included (signature included if present), auth_date age<=3600s, future skew<=30s, reject duplicate fields, no trust in initDataUnsafe. Telegram user id int>0 up to52bits, not bool. No financial credits are granted. Trial quota is claimed once only after consent; returning consented users may claim via Service.wallet.
- POST `/api/demo-session` JSON `{consent:true}` -> same session shape for user0 only in mock+demo_credits, actual loopback peer+Host. Save consent and grant_demo(0,3) once. Never available in OpenAI. Local demo user0 is valid in Media and isolated from positive Telegram IDs. Bot delivery/notification skips user0; browser retrieves own results.

Authenticated:
- GET `/api/me` -> `{user:{id,first_name}, consent, available, reserved, mode, trial_access}`; trial balance is separate from the financial wallet.
- POST `/api/consent` JSON `{accepted:true}` -> `{ok:true}`.
- POST `/api/demo-credits` -> `{granted:bool}`; mock policy unchanged; signed consenting trial users claim their one-time quota, never financial credits.
- POST `/api/photos` raw image bytes (content type application/octet-stream or image/*) -> `{id:string}`. Consent required.
- GET `/api/photos/{id}` -> sanitized own JPEG, bearer required, UUID only; deleted/expired files404.
- POST `/api/jobs` JSON `{request_key:UUIDstring,preset:string,photos:[photo_id],description:string,confirmed:true}` -> `{id,status}`. Consent required; server rechecks cost/input count/owner through Service.submit; description1..1500; duplicate same key no new call. Confirmation must be true. Never call provider inline.
- GET `/api/jobs` -> `{jobs:[{id,preset,label,cost,status,created,has_result,error}]}` max20 own latest; error only safe strings.
- GET `/api/jobs/{id}` -> same job object, only owner.
- GET `/api/jobs/{id}/result` -> own existing generated/delivered JPEG as attachment; no provider call. Same identity/status/TTL checks as bot.
- POST `/api/delete` JSON `{confirmed:true}` -> Service.delete(user), invalidate own web sessions, `{ok:true}`; active/review prevents removal, financial records preserved.

## Frontend

Russian app «Образ», photo-first layout, four bottom tabs Студия/Образы/Результаты/Профиль. Calm neutral theme with accepted purple accent; no generated illustrations/portraits or external stock. Owner provides assets per design brief; use ordinary Unicode icons in controls meanwhile. Accessible controls >=44px, visible labels/focus, reduced motion and mobile 320px, Telegram safe areas/themeChanged, guarded BackButton/MainButton/HapticFeedback capabilities. Native price1/merge2 unchanged.

Initialize catalog; Telegram.WebApp.initData -> session API. Outside Telegram, explicit consent screen before local demo-session; non-mock no anonymous access. Keep bearer in memory/sessionStorage (no photo/prompt persistence), never URL/localStorage. If session401 clear and offer re-entry; no automatic image retry. initData and secrets never displayed. Bridge https://telegram.org/js/telegram-web-app.js optional in local browser; plain browser fallback works.

Upload exact required1/2 files via API raw bytes, preview with object URLs (revoke when replaced); choose preset, write description<=1500, review price then explicit submit (crypto.randomUUID request_key held until definitive acceptance so uncertain network retry can reuse). On unknown POST outcome do not silently retry paid operation. Poll own job every1.5-2s until terminal; update stage text, no fakepercent. Results fetch authenticated blob; download uses blob and original-output quality, before/after slider is a frontend component, not Telegram's built-in editor. Demo outputs labelled test copy, not AI-edit.

No paid sales/decorative fake balances, no invisible generic prompts, no automatic external sharing. Delete asks explicit confirmation. Support/contact and TTL24h clear. Root browser-tests main consent/upload/confirm/result/download/merge/ownership/mobile/dark flows; fake image fixture made with Pillow for tests only.

Official auth reference checked: https://core.telegram.org/bots/webapps#validating-data-received-via-the-mini-app . Public Telegram opening requires future HTTPS and configuration; present preview URL http://127.0.0.1:8089/ .

GitHub Pages publication approved: frontend ./config.js defines window.OBRAZ_API_BASE, assets ./app.css and ./app.js relative. On *.github.io with no configured API, interface-only preview: no failed automatic /api calls, no fake balances/generated results, no automatic request to localhost. Locally use the running API. Pages supplies HTTPS UI entry; operational generation inside Telegram still needs a reachable authenticated HTTPS backend. Deployment uploads only web assets, never Python media/secrets.

Trial delta 2026-10-08: ENABLE_TRIAL_ACCESS defaults false, valid only for openai + billing disabled. Store persists trial_granted/trial_used/trial_reserved and jobs.trial; at most three saved outputs, one per service use including merge. Same request_key cannot change trial flag or cost. Failure releases reservation; recovered running jobs remain review. Reentry/delete/reconsent cannot reset used quota. Public OBRAZ_PREVIEW_TRIAL is presentation only, never authenticates or grants quota; API catalog decides actual costs and mode.
