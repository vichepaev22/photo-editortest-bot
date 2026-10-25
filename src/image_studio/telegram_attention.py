"""Один ограниченный проход штатных цветов; Telegram не поддерживает CSS shimmer."""

import asyncio
import logging

from aiogram.exceptions import TelegramAPIError, TelegramNetworkError, TelegramRetryAfter

log = logging.getLogger(__name__)
ORDER = ("dlg:pick:glasses", "dlg:pick:hair", "dlg:more", "studio", "guide:open")


class EntryAttention:
    def __init__(self, dialog, *, pause=asyncio.sleep):
        self.dialog, self.pause = dialog, pause
        self.tasks = {}

    async def edit(self, message, card, key, rich):
        if rich:
            await message.bot.edit_message_text(
                chat_id=message.chat.id,
                message_id=message.message_id,
                rich_message=self.dialog.rich(card, highlight=key),
                request_timeout=3,
            )
        else:
            await message.bot.edit_message_reply_markup(
                chat_id=message.chat.id,
                message_id=message.message_id,
                reply_markup=self.dialog.keyboard(card, highlight=key),
                request_timeout=3,
            )

    async def play(self, message, card, *, rich):
        painted, restore = False, True
        try:
            for key in ORDER:
                if key == "studio" and not self.dialog.settings.mini_app_url:
                    continue
                await self.pause(2)
                await self.edit(message, card, key, rich)
                painted = True
            await self.pause(2)
        except asyncio.CancelledError:
            raise
        except (TelegramRetryAfter, TelegramNetworkError) as exc:
            # Do not keep editing against a rate limit or an uncertain response.
            restore = False
            log.info("entry_attention_stopped type=%s", type(exc).__name__)
        except TelegramAPIError as exc:
            restore = False
            log.info("entry_attention_stopped type=%s", type(exc).__name__)
        finally:
            if painted and restore:
                try:
                    await self.edit(message, card, None, rich)
                except TelegramAPIError:
                    pass

    def start(self, message, card, *, rich):
        user = message.chat.id
        if user in self.tasks or len(self.tasks) >= 3:
            return

        async def run():
            try:
                await self.play(message, card, rich=rich)
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
