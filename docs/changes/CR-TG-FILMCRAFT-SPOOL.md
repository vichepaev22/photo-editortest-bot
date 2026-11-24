# Рабочая папка Filmcraft

Дополняет [контракт Filmcraft](CR-TG-FILMCRAFT-002.md). Продакшен-бот работает
с `ProtectSystem=strict`. Вместе с `filmcraft.conf` установите
`filmcraft-spool.conf` в каталог `image-studio-bot.service.d`. Это даёт
процессу Telegram запись в `/var/lib/obraz-filmcraft`.

После изменения откройте Filmcraft через установленный обработчик в пространстве
монтирования службы. Процесс должен увидеть подсказку и создать рабочую папку
от своего пользователя. Для проверки используется тестовое событие без
отправки в Telegram и без обращения к OpenAI.
