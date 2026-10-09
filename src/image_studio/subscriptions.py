import logging

from aiogram import F
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from .store import DomainError

CHANNELS = ("@FutureDarkSide", "@nofuturenews")
CHECK_CALLBACK = "subscription:check"
OFFER_TEXT = ("Хочешь ещё одну бесплатную генерацию? Подпишись на эти два канала "
              "и нажми «Проверить подписку».\n\nБонус выдаётся один раз.")
SUBSCRIPTION_KEYBOARD = InlineKeyboardMarkup(inline_keyboard=[
    [InlineKeyboardButton(text=channel, url="https://t.me/" + channel[1:]) for channel in CHANNELS],
    [InlineKeyboardButton(text="✅ Проверить подписку · +1 генерация", callback_data=CHECK_CALLBACK)],
    [InlineKeyboardButton(text="🏠 Главное меню", callback_data="nav:home")],
])
log = logging.getLogger(__name__)


class SubscriptionUnavailable(Exception):
    pass


async def missing_subscriptions(bot, user):
    """Fail closed; getChatMember for others is guaranteed only to chat admins.

    https://core.telegram.org/bots/api#getchatmember
    """
    try:
        for channel in CHANNELS:
            member = await bot.get_chat_member(channel, bot.id)
            if member.status not in {"creator", "administrator"}:
                raise SubscriptionUnavailable
        missing = []
        for channel in CHANNELS:
            member = await bot.get_chat_member(channel, user)
            if member.status not in {"creator", "administrator", "member"} and not (
                member.status == "restricted" and getattr(member, "is_member", False) is True
            ):
                missing.append(channel)
        return tuple(missing)
    except SubscriptionUnavailable:
        raise
    except Exception as exc:
        log.warning("subscription_check_failed type=%s", type(exc).__name__)
        raise SubscriptionUnavailable from None


async def subscribed_to_both(bot, user):
    return not await missing_subscriptions(bot, user)


async def offer_channel_bonus(bot, store, user, job):
    """Claim before sending: delivery retries cannot duplicate this optional offer."""
    try:
        if store.claim_channel_bonus_offer(user, job):
            await bot.send_message(user, OFFER_TEXT, reply_markup=SUBSCRIPTION_KEYBOARD)
    except Exception as exc:
        log.warning("subscription_offer_failed type=%s", type(exc).__name__)


def subscription_eligible(store, service, user):
    return not service.is_unlimited(user) and not store.has_channel_bonus(user)


def subscription_hint(store, service, user):
    if service.is_unlimited(user):
        return ""
    if store.has_channel_bonus(user):
        return "\n\n✅ Бонус за подписку уже начислен. Он выдаётся один раз."
    return ("\n\n🎁 Ещё 1 генерация: подпишитесь на оба канала и нажмите «Проверить подписку». "
            "Бонус выдаётся один раз.")


def register_subscriptions(router, store, service, show_balance, *, enabled):
    @router.message(Command("bonus"))
    async def bonus(message: Message):
        user = message.from_user
        if (user is None or user.is_bot or message.chat.type != "private"
                or message.chat.id != user.id or user.id <= 0):
            return
        if enabled:
            await show_balance(message, user.id)
        else:
            await message.answer("Бонус за подписку сейчас недоступен.")

    @router.callback_query(F.data == CHECK_CALLBACK)
    async def check(callback: CallbackQuery):
        user, message = callback.from_user, callback.message
        if (user.is_bot or user.id <= 0 or not isinstance(message, Message)
                or message.chat.type != "private" or message.chat.id != user.id):
            await callback.answer("Откройте /bonus в личном чате с ботом.", show_alert=True)
            return
        await callback.answer()
        if not enabled:
            status = "Бонус за подписку сейчас недоступен."
        elif service.is_unlimited(user.id):
            status = "Для вашего аккаунта уже включено безлимитное тестирование."
        elif not store.has_consent(user.id):
            status = "Сначала подтвердите условия и согласие в /start."
        elif store.has_channel_bonus(user.id):
            status = "Бонус уже начислен. Повторная выдача не предусмотрена."
        else:
            try:
                missing = await missing_subscriptions(callback.bot, user.id)
                if missing:
                    status = ("Не подтверждена подписка на: " + ", ".join(missing)
                              + ". Подпишитесь и нажмите «Проверить подписку».")
                elif store.grant_channel_bonus(user.id):
                    status = "✅ Подписка подтверждена. Начислена 1 дополнительная генерация."
                else:
                    status = "Бонус уже начислен. Повторная выдача не предусмотрена."
            except SubscriptionUnavailable:
                status = "Сейчас не удалось проверить подписку. Попробуйте позже."
            except DomainError as exc:
                status = {
                    "already_active": "Дождитесь завершения текущей генерации и проверьте подписку снова.",
                    "consent_required": "Сначала подтвердите условия и согласие в /start.",
                }.get(str(exc), "Бонус пока не начислен. Попробуйте позже.")
        try:
            await show_balance(message, user.id, edit=True, status=status)
        except TelegramAPIError as exc:
            if not isinstance(exc, TelegramBadRequest) or "message is not modified" not in exc.message.lower():
                log.warning("subscription_status_edit_failed type=%s", type(exc).__name__)
