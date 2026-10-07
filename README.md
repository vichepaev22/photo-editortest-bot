# Образ — Telegram Image MVP

Локальный MVP для русскоязычной примерки причёсок, одежды, очков, фона, улучшения и объединения двух фото. OpenAI Image2.5 Flare/Sunburst, атомарные кредиты, повторная доставка, внешний billing sandbox ЮKassa. Реальные продажи отключены; отсутствие ключа не маскируется демо-результатом.

Бот: [@photo_editortest_bot](https://t.me/photo_editortest_bot). Публичный интерфейс: [Образ на GitHub Pages](https://vichepaev22.github.io/photo-editortest-bot/). Исходники: [photo-editortest-bot](https://github.com/vichepaev22/photo-editortest-bot).

Нижняя клавиатура и Mini App со вкладками «Студия / Образы / Результаты / Профиль», загрузкой1–2фото, подтверждением цены, сравнением и скачиванием. Пока обработчик работает локально, Pages показывает предпросмотр без отправки фотографий. Полный локальный DEMO доступен на http://127.0.0.1:8089/ после запуска.

## Документы

Варианты бесплатного размещения и конкретный план переноса: [hosting-options.md](docs/hosting-options.md). Свежие возможности Telegram и цветовая схема: [telegram-design.md](docs/telegram-design.md). Для обновления нижней клавиатуры отправить `/start`.

Текущий локальный пилот 2026-10-08: OpenAI, Flare / medium; каждый Telegram-пользователь после согласия получает **3 бесплатные генерации один раз**. Причёска, очки и объединение двух фото — по одной; новый вариант — ещё одна. После трёх лимит исчерпан, повторный вход или удаление фото его не сбрасывают. Для реальной правки открыть бот → `/start` → фото и описание → подтверждение. Pages пока остаётся предпросмотром; анонимная обработка в live API отключена. DEMO можно запустить отдельно; его данные и кредиты не переносятся в live.

- [PRD](docs/PRD.md), [возможности](docs/capabilities.md), [приёмка](docs/acceptance.md), [решения](docs/decisions.md).
- [Финансовая модель](docs/financial-model.md), [интерактивный калькулятор](reports/finance.html).
- [Платежные инструкции Vibe](docs/vibe-payment-notes.md).
- [Возможности Telegram и концепция дизайна](docs/telegram-design.md) — официальные источники, цвета кнопок, нижняя клавиатура и предложение Mini App.
- [Проверка Mini App](docs/mini-app.md), [Pages и будущий VPS](docs/github-pages.md), [ТЗ на изображения владельца](docs/design-assets-brief.md).
- [PoC](docs/POC.md), [проверки и ограничения](docs/verification.md), [план](docs/plan.md).

## Установка и воспроизведение

Работать из этой папки в PowerShell. Python3.12+; проверено3.12. Установленные версии зафиксированы в requirements.lock. Корневые проекты пользователя не изменяются.

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.lock
.\.venv\Scripts\python.exe -m pip install --no-deps --no-build-isolation -e .
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m ruff check src tests
.\.venv\Scripts\python.exe -m image_studio offline-poc
.\.venv\Scripts\python.exe -m image_studio finance
```

При установке с нуля setuptools нужен для сборки: `python -m pip install setuptools` либо использовать `pip install -e '.[dev]'` с обычной build isolation. Созданная .venv уже готова.

## Что нужно от владельца

1. [@BotFather](https://t.me/BotFather): /newbot → отдельный тестовый бот. Токен сохранить в локальном `.env` в `TELEGRAM_BOT_TOKEN`; в чат не присылать.
2. [OpenAI API billing](https://platform.openai.com/settings/organization/billing/overview): проверить допустимость региона/аккаунта, выбратьAPIорганизацию, Add payment details, пополнить$10, отключить Use auto-reload для пилота. [Официальная инструкция](https://help.openai.com/en/articles/8264644-setting-up-and-managing-prepaid-api-billing): минимум$5, по умолчанию$10; баланс может появиться не сразу. Это отдельная оплата от ChatGPT.
3. [API keys](https://platform.openai.com/api-keys): проектный ключ для Image Studio, сохранить какOPENAI_API_KEY в локальном.env. [Настройки организации](https://platform.openai.com/settings/organization/general): пройти verification, если доступ кImageеё потребует. Логин, платёжные данные и документы владельца не хранить в проекте.
4. Две фотографии взрослых с согласия изображённых людей; разместить локально в непубликуемой папке. Не нужно отправлять их в чат.
5. Позже: [ЮKassa](https://yookassa.ru/) и [sandbox](https://yookassa.ru/developers/payment-acceptance/testing-and-going-live/testing), выбор статуса продавца, контакта поддержки и схемы чеков. Для первого Image-PoC эквайринг/хостинг ещё не нужны.

Россия не указана в [странах API OpenAI](https://help.openai.com/en/articles/5347006-openai-api-supported-countries-and-territories); сначала установить допустимый регион оператора/аудитории. Русский язык не ограничивает продукт Россией. Публичный платный запуск требует отдельной модели оплаты: [Telegram](https://core.telegram.org/bots/payments-stars) требуетStars для цифровых услуг внутри приложения даже при внешнем сайте. По указанию пользователяStarsне реализованы; ЮKassa интегрирована как внешний test-only модуль без buy-link в боте.

## Запуск

Скопировать `.env.example` в `.env` (если.envещё нет). В mock-режиме выставитьIMAGE_PROVIDER=mock иENABLE_DEMO_CREDITS=true, добавитьTelegramтокен. Затем:

```powershell
.\.venv\Scripts\python.exe -m image_studio run
```

Бот: /start → согласие → /demo → функция → фото → описание → подтверждение. DEMO возвращает копию с отметкой, не правкуИИ. /balance, /cancel, /result ID, /delete, /support.

Для бесплатного OpenAI-пилота: добавить `OPENAI_API_KEY`, `IMAGE_PROVIDER=openai`, `ENABLE_DEMO_CREDITS=false`, `ENABLE_TRIAL_ACCESS=true`, `BILLING_ENABLED=false`. Три генерации начисляются автоматически после согласия; ручной ID не требуется. Вне trial администратор может выдать пилотные финансовые кредиты:

```powershell
.\.venv\Scripts\python.exe -m image_studio pilot-credits --user-id 123456789 --amount 3
```

Реальный бот вызывает платный API только после подтверждения и резерва попытки; в trial платит владелец, каждый новый результат расходует одну из трёх генераций. Сначала провести пользовательский тест по [POC.md](docs/POC.md). При обычной установке запуск ручной; на компьютере владельца текущий OpenAI-пилот запущен штатным скриптом.

## Эксплуатационные пределы

Один polling-процесс и одинworker; SQLite+медиа должны резервироваться согласованно. Завершённые локальные фото/описанияTTL24h. Активные/reviewфайлы сохраняются до поддержки; старыеdraftописания в памяти30мин. Финансовые записи хранятся отдельно. При рестарте running→review, без автоматического нового платного вызова. Освободить резерв после разбора: `python -m image_studio release-job JOB_ID`. Это не возвращает деньги, возможно уже списанные OpenAI.

Внешний billing: `python -m image_studio billing`, loopback8088. По умолчаниюBILLING_ENABLED=false. При включении нужны тестовыймагазин, HTTPS returnURL и Beareradmin-token≥32символа. Транзакцииtest=falseотклоняются. Подробности и гейты в[POC.md](docs/POC.md). Подготовка публичногоcheckout/HTTPS/rate-limit/реальных чеков/регионов — следующий этап.

Размещение: сначала локальный тест по выбору владельца, затем VPS. OpenAI launcher использует `DATA_DIR=data/live-pilot`, чтобы тестовые кредиты sandbox/mock не попали в рабочую БД. Детальный порядок и требования сервера: docs/plan.md.

## Готовый локальный запуск

Бот: https://t.me/photo_editortest_bot. Двойной щелчок start-demo.cmd, либо scripts/Start-Bot.ps1 -Mode Demo. Статус/остановка: scripts/Status-Bot.ps1 и scripts/Stop-Bot.ps1. Полный порядок и переход на настоящий OpenAI после пополнения: [local-run.md](docs/local-run.md).
