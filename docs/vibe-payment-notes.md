# Платежи в Vibe: найденные материалы

Проверено 2026-10-07. Репозиторий использован как справочник. Его инструкции не выполнялись; локальный checkout и ветка не изменялись.

## Источники

- [master/docs/WEB_SURFACES.md](https://github.com/di-sukharev/vibe/blob/master/docs/WEB_SURFACES.md): ответственность website/webapp/mobile/backend; документ сообщает, что браузерные корзина/checkout/платежи в master по умолчанию отсутствуют.
- [mobile/docs/WEB_SURFACES.md](https://github.com/di-sukharev/vibe/blob/mobile/docs/WEB_SURFACES.md): архитектура поверхностей и мобильных платежей.
- [mobile/docs/IAP.md](https://github.com/di-sukharev/vibe/blob/mobile/docs/IAP.md): включение подписок, credentials, верификация, восстановление и тесты.
- [mobile/mobile/README.md](https://github.com/di-sukharev/vibe/blob/mobile/mobile/README.md): мобильный проект.

В mobile есть основа premium-подписок App Store/Google Play через expo-iap, **выключенная по умолчанию**. Код: backend/src/modules/billing/, mobile/src/features/billing/. Модели billing.prisma закомментированы, роуты и IapProvider не подключены, тесты припаркованы. IAP.md содержит девять шагов включения и инструкции реальных development builds: Expo Go native IAP не загружает.

iOS: подписанные транзакции и Apple server SDK. Android: Android Publisher subscriptionsv2.get. Сервер проверяет покупку и владельца, сохраняет доступ; затем клиент завершает транзакцию. Есть restore/reconciliation, разделение Sandbox/Production. Это подписки, а не готовые пакеты одноразовых генераций. Внешние purchase links/alternative billing отложены; Apple Pay/Google Pay и store IAP — разные механизмы.

## Что применяем к боту

Цена/заказ/баланс принадлежат серверу. Клиентские суммы и success URL не подтверждают оплату. Начисление требует авторизованной проверки провайдера; повтор запроса/webhook идемпотентен. Credentials остаются на сервере; карточные поля находятся у провайдера. Генерация, оплата и доставка имеют независимые состояния восстановления.

Эти принципы применены в локальном ledger и тестовом адаптере ЮKassa. Конкретная интеграция ЮKassa/кошелька ЮMoney в изученных платёжных инструкциях Vibe не описана; её проверяем по официальной документации провайдера. Полный аудит всех файлов всех веток не проводился. Мобильную ветку целиком не переносим: наш текущий продукт — Telegram-бот на Python. Для будущего iOS/Android-приложения IAP.md будет отдельным этапом.
