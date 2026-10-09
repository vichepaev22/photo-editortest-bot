# Проверки и границы результата

2026-10-07. Локальный MVP, native UI и Mini App; настоящий Image-PoC и VPS ещё не выполнены. Публикация статического интерфейса описана в github-pages.md.

## Подтверждено выполнением

- Python 3.12, отдельная .venv, requirements.lock; OpenAI SDK 2.54.0 и aiogram 3.31.0.
- Ledger/finance тесты запускались RED до реализации (отсутствующие модули), затем GREEN.
- Итоговый pytest после локального launch delta: **35 passed**, 24.79 s; тесты не вызывают внешние API.
- Offline-PoC: 2 mock-вызова и 2 результата через локальный callback, баланс 0; внешних API-вызовов нет.
- Итоговые Ruff check, compileall и pip check прошли. Ruff выявил порядок импортов в regression-тесте; он исправлен, повторный lint прошёл. Финансовые JSON/HTML отчёты и offline-PoC обновлены.
- Установленная сигнатура images.edit просмотрена; SDK-контракт проверен с mock HTTP.
- Jev фактически вызван: jev-1.13.0, input577/output59, рекомендован gpt-6.1-sol для ревью (confidence0.48 не означает правильность кода).
- Независимый gpt-6.1-sol воспроизвёл 3 P2: возраст заказа блокировал первый возврат; отменённый возврат оставлял lock; повтор Telegram photo message принимался вторым фото. Добавлены RED regression-тесты, затем GREEN. Повторный reviewer запустил 4 целевых теста: **4 passed**, исправления приняты; существенных проблем в проверенном изменении не найдено.

## Не проверено

- Платные правки OpenAI, сходство лиц, сравнение моделей, измеренный usage/цена/latency. Ключ и наличие выбранной модели проверены настоящим read-only GET model; остаток денег этим не подтверждается.
- Пользовательский проход с реальным download/sendDocument ещё не подтверждён. Настоящие getMe/getWebhookInfo/getUpdates/setMyCommands/getMyCommands и работа фонового процесса проверены. Дедупликация фото действует в памяти текущей заявки, не является полноценным долговечным inbox.
- Магазин sandbox ЮKassa: тесты используют подставные HTTP-ответы. Gateway требует test=true; live продажи отключены.
- Коммерческая схема с внешней оплатой цифровых услуг внутри Telegram: требование Stars остаётся ограничением.
- Удаление копий у Telegram/OpenAI. TTL удаляет только нашу локальную копию.
- VPS, аренда сервера, создание ключей аккаунтов. Управление авторизованным браузером владельца не подтверждено. GitHub/Pages публикуются отдельным проверяемым срезом.

Первый реальный пилот использует новый DATA_DIR=data-live-pilot; демо-баланс и тестовые начисления не переносить в рабочую БД. Публичная бесплатная выдача в OpenAI режиме отключена; локальный pilot-credits требует согласия, выдаёт 1–10 кредитов и пишет аудит. Биллинг организации OpenAI учитывается отдельно от пользовательского ledger.

## Локальный запуск 2026-10-07

Владелец добавил Telegram/OpenAI ключи в локальный .env. getMe подтвердил photo_editortest_bot, webhook отсутствует. OpenAI models.retrieve подтвердил gpt-image-2.5-flare-2026-09-08 без Image-вызова. BotRuntime блокирует второй экземпляр; штатные stop/restart фактически проверены, бот оставлен ready/mock в фоне. Меню 10 команд перепроверено Telegram API. .env не переписывался; DEMO и OpenAI-пилот используют раздельные data/demo и data/live-pilot.

Сеть: HTTPX успешно использовал существующий HTTPS proxy, прямой HTTPX и стандартный aiogram дали timeout. Добавлена официальная proxy-сессия aiogram с сохранением TLS, HTTPS/ALL/NO_PROXY, aiohttp-socks и lockfile. Два proxy regression-теста RED→GREEN, после исправления реальный запуск успешен. Пакет установлен только в .venv; глобальный pip/прокси не менялись.

Независимый reviewer: 4 launch tests passed, отдельный процесс не изменяет status, fake startup/stop и защиты имени/webhook проверены; затем 2 proxy tests passed. Secrets/logs/live API reviewer не читал. Ruff/compileall/pip check прошли. Генераций OpenAI при подключении и запуске DEMO: 0. Автозапуск Windows не настроен; фоновой процесс проверен в текущем сеансе. Инструкция: local-run.md.

## Native UI / Mini App 2026-10-07

