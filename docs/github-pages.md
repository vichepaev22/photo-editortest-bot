# GitHub Pages и обработчик фотографий

Владелец разрешил публикацию на GitHub и Pages. Репозиторий: https://github.com/vichepaev22/photo-editortest-bot . Планируемый адрес: https://vichepaev22.github.io/photo-editortest-bot/ . Факт успешной публикации фиксируется после проверки workflow и HTTP.

Pages размещает HTML/CSS/JavaScript, не запускает Python и не хранит серверные API-ключи. [Официальное описание](https://docs.github.com/en/pages/getting-started-with-github-pages/what-is-github-pages). Workflow загружает только src/image_studio/web; backend-код доступен в репозитории, но не работает на Pages.

Без опубликованного обработчика сайт показывает честный предпросмотр: вкладки, загрузка собственных фото в память браузера, выбор функции, описание и предварительное подтверждение. Фотографии не отправляются на GitHub или OpenAI, готовые ИИ-результаты и остаток попыток не имитируются. Нативный Telegram-бот продолжает работать с локальным обработчиком.

Для полноценной Mini App: разместить существующий Python-сервис на выбранном VPS, обеспечить HTTPS reverse proxy к защищённому studio API, указать его адрес в публичном config.js (`window.OBRAZ_API_BASE`) и Pages URL в серверном MINI_APP_URL. Сервер проверяет initData и владение данными, разрешает только точный origin приложения. Адрес обработчика не секрет; API-ключи и bot token остаются в серверном .env.

Не подключаем публичный сайт к localhost пользователя и не публикуем открытый локальный mock endpoint. Локальное демо проверяется напрямую через http://127.0.0.1:8089/ . Статический Pages URL можно открыть из Telegram уже сейчас; генерация внутри него станет доступна после публичного HTTPS backend.

Публикация через GitHub Actions: тесты offline + Ruff, затем загрузка и deploy статического интерфейса. Единственная ветка публикации — codex/ui-mini-app. Фотографии, .env, data, локальные логи, .smol и routing/runtime reports исключены. Изображения для оформления предоставляет владелец по design-assets-brief.md.
