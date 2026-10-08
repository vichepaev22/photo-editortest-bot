# GitHub Pages и серверный API

Текущее размещение, 2026-10-08:

- Интерфейс: https://vichepaev22.github.io/photo-editortest-bot/
- API: https://31.76.80.185/api/
- Исходники: https://github.com/vichepaev22/photo-editortest-bot
- Bot/worker/API: Play2go VPS, systemd, один процесс.

Pages размещает HTML/CSS/JavaScript. Python и API-ключи находятся на VPS.
[Описание GitHub Pages](https://docs.github.com/en/pages/getting-started-with-github-pages/what-is-github-pages).
Workflow публикует только `src/image_studio/web` после offline CI и Ruff.
Рабочая ветка публикации: `codex/ui-mini-app`.

Публичный адрес API записан в `config.js`; секретов там нет. Nginx обслуживает
доверенный HTTPS с автоматическим продлением сертификата. Python API слушает
только loopback. Сервер проверяет подпись Telegram initData, доступ к собственным
фото/задачам и точный origin `https://vichepaev22.github.io`.

Вход в студию выполняется из Telegram через кнопку «Студия». Обычная вкладка
браузера без Telegram initData не даёт доступ к обработке. Анонимное демо на
публичном API закрыто. Нативный бот и Mini App используют общую базу, историю
и три trial-генерации на пользователя; повторный вход не увеличивает квоту.

До подключения VPS сайт работал как предпросмотр без загрузки фото и генерации.
Этот этап завершён. При подключении проверены HTTPS, CORS, отказ анонимному
и поддельному входу, подписанный вход существующего пользователя, сохранённая
квота и чтение готового результата. Это проверка API с синтетически подписанным
initData, а не запись пользовательского сеанса Telegram. Новый платный вызов
Image для проверки не выполнялся.

Установка и обслуживание: [vps-deployment.md](vps-deployment.md).
Полноценный пользовательский тест Mini App внутри Android/iOS/Desktop остаётся
отдельной проверкой. Иллюстрации предоставляет владелец по
[design-assets-brief.md](design-assets-brief.md).