- Полный локальный suite: **69 passed**,35.07s; Ruff и node --check app.js прошли. Новые UI/auth regression сначала воспроизведены RED, затем GREEN. Backend29 и native UI5 тестов покрывают HMAC, дубликаты/TTL сессии, владение файлами, согласие, цену, идемпотентность, контекст клавиатуры и результат без нового provider вызова.
- BotRuntime штатно остановлен при пустой очереди и перезапущен ready/mock; совместный API `/api/health` вернул ok:true,mode:mock,local_demo:true. Это настоящий локальный процесс и сеть Telegram для запуска; отдельный real sendDocument с личным фото этим не подтверждён.
- Браузерный DEMO с изолированным user0: согласие→3попытки; hair→явное подтверждение1→результат/скачивание/slider75%; merge с двумя различными файлами→подтверждение2→результат; остаток0. Только синтетические тестовые файлы, ноль OpenAI Image-запросов. Баланс владельца не затронут.
- В Chromium проверены32сочетания: четыре вкладки × ширины320/390/768/1440 × light/dark; горизонтального переполнения нет. Это browser smoke, не подтверждение поведения Telegram Android/iOS/Desktop. Safe areas и новые нативные стили требуют проверки на устройствах.
- Независимый reviewer выполнил34focused tests и нашёл две P2: неполное согласие Mini App и потеря blob/черновика при bfcache. Обе исправлены и независимо повторно приняты: executable Node VM воспроизведение подтвердило согласие, сохранение медиа, GET-only восстановление сессии/polling,401cleanup и отзыв blob при обычном уходе. Новых существенных проблем в этом срезе не найдено.
- Пользователь предоставляет все изображения оформления. Скриншот проверяет вёрстку, синтетические fixtures проверяют процесс; они не являются artwork или примерами качества Image.

Открытые гейты: настоящий Image-PoC/usage/качество, финальные материалы владельца, клиентские тесты Telegram, доступный извне HTTPS API, коммерческие платежи. Публичный интерфейс без API — предпросмотр без генераций и без отправки фотографий.

## Публикация 2026-10-07

Исходники опубликованы в публичном photo-editortest-bot, ветка codex/ui-mini-app. Перед commit/push проверены53файла: совпадений с известными локальными ключами/токенами, пользовательских абсолютных путей, личного email и приватных auth callback данных нет. История нового репозитория содержит только проверенный исходник; author email — GitHub noreply. `.env`, data, изображения, логи и routing-артефакты не публикуются.

