"""Три пары «печатающийся текст → готовый коллаж» только в Telegram."""

import asyncio
import logging
from pathlib import Path

from aiogram.exceptions import TelegramBadRequest, TelegramNetworkError, TelegramRetryAfter
from aiogram.types import FSInputFile

from .streaming_answer import animated_welcome
from .telegram_instructions import PANEL_TEXT

log = logging.getLogger(__name__)
ASSETS = Path(__file__).with_name("assets") / "onboarding"
SLIDES = (
    (
        "obraz-start-01-style.png",
        "Привет! Я Образ — твоя фотостудия ✨\nСейчас покажу, что умею 👇\n\n"
        "💇 Хочется перемен? Посмотри, как тебе новая стрижка, цвет волос или укладка.\n"
        "👕 Примерь другой стиль: от уютного свитера до делового образа.\n"
        "👓 Подбери очки — попробуй разные формы и цвета оправы.\n\n"
        "Загрузи своё фото и создай новый образ 📸",
    ),
    (
        "obraz-start-02-photo.png",
        "И это ещё не всё ✨\n\n"
        "🪄 Фото получилось тусклым? Добавь света, естественных цветов и чётких деталей.\n"
        "🌄 Не нравится фон? Замени его — на парк, студию или место, которое придумаешь сам.\n"
        "🧩 Хочется оказаться вместе на одном снимке? Пришли две фотографии — объединю их в один кадр.\n\n"
        "Твои фотографии могут больше 💫",
    ),
    (
        "obraz-start-03-tools.png",
        "И напоследок — ещё две полезные фишки 👇\n\n"
        "🎬 Понравилась фотография: поза, свет или фон?\n"
        "Пришли её вместе со своим фото — создам похожий кадр с тобой.\n\n"
        "📄 Нужно фото на документы?\n"
        "Подготовь аккуратный портрет и получи четыре одинаковых снимка 35 × 45 мм на одном листе.\n\n"
        + PANEL_TEXT,
    ),
)


async def _send_collage(message, filename, pause):
    for attempt in range(2):
        try:
            await message.bot.send_photo(
                chat_id=message.chat.id, photo=FSInputFile(ASSETS / filename),
                disable_notification=True, parse_mode=None,
            )
            return
        except TelegramRetryAfter as exc:
            await pause(max(1.5, exc.retry_after))
            if attempt:
                log.warning("onboarding_photo_rate_limited")
        except (TelegramBadRequest, TelegramNetworkError, OSError) as exc:
            # При таймауте картинка могла дойти: не дублируем неопределённую отправку.
            # Остальные описания, клавиатура и согласие должны оставаться доступны.
            log.warning("onboarding_photo_unavailable type=%s", type(exc).__name__)
            return


async def send_onboarding(message, keyboard, *, notice="", pause=asyncio.sleep):
    for index, (filename, text) in enumerate(SLIDES):
        last = index == len(SLIDES) - 1
        if last and notice:
            text += "\n\n" + notice
        await animated_welcome(message, text, keyboard if index == 0 or last else None, pause=pause)
        await _send_collage(message, filename, pause)
        if not last:
            await pause(2)
