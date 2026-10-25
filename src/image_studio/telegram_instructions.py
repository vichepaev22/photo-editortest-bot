"""Три пользовательские карточки: только справка, без Image/LLM и изменений баланса."""

import asyncio
import logging
import re
from pathlib import Path

from aiogram.exceptions import TelegramAPIError, TelegramNetworkError, TelegramRetryAfter
from aiogram.types import FSInputFile

from .streaming_answer import animated_welcome

log = logging.getLogger(__name__)
ASSETS = Path(__file__).with_name("assets") / "instructions"
PANEL_TEXT = (
    "Выбери действие на нижней панели или просто пришли фотографию — начнём работать над твоим образом ✨"
)
SLIDES = (
    (
        "obraz-guide-01-photo.png",
        "📸 Начни с простого — пришли фото\n\n"
        "📎 Нажми на скрепку, выбери свою фотографию и отправь её в чат.\n"
        "✍️ Затем напиши, что хочешь изменить. Например: «Примерь круглые очки».\n\n"
        "Я подскажу возможности и уточню детали — выбирать функцию заранее необязательно ✨\n\n"
        "➤ Главное — нажимай «Отправить» после выбора фото и после своего сообщения.",
    ),
    (
        "obraz-guide-02-studio.png",
        "🎨 Открой студию — пространство для твоих образов\n\n"
        "Нажми «Студия» внизу слева или «Открыть студию» под приветствием. "
        "Откроется полноценное мини-приложение 📱\n\n"
        "📸 Загрузи фото, выбери функцию и опиши пожелание.\n"
        "✨ Примеряй образы, меняй детали и сохраняй результат — всё под рукой.",
    ),
    (
        "obraz-guide-03-menu.png",
        "✨ Выбирай удобную функцию\n\n"
        "👓 «Очки» и 💇 «Причёска» — быстрый старт. "
        "Нажми «Ещё», чтобы увидеть остальные возможности.\n\n"
        "⌨️ Основное меню — на нижней клавиатуре. Если она скрыта, "
        "нажми значок клавиатуры рядом с полем сообщения.\n\n"
        "💬 Или просто напиши: «Открой основное меню» — и я его покажу.\n\n"
        "Дальше подскажу, какое фото прислать и что описать 📸",
    ),
)


def is_menu_request(text):
    if not isinstance(text, str):
        return False
    return bool(
        re.fullmatch(
            r"(?:пожалуйста[,\s]+)?(?:открой(?:те)?|покажи(?:те)?|верни(?:те)?)\s+"
            r"(?:(?:основное|главное)\s+)?меню(?:\s*,?\s*пожалуйста)?[.! ]*",
            text.strip().casefold(),
        )
    )


class InstructionGuide:
    def __init__(self, keyboard, *, pause=asyncio.sleep):
        self.keyboard, self.pause = keyboard, pause
        self.tasks = {}

    async def show(self, message):
        for index, (filename, text) in enumerate(SLIDES):
            last = index == len(SLIDES) - 1
            await animated_welcome(message, text, None, pause=self.pause)
            sent = False
            for attempt in range(2):
                try:
                    await message.bot.send_photo(
                        chat_id=message.chat.id,
                        photo=FSInputFile(ASSETS / filename),
                        caption=PANEL_TEXT if last else None,
                        reply_markup=self.keyboard if last else None,
                        disable_notification=True,
                        parse_mode=None,
                    )
                    sent = True
                    break
                except TelegramRetryAfter as exc:
                    if attempt:
                        break
                    await self.pause(max(1.5, exc.retry_after))
                except (TelegramAPIError, OSError) as exc:
                    # Never repeat a photo POST after an uncertain network result.
                    log.warning("instruction_photo_unavailable type=%s", type(exc).__name__)
                    break
            if last and not sent:
                await message.answer(PANEL_TEXT, reply_markup=self.keyboard, parse_mode=None)
            if not last:
                await self.pause(2)

    def launch(self, message):
        user = message.chat.id
        if user in self.tasks or len(self.tasks) >= 100:
            return

        async def run():
            try:
                await self.show(message)
            except asyncio.CancelledError:
                raise
            except (TelegramAPIError, TelegramNetworkError, OSError) as exc:
                log.warning("instruction_unavailable type=%s", type(exc).__name__)
            finally:
                if self.tasks.get(user) is asyncio.current_task():
                    self.tasks.pop(user, None)

        self.tasks[user] = asyncio.create_task(run())

    async def stop(self, user):
        task = self.tasks.pop(user, None)
        if task:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    async def close(self):
        await asyncio.gather(*(self.stop(user) for user in list(self.tasks)))
