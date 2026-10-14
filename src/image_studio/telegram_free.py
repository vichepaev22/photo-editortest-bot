from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError, TelegramRetryAfter

from .recurring_free import FreeDelivery, FreeDeliveryError


def telegram_free_delivery(bot, store, keyboard):
    async def send(user, text):
        try:
            message = await bot.send_message(user, text, reply_markup=keyboard)
        except TelegramRetryAfter as error:
            raise FreeDeliveryError('queued', error.retry_after) from None
        except TelegramForbiddenError:
            raise FreeDeliveryError('blocked') from None
        except TelegramBadRequest:
            raise FreeDeliveryError('failed') from None
        # Network/server failures are deliberately left uncertain, without automatic replay.
        return message.message_id
    return FreeDelivery(store, send)
