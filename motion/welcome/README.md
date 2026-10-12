# Образ · — приветственный фильм

22 секунды, 1080×1920, 30 fps. Семь сцен: кабинет → загрузка → девять причёсок →
три оправы → лист 2×2 → документ в руках персонажа → кабинет и кнопка.

Готовые файлы: [1080×1920, 1.75 MB](out/obraz-welcome-22s.mp4),
[720×1280, 0.77 MB](out/obraz-welcome-miniapp.mp4), [poster](out/obraz-welcome-poster.webp).
Оба MP4 без звука, H.264/yuv420p, с faststart. [Локальный проигрыватель](out/watch.html).

Поручение 12.10.2026: владелец после сценария запросил полностью готовый ролик.
Для текущей сборки персонаж и реквизит нарисованы программно как SVG-слои;
ранее запрошенные растровые материалы для этой версии не требуются.
Элементы можно заменить рисунками владельца без изменения тайминга.

Все изменения зависят от useCurrentFrame. CSS animation/transition, рандомное время,
сетевые изображения, внешние шрифты и Image API не используются.
Портрет и геометрия лица переиспользуются, меняются только волосы, одежда/фон документа
и оправы. Ролик не является проверкой качества GPT Image на настоящих фото.

## Сборка

Runtime: Remotion/@remotion/bundler/@remotion/renderer 4.0.529, React 19.2.3,
TypeScript 5.9.3, локальный Chrome headless из animated-bar-chart.
На данном компьютере node_modules — локальный junction к уже установленным пакетам
mirror-neurons-reel. Исходники того проекта не изменяются. Junction/build/stills/master
исключены из Git; системные шрифты не распространяются.

```powershell
node node_modules/typescript/bin/tsc --noEmit
node scripts/render.mjs --stills
node scripts/render.mjs
node scripts/finish.mjs
```

## Живое приветствие Mini App

По уточнению владельца MP4 не является интерактивным приветствием. Scene.tsx — чистый
React/SVG/DOM без Remotion runtime; Film.tsx использует его для видео, а Native.tsx
анимирует его через requestAnimationFrame в отдельном Shadow DOM. Изоляция сохраняет
SVG от общих правил студии. Системные кнопки снаружи рисунка: настоящее начало/пропуск,
replay в профиле, память закрытия, reduced motion, пауза в фоне и safe areas.
Ни ролик, ни живое приветствие не вызывают Image и не расходуют генерацию.

```powershell
node scripts/build-native.mjs
node node_modules/typescript/bin/tsc --noEmit
```

Выход: src/image_studio/web/welcome.js, 226594 bytes (включает React19.2.3).
В установленном пакете отсутствуют типы react-dom/client: локальная узкая декларация
содержит только документированные createRoot/render/unmount и используемые options.
Браузерная проверка: первый вход → весь сценарий → реальная CTA, reload без приветствия,
replay/пропуск, 320×568/reduced motion; 0 video элементов, 0 page errors, 0 job POST.
Команда/receipt: data/runtime/verify-welcome-upload-1012.cjs и welcome-upload-browser-1012.json.
Это локальный Chromium; настоящий iPhone не тестировался. Выпуск4bd5d9f опубликован
на GitHub Pages/VPS; публичные assets совпали, служба ready/NRestarts0.
API/консенсусы студии продолжают действовать после настоящей CTA; картинка внутри MP4
остаётся частью отдельного медиаэкспорта.

## Документация

Context7 недоступен в текущем наборе инструментов. Fallback — официальные страницы,
проверенные 12.10.2026, и декларации установленного 4.0.529:

- https://www.remotion.dev/docs/renderer/render-media
- https://www.remotion.dev/docs/renderer/render-still
- https://www.remotion.dev/docs/interpolate
- https://www.remotion.dev/docs/bundler

Для фильма использованы давно поддерживаемые bundle/selectComposition/renderStill/
renderMedia с локальным browserExecutable, h264/yuv420p, frame-driven числовые
стили SVG/DOM. Новые API из другой версии не используются.

Применены remotion-best-practices (покадровая анимация), ранее выбранный
emil-design-eng (темп и непрерывность) и process-design-vibecoding (scope/приёмка).
Результаты реального экспорта и проверки файла: [out/verification.md](out/verification.md).
