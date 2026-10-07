# Где разместить «Образ»

Проверено 2026-10-08 по официальным документам. Это сравнение и план переноса; облачные аккаунты и ресурсы не создавались. Бот пока работает на компьютере владельца, Pages размещает предпросмотр. Бесплатный хостинг не отменяет плату за OpenAI Image.

## Выбор для текущего проекта

**Oracle Always Free — первый кандидат для сохранения существующего Python-кода**, если доступны регистрация и VM. **Cloudflare — кандидат для событийной версии** с очередью и отдельными хранилищами. Render Free подходит для временной демонстрации после изменения хранения данных. Telegram Serverless заслуживает отдельного PoC; его бесплатность и необходимые эксплуатационные лимиты по прочитанной документации не подтверждены.

| Сервис | Проверенные условия | Что означает для нашего проекта |
|---|---|---|
| [Oracle Always Free](https://docs.oracle.com/en-us/iaas/Content/FreeTier/freetier_topic-Always_Free_Resources.htm) | A1 до 2 OCPU / 12 GB, 200 GB block storage, 10 TB исходящего трафика; в пределах Always Free | Сохраняем aiogram, polling, worker, FastAPI и SQLite на диске. Нужны Linux-запуск, HTTPS и backup |
| [Cloudflare Workers](https://developers.cloudflare.com/workers/platform/limits/) | Free: 100 000 запросов/день, 10 ms CPU на HTTP-вызов. [D1](https://developers.cloudflare.com/d1/platform/pricing/): 5 GB, [Queues](https://developers.cloudflare.com/queues/platform/pricing/): 10 000 операций/день, [R2](https://developers.cloudflare.com/r2/pricing/): 10 GB-month | Меняем polling на webhook, Store на D1, Media на приватное R2, worker на consumer. Бесплатность действует внутри отдельных квот; R2 подключается через тарифицируемую подписку |
| [Render Free](https://render.com/docs/free) | 750 instance-hours/месяц, сон после 15 минут без входящих запросов; нет persistent disk/free background worker; бесплатная PostgreSQL БД истекает через 30 дней | Прямой перенос текущей SQLite потеряет квоту и историю при sleep/restart/deploy. Нужны webhook и внешние постоянные хранилища |
| [Telegram Serverless](https://core.telegram.org/bots/serverless) | JavaScript/V8, встроенная SQLite-backed БД, Bot API и внешний HTTP; 6 октября добавлено размещение Mini App | Не переносит наш Python-процесс напрямую. Нужны адаптация логики, проверка атомарного резервирования, секретов OpenAI, длительности вызова и хранения фото. Цена и достаточные runtime-квоты пока не подтверждены |

Oracle требует карту для проверки личности, допускает авторизационные удержания; свободная VM может отсутствовать, малозагруженные экземпляры могут быть изъяты после семи дней низкой активности. Always Free отличается от рекламного trial $300/30 дней. [Условия регистрации](https://www.oracle.com/cloud/free/faq/). R2 имеет checkout и платные превышения; обещать подключение без платёжного метода нельзя. [Подключение R2](https://developers.cloudflare.com/r2/get-started/).

## Почему нельзя просто скопировать Python на бесплатный web-хостинг

Наш процесс держит polling Telegram, worker генерации, FastAPI, SQLite WAL и временные фото. Если хостинг стирает диск, однократные три попытки станут доступны снова, а финансовая история и результаты исчезнут. Исходящие polling-запросы не заменяют входящий трафик, по которому Render определяет сон. Задание должно переживать остановку, а не запускать второй платный Image-вызов после пробуждения.

Python/FastAPI есть в [Python Workers](https://developers.cloudflare.com/workers/languages/python/), но это Pyodide с временной файловой системой; совместимость aiogram/aiohttp/Pillow требует отдельного прототипа. [Ограничения библиотек](https://developers.cloudflare.com/workers/languages/python/packages/), [файловая система](https://developers.cloudflare.com/workers/languages/python/stdlib/). Не следует выдавать поддержку Python за совместимость с текущим серверным процессом.

## Конкретный план переноса на VM

1. Получить доступную Always Free VM и проверить исходящий HTTPS к Telegram/OpenAI из её региона. Доступность API с выбранного хостинга пока не измерена.
2. Подготовить Linux-окружение из lockfile, отдельного пользователя процесса, systemd и обратный прокси с HTTPS. API остаётся на loopback, снаружи доступны только нужные маршруты. Интерфейс может оставаться на GitHub Pages.
3. Дождаться отсутствия queued/running задач, штатно остановить локальный бот, сделать SQLite backup, перенести БД и медиа через закрытый канал. Ключи передавать отдельно, никогда через Git. В каждый момент работает один polling-процесс.
4. Проверить существующие financial и trial поля, задачи и согласие до/после переноса; запуск, восстановление, TTL, HMAC-вход и чужие файлы. На тестовом пользователе проверить три результата и запрет четвёртого, используя контролируемый provider без расходования пользовательского OpenAI-баланса.
5. Подключить HTTPS API в Pages config, проверить настоящий Telegram-вход. На согласованном фото пользователь подтверждает первый реальный вызов; его usage и качество — отдельный live-гейт. После устойчивого запуска компьютер владельца можно выключить.

Имя @photo_editortest_bot сохраняется: меняется место выполнения кода, а не Telegram-аккаунт бота.

## Альтернатива Cloudflare

Рекомендуемая схема: Telegram webhook и Mini App → Worker → D1; ID задания → Queue → OpenAI; приватные входы/результаты → R2. HTTP-обработчик быстро подтверждает приём, очередь выполняет генерацию. Consumer имеет предел 15 минут, а `waitUntil()` после ответа HTTP — только 30 секунд; текущий OpenAI timeout 180 секунд требует отдельного исполнителя. [Лимиты Workers](https://developers.cloudflare.com/workers/platform/limits/), [Queues limits](https://developers.cloudflare.com/queues/platform/limits/).

Повторная доставка очереди возможна: нужны атомарный claim, ключ идемпотентности и сохранение `review` для прерванного платного запроса. Сообщение очереди содержит ID, не фото. [Гарантии доставки](https://developers.cloudflare.com/queues/reference/delivery-guarantees/). Приватный API должен закрывать доступ через 24 часа независимо от задержки физического удаления lifecycle R2. [Lifecycle](https://developers.cloudflare.com/r2/buckets/object-lifecycles/).

## Другие найденные варианты

- [Koyeb Free](https://www.koyeb.com/docs/reference/instances): 512 MB, 0,1 vCPU, sleep через час, без volume/free worker — преимуществ для текущей архитектуры не даёт.
- [Railway](https://docs.railway.com/pricing/plans): Free $1/месяц и volume до 0,5 GB; [trial](https://docs.railway.com/pricing/free-trial) $5/30 дней, у непроверенного аккаунта ограничен outbound. Это бюджет ресурсов, а не обещание непрерывного бесплатного месяца.
- GitHub Pages оставляем для статического интерфейса: текущий Python/SQLite/worker на нём не размещаются. Уже опубликованный интерфейс становится рабочей Mini App после подключения доступного HTTPS backend.

Решение о целевой платформе ещё не принято. До его проверки текущий локальный OpenAI-пилот и квота три генерации сохраняются.