[GitHub Actions](https://github.com/vichepaev22/photo-editortest-bot/actions/runs/37637270293): success,69passed5.75s на Linux и Ruff. Pages HTML и3ресурса — HTTP200; публичный browser smoke и32layouts прошли, консоль без ошибок/предупреждений. Предпросмотр не вызывает `/api`/localhost, баланс «—», загруженное фото очищается локально. Native Telegram menu проверен read-only API: web_app «Студия» с опубликованным URL,11команд. Настоящее открытие Mini App на клиентском устройстве остаётся проверкой владельца.

Для дальнейшего локального browser теста user0 после двух проверочных задач начислены3пилотные попытки отдельным audit event. Одноразовый demo grant не сбрасывался; история и баланс Telegram-пользователей сохранены. OpenAI Image-запросов в этом срезе0.

## Активация OpenAI-пилота 2026-10-07

Владелец сообщил о пополнении$10 и поручил начать. Настоящие read-only models.retrieve по его ключу вернули обе версии Flare/Sunburst; это проверка ключа и списка моделей, не prepaid balance или права конкретного Images edit.

Бот штатно запущен Start-Bot.ps1 -Mode OpenAI. После завершения launcher проверены running:true,state:ready,mode:openai; `/api/health` вернул ok:true,mode:openai,local_demo:false. Настоящий POST localhost `/api/demo-session` отклонён403. Ни пользователь0, ни публичная выдача кредитов в live не создаются.

В новой data/live-pilot один ранее согласившийся Telegram-тестировщик получил3пилотные попытки с audit event. Подтверждён остаток3/резерв0 и отсутствие задач перед пользовательским тестом. Перенесена только существующая политика согласия; демо-медиа/баланс и история сохранены отдельно. ENV changed provider/data/demo flag/billing flag; прочие значения, включая ключи, модель и quality, проверены на неизменность; rollback-копия локальная и исключена из git.

Исходный код не менялся; новые unit tests не запускались для изменения конфигурации. Проверены настоящий запуск, сеть/model lookup, health и закрытый anonymous endpoint. Генераций Image агентом0; первой пользовательской правки и измеренного usage/качества ещё нет. Публичная Pages Mini App остаётся предпросмотром до VPS HTTPS API. Агрегированное доказательство: reports/live-activation.json; идентификаторы/секреты/медиа в него не включены.

## Три бесплатные генерации 2026-10-08

Изменение CR-008 проведено по process-design-vibecoding с Astra для разделения Store, интерфейса и независимого ревью. Принятые требования сохранены; пользователь уточнил единицу: три использования сервиса, объединение и новый вариант по одной генерации. До source изменений записаны контракт и manifest, затем подтверждены RED Store (29), Service/config (10) и первые native/API сценарии (6). Итоговые новые сценарии дополнены до 52 тестов, вместе с прежними 69 — **121 passed, 58.41s**. Ruff для src/tests, Node syntax для app.js/config.js и PowerShell parser для launcher — PASS. Все Image-проверки этого среза offline, реальная стоимость/качество не измерялись.

Независимый reviewer сначала получил 80 passed / 1 failed и выявил P2: конкурентное включение WAL при миграции legacy SQLite. Исправление: отдельное autocommit-соединение и максимум пять повторов только SQLITE_BUSY/SQLITE_LOCKED; другие ошибки без повторов, соединения закрываются. Пять fault-injection сценариев сначала RED, затем GREEN. Автор Store проверил 43 теста и 100/100 повторов миграции по четыре одновременных подключения. Независимый reviewer повторно принял fix: 8 целевых тестов и 20/20 конкурентных повторов. Независимые Node VM smokes проверили Pages без API/auth/фиктивного баланса, trial merge=1 и legacy merge=2. Открытых существенных дефектов ревью не осталось.

Штатный live-процесс остановлен при пустой очереди. Перед миграцией создана согласованная SQLite-backup и копия локального .env в ignored data/runtime. Добавочная миграция сохранила все исходные записи пяти таблиц, после неё существующему согласившемуся пользователю назначена отдельная квота 3; прежний финансовый баланс 3, резерв 0 и spent 0 сохранены. Из .env изменён только ENABLE_TRIAL_ACCESS; ключи, Flare snapshot/medium и другие настройки неизменны.

OpenAI launcher завершился ready; отдельная проверка после его выхода подтвердила running:true. Настоящие localhost health/catalog: openai, local_demo:false, trial_access:true, все шесть функций по 1. Анонимный POST demo-session отклонён403. Генераций Image агентом0, пользовательских фото в проверках не использовано. Отчёт: reports/trial-verification.json. Pages продолжает показывать предпросмотр до доступного извне HTTPS API.

После аудита22 staged-файлов (0 совпадений с локальными секретами/личными путями/email/auth callback; runtime не включён) исходный commit7981b67 опубликован. [CI и deploy](https://github.com/vichepaev22/photo-editortest-bot/actions/runs/37674222883): success, **121 passed,8.47s** на Linux и Ruff PASS. Публичный Chromium smoke подтвердил текст3бесплатных генераций, merge1, баланс«—», отсутствие API/localhost-запросов и ошибок/предупреждений консоли. Входной экран при фактических320px без горизонтального переполнения; все четыре вкладки при фактических929px в light/dark также без переполнения. Это browser smoke, не проверка Telegram на физическом телефоне; прежний32-layout срез остаётся отдельным историческим доказательством.

## Цветные клавиатуры и размещение 2026-10-08

По официальным KeyboardButton/InlineKeyboardButton и changelog повторно подтверждены primary/success/danger; фактически установлен aiogram3.31.0. В коде окрашены все шесть нижних действий (4primary/2success), inline-выбор и навигация получили default primary, явный success подтверждения сохранён. Изменения только представления; тексты, действия и trial-домен сохранены. Новых unit tests не добавлялось: автор выполнил8существующих nativeUI tests (20.15s) и Ruff; lead отдельно проверил сериализованный SDK-payload MAIN/MENU, точные шесть текстов и стили, persistence/resize и явный success. Отображение на реальном клиенте остаётся проверкой пользователя через `/start`.

Бот штатно остановлен после завершения активной работы и запущен в OpenAI; после выхода launcher подтверждён running:true,state:ready. К этому моменту уже выдан один пользовательский результат: агрегаты trial_used1/reserved0/remaining2, финансовые balance3/reserved0/spent0. После перезапуска эти значения сохранены, catalog trialtrue и все цены1. Это подтверждает доставку результата и учёт квоты, но не является оценкой визуального качества: личные фотографии не просматривались. Новых Image-вызовов агентом0.

Исследование хостинга выполнено по официальным Oracle/Cloudflare/Render и Telegram Serverless; лимиты и ограничения записаны в hosting-options.md. Регистрация, создание ресурсов, перенос backend и платежи не выполнялись. Primary workflow process-design-vibecoding, для независимых частей использован Astra; существующие решения сохранены.

## Нативный сценарий после первого результата, 2026-10-08

По process-design-vibecoding выполнен CR-009: автостарт после описания в trial, понятный шаг1/10 MB, повторное использование оригинала и reply-to-photo, отсутствие технических ID в обычных сообщениях/имени скачиваемого файла, upload_photo каждые4секунды во время работы. Mini App и коммерческое подтверждение сохранены. До изменений записаны intent/contracts/manifest; backend и native UI разделены, интеграция выполнена lead.

Первый полный локальный прогон: **164 passed / 1 failed,110.38s**. Ошибка была в новом test harness: первым записанным callback-методом являлся AnswerCallbackQuery, а тест ожидал markup сообщения. Исправлена выборка нужного inline button, требование label/callback сохранено. После исправления native flow + UI: **18 passed,46.87s**. После уточнения скачиваемого имени и добавления сценария «новое фото» финальный focused flow: **12 passed,21.77s**. Ruff src/tests и Node syntax app.js — PASS. Полный набор после этих изменений локально не повторялся: владелец попросил ограничить проверки; итоговая обязательная CI выполняется при публикации.

Backend-автор проверил48целевых тестов. Независимый reviewer отдельно проверил reusable-input/media контракт:34новых +24прежних passed, существенных замечаний нет. UI-автор не завершил собственный GREEN из-за лимита агента; интеграцию и focused проверки завершил lead. Затем выполнено независимое статическое ревью финальных bot/service/media по N1–N8: один P2 на TTL, описанный ниже. Статическое ревью не является live-тестом внешнего API.

Перед запуском создана согласованная backup БД и копия .env в ignored runtime; действующего процесса и queued/running задач на тот момент не было. Updated OpenAI launcher завершился ready, отдельная проверка после его выхода: running:true. Health: openai/local_demo:false; catalog trial:true, шесть функций по1; anonymous POST demo-session→403. Financial/trial поля и статусы задач совпали с baseline, `.env` hash неизменен. Агрегат пользователя: trial_used1/reserved0/remaining2, financial balance3/reserved0/spent0. Этот snapshot не обещает неизменность остатка после новых пользовательских генераций. Приватное доказательство: data/runtime/native-ux-verification.json; медиа и идентификаторы не публикуются. Image-вызовов агентом0.

Коллаж9/12вариантов описан в photo-variants.md как проверяемый prompt, не специальная API-функция и не реализованный preset. Качество такого вывода не измерено; платных экспериментов нет.

Публикация c21e00d: аудит20staged-файлов без известных секретов/личных путей/email/auth callback. [CI и Pages](https://github.com/vichepaev22/photo-editortest-bot/actions/runs/37729203267) — success, **167 passed,12.03s**, Ruff PASS. Публичные HTML/app.js вернули200:10 MB и10_000_000bytes, прежней MiB-метки нет. Файлы установки Oracle вошли в этот commit, сервер не запускался.

Независимое ревью нашло P2: фото выбрано незадолго до24h, описание отправлено после expiry, cleanup ещё не удалил файл — submit проверял только owner/existence. Исправление: повторный size/ownership/existence/TTL guard до grant/reserve. Один regression воспроизвёл RED «DID NOT RAISE», после исправления **1 passed/34 deselected,4.10s**, Ruff для двух файлов PASS; широкий локальный прогон не повторялся. Reviewer статически повторно принял исправление, других существенных замечаний по auto-submit/reply/activity не нашёл. Перед restart активных задач0; backup создана. После штатного запуска ready/OpenAI подтверждены unchanged wallets/job states/.env и403anonymous DEMO. Image-вызовов0. Приватный отчёт: data/runtime/ttl-review-verification.json.

## Подготовка Oracle и проверка Serverless, 2026-10-08

Созданы fresh install.sh, systemd unit, безопасный env example и runbook. Bash `-n` — PASS; первый запуск Git Bash был ограничен Windows sandbox, read-only syntax check затем успешно выполнен вне неё. Независимое статическое ревью трёх файлов Oracle-пакета — PASS, существенных дефектов не найдено. Установщик на Linux/ARM не выполнялся; unit проверен чтением, не настоящим systemd. Автоматический старт отключён до приватного переноса ledger/media. OCI CLI/config на компьютере отсутствуют; открытая консоль требует входа в существующий аккаунт. VM, security rules, SSH credential и HTTPS backend не созданы, реальный cloud перенос не подтверждён.

Официальная Serverless страница прочитана через браузер, когда web parser вернул ошибку. Подтверждены октябрьское Mini App HTTPS/endpoint обновление, multipart fetch/InputFile,20 MB download cap,30 MB response cap, BLOB/query builder и ограничения foreign keys. `npm view` подтвердил CLI0.2.0/Node≥18; tarball скачан `npm pack --ignore-scripts` в ignored runtime, прочитаны SDK reference, API client/endpoints, run и package.json. Команды login/run/push/migrate/webhook sync не выполнялись. Цена/free quotas, server secrets, cloud timeout и многооперационные транзакции из этих источников не установлены. Эти сведения требуют проверки до выбора платформы. Полный разбор: telegram-serverless-study.md.

Новых бизнес-тестов для подготовки хостинга не добавлено, платных Image-проб нет. Текущий бот остаётся на ПК до подтверждённого доступа к Oracle и безопасного переключения одного polling-процесса.
# Проверка подготовки Telegram Serverless и диагностики Oracle — 2026-10-08

- Oracle: субагент сверил официальный Free Tier FAQ и правила восстановления; lead наблюдал общий refusal после card confirmation. Встроенная проверка не показывает invalid обязательных адресных полей; истинная причина отказа не установлена, former OCI tenancy ещё уточняется. Личные/платёжные сведения не включены в публикуемый отчёт. Retry, регистрация, отправка обращения, создание VM и платные действия не выполнялись.
- Public Telegram Serverless descriptor: HTTP200 / `ok:true`, schema version1, SDK-reference совпадает с официальным CLI0.2.0. Это публичное чтение без credentials, не cloud PoC.
- Один bounded `node experiments/telegram-serverless/check.mjs`: synthetic PNG, fake SDK/DB; successful file returned once, concurrent duplicate blocked, oversized rejected before download, foreign private chat ignored; network/Image API calls0. Node syntax всех трёх SDK-модулей и `git diff --check` прошли.
- Независимое статическое ревью нашло P2: invalid/instruction reply был до claim. В тот же smoke добавлен oversized replay: RED `rejected != duplicate`; claim перенесён до любых API-вызовов, terminal instruction/rejected statuses сохраняются, uncertain send→review. GREEN: delivered1 / duplicates2 / oversize once / foreign ignored / network0. Статическое re-review изменённых core/handler: PASS. Повторный полный локальный Python-suite не запускался.
- Probe не содержит OpenAI key/вызова, генерационного ledger, реальных фото или переноса рабочих данных. CLI login/push/migrate/run/webhook sync не выполнялись. Pricing/free quota, защищённые secrets, long-running execution и транзакции ledger не подтверждены.
- При read-only проверке local status прежний процесс оказался остановлен. Восстановлен существующий OpenAI режим; после завершения launcher runtime running/ready и `/api/health` ok, local_demo=false. Хэши users/jobs/.env совпали с private snapshot перед запуском; активных задач перед запуском0. Это readiness и сохранение состояния, не новая пользовательская/платная генерация.


## VPS и HTTPS — 2026-10-08

По process-design-vibecoding и Astra выполнен перенос на существующий VPS владельца. Работа разделена: worker подготовил export helper, второй worker HTTPS installer, отдельный агент прочитал Play2go wiki, независимый reviewer проверил пакеты; установку, сверку и переключение выполнил lead. Модели субагентов отдельно не подтверждались runtime, поэтому не заявляются.

Ubuntu26.04/x86_64/2CPU/около4GB RAM/60GBdisk подтверждены SSH. Python приложения3.12.15 установлен uv0.12.23 под/opt. Опубликованный backend13c9e9c установлен с requirements.lock; модельFlare snapshot/medium сохранена. Telegram getMe/getWebhookInfo200, ожидаемое имя совпало, webhook пуст; OpenAI models.retrieve для выбранного snapshot успешен, Image-вызовов0.

Локальный poller штатно остановлен при active0. Экспортёр создал SQLite backup + media, нормализовал только копию jobs.result/inputs и сохранил mtime. На VPS SHA256 archive/DB, все строки таблиц и время изменения файлов совпали; целостность SQLite подтверждена. Квота, согласия и история сохранены. Фото/снимки/ключи находятся в ignored private runtime и защищённых серверных каталогах, не опубликованы.

Focused helper на Windows:13passed/3skipped из-за запрета создания symlink. На целевом Linux запущены только эти3synthetic symlink cases:3passed/0.05s. Ruff для helper/tests PASS. Два независимых статических ревью первоначальных export/install/HTTPS — PASS. Runtime выявил exit1 у systemctl query без совпадений; исправлен guard. В интеграции обнаружен backend Host allowlist: proxy теперь передаёт внутренний Host127.0.0.1:8089, внешний сохраняет в X-Forwarded-Host. После правок shellsyntax PASS; фактический HTTPS API подтверждён. Полный локальный suite не повторялся.

systemd service active/running/enabled, User=image-studio, NRestarts0 при запуске; health openai/local_demofalse,8089 только127.0.0.1. Локальный status runningfalse. Сертификат Let’s Encrypt на публичный IPv4 выпущен и проверен OpenSSL; Nginx обслуживает API через443, HTTPтолькоACME. Certbot5.8.0renewal timer enabled; один dry-run с deployhooks success, штатная renewal service Resultsuccess/ExecMainStatus0.

Через публичный доверенный TLS проверены health200, exactPagesCORS, anonymousme401, forgedinitData401, публичныйdemo404. Smoke использует синтетически подписанное initData существующего согласившегося пользователя: me сохранилremaining2/reserved0, готовый перенесённый JPEG доступен. Это API-интеграция, не пользовательский webviewTelegram и не новая Image-генерация. Полноценный пользовательский тест Mini App и качества остаётся владельцу; агент не потратил trial-попытку или OpenAIImage бюджет.

Публикация frontend config и deployment документов проходит стандартную CI/Pages в рабочей ветке. Проверка провайдерской панели не дала доступного browserDOM; конфигурация и доступ установлены SSH, wikiVDS исследована отдельно. Backup провайдера не подтверждён; приватный локальный снимок является backup на момент миграции.
## UX «Образ ·»: 8 октября 2026

Для этой правки применены process-design-vibecoding, design-taste-frontend и OpenAI Docs; native, Mini App и исследование качества выполнялись параллельно. Изменены тексты и цвета, добавлены шесть копируемых примеров и удаление служебных шагов после доставки. Постоянные поля выбора файла устраняют зависимость первого клика от завершения входа и перерисовки.

Локально: Ruff — PASS; существующие `test_bot_flow.py` — 21 passed и `test_bot_ui.py` — 8 passed. После усиления проверки изоляции новой заявки повторены только два соответствующих теста. Статическое независимое ревью native-кода — PASS. `node --check` — PASS. Полный локальный suite повторно не запускался; стандартный workflow GitHub проверяет публикацию отдельно.

Один синтетический Chromium-сценарий 390×844: первый клик открывает picker до задержанного входа; поле остаётся тем же DOM-элементом; до серверного согласия нет загрузок, после согласия при квоте 0 проходит одна загрузка, генераций 0. Панель согласия скрывается после подтверждения. Светлая/тёмная тема сохраняет зелёный акцент при синем Telegram `button_color`; контраст основной кнопки 6,38/8,29, переполнения и ошибок JS нет. Это проверка с подставными ответами сервера, не мобильный Telegram и не платный Image-тест.

Tracker удаляет только известные ID сообщений шагов/обработки: исходные пользовательские сообщения, готовые изображения, помощь и ошибки сохраняются. Tracker ограничен 1000 заявками, 100 ID на заявку и сроком 24 часа; хранится в памяти. История до обновления и забытые после рестарта ID не очищаются. Фактическое отображение CopyTextButton и завершённый новый сценарий после обновления требуют проверки владельцем в Telegram.

Модель, medium, квота и выдача JPEG в этой правке не меняются. Более высокое качество, сохранение исходного PNG и будущий обязательный watermark описаны в `image-quality-and-branding.md`; художественные материалы и watermark сейчас не добавлены. Публикация и безопасный перезапуск VPS фиксируются отдельно после выполнения, а не выводятся из локальных тестов.

## CR-011: демо покупки

Выполнены нейтральная нижняя клавиатура с единственной зелёной «Поддержкой», URL @nedelsky и одно редактируемое сообщение покупки: три тарифа, СБП/Крипта, тестовая ссылка, документы, назад и отмена. Checkout не использует Store/Service, имеет привязку к private user/message/token/revision, TTL 60 минут и cap 1000; ошибочный edit не создаёт запасное новое сообщение.

Focused offline run `test_purchase_demo.py` + `test_bot_ui.py`: 51 passed in 49.83s, Ruff PASS. После добавления бренда и имени Platega повторены только сценарии отображения/переходов: 12 passed, 31 deselected in 19.60s. Проверены сохранность кошелька/заявки, старые/чужие callback, истечение, повторные нажатия, ошибки редактирования и единый message_id. Полный локальный suite, внешние платежи и Image API не запускались. Независимое ревью, CI/публикация и VPS acceptance фиксируются отдельно после выполнения.

Platega показана только как DEMO. Реальные продажи цифровых услуг в Telegram требуют Stars. 60 минут — локальная демо-сессия, не проверенная expiry провайдера. Количество генераций и срок доступа в тарифах не обещаны. Перед реальными продажами предоставленные юридические шаблоны требуют заполнения; их содержание не менялось.

Дополнение CR-011, ручная клавиатура: по официальному ReplyKeyboardMarkup включена обычная сворачиваемая клавиатура (is_persistent=false, one_time_keyboard=false). Повторен только существующий navigation test: 1 passed in 11.75s; focused Ruff и diff check PASS. Вид самого значка определяется клиентом Telegram, физическое устройство не проверялось.

## Панель владельца и фото на документы — 2026-10-08

CR-012 выполнен по process-design-vibecoding с координацией Astra. Два worker разделили SQLite/admin и подготовку листа; lead интегрировал нативный сценарий, проверенный вход Mini App, интерфейс и конфигурацию. Независимый reviewer проверил доступ, источник платежной статистики, единый пункт меню и выдачу PNG. Использованные модели отдельно не подтверждались runtime и не заявляются.

Панель доступна только владельцу по числовому ID из приватного окружения, только в личном чате. Публичного admin API нет. Постоянно сохраняются ID, публичный username при наличии и даты посещений; готовые обработки и реальные покупки рассчитываются по существующим записям. Демо-покупки и тестовые платежи не включаются. Миграция добавляет поля, сохраняя прежние записи и квоты; неизвестные старые платежи консервативно помечаются тестовыми.

Worker admin: **37 passed / 25s**, Ruff PASS. Worker обработки листа и существующих сервисных сценариев: **33 passed**, Ruff PASS. После объединения вариантов в одну функцию lead выполнил только целевые интеграционные проверки: **6 passed / 55 deselected / 11.99s** (доступ владельца, документные варианты, подписанный вход, PNG и повторное скачивание). Ruff src/tests, Node syntax app.js и diff check — PASS. Reviewer обнаружил два устаревших ожидаемых названия в новом тесте: исправлены, **1 passed / 19 deselected / 1.97s**. Дополнительно исправлена поддержка коротких collectible username: **1 passed / 37 deselected / 6.71s**, focused Ruff PASS. Полный локальный suite не повторялся по просьбе владельца; обязательный полный прогон выполняется CI при публикации.

Один браузерный предпросмотр на 390×844: семь функций, один видимый пункт «Фото на документы», четыре варианта внутри, корректное переключение пояснений и отсутствие горизонтального переполнения. Использована приватная статическая копия с отключённым backend: это проверка интерфейса, а не входа в Telegram, качества модели или реальной отправки задания.

Лист PNG 826×1062 px содержит четыре одинаковые плитки 413×531 px при 300 DPI. Вариант исходника пропускает OpenAI; остальные используют один вызов модели за одним портретом. Один лист расходует одну успешную попытку. Физическая печать, сходство на реальных фотографиях и приём конкретным ведомством не проверялись. ИИ-фото не позиционируется как подходящее для паспорта РФ; источники и пределы проверки указаны в document-photos.md.

Публикация и серверное обновление фиксируются после выполнения. Платных Image-вызовов, реальных платежей и тестовых сообщений пользователям агентами не было.

## Подсказки о причёсках и совместимость проверок — 2026-10-09

По CR-013 добавлены рекомендуемый диапазон 1–12 и шаблоны одного образа, сетки 3×3/9 и 3×4/12. В нативном сценарии используются CopyText-кнопки; в Mini App шаблон только заполняет описание. Подтверждение/отправка и расходование одной попытки за готовый выход остаются прежними. Гарантия точного числа ячеек и отдельные файлы не обещаются.

Для этой правки повторены только существующие целевые сценарии Step2: **6 passed / 19 deselected / 17.34s**. Focused Ruff и Node syntax — PASS. Один приватный мобильный предпросмотр 390×844 подтвердил вставку 9/12, закрытое подтверждение после выбора примера, скрытие блока вне причёсок и отсутствие горизонтального переполнения. Backend в копии отключён; Image-вызовов нет. Независимое статическое ревью этого UI delta — PASS.

Первый обязательный CI для c0adb59: **32 failed / 268 passed / 13.76s**. Все 32 сбоя относятся к четырём snapshot-сравнениям в tests/test_purchase_demo.py: новый middleware законно меняет поля посещения, а прежний full-dump ожидал отсутствие любых изменений БД. Статическая сверка лога подтверждает неизменность исходных полей учёта в этих сбоях; это не проверка реальной продажи. Снимок корректируется только в тестовой in-memory копии: нормализуются username/first_seen/last_seen; новый посетитель пропускается лишь с нулём во всех полях учёта, существующие ID и остальные таблицы сохраняются в сравнении. Повторный CI и итог deployment записываются после выполнения.

Перед обновлением независимое ревью приватного deploy helper выявило неполный rollback окружения. Исправлено: root-only env восстанавливается из backup, ошибки остановки/checkout/restore/permissions не подавляются; при невозможности восстановить код или окружение сервис остаётся остановленным. Повторное статическое ревью — PASS. Ошибочный rollback на рабочем сервере специально не запускался.

Исправление snapshot проверено одним focused run: **47 passed / 55.59s**, Ruff файла — PASS. Guard-тесты обнаруживают каждое поле учёта нового пользователя, удаление существующего ID и изменения остальных таблиц; исходная БД и timestamps при сравнении не меняются. Независимое статическое ревью helper/guards — PASS. Машинная сверка повторённого CI-log подтвердила, что в показанных изменённых строках пользователей различались только first_seen/last_seen. Runtime-код покупки при исправлении не менялся.

### Итог публикации и запуска, 09.10.2026

Приложение **49deea5763c73b058c2a97e1d989ab4807935e63** опубликовано; [обязательный CI и Pages](https://github.com/vichepaev22/photo-editortest-bot/actions/runs/37830615677) — success: **304 passed / 21.55s**, Ruff PASS. Перед публикацией проверены фактические приватные credentials в staged файлах, отдельно отсутствие ID владельца/личного пути/auth callback в первом feature commit; совпадений не было. Финансовые черновики этой поставкой не публиковались.

VPS обновлён через закреплённый SSH-доступ. Активных генераций перед остановкой не было. Созданы root-only согласованная SQLite backup, копия приватного env и архив медиа. После добавочной миграции совпали все прежние строки и столбцы каждой таблицы, integrity_check=ok; хэши 28 файлов совпали до старта. Другие значения env не менялись; ADMIN_TELEGRAM_ID настроен приватно. Это сверка сохранности на момент обновления, не обещание неизменности данных после новых действий пользователей.

Systemd: enabled, active/running, User=image-studio, NRestarts=0; runtime ready/OpenAI и PID согласованы. Публичные health/catalog — HTTP200, local_demo=false, trial=true; семь видимых функций с одним пунктом «Фото на документы», внутренний исходник не вынесен в каталог. Публичный app.js Pages HTTP200 и побайтово совпал с файлом проверенного релиза. Локальный preview-сервер остановлен. Частная сверка: data/runtime/admin-documents-public-verification.json; серверная verification.json хранится рядом с backup и не публикуется.

Платных Image-вызовов, реальных платежей и отправки тестовых сообщений агентами — **0**. Фактический клиент Telegram, сходство/число вариантов на реальных фотографиях и физическая печать остаются пользовательской проверкой. Итоговая запись меняет только документацию, поэтому полный CI повторно не запускается для неё.

## Безлимит владельца — 2026-10-09

CR-014 по process-design-vibecoding и Astra: backend worker владеет сохранением quota_exempt и политикой Service; lead — Telegram/API/Mini App, память и rollout. ID владельца остаётся приватным, отдельная настройка по умолчанию выключена. Остальным не меняется лимит три. Подписки/рефералы только записаны в growth-backlog.md и в одной заметке памяти по прямой просьбе владельца, разработка не начата.

Один focused backend run нового owner файла и двух существующих trial-файлов: **62 passed / 14.22s**, Ruff PASS. Lead проверил только два новых сценария API/native: **2 passed / 61 deselected / 10.89s**, без сети и оплаты. Проверены четыре результата владельца без изменения кошелька/trial, отсутствие повышения доступа через JSON другого пользователя и нативное отображение; дополнительные lifecycle/replay/concurrency/migration проверки входят в worker run. Ruff интеграционных файлов и Node syntax — PASS. Независимое статическое ревью backend и integration — PASS; оно не является runtime-тестом.

Один мобильный preview 390×844 с подставленным API: у владельца при числовом available=0 показаны ∞ и подтверждение безлимита; другой пользователь с нулём заблокирован. Ввод/просмотр подтверждения не отправили ни одного POST jobs, переполнения нет. Это синтетическое UI-поведение, не реальный Telegram WebView или Image-вызов. Локальный preview-сервер остановлен. CI, безопасное обновление и live signed owner /api/me фиксируются после выполнения; реальная генерация при rollout не запускается.

### Итог CR-014: публикация и VPS

Релиз приложения **d8d5163011ed9b15d85b63de1ae7c12f405d809f**: [обязательный CI и Pages](https://github.com/vichepaev22/photo-editortest-bot/actions/runs/37833858624) — success, **324 passed / 20.40s**, Ruff PASS. На VPS включено исключение только для ID владельца из приватного окружения; значение дополнительно сверено с предоставленным владельцем ID. Другим пользователям оставлены три бесплатные генерации.

До остановки и после неё активных генераций не было. Созданы root-only SQLite backup, копия окружения и архив медиа. Добавочная миграция сохранила все прежние строки и столбцы каждой таблицы; integrity_check=ok, хэши 25 файлов совпали до запуска. В env изменён только ENABLE_OWNER_UNLIMITED_TESTING. Независимый reviewer выявил несовместимость старого кода с активной exempt-задачей при rollback: после остановки добавлен guard, оставляющий сервис остановленным и новый код/данные сохранёнными при queued/running/review exempt-заданиях либо ошибке чтения БД. Повторное статическое ревью и Bash syntax — PASS. Аварийный rollback на рабочем сервере не запускался.

После запуска systemd enabled/active/running, User=image-studio, NRestarts=0; runtime ready/OpenAI, PID согласованы. Одна синтетически подписанная Telegram-сессия существующего владельца на живом API подтвердила unlimited=true при сохранении числового available. Это серверная авторизация, не вход через настоящий клиент Telegram. Создание сессии штатно обновляет только посещение владельца; фоновые действия пользователей после старта допустимы. Исключение других ID подтверждено серверной политикой и локальными API-тестами, без создания фиктивного пользователя на VPS.

Публичные HTTPS health/catalog — HTTP200, OpenAI/local_demo=false, trial=true, семь функций. Публичный app.js Pages — HTTP200, побайтово совпал с релизом. Частный результат: data/runtime/owner-testing-public-verification.json; серверный verification.json находится в root-only backup. Платных Image-вызовов, реальных платежей и тестовых сообщений агентами — **0**. Фактическое отображение в Telegram и новый результат модели остаются пользовательской проверкой. Финальная запись меняет документацию, повторный полный CI для неё не запускается.


## CR-015: migration to @obraz_photo_bot — 2026-10-09

The existing process-design-vibecoding/Astra workflow was reused. The new token was verified privately through Telegram getMe; username matches, no webhook, pending updates0 at preflight. VPS had9users/12delivered jobs/trial_reserved0 and no active tasks. No secret values or user IDs are published.

Worker changed only the deterministic native description key to include the actual bot ID and added one cross-bot/duplicate/history regression. Two exact focused tests: **2 passed / 17.84s**, Ruff PASS. Launcher PowerShell syntax and private cutover Bash syntax PASS. A single private synthetic trial-refresh smoke confirmed old table/accounting/consent/owner preservation, one-time refresh without regrant after a consumed use, and active-review-job blocking; network0.

Independent static review found an incomplete-backup rollback risk and a repeated-cutover auth-check defect. Restore now requires a successful backup flag plus integrity validation before opening the live destination; a same-token rerun skips the old-signature rejection check. Static re-review PASS. No forced failure/rollback or real-user message was tested on the server.

Publication CI, actual cutover, signed new-token/old-token authentication and public HTTPS acceptance are recorded after completion. No paid Image call or real payment is used for verification. Existing financial drafts remain outside this delivery.


### CR-015 acceptance: deployed

Application release **8f3f1e7a34e8d6034b39ee0ba5a0a9c50877deb2**: [CI and Pages](https://github.com/vichepaev22/photo-editortest-bot/actions/runs/37899351762) success, **325 passed / 21.40s**, Ruff PASS. Publication audit found no actual old/new credentials, owner ID or personal path in staged files; financial drafts were not included.

Cutover completed through pinned SSH. The stopped-runtime backup includes SQLite, the old private environment and media; all previous table rows/columns matched after normalising only authorised regular-user trial_used, plus exactly one new migration event. Owner trial fields, consent, paid wallet, jobs, invoices/payments and history were preserved. Integrity ok, all31media hashes matched before startup. Only Telegram token/username changed in the server environment. The same two fields were mirrored locally after acceptance, with a private prior-env backup; local poller stays stopped.

New @obraz_photo_bot runtime ready/OpenAI, systemd enabled/active/running as image-studio, NRestarts0. One synthetic signed owner session accepted the new token and confirmed unlimited=true; a fresh signature using the old token was rejected401. Startup commands and WebApp studio menu were verified via Telegram API. This is server authentication/configuration evidence, not a real user Telegram WebView or completed new image. No rollback was forced in production.

Public HTTPS health/catalog200, trial=true/seven presets; Pages app.js200 exactly matches release bytes. Private receipts: data/runtime/bot-migration-server-verification.json and bot-migration-public-verification.json. Server backup/receipt remain root-only. Free-trial refresh is one-time and does not extend media TTL; normal post-start user activity may change counters/visits. Agent Image calls, real payments and test messages during cutover:0. The previously authorised one-off announcement is a separate completed operation. Final receipt changes documentation only, so no redundant full CI is triggered for it.
