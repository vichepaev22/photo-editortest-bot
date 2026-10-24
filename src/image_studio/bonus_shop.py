"""Магазин в личном чате Telegram; первый пилот доступен только владельцу."""

import io

from aiogram import F
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message

from .admin_store import DomainError
from .bonus_catalog import PILOT_OWNER
from .media import MAX_BYTES, normalize


def keyboard(rows):
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text=b[0], callback_data=b[1], style=b[2] if len(b) > 2 else None)
        for b in row] for row in rows])


TABS = [[("🎁 Магазин", "bonus:home"), ("🧩 Задания", "bonus:tasks")],
        [("🧾 История", "bonus:history"), ("💎 Баланс", "nav:balance")]]
ERRORS = {
    "bonus_unavailable": "Этот тест пока доступен только владельцу.",
    "bonus_stale": "Этот шаг уже изменился. Откройте актуальные задания.",
    "bonus_started": "Задание уже начато. Его результаты и награда сохраняются.",
    "bonus_step_order": "Сначала завершите предыдущий результат этой серии.",
    "bonus_insufficient": "Бонусов пока недостаточно для этого обмена.",
    "consent_required": "Сначала подтвердите согласие в /start.",
    "already_active": "Ваше фото уже создаётся. Дождитесь результата.",
    "invalid_inputs": "Фото больше недоступно. Загрузите его заново в этом задании.",
    "trial_exhausted": "Нужна доступная генерация. Откройте «Мои попытки».",
    "insufficient_credits": "Недостаточно генераций. Откройте «Мои попытки».",
}


