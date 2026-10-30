# Telegram: нативная обработка как в «Ровно» — 30.10.2026

CR-TG-PROCESSING-002. Применён сокращённый process-design-vibecoding.
Каноническое ТЗ — docs/PRD.md, план — docs/plan.md.
Статус: реализовано и проверено offline; установка на VPS ожидает SSH-доступа.

## Поведение

- Диалог: получение/подготовка фото, разбор пожелания. Статус появляется при
  ожидании дольше0,5с; мгновенный ответ не задерживается ради статуса.
- Генерация: реальные стадии Service.progress; время от начала UI-сессии,
  последние3завершённых этапа. Никаких скрытых мыслей и процентов готовности.
- Нативный sendRichMessageDraft/tg-thinking обновляется раз в4с; переходы
  ограничены паузой1,2с. Один draft_id и can_stop=false.
- При отказе rich: plain draft; при повторном отказе — прежний статус для Image
  или только typing для диалога. retry_after не обходится сменой этапа.
- Длинный готовый ответ: два растущих plain-превью с тем же ID, один sendMessage.
- Ответ/файл связан ReplyParameters с исходным сообщением в своём личном чате.
  Удалённый исходник не препятствует доставке (allow_sending_without_reply).
  При перезапуске оперативная привязка теряется; прежняя доставка сохраняется.
- Новое сообщение прекращает устаревший UI; уже начатый Image не отменяется.
- Ошибка UI не повторяет провайдер/готовый файл. Ошибка обработки получает одно
  постоянное уведомление; shutdown закрывает задачи. Историю не удаляем.

## Ограничение прокрутки

Изучены rovno/progress.py, rovno/telegram.py и
rovno-osanka-bot/docs/telegram-status-admin-20261029.md. В «Ровно» реализованы
native drafts/thinking и ReplyParameters, а не управление прокруткой экрана.
Точное положение нового запроса наверху экрана зависит от Telegram-клиента.
Проверку на iPhone выполняет владелец; агент её не проводил.

## Контракты

Context7: /websites/core_telegram_bots_api, /aiogram/aiogram/v3.31.0.
Установленная aiogram3.31.0 содержит SendRichMessageDraft и нужные поля;
зависимости не обновлялись. Draft временный, TTL30s. Пустой text не используется
для очистки: это placeholder Thinking. Постоянный ответ завершает preview;
при остановке без ответа он исчезает по TTL платформы.

Источники: [Telegram draft](https://core.telegram.org/bots/api#sendrichmessagedraft),
[thinking](https://core.telegram.org/bots/api#inputrichblockthinking),
[reply](https://core.telegram.org/bots/api#replyparameters),
[aiogram3.31](https://github.com/aiogram/aiogram/blob/v3.31.0/docs/api/methods/send_rich_message_draft.rst).

## Проверка и поставка

34исходных проверки прошли; один сценарий ранней ошибки нашёл поздний typing.
Исправлено: контекст закрывается до отправки сообщения об ошибке.
28проверок проблемного сценария/bot_flow затем passed.
Отдельный clean release без чужих dirty Jev-правок:16processing/live/dialog passed.
Ruff clean src/tests PASS. Обязательный [CI41046953735](https://github.com/vichepaev22/photo-editortest-bot/actions/runs/41046953735) успешно завершён:
651passed,3skipped; Ruff PASS; welcome scheduling и workflow success.
Коммит1ad57ade326082e9b5bb72b7416202ec82e27ae3 опубликован.
Результат установки фиксируется только после восстановления доступа. Новых платных вызовов, тестовых сообщений людям и финансовых изменений0.

MAX/Mini App, тарифы/качество/квоты, Jev и магазин владельца не меняются.
Исходные dirty-правки сохранены; в Git-релиз попадают только изменения этой задачи.
На VPS требуется сначала сверить фактический код: Jev уже поставлен другим
релизом и должен остаться на месте при наложении текущего изменения.

На30.10 SSH22 принимает TCP, но не присылает server banner даже при стандартном
OpenSSH-приветствии; HTTP/HTTPS доступны. Авторизация и изменение VPS ещё не
выполнялись. Подготовлен read-only preflight; рабочие службы не остановлены.

Последняя проверка: SSH через настроенный системный HTTP CONNECT proxy тоже
не прошёл до авторизации (SSHException). Проверка host key не отключалась;
попыток смены пароля, отключения защиты или перезапуска сервера не было.