class BonusShop:
    def __init__(self, settings, store, media, service):
        self.enabled = (settings.bonus_shop_enabled and settings.admin_user_id == PILOT_OWNER
                        and service.is_unlimited(PILOT_OWNER))
        self.store, self.media, self.service = store, media, service

    def allowed(self, user):
        return self.enabled and type(user) is int and user == PILOT_OWNER

    def leave(self, user):
        if user == PILOT_OWNER:
            self.store.bonus_session(user, clear=True)

    def balance_content(self, user):
        state = self.store.bonus_snapshot(user)
        return (f"\n\n🎁 Бонусы: {state['points']} · ⭐ {state['xp']} XP\n"
                f"Получено обменом: {state['redeemed']} генераций.")

    @staticmethod
    def balance_keyboard():
        return keyboard([[("🎁 Бонусы и задания", "bonus:home")], [("🏠 Главное меню", "nav:home")]])

    async def show(self, message, text, rows, *, edit=True):
        markup = keyboard(rows)
        if edit:
            try:
                return await message.edit_text(text, reply_markup=markup, parse_mode=None)
            except TelegramBadRequest as error:
                if "message is not modified" in error.message.lower():
                    return message
                if not any(part in error.message.lower() for part in ("message to edit not found", "can't be edited")):
                    raise
        return await message.answer(text, reply_markup=markup, parse_mode=None)

    def mission(self, user, mission_id):
        for mission in self.store.bonus_snapshot(user)["missions"]:
            if mission["id"] == mission_id:
                return mission
        raise DomainError("bonus_stale")

    def available_step(self, user, step_id):
        step = self.store.bonus_step(user, step_id)
        mission = self.mission(user, step["mission_id"])
        if step["done"]:
            raise DomainError("bonus_stale")
        if any(not row["done"] for row in mission["steps"] if row["position"] < step["position"]):
            raise DomainError("bonus_step_order")
        if step["job_id"]:
            raise DomainError("already_active")
        return step

    async def home(self, message, user, *, edit=True, notice=""):
        state = self.store.bonus_snapshot(user)
        done = sum(m["done"] for m in state["missions"])
        text = ("Образ · Бонусный магазин\n\n"
                f"🎁 {state['points']} бонусов · ⭐ {state['xp']} XP\n"
                f"Задания: {done}/5 · заработано {state['earned']}/550\n"
                f"Получено обменом: {state['redeemed']} генераций\n\n"
                "100 бонусов = 1 генерация. XP — только ваш прогресс.\n"
                "Обмен добавляет генерации к обычному остатку.")
        if self.service.is_unlimited(user):
            text += " Ваш безлимит сохраняется."
        if notice:
            text += "\n\n" + notice
        rows = [[(f"{quantity} ген. · {quantity * 100} бонусов", f"bonus:quote:{quantity}", "success")]
                for quantity in (1, 3, 5) if state["points"] >= quantity * 100]
        if state["points"] < 100:
            text += ("\n\nОставшиеся бонусы сохраняются. Повторная программа пока не открыта."
                     if done == 5 else "\n\nВыполняйте задания: первый обмен доступен от 100 бонусов.")
        return await self.show(message, text, rows + TABS, edit=edit)

    async def tasks(self, message, user):
        state = self.store.bonus_snapshot(user)
        text = ("🧩 Ваши задания\n\n"
                "Собирайте разные готовые работы: одиночные, пары и серии из трёх. "
                "Задания можно проходить в любом порядке.\n\n"
                "Награды за задания — 500 бонусов. За всю программу — ещё 50. "
                "Каждый отдельный результат использует одну генерацию; у вас действует безлимит.\n\n"
                "Можно совместить изменения в обычной студии. Здесь у каждого шага свой "
                "готовый файл и назначение: одна обработка засчитывается один раз.")
        rows = []
        for mission in state["missions"]:
            progress = sum(s["done"] for s in mission["steps"])
            text += (f"\n\n{'✅' if mission['done'] else '◦'} {mission['title']}\n"
                     f"{progress}/{len(mission['steps'])} работ · +{mission['reward']} бонусов")
            rows.append([(mission["title"], "bonus:mission:" + mission["id"])])
        return await self.show(message, text, rows + TABS)

    async def detail(self, message, user, mission_id, *, notice=""):
        mission = self.mission(user, mission_id)
        text = f"🧩 {mission['title']}\n\n{mission['summary']}\n\n"
        rows, offered = [], False
        for step in mission["steps"]:
            text += f"{'✅' if step['done'] else '◦'} {step['position'] + 1}. {step['title']}\n"
            if not step["done"] and not offered:
                offered = True
                if step["job_id"]:
                    rows.append([("🖼 Мои результаты", "nav:results")])
                else:
                    rows.append([("📸 Создать этот результат", "bonus:step:" + step["id"], "success")])
        text += (f"\nНаграда: +{mission['reward']} бонусов после всех работ.\n"
                 "Один шаг = один отдельный файл. За доставленный результат +10 XP.")
        if mission["done"]:
            text += "\n\n✅ Награда уже начислена."
        if notice:
            text += "\n\n" + notice
        if not any(s["job_id"] or s["done"] for s in mission["steps"]):
            rows.append([("🔄 Другое задание", "bonus:swap:" + mission_id)])
        return await self.show(message, text, rows + TABS)

    async def select(self, message, user, step_id):
        step = self.available_step(user, step_id)
        if any(j["status"] in {"queued", "running", "review"} for j in self.store.jobs(user)):
            raise DomainError("already_active")
        self.store.bonus_session(user, step_id=step_id, panel_id=message.message_id)
        rows = []
        if self.service.reusable_inputs(user, 1):
            rows.append([("Использовать загруженное фото", "bonus:reuse:" + step_id)])
        rows += [[("К заданию", "bonus:mission:" + step["mission_id"])]] + TABS
        return await self.show(message, f"📸 {step['title']}\n\n"
                               "Загрузите своё фото файлом или фотографией, до 10 MB. "
                               "Можно сделать селфи у нейтральной стены.\n\n"
                               "Перед обработкой покажу точное описание результата.", rows)

    async def review(self, message, user, step_id, path):
        step = self.available_step(user, step_id)
        if self.service._owned_recent_file(user, path, MAX_BYTES) is None:
            raise DomainError("invalid_inputs")
        session = self.store.bonus_session(user, step_id=step_id, panel_id=message.message_id, input_path=path)
        cost = "♾ Ваш безлимит · API-запрос оплачивается из общего баланса OpenAI."
        return await self.show(message, f"✨ {step['title']}\n\n{step['description']}\n\n{cost}\n"
                               "Это один результат текущего задания.",
                               [[("✨ Создать · безлимит", "bonus:submit:" + session["token"], "success")],
                                [("Загрузить другое фото", "bonus:step:" + step_id)]] + TABS)

    async def after_job(self, bot, user, job, *, failed=False):
        if not self.allowed(user):
            return
        session = self.store.bonus_session(user)
        if not session:
            return
        step = self.store.bonus_step(user, session["step_id"])
        with self.store.tx() as c:
            bound = c.execute("SELECT step_id FROM bonus_jobs WHERE job_id=?", (job,)).fetchone()
        if not bound or bound["step_id"] != step["id"]:
            return
        # A message handle only; no user input or live API call is fabricated.
        panel = Message(message_id=session["panel_id"], date=0,
                        chat={"id": user, "type": "private"}).as_(bot)
        self.leave(user)
        await self.detail(panel, user, step["mission_id"], notice=(
            "Обработка не выполнена. Прогресс и награда не изменились; можно повторить шаг."
            if failed else "Готовая работа засчитана. Прогресс и бонусы сохранены."))

    def register(self, router, drafts):
        @router.callback_query(F.data.startswith("bonus:"))
        async def callback(query):
            user = query.from_user.id
            if (not self.allowed(user) or not isinstance(query.message, Message)
                    or query.message.chat.id != user):
                await query.answer("Этот тест пока доступен только владельцу.", show_alert=True)
                return
            await query.answer()
            try:
                if not self.store.has_consent(user):
                    raise DomainError("consent_required")
                parts = query.data.split(":")
                action, arg = parts[1], parts[2] if len(parts) == 3 else None
                if len(parts) > 3:
                    raise DomainError("bonus_stale")
                if action != "submit":
                    self.leave(user)
                drafts.pop(user, None)
                self.store.admin_session_clear(user)
                if action == "home" and arg is None:
                    await self.home(query.message, user)
                elif action == "tasks" and arg is None:
                    await self.tasks(query.message, user)
                elif action == "history" and arg is None:
                    state = self.store.bonus_snapshot(user)
                    text = "🧾 История бонусов\n\n" + ("\n\n".join(
                        f"{row['title']}\n{row['points']:+} бонусов"
                        + (f" · +{row['generations']} ген." if row["generations"] else "")
                        for row in state["history"]) or "Пока операций нет.")
                    await self.show(query.message, text, TABS)
                elif action in {"mission", "swap"} and arg:
                    if action == "swap":
                        self.store.bonus_swap(user, arg)
                    await self.detail(query.message, user, arg)
                elif action == "step" and arg:
                    await self.select(query.message, user, arg)
                elif action == "reuse" and arg:
                    inputs = self.service.reusable_inputs(user, 1)
                    if not inputs:
                        raise DomainError("invalid_inputs")
                    await self.review(query.message, user, arg, inputs[0])
                elif action == "submit" and arg:
                    session = self.store.bonus_session(user)
                    if not session or session["token"] != arg or not session["input_path"]:
                        raise DomainError("bonus_stale")
                    step = self.available_step(user, session["step_id"])
                    self.service.submit(user, "bonus:" + arg, step["preset"],
                                        [session["input_path"]], step["description"], bonus_step=step["id"])
                    await self.show(query.message, "✨ Создаём работу для задания\n\n" + step["title"]
                                    + "\nОбычно до 2 минут. Готовый файл придёт в этот чат.",
                                    [[("Посмотреть задания", "bonus:tasks")]])
                elif action == "quote" and arg in {"1", "3", "5"}:
                    quote = self.store.bonus_quote(user, int(arg))
                    await self.show(query.message, f"Обменять бонусы?\n\nГенераций: {arg}\n"
                                    f"Спишется: {int(arg) * 100} бонусов\n"
                                    f"Останется: {quote['points'] - int(arg) * 100} бонусов",
                                    [[("Подтвердить обмен", "bonus:confirm:" + quote["id"], "success")],
                                     [("Отмена", "bonus:cancel:" + quote["id"])]] )
                elif action in {"confirm", "cancel"} and arg:
                    if action == "confirm":
                        added = self.store.bonus_exchange(user, arg)
                        notice = "✅ Генерации добавлены в ваш остаток." if added else "Этот обмен уже выполнен."
                    else:
                        self.store.bonus_cancel_quote(user, arg)
                        notice = "Обмен отменён. Бонусы сохранены."
                    await self.home(query.message, user, notice=notice)
                else:
                    raise DomainError("bonus_stale")
            except DomainError as error:
                await self.show(query.message, ERRORS.get(str(error), "Откройте актуальные задания и повторите."), TABS)

        @router.message(F.photo | F.document,
                        lambda message: self.allowed(message.from_user.id)
                        and self.store.bonus_session(message.from_user.id) is not None)
        async def photo(message):
            user = message.from_user.id
            session = self.store.bonus_session(user)
            panel = Message(message_id=session["panel_id"], date=0,
                            chat={"id": user, "type": "private"}).as_(message.bot)
            try:
                self.available_step(user, session["step_id"])
                attachment = message.photo[-1] if message.photo else message.document
                if attachment.file_size and attachment.file_size > MAX_BYTES:
                    raise ValueError("image_too_large")
                buffer = io.BytesIO()
                await message.bot.download(attachment, destination=buffer)
                data = normalize(buffer.getvalue())
                path = self.media.save(user, data)
                self.service.remember_inputs(user, [path])
                await self.review(panel, user, session["step_id"], path)
            except (ValueError, OSError):
                await self.show(panel, "Не удалось прочитать фото. Пришлите другой снимок до 10 MB.",
                                [[("Загрузить фото", "bonus:step:" + session["step_id"])]] + TABS)
            except DomainError as error:
                self.leave(user)
                await self.show(panel, ERRORS.get(str(error), "Откройте задания заново."), TABS)

        @router.message(F.text, lambda message: self.allowed(message.from_user.id)
                        and self.store.bonus_session(message.from_user.id) is not None
                        and not (message.text or "").startswith("/")
                        and message.text not in self.navigation)
        async def explain(message):
            await message.answer("В этом задании пришлите фото или используйте загруженное. "
                                 "Работа засчитывается после создания и доставки, а не по текстовому сообщению.",
                                 parse_mode=None)

    navigation = set()
