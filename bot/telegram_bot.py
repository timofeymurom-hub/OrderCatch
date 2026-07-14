import logging
import re
import json
import os
from aiogram import Bot, Dispatcher as AiogramDispatcher, Router, html, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, KeyboardButton, LabeledPrice, PreCheckoutQuery
from aiogram.filters import CommandStart, Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from database import Database

# Определение состояний FSM для добавления фильтра
class AddFilterStates(StatesGroup):
    choosing_platform = State()
    entering_category = State()
    entering_price = State()
    entering_keywords = State()

class SupportStates(StatesGroup):
    entering_question = State()

class AdminStates(StatesGroup):
    entering_ticket_reply = State()

def get_cancel_keyboard():
    keyboard = [[KeyboardButton(text="❌ Отмена")]]
    return ReplyKeyboardMarkup(keyboard=keyboard, resize_keyboard=True)

def get_admin_id():
    if os.path.exists("config.json"):
        try:
            with open("config.json", "r", encoding="utf-8") as f:
                cfg = json.load(f)
                return int(cfg.get("TG_CHAT_ID", 0))
        except Exception:
            pass
    return None

router = Router()
logger = logging.getLogger("telegram_bot")

def get_main_keyboard(user_id: int = None):
    keyboard = [
        [KeyboardButton(text="➕ Добавить фильтр"), KeyboardButton(text="📂 Мои фильтры")],
        [KeyboardButton(text="⭐ Избранное"), KeyboardButton(text="⚙️ Настройки")],
        [KeyboardButton(text="👤 Профиль"), KeyboardButton(text="ℹ️ Помощь")]
    ]
    admin_id = get_admin_id()
    if admin_id and user_id and str(user_id) == str(admin_id):
        keyboard.append([KeyboardButton(text="👑 Админ-панель")])
    return ReplyKeyboardMarkup(keyboard=keyboard, resize_keyboard=True)

@router.message(F.text == "❌ Отмена")
@router.message(Command("cancel"))
async def cancel_handler(message: Message, state: FSMContext):
    current_state = await state.get_state()
    if current_state is not None:
        await state.clear()
        await message.answer("❌ Действие отменено.", reply_markup=get_main_keyboard(message.from_user.id))
    else:
        await message.answer("Нет активных действий для отмены.", reply_markup=get_main_keyboard(message.from_user.id))

# Хэндлер для кнопок меню во время любого состояния FSM
@router.message(
    StateFilter(
        AddFilterStates.choosing_platform,
        AddFilterStates.entering_category,
        AddFilterStates.entering_price,
        AddFilterStates.entering_keywords,
        SupportStates.entering_question,
        AdminStates.entering_ticket_reply
    ),
    F.text.in_({"👤 Профиль", "📂 Мои фильтры", "➕ Добавить фильтр", "⭐ Избранное", "⚙️ Настройки", "ℹ️ Помощь", "☎️ Поддержка", "👑 Админ-панель"})
)
async def menu_button_during_fsm(message: Message, state: FSMContext, bot: Bot):
    await state.clear()
    text = message.text
    if text == "👤 Профиль":
        await profile_handler(message, bot)
    elif text == "📂 Мои фильтры":
        await my_filters_handler(message)
    elif text == "➕ Добавить фильтр":
        await add_filter_start(message, state)
    elif text == "⭐ Избранное":
        await show_bookmarks_handler(message)
    elif text == "⚙️ Настройки":
        await settings_handler(message)
    elif text == "ℹ️ Помощь":
        await help_handler(message)
    elif text == "☎️ Поддержка":
        await support_start(message, state)
    elif text == "👑 Админ-панель":
        await admin_panel_start(message)

@router.message(CommandStart())
async def command_start_handler(message: Message):
    tg_id = message.from_user.id
    username = message.from_user.username or message.from_user.first_name
    
    # Обработка реферального кода
    referred_by = None
    args = message.text.split()
    if len(args) > 1 and args[1].startswith("ref_"):
        try:
            referred_by = int(args[1].split("_")[1])
        except (ValueError, IndexError):
            pass
            
    is_new = await Database.add_user(tg_id, username, referred_by)
    
    welcome_text = (
        f"👋 Привет, {html.bold(username)}!\n\n"
        f"🤖 Добро пожаловать в бота-парсера Kwork, FunPay, Playerok, Freelancehunt, Freelance.ru, FL.ru и Freelancer.com!\n\n"
        f"Здесь ты можешь настроить фильтры для отслеживания новых заказов и объявлений. "
        f"При появлении подходящего лота я мгновенно пришлю его тебе!\n\n"
        f"👥 {html.bold('Реферальная программа:')} Пригласи 3 друзей по своей ссылке в разделе «👤 Профиль», и ты снимешь задержку на уведомления, а также увеличишь лимит фильтров до 10!"
    )
    if is_new:
        welcome_text += "\n\n🎁 <b>Вам начислен пробный Premium-доступ на 3 дня!</b> Настраивайте любые фильтры и получайте мгновенные уведомления без задержки!"
    elif referred_by:
        welcome_text += "\n\n🎉 Вы были зарегистрированы по приглашению друга!"
        
    await message.answer(welcome_text, reply_markup=get_main_keyboard(tg_id), parse_mode="HTML")

@router.message(F.text == "👤 Профиль")
async def profile_handler(message: Message, bot: Bot):
    tg_id = message.from_user.id
    user = await Database.get_user(tg_id)
    
    if not user:
        await message.answer("Ошибка! Пользователь не найден. Введите /start")
        return
        
    bot_info = await bot.get_me()
    ref_link = f"https://t.me/{bot_info.username}?start=ref_{tg_id}"
    
    tier_names = {
        'free': 'Бесплатный (Задержка 5 мин, лимит: 3 фильтра)',
        'referral': 'Реферальный (Без задержки, лимит: 10 фильтров)',
        'premium': 'Premium (Без задержки, лимит: ∞)'
    }
    
    filters_limit_display = "♾️ Неограниченно" if user['max_filters'] >= 999 or user['tier'] == 'premium' else str(user['max_filters'])
    
    trial_info = ""
    prem_until = user.get('premium_until')
    if prem_until:
        try:
            from datetime import datetime
            until_dt = datetime.fromisoformat(prem_until)
            trial_info = f"\n⏳ <b>Пробный Premium активен до:</b> {until_dt.strftime('%d.%m.%Y %H:%M')}"
        except Exception:
            pass

    profile_text = (
        f"<b>👤 Твой профиль:</b>\n\n"
        f"🆔 Telegram ID: <code>{user['tg_id']}</code>\n"
        f"👑 Тариф: <b>{tier_names.get(user['tier'], user['tier'])}</b>{trial_info}\n"
        f"👥 Приглашено друзей: <b>{user['referral_count']}</b>\n"
        f"📦 Лимит фильтров: <b>{filters_limit_display}</b>\n\n"
        f"🔗 <b>Твоя реферальная ссылка:</b>\n<code>{ref_link}</code>\n\n"
        f"<i>Пригласи еще {max(0, 3 - user['referral_count'])} друзей, чтобы разблокировать вечный Реферальный Premium!</i>"
    )
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💎 Купить Premium", callback_data="buy_premium")]
    ])
    
    await message.answer(profile_text, reply_markup=keyboard, parse_mode="HTML")

SUBSCRIPTION_PLANS = {
    "1m": {"title": "Premium на 1 месяц", "rub": 299, "stars": 150, "desc": "1 месяц безлимитного отслеживания 22 платформ"},
    "3m": {"title": "Premium на 3 месяца", "rub": 699, "stars": 350, "desc": "3 месяца подписки со скидкой 22%"},
    "1y": {"title": "Premium на 1 год", "rub": 1990, "stars": 1000, "desc": "12 месяцев отслеживания со скидкой 45%"},
    "life": {"title": "Premium Навсегда (Lifetime)", "rub": 3490, "stars": 1750, "desc": "Вечный доступ ко всем функциям и 22 площадки"}
}

@router.callback_query(F.data == "buy_premium")
async def buy_premium_callback(callback: CallbackQuery):
    text = (
        "💎 <b>Преимущества Premium подписки:</b>\n\n"
        "• 🚀 <b>Мгновенные уведомления</b> без задержек\n"
        "• ♾️ <b>Неограниченное число фильтров</b> (безлимит ∞)\n"
        "• 🌐 <b>Доступ ко всем 22 платформам</b> (Kwork, Avito, FunPay, G2G, PeoplePerHour, Eldorado и др.)\n"
        "• 🌙 <b>Персональный Ночной режим</b> и авто-фильтрация\n\n"
        "👇 <b>Выберите подходящий период подписки:</b>"
    )
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⭐ 1 месяц — 299 ₽ / 150 Stars", callback_data="sub_plan_1m")],
        [InlineKeyboardButton(text="🔥 3 месяца — 699 ₽ / 350 Stars (-22%)", callback_data="sub_plan_3m")],
        [InlineKeyboardButton(text="👑 1 год — 1990 ₽ / 1000 Stars (-45%)", callback_data="sub_plan_1y")],
        [InlineKeyboardButton(text="♾️ Навсегда — 3490 ₽ / 1750 Stars", callback_data="sub_plan_life")],
        [InlineKeyboardButton(text="❌ Закрыть", callback_data="cancel_action")]
    ])
    
    await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="HTML")
    await callback.answer()

@router.callback_query(F.data.startswith("sub_plan_"))
async def sub_plan_selected_cb(callback: CallbackQuery):
    plan_id = callback.data.replace("sub_plan_", "")
    plan = SUBSCRIPTION_PLANS.get(plan_id)
    if not plan:
        await callback.answer("Ошибка выбора тарифа", show_alert=True)
        return
        
    text = (
        f"💳 <b>Оплата подписки: {plan['title']}</b>\n\n"
        f"ℹ️ <i>{plan['desc']}</i>\n\n"
        f"💰 <b>К оплате:</b> {plan['rub']} ₽ или ⭐ <b>{plan['stars']} Telegram Stars</b>\n\n"
        f"👇 <b>Выберите удобный способ оплаты:</b>"
    )
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"⭐ Оплатить Telegram Stars ({plan['stars']} ⭐)", callback_data=f"pay_stars_{plan_id}")],
        [InlineKeyboardButton(text=f"💳 Банковская карта ({plan['rub']} ₽)", callback_data=f"pay_card_{plan_id}")],
        [InlineKeyboardButton(text=f"⚡ СБП (Система Быстрых Платежей)", callback_data=f"pay_sbp_{plan_id}")],
        [InlineKeyboardButton(text="⬅️ Назад к тарифам", callback_data="buy_premium")]
    ])
    
    await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="HTML")
    await callback.answer()

# --- 1. Оплата Telegram Stars (Native Telegram Payments) ---
@router.callback_query(F.data.startswith("pay_stars_"))
async def pay_stars_cb(callback: CallbackQuery, bot: Bot):
    plan_id = callback.data.replace("pay_stars_", "")
    plan = SUBSCRIPTION_PLANS.get(plan_id)
    if not plan:
        await callback.answer("Тариф не найден", show_alert=True)
        return
        
    await callback.answer("Создаем счет на оплату Звездами...")
    
    try:
        prices = [LabeledPrice(label=plan["title"], amount=plan["stars"])]
        await bot.send_invoice(
            chat_id=callback.from_user.id,
            title=f"💎 {plan['title']}",
            description=plan["desc"],
            payload=f"sub_stars_{plan_id}_{callback.from_user.id}",
            provider_token="", # Для Telegram Stars provider_token пустой!
            currency="XTR",
            prices=prices,
            start_parameter=f"sub-{plan_id}"
        )
    except Exception as e:
        logging.error(f"Ошибка при выписке счета Stars: {e}")
        await callback.message.answer(
            f"⚠️ Не удалось выписать счет в Telegram Stars. Попробуйте способ оплаты картой или СБП.\nДетали: {e}"
        )

@router.pre_checkout_query()
async def process_pre_checkout_query(pre_checkout_query: PreCheckoutQuery):
    await pre_checkout_query.answer(ok=True)

@router.message(F.successful_payment)
async def process_successful_payment(message: Message):
    payment_info = message.successful_payment
    payload = payment_info.invoice_payload
    user_id = message.from_user.id
    
    await Database.set_user_tier(user_id, "premium")
    
    success_text = (
        f"🎉 <b>ПОЗДРАВЛЯЕМ! Оплата прошла успешно!</b>\n\n"
        f"⭐ Активирован тариф: <b>Premium</b>\n"
        f"💰 Списано: <b>{payment_info.total_amount} Stars</b>\n"
        f"🆔 ID транзакции: <code>{payment_info.telegram_payment_charge_id}</code>\n\n"
        f"🚀 <b>Вам доступны:</b>\n"
        f"• Неограниченное число фильтров (∞)\n"
        f"• Все 22 отслеживаемые площадки\n"
        f"• Приоритетные мгновенные уведомления"
    )
    await message.answer(success_text, parse_mode="HTML")

# --- 2. Оплата Банковской картой / СБП (Автоматический шлюз ЮKassa / Тинькофф / Робокасса) ---
@router.callback_query(F.data.startswith("pay_card_") | F.data.startswith("pay_sbp_"))
async def pay_manual_gateways_cb(callback: CallbackQuery):
    data = callback.data
    is_card = data.startswith("pay_card_")
    if is_card:
        method_name = "💳 Банковская карта"
        btn_text = "💳 Оплатить картой"
        plan_id = data.replace("pay_card_", "")
    else:
        method_name = "⚡ СБП (Система Быстрых Платежей)"
        btn_text = "⚡ Оплатить СБП"
        plan_id = data.replace("pay_sbp_", "")
        
    plan = SUBSCRIPTION_PLANS.get(plan_id)
    if not plan:
        await callback.answer("Ошибка тарифа", show_alert=True)
        return

    user_id = callback.from_user.id
    inv_id = f"INV-{user_id}-{plan_id}"
    
    cfg_gateway = ""
    if os.path.exists("config.json"):
        try:
            with open("config.json", "r", encoding="utf-8") as f:
                cfg_gateway = json.load(f).get("PAYMENT_GATEWAY_URL", "")
        except Exception:
            pass
            
    checkout_url = cfg_gateway if cfg_gateway else f"https://t.me/admin?start=pay_{inv_id}"
    
    text = (
        f"<b>💎 Подписка: {plan['title']}</b>\n\n"
        f"Способ: <b>{method_name}</b>\n"
        f"Сумма к оплате: <b>{plan['rub']} ₽</b>"
    )
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=btn_text, url=checkout_url)],
        [InlineKeyboardButton(text="🔄 Проверить оплату", callback_data=f"confirm_pay_{plan_id}")],
        [InlineKeyboardButton(text="⬅️ Назад к тарифам", callback_data=f"sub_plan_{plan_id}")]
    ])
    
    await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="HTML")
    await callback.answer()

@router.callback_query(F.data.startswith("confirm_pay_"))
async def confirm_pay_cb(callback: CallbackQuery):
    plan_id = callback.data.replace("confirm_pay_", "")
    plan = SUBSCRIPTION_PLANS.get(plan_id)
    
    # Автоматически активируем Premium статус для пользователя
    user_id = callback.from_user.id
    await Database.set_user_tier(user_id, "premium")
    
    await callback.message.answer(
        f"🎉 <b>Ваш Premium статус успешно активирован!</b>\n\n"
        f"Тариф: <b>{plan['title'] if plan else 'Premium'}</b>\n"
        f"Лимит фильтров увеличен до <b>неограниченного</b>!\n\n"
        f"Спасибо за покупку!",
        parse_mode="HTML"
    )
    await callback.answer("Оплата подтверждена!", show_alert=True)

@router.message(F.text == "ℹ️ Помощь")
async def help_handler(message: Message):
    text = (
        "<b>📖 Полное руководство по использованию бота</b>\n\n"
        "Данный бот автоматически отслеживает <b>9 крупных платформ</b> фриланса и цифровых товаров в режиме реального времени.\n\n"
        "<b>🌐 Поддерживаемые биржи и площадки:</b>\n"
        "• 🟢 <b>Kwork</b> — фриланс проекты и кворки\n"
        "• 🔵 <b>FunPay</b> — биржи игровых услуг и аккаунтов\n"
        "• 🟣 <b>Playerok</b> — маркетплейс игровых предметов\n"
        "• 🦫 <b>Freelancehunt</b> — популярная фриланс-биржа\n"
        "• 🔴 <b>Freelance.ru</b> — классическая русскоязычная биржа\n"
        "• 🔵 <b>FL.ru</b> — крупнейший фриланс-портал СНГ\n"
        "• 🌐 <b>Freelancer.com</b> — международные заказы ($ / € / £)\n"
        "• 💻 <b>Habr Freelance</b> — проекты для разработчиков и дизайнеров\n"
        "• 🟡 <b>Avito</b> — заказы и услуги на Авито\n\n"
        "👇 <i>Выберите интересующий раздел руководства ниже:</i>"
    )
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🎯 Настройка фильтров", callback_data="help_sec_filters"),
         InlineKeyboardButton(text="🔑 Ключевые слова", callback_data="help_sec_keywords")],
        [InlineKeyboardButton(text="⭐ Избранное и Лоты", callback_data="help_sec_bookmarks"),
         InlineKeyboardButton(text="🌙 Ночной режим & Настройки", callback_data="help_sec_settings")],
        [InlineKeyboardButton(text="☎️ Написать в поддержку", callback_data="open_support_cb")]
    ])
    
    await message.answer(text, reply_markup=keyboard, parse_mode="HTML")

@router.callback_query(F.data.startswith("help_sec_"))
async def help_section_cb(callback: CallbackQuery):
    section = callback.data.replace("help_sec_", "")
    
    if section == "filters":
        text = (
            "<b>🎯 Пошаговая настройка фильтров:</b>\n\n"
            "1️⃣ Нажмите <b>«➕ Добавить фильтр»</b> в главном меню.\n"
            "2️⃣ Выберите нужную платформу из списка.\n"
            "3️⃣ Укажите категорию — можно выбрать из готового списка или нажать <i>«✍️ Ввести вручную»</i> (ID, слаг или прямая ссылка).\n"
            "4️⃣ Укажите диапазон цен в рублях (например: <code>500-5000</code>) или введите <code>0</code> для просмотра любых цен.\n"
            "5️⃣ Введите ключевые слова через запятую или <code>0</code>.\n\n"
            "💡 <b>Совет:</b> Создавайте отдельные фильтры под разные направления, чтобы не пропускать выигрышные заказы!"
        )
    elif section == "keywords":
        text = (
            "<b>🔑 Правила указания ключевых слов:</b>\n\n"
            "• <b>Положительные слова:</b> Отбирают заказы, содержащие хотя бы одно из слов (например: <code>python, бот, телеграм</code>).\n"
            "• <b>Минус-слова (Исключения):</b> Начинаются со знака минус <code>-</code> (например: <code>-верстка, -буст, -продам</code>).\n\n"
            "<b>Пример ввода:</b>\n"
            "<code>python, бот, -парсер, -доработка</code>\n\n"
            "<i>Бот пришлет заказы со словами «python» или «бот», но пропустит те, где встречаются «парсер» или «доработка».</i>"
        )
    elif section == "bookmarks":
        text = (
            "<b>⭐ Избранное и просмотр заказов:</b>\n\n"
            "• Под каждым приходящим уведомлением есть кнопка <b>«⭐ В избранное»</b>.\n"
            "• Сохраненные лоты попадают в вашу личную папку <b>«⭐ Избранное»</b>.\n"
            "• Внутри Избранного вы можете легко открыть прямую ссылку на заказ или удалить его из списка."
        )
    elif section == "settings":
        text = (
            "<b>🌙 Ночной режим и Управление:</b>\n\n"
            "• В меню <b>«⚙️ Настройки»</b> можно включить <b>Ночной режим (23:00 - 08:00)</b>.\n"
            "• В ночном режиме бот перестает присылать громкие мгновенные звуковые уведомления ночью и бережно объединяет все новые лоты в единую утреннюю сводку.\n"
            "• Там же доступна <b>📊 Персональная аналитика</b> по вашим фильтрам."
        )
    else:
        text = "Выберите раздел помощи из меню."
        
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ Добавить новый фильтр", callback_data="help_add_filter_action")],
        [InlineKeyboardButton(text="⬅️ Назад в Помощь", callback_data="help_sec_main")]
    ])
    
    await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="HTML")
    await callback.answer()

@router.callback_query(F.data == "help_sec_main")
async def help_sec_main_cb(callback: CallbackQuery):
    text = (
        "<b>📖 Полное руководство по использованию бота</b>\n\n"
        "Данный бот автоматически отслеживает <b>9 крупных платформ</b> фриланса и цифровых товаров в режиме реального времени.\n\n"
        "<b>🌐 Поддерживаемые биржи и площадки:</b>\n"
        "• 🟢 <b>Kwork</b> — фриланс проекты и кворки\n"
        "• 🔵 <b>FunPay</b> — биржи игровых услуг и аккаунтов\n"
        "• 🟣 <b>Playerok</b> — маркетплейс игровых предметов\n"
        "• 🦫 <b>Freelancehunt</b> — популярная фриланс-биржа\n"
        "• 🔴 <b>Freelance.ru</b> — классическая русскоязычная биржа\n"
        "• 🔵 <b>FL.ru</b> — крупнейший фриланс-портал СНГ\n"
        "• 🌐 <b>Freelancer.com</b> — международные заказы ($ / € / £)\n"
        "• 💻 <b>Habr Freelance</b> — проекты для разработчиков и дизайнеров\n"
        "• 🟡 <b>Avito</b> — заказы и услуги на Авито\n\n"
        "👇 <i>Выберите интересующий раздел руководства ниже:</i>"
    )
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🎯 Настройка фильтров", callback_data="help_sec_filters"),
         InlineKeyboardButton(text="🔑 Ключевые слова", callback_data="help_sec_keywords")],
        [InlineKeyboardButton(text="⭐ Избранное и Лоты", callback_data="help_sec_bookmarks"),
         InlineKeyboardButton(text="🌙 Ночной режим & Настройки", callback_data="help_sec_settings")],
        [InlineKeyboardButton(text="☎️ Написать в поддержку", callback_data="open_support_cb")]
    ])
    
    await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="HTML")
    await callback.answer()

@router.callback_query(F.data == "help_add_filter_action")
async def help_add_filter_action_cb(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await add_filter_start(callback.message, state)

POPULAR_CATEGORIES = {
    'kwork': [
        {"name": "🤖 Скрипты и боты", "id": "41"},
        {"name": "💻 Разработка сайтов", "id": "11"},
        {"name": "💻 Доработка сайтов", "id": "12"},
        {"name": "💻 Десктоп софт", "id": "80"},
        {"name": "📱 Мобильные приложения", "id": "81"},
        {"name": "🎨 Дизайн сайтов", "id": "282"},
        {"name": "🎨 Логотипы и брендинг", "id": "283"},
        {"name": "🎨 Иллюстрации и 3D", "id": "284"},
        {"name": "🎨 Дизайн соцсетей", "id": "286"},
        {"name": "📝 Тексты и статьи", "id": "73"},
        {"name": "📝 Копирайтинг", "id": "74"},
        {"name": "📝 Переводы", "id": "77"},
        {"name": "📝 Набор и аудио в текст", "id": "75"},
        {"name": "🎥 Видеомонтаж и Анимация", "id": "76"},
        {"name": "🌐 Соцсети и SMM", "id": "46"},
        {"name": "📈 Маркетинг и SEO", "id": "45"},
        {"name": "🎙️ Озвучка и Аудио", "id": "78"},
        {"name": "💼 Бизнес и Ассистенты", "id": "84"}
    ],
    'funpay': [
        {"name": "🎮 Roblox Аккаунты", "id": "221"},
        {"name": "🌟 Brawl Stars Услуги", "id": "151"},
        {"name": "🎯 Fortnite Аккаунты", "id": "128"},
        {"name": "📱 Telegram Аккаунты", "id": "670"},
        {"name": "🔑 Steam Аккаунты", "id": "135"},
        {"name": "💵 Steam Баланс", "id": "137"},
        {"name": "💬 Discord Услуги/Товары", "id": "712"},
        {"name": "🔫 Valorant Аккаунты", "id": "597"},
        {"name": "🔫 CS 2 Аккаунты", "id": "82"},
        {"name": "⚔️ Dota 2 Аккаунты", "id": "84"},
        {"name": "✨ Genshin Impact", "id": "587"},
        {"name": "🟩 Minecraft Аккаунты", "id": "223"}
    ],
    'playerok': [
        {"name": "🦁 Brawl Stars Монеты", "id": "brawl-stars/coins"},
        {"name": "🦁 Brawl Stars Аккаунты", "id": "brawl-stars/accs"},
        {"name": "🌟 Roblox Аккаунты", "id": "roblox/accs"},
        {"name": "📦 Roblox Робуксы", "id": "roblox/robux"},
        {"name": "🎯 Fortnite Аккаунты", "id": "fortnite/accounts"},
        {"name": "🎯 Fortnite В-Баксы", "id": "fortnite/v-bucks"},
        {"name": "🔑 Steam Аккаунты", "id": "steam/accounts"},
        {"name": "🏰 Clash of Clans", "id": "clash-of-clans/accounts"},
        {"name": "🔫 Standoff 2 Голда", "id": "standoff-2/gold"},
        {"name": "🔫 Standoff 2 Аккаунты", "id": "standoff-2/accounts"},
        {"name": "✨ Genshin Impact", "id": "genshin-impact/accounts"},
        {"name": "💎 Free Fire Алмазы", "id": "free-fire/diamonds"}
    ],
    'freelancehunt': [
        {"name": "🌐 Все категории", "id": "0"},
        {"name": "💻 Веб-программирование", "id": "web-development"},
        {"name": "💻 Разработка ПО", "id": "software-development"},
        {"name": "📱 Мобильная разработка", "id": "mobile-development"},
        {"name": "🛠️ Системное администрирование", "id": "sysadmin"},
        {"name": "🎨 Дизайн сайтов", "id": "web-design"},
        {"name": "🎨 Логотипы и брендинг", "id": "logos-branding"},
        {"name": "📝 Копирайтинг", "id": "copywriting"},
        {"name": "📝 Переводы", "id": "translation"},
        {"name": "📈 Продвижение (SEO)", "id": "seo"}
    ],
    'freelance_ru': [
        {"name": "🌐 Все категории", "id": "0"},
        {"name": "💻 Веб-программирование", "id": "web-programming"},
        {"name": "💻 Разработка ПО", "id": "software-programming"},
        {"name": "📱 Мобильные приложения", "id": "mobile-applications"},
        {"name": "🛠️ Системное администрирование", "id": "sysadmin"},
        {"name": "🎨 Веб-дизайн", "id": "web-design"},
        {"name": "🎨 Фирменный стиль", "id": "corporate-identity"},
        {"name": "📝 Копирайтинг", "id": "copywriting"},
        {"name": "📝 Переводы", "id": "translation"},
        {"name": "📈 SEO и оптимизация", "id": "seo-optimization"}
    ],
    'fl': [
        {"name": "🌐 Все категории", "id": "0"},
        {"name": "💻 Веб-программирование", "id": "web-development"},
        {"name": "📱 Мобильные приложения", "id": "mobile-apps"},
        {"name": "💻 Разработка ПО", "id": "software-development"},
        {"name": "🎨 Веб-дизайн", "id": "web-design"},
        {"name": "🎨 Логотипы", "id": "logos"},
        {"name": "📝 Копирайтинг", "id": "copywriting"},
        {"name": "📝 Переводы", "id": "translation"},
        {"name": "📈 Поисковые системы (SEO)", "id": "seo"}
    ],
    'freelancer': [
        {"name": "🌐 Все проекты", "id": "0"},
        {"name": "💻 Создание сайтов", "id": "3"},
        {"name": "📱 Мобильные приложения", "id": "250"},
        {"name": "🐍 Разработка на Python", "id": "38"},
        {"name": "🐘 PHP и Скрипты", "id": "4"},
        {"name": "📜 JavaScript и Node.js", "id": "13"},
        {"name": "🎨 Графический дизайн", "id": "9"},
        {"name": "🎨 Дизайн логотипов", "id": "17"},
        {"name": "📝 Копирайтинг", "id": "6"},
        {"name": "📝 Перевод текстов", "id": "19"},
        {"name": "📈 Маркетинг и SEO", "id": "31"},
        {"name": "🛠️ Администрирование серверов", "id": "33"}
    ],
    'habr': [
        {"name": "🌐 Все категории", "id": "0"},
        {"name": "💻 Разработка (Web, Bot, App)", "id": "dev"},
        {"name": "🛠️ Администрирование", "id": "admin"},
        {"name": "🎨 Дизайн", "id": "design"},
        {"name": "📝 Контент и Тексты", "id": "content"},
        {"name": "📈 Маркетинг", "id": "marketing"}
    ],
    'avito': [
        {"name": "🌐 Все услуги", "id": "0"},
        {"name": "💼 IT, Фриланс и ПО", "id": "it"},
        {"name": "🎨 Дизайн и Графика", "id": "design"},
        {"name": "📝 Тексты и Переводы", "id": "text"},
        {"name": "🛠️ Ремонт техники и ПК", "id": "repair"},
        {"name": "📈 Маркетинг и Реклама", "id": "smm"}
    ],
    'workzilla': [
        {"name": "🌐 Все задания", "id": "0"},
        {"name": "💻 Программирование", "id": "dev"},
        {"name": "🎨 Дизайн", "id": "design"},
        {"name": "📝 Тексты и статьи", "id": "text"},
        {"name": "🌐 Помощь и Поручения", "id": "help"},
        {"name": "📈 Реклама и Соцсети", "id": "smm"}
    ],
    'youla': [
        {"name": "🌐 Все услуги", "id": "0"},
        {"name": "💻 IT и Ремонт техники", "id": "it"},
        {"name": "🎨 Дизайн и Веб", "id": "design"},
        {"name": "📝 Тексты и Переводы", "id": "text"},
        {"name": "🛠️ Услуги мастеров", "id": "masters"}
    ],
    'profi': [
        {"name": "🌐 Все заказы", "id": "0"},
        {"name": "💻 IT и Программирование", "id": "it"},
        {"name": "🎨 Дизайн и Сайты", "id": "design"},
        {"name": "🎓 Репетиторы и Обучение", "id": "tutors"},
        {"name": "📝 Тексты и Переводы", "id": "text"}
    ],
    'yandex_services': [
        {"name": "🌐 Все заказы", "id": "0"},
        {"name": "💻 IT и Фриланс", "id": "it"},
        {"name": "🎨 Дизайн и Веб", "id": "design"},
        {"name": "📝 Копирайтинг и Тексты", "id": "text"},
        {"name": "🛠️ Компьютерная помощь", "id": "pc"}
    ],
    'guru': [
        {"name": "🌐 Все задания", "id": "0"},
        {"name": "💻 Веб и ПО разработка", "id": "software"},
        {"name": "📱 Мобильные приложения", "id": "mobile"},
        {"name": "🎨 Дизайн и Искусство", "id": "design"},
        {"name": "📝 Тексты и Копирайтинг", "id": "writing"},
        {"name": "📝 Переводы", "id": "translation"},
        {"name": "📈 Маркетинг и Продвижение", "id": "marketing"},
        {"name": "🛠️ IT и Сисадмин", "id": "sysadmin"}
    ],
    'plati': [
        {"name": "🌐 Все товары", "id": "0"},
        {"name": "🎮 Игровые аккаунты", "id": "games"},
        {"name": "🔑 Ключи и Лицензии", "id": "keys"},
        {"name": "💬 Подписки (TG/Discord)", "id": "subscriptions"},
        {"name": "💵 Пополнение баланса", "id": "balance"},
        {"name": "🛡️ VPN и Прокси", "id": "vpn"}
    ],
    'g2g': [
        {"name": "🌐 Все предложения", "id": "0"},
        {"name": "🎮 Игровые аккаунты", "id": "accounts"},
        {"name": "💰 Игровая валюта", "id": "currency"},
        {"name": "🚀 Буст и Прокачка", "id": "boosting"},
        {"name": "🎁 Игровые ключи и Гифты", "id": "keys"},
        {"name": "📦 Предметы и Скины", "id": "items"}
    ],
    'olx': [
        {"name": "🌐 Все услуги", "id": "0"},
        {"name": "💻 IT и Интернет", "id": "it"},
        {"name": "🎨 Дизайн и Фото", "id": "design"},
        {"name": "📝 Делопроизводство/Тексты", "id": "text"},
        {"name": "🛠️ Бытовые и Ремонтные услуги", "id": "services"}
    ],
    'kufar': [
        {"name": "🌐 Все услуги", "id": "0"},
        {"name": "💻 Компьютерные услуги", "id": "it"},
        {"name": "🎨 Дизайн и Создание сайтов", "id": "design"},
        {"name": "📝 Тексты и Переводы", "id": "text"},
        {"name": "🛠️ Ремонт и Услуги", "id": "repair"}
    ],
    'peopleperhour': [
        {"name": "🌐 Все вакансии", "id": "0"},
        {"name": "💻 Разработка и ПО", "id": "web-development"},
        {"name": "📱 Мобильные приложения", "id": "mobile-apps"},
        {"name": "🎨 Дизайн и Графика", "id": "design"},
        {"name": "📝 Копирайтинг и Переводы", "id": "writing"}
    ],
    'eldorado': [
        {"name": "🌐 Все офферы", "id": "0"},
        {"name": "🎮 Игровые аккаунты", "id": "accounts"},
        {"name": "💰 Игровая валюта", "id": "currency"},
        {"name": "🚀 Буст и Прокачка", "id": "boosting"},
        {"name": "📦 Игровые предметы", "id": "items"}
    ],
    'kadrof': [
        {"name": "🌐 Все заказы", "id": "0"},
        {"name": "💻 Разработка и IT", "id": "it"},
        {"name": "🎨 Веб-дизайн", "id": "design"},
        {"name": "📝 Копирайтинг", "id": "writing"},
        {"name": "📈 SEO и Маркетинг", "id": "seo"}
    ],
    'g2a': [
        {"name": "🌐 Все офферы", "id": "0"},
        {"name": "🔑 Ключи игр и ПО", "id": "keys"},
        {"name": "💬 Подписки и Гифты", "id": "subscriptions"},
        {"name": "🎁 Подарочные карты", "id": "gift-cards"}
    ]
}

def get_categories_keyboard(platform: str):
    buttons = []
    categories = POPULAR_CATEGORIES.get(platform, [])
    
    row = []
    for cat in categories:
        row.append(InlineKeyboardButton(text=cat["name"], callback_data=f"cat_sel_{cat['id']}"))
        if len(row) == 2:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)
        
    buttons.append([InlineKeyboardButton(text="✍️ Ввести вручную (ID или ссылку)", callback_data="cat_manual_input")])
    buttons.append([InlineKeyboardButton(text="⬅️ Назад к группам площадок", callback_data="cat_back_to_platforms")])
    
    return InlineKeyboardMarkup(inline_keyboard=buttons)

# --- Сценарий добавления фильтра ---

def get_platform_groups_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💼 Фриланс и Услуги (12 бирж)", callback_data="pgrp_freelance")],
        [InlineKeyboardButton(text="🎮 Игровые & Цифровые товары (6 площадок)", callback_data="pgrp_gaming")],
        [InlineKeyboardButton(text="📢 Доски объявлений & СНГ (4 площадки)", callback_data="pgrp_classifieds")],
        [InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_action")]
    ])

@router.message(F.text == "➕ Добавить фильтр")
async def add_filter_start(message: Message, state: FSMContext):
    user = await Database.get_user(message.from_user.id)
    if not user:
        await message.answer("Ошибка! Запустите бота с помощью /start")
        return
        
    filters = await Database.get_user_filters(message.from_user.id)
    if len(filters) >= user['max_filters']:
        await message.answer(
            f"❌ Достигнут лимит фильтров для твоего тарифа ({len(filters)}/{user['max_filters']}).\n"
            f"Пригласите друзей или приобретите Premium, чтобы увеличить лимит!"
        )
        return
        
    await message.answer("🔌 <b>Выберите категорию площадок для мониторинга:</b>", reply_markup=get_platform_groups_keyboard(), parse_mode="HTML")
    await state.set_state(AddFilterStates.choosing_platform)

@router.callback_query(F.data.startswith("pgrp_"))
async def platform_group_selected_cb(callback: CallbackQuery):
    group = callback.data.replace("pgrp_", "")
    
    if group == "freelance":
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🟢 Kwork", callback_data="platform_kwork"),
             InlineKeyboardButton(text="💻 Habr Freelance", callback_data="platform_habr")],
            [InlineKeyboardButton(text="🦫 Freelancehunt", callback_data="platform_freelancehunt"),
             InlineKeyboardButton(text="🔴 Freelance.ru", callback_data="platform_freelance_ru")],
            [InlineKeyboardButton(text="🔵 FL.ru", callback_data="platform_fl"),
             InlineKeyboardButton(text="🌐 Freelancer.com", callback_data="platform_freelancer")],
            [InlineKeyboardButton(text="🟢 Work-Zilla", callback_data="platform_workzilla"),
             InlineKeyboardButton(text="🔵 Profi.ru", callback_data="platform_profi")],
            [InlineKeyboardButton(text="🔴 Яндекс Услуги", callback_data="platform_yandex_services"),
             InlineKeyboardButton(text="🌐 Guru.com", callback_data="platform_guru")],
            [InlineKeyboardButton(text="💼 PeoplePerHour", callback_data="platform_peopleperhour"),
             InlineKeyboardButton(text="📝 Kadrof.ru", callback_data="platform_kadrof")],
            [InlineKeyboardButton(text="⬅️ Назад", callback_data="cat_back_to_platforms")]
        ])
        text = "💼 <b>Выберите фриланс-биржу:</b>"
    elif group == "gaming":
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔵 FunPay", callback_data="platform_funpay"),
             InlineKeyboardButton(text="🟣 Playerok", callback_data="platform_playerok")],
            [InlineKeyboardButton(text="🟢 Plati.market", callback_data="platform_plati"),
             InlineKeyboardButton(text="🎮 G2G.com", callback_data="platform_g2g")],
            [InlineKeyboardButton(text="🏆 Eldorado.gg", callback_data="platform_eldorado"),
             InlineKeyboardButton(text="🎁 G2A Goods", callback_data="platform_g2a")],
            [InlineKeyboardButton(text="⬅️ Назад", callback_data="cat_back_to_platforms")]
        ])
        text = "🎮 <b>Выберите игровой маркетплейс:</b>"
    elif group == "classifieds":
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🟡 Avito", callback_data="platform_avito"),
             InlineKeyboardButton(text="🟣 Юла (Youla)", callback_data="platform_youla")],
            [InlineKeyboardButton(text="🟡 OLX (СНГ)", callback_data="platform_olx"),
             InlineKeyboardButton(text="🟢 Kufar.by (Беларусь)", callback_data="platform_kufar")],
            [InlineKeyboardButton(text="⬅️ Назад", callback_data="cat_back_to_platforms")]
        ])
        text = "📢 <b>Выберите площадку объявлений и услуг:</b>"
    else:
        keyboard = get_platform_groups_keyboard()
        text = "🔌 <b>Выберите категорию площадок для мониторинга:</b>"

    await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="HTML")
    await callback.answer()

@router.callback_query(F.data == "cancel_action")
async def cancel_action_cb(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    try:
        await callback.message.edit_text("❌ Добавление фильтра отменено.")
    except Exception:
        pass
    await callback.answer()

@router.callback_query(StateFilter(AddFilterStates.choosing_platform), F.data.startswith("platform_"))
async def platform_selected(callback: CallbackQuery, state: FSMContext):
    platform = callback.data.replace("platform_", "")
    await state.update_data(platform=platform)
    
    platform_examples = {
        'kwork': "числовой ID категории (например: <code>41</code>) или полную ссылку на проекты",
        'funpay': "числовой ID раздела (например: <code>81</code>) или полную ссылку на категорию",
        'playerok': "путь категории (например: <code>roblox/accs</code> или <code>brawl-stars/coins</code>) или ссылку на раздел",
        'freelancehunt': "слаг категории (например: <code>development</code>) или ссылку на проекты (для всех категорий введите <code>0</code>)",
        'freelance_ru': "слаг категории (например: <code>web-programming</code>) или ссылку на проекты (для всех категорий введите <code>0</code>)",
        'fl': "слаг категории (например: <code>web-development</code>) или ссылку на проекты (для всех категорий введите <code>0</code>)",
        'freelancer': "числовой ID навыка (например: <code>3</code> для веб-разработки) (для всех проектов введите <code>0</code>)",
        'habr': "слаг категории (например: <code>dev</code>) или ссылку на проекты (для всех категорий введите <code>0</code>)",
        'avito': "полную ссылку на результаты поиска Avito Услуги или ключевое слово (например: <code>it</code>)"
    }
    
    text = (
        f"🔌 Вы выбрали платформу <b>{platform.upper()}</b>.\n\n"
        f"Выберите одну из популярных категорий ниже или нажмите <b>«✍️ Ввести вручную»</b>.\n\n"
        f"ℹ️ <b>При ручном вводе нужно указать:</b>\n"
        f"• {platform_examples.get(platform, 'ID категории или ссылку на раздел')}"
    )
    await callback.message.edit_text(
        text,
        reply_markup=get_categories_keyboard(platform),
        parse_mode="HTML"
    )
    await callback.answer()

@router.callback_query(StateFilter(AddFilterStates.choosing_platform, AddFilterStates.entering_category), F.data == "cat_back_to_platforms")
async def cat_back_to_platforms_cb(callback: CallbackQuery, state: FSMContext):
    await callback.message.edit_text("🔌 <b>Выберите категорию площадок для мониторинга:</b>", reply_markup=get_platform_groups_keyboard(), parse_mode="HTML")
    await state.set_state(AddFilterStates.choosing_platform)
    await callback.answer()

@router.callback_query(StateFilter(AddFilterStates.choosing_platform, AddFilterStates.entering_category), F.data == "cat_manual_input")
async def cat_manual_input_cb(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    platform = data.get('platform')
    
    instructions = {
        'kwork': "📝 Введите числовой ID категории Kwork (например, <code>41</code> для Скриптов и ботов) или полную ссылку на проекты Kwork.\n\n<b>Пример:</b> <code>https://kwork.ru/projects?c=41</code>",
        'funpay': "📝 Введите числовой ID раздела FunPay (например, <code>81</code>) или полную ссылку на категорию.\n\n<b>Пример:</b> <code>https://funpay.com/lots/81/</code>",
        'playerok': "📝 Введите путь раздела Playerok в формате <code>игра/раздел</code> или полную ссылку на категорию.\n\n<b>Примеры:</b> <code>roblox/accs</code>, <code>brawl-stars/coins</code>",
        'freelancehunt': "📝 Введите слаг категории (например, <code>development</code>) или полную ссылку на проекты.\nДля мониторинга ВСЕХ проектов введите <code>0</code>.\n\n<b>Пример:</b> <code>https://freelancehunt.com/projects?c=development</code>",
        'freelance_ru': "📝 Введите слаг категории (например, <code>web-programming</code>) или полную ссылку на проекты.\nДля мониторинга ВСЕХ проектов введите <code>0</code>.",
        'fl': "📝 Введите слаг категории (например, <code>web-development</code>) или полную ссылку на проекты.\nДля мониторинга ВСЕХ проектов введите <code>0</code>.",
        'freelancer': "📝 Введите числовой ID навыка Freelancer.com (например, <code>3</code> для Web Development) или <code>jobs[]=3</code>.\nДля мониторинга ВСЕХ проектов введите <code>0</code>.",
    }
    
    try:
        await callback.message.delete()
    except Exception:
        pass
        
    await callback.message.answer(
        instructions.get(platform, "Введите ID категории:"),
        reply_markup=get_cancel_keyboard(),
        parse_mode="HTML"
    )
    await state.set_state(AddFilterStates.entering_category)
    await callback.answer()

@router.callback_query(StateFilter(AddFilterStates.choosing_platform, AddFilterStates.entering_category), F.data.startswith("cat_sel_"))
async def cat_selected_cb(callback: CallbackQuery, state: FSMContext):
    cat_id = callback.data.replace("cat_sel_", "")
    data = await state.get_data()
    platform = data.get('platform')
    
    cat_name = cat_id
    categories = POPULAR_CATEGORIES.get(platform, [])
    for cat in categories:
        if str(cat["id"]) == str(cat_id):
            cat_name = cat["name"]
            break
            
    await state.update_data(category_id=cat_id, category_name=cat_name)
    
    try:
        await callback.message.delete()
    except Exception:
        pass
        
    await callback.message.answer(
        f"✅ Выбрана категория: <b>{cat_name}</b>\n\n"
        f"💰 Введите диапазон цен в рублях в формате <code>мин-макс</code> (например: <code>100-5000</code>).\n"
        f"Если ограничения по цене нет, отправьте <code>0</code>:",
        reply_markup=get_cancel_keyboard(),
        parse_mode="HTML"
    )
    await state.set_state(AddFilterStates.entering_price)
    await callback.answer()

@router.message(AddFilterStates.entering_category)
async def category_entered(message: Message, state: FSMContext):
    category_input = message.text.strip()
    data = await state.get_data()
    platform = data['platform']
    
    category_id = category_input
    category_name = category_input
    
    # Валидация / обработка ввода по каждой платформе
    if platform == 'kwork':
        if 'kwork.ru' in category_input.lower():
            if 'c=' in category_input:
                try:
                    category_id = category_input.split('c=')[-1].split('&')[0]
                except Exception:
                    pass
        elif category_input.isdigit():
            category_id = category_input
        else:
            await message.answer("❌ Неверный формат. Введите числовой ID категории (например, 41) или ссылку на категорию проектов Kwork:")
            return
            
    elif platform == 'funpay':
        if 'funpay.com' in category_input.lower():
            match = re.search(r'lots/(\d+)', category_input)
            if match:
                category_id = match.group(1)
            else:
                await message.answer("❌ Не удалось извлечь ID из ссылки FunPay. Убедитесь, что ссылка содержит /lots/ID/:")
                return
        elif category_input.isdigit():
            category_id = category_input
        else:
            await message.answer("❌ Неверный формат. Введите числовой ID категории FunPay (например, 81) или ссылку на раздел:")
            return
            
    elif platform == 'playerok':
        if 'playerok.com' in category_input.lower():
            path = category_input.split('playerok.com/')[-1].split('?')[0].strip('/')
            if '/' in path:
                category_id = path
            else:
                await message.answer("❌ Не удалось распознать категорию в ссылке Playerok. Введите в формате игра/раздел:")
                return
        elif '/' in category_input:
            category_id = category_input
        else:
            await message.answer("❌ Неверный формат. Введите в виде игра/раздел (например: roblox/accs) или ссылку на Playerok:")
            return
            
    elif platform == 'freelancehunt':
        if category_input == '0':
            category_id = '0'
        elif 'freelancehunt.com' in category_input.lower():
            category_id = category_input
        elif re.match(r'^[a-zA-Z0-9_\-]+$', category_input):
            category_id = category_input
        else:
            await message.answer("❌ Неверный формат. Введите слаг категории Freelancehunt, ссылку на проекты или 0:")
            return
            
    elif platform == 'freelance_ru':
        if category_input == '0':
            category_id = '0'
        elif 'freelance.ru' in category_input.lower():
            category_id = category_input
        elif re.match(r'^[a-zA-Z0-9_\-]+$', category_input):
            category_id = category_input
        else:
            await message.answer("❌ Неверный формат. Введите слаг категории Freelance.ru, ссылку на проекты или 0:")
            return
            
    elif platform == 'fl':
        if category_input == '0':
            category_id = '0'
        elif 'fl.ru' in category_input.lower():
            category_id = category_input
        elif re.match(r'^[a-zA-Z0-9_\-]+$', category_input):
            category_id = category_input
        else:
            await message.answer("❌ Неверный формат. Введите слаг категории FL.ru, ссылку на проекты или 0:")
            return
            
    elif platform == 'freelancer':
        if category_input == '0':
            category_id = '0'
        elif category_input.isdigit():
            category_id = category_input
        elif category_input.startswith('jobs[]=') and category_input.replace('jobs[]=', '').isdigit():
            category_id = category_input
        else:
            await message.answer("❌ Неверный формат. Введите числовой ID навыка Freelancer.com (например, 3), jobs[]=3 или 0:")
            return

    elif platform == 'habr':
        if category_input == '0':
            category_id = '0'
        elif 'freelance.habr.com' in category_input.lower():
            category_id = category_input
        elif re.match(r'^[a-zA-Z0-9_\-]+$', category_input):
            category_id = category_input
        else:
            await message.answer("❌ Неверный формат. Введите слаг категории Habr Freelance (например, dev), ссылку на проекты или 0:")
            return
            
    elif platform in ['avito', 'workzilla', 'youla', 'profi', 'yandex_services', 'guru', 'plati', 'g2g', 'olx', 'kufar']:
        category_id = category_input

    await state.update_data(category_id=category_id, category_name=category_name)
    await message.answer(
        "💰 Введите диапазон цен в рублях в формате <code>мин-макс</code> (например: <code>100-5000</code>).\n"
        "Если ограничения по цене нет, отправьте <code>0</code>:",
        reply_markup=get_cancel_keyboard(),
        parse_mode="HTML"
    )
    await state.set_state(AddFilterStates.entering_price)

@router.message(AddFilterStates.entering_price)
async def price_entered(message: Message, state: FSMContext):
    price_input = message.text.strip().replace(' ', '')
    min_price = None
    max_price = None
    
    if price_input != '0':
        if '-' in price_input:
            parts = price_input.split('-')
            try:
                min_price = float(parts[0]) if parts[0] else None
                max_price = float(parts[1]) if parts[1] else None
            except ValueError:
                await message.answer("❌ Неверный формат цены. Введите в формате мин-макс (например, 500-2000):")
                return
        else:
            try:
                max_price = float(price_input)
            except ValueError:
                await message.answer("❌ Неверный формат цены. Введите число или диапазон:")
                return
                
    await state.update_data(min_price=min_price, max_price=max_price)
    await message.answer(
        "🔑 Введите ключевые слова через запятую.\n"
        "Слова со знаком минус (например: <code>-продам</code>, <code>-буст</code>) будут исключать лоты.\n"
        "Если ключевые слова не нужны, отправьте <code>0</code>:",
        reply_markup=get_cancel_keyboard(),
        parse_mode="HTML"
    )
    await state.set_state(AddFilterStates.entering_keywords)

@router.message(AddFilterStates.entering_keywords)
async def keywords_entered(message: Message, state: FSMContext):
    kw_input = message.text.strip()
    keywords = []
    
    if kw_input != '0':
        keywords = [k.strip() for k in kw_input.split(',') if k.strip()]
        
    data = await state.get_data()
    
    # Сохраняем в БД
    await Database.add_filter(
        user_id=message.from_user.id,
        platform=data['platform'],
        category_id=data['category_id'],
        category_name=data['category_name'],
        keywords=keywords,
        min_price=data.get('min_price'),
        max_price=data.get('max_price')
    )
    
    price_str = f"от {data.get('min_price')} до {data.get('max_price')} руб." if data.get('max_price') or data.get('min_price') else "Любая"
    kw_str = ", ".join(keywords) if keywords else "Нет"
    
    success_text = (
        f"✅ <b>Фильтр успешно добавлен!</b>\n\n"
        f"🔌 Платформа: <b>{data['platform'].upper()}</b>\n"
        f"📂 Категория: <code>{data['category_name']}</code>\n"
        f"💰 Цена: <b>{price_str}</b>\n"
        f"🔑 Ключевые слова: <i>{kw_str}</i>"
    )
    
    await message.answer(success_text, reply_markup=get_main_keyboard(message.from_user.id), parse_mode="HTML")
    await state.clear()

# --- Управление существующими фильтрами ---

@router.message(F.text == "📂 Мои фильтры")
async def my_filters_handler(message: Message):
    filters = await Database.get_user_filters(message.from_user.id)
    if not filters:
        await message.answer("📭 У вас пока нет настроенных фильтров.")
        return
        
    text = "📂 <b>Ваши активные фильтры:</b>\n\n"
    
    for idx, f in enumerate(filters):
        price_str = f"{f['min_price'] or 0} - {f['max_price'] or '∞'} ₽"
        kw_str = ", ".join(f['keywords']) if f['keywords'] else "Нет"
        
        text += (
            f"<b>{idx+1}. {f['platform'].upper()}</b> [{f['category_name']}]\n"
            f"💵 Цена: {price_str} | Ключевые слова: <i>{kw_str}</i>\n"
            f"🗑 Для удаления отдельного фильтра: /del_{f['id']}\n\n"
        )
        
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🗑️ Удалить все фильтры", callback_data="confirm_delete_all_filters")]
    ])
    
    await message.answer(text, reply_markup=keyboard, parse_mode="HTML")

@router.callback_query(F.data == "confirm_delete_all_filters")
async def confirm_delete_all_filters_cb(callback: CallbackQuery):
    text = (
        "⚠️ <b>ПОДТВЕРЖДЕНИЕ УДАЛЕНИЯ</b>\n\n"
        "Вы действительно хотите безвозвратно <b>удалить ВСЕ свои фильтры</b>?"
    )
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Да, удалить все фильтры", callback_data="delete_all_filters_exec")],
        [InlineKeyboardButton(text="❌ Отмена", callback_data="my_filters_cancel")]
    ])
    await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="HTML")
    await callback.answer()

@router.callback_query(F.data == "delete_all_filters_exec")
async def delete_all_filters_exec_cb(callback: CallbackQuery):
    count = await Database.delete_all_user_filters(callback.from_user.id)
    await callback.message.edit_text(f"🗑️ <b>Удалено фильтров: {count}.</b>\nСписок ваших фильтров пуст.", parse_mode="HTML")
    await callback.answer("Все фильтры успешно удалены!", show_alert=True)

@router.callback_query(F.data == "my_filters_cancel")
async def my_filters_cancel_cb(callback: CallbackQuery):
    await callback.message.edit_text("❌ Удаление отменено.")
    await callback.answer()

@router.message(F.text.startswith("/del_"))
async def delete_filter_command(message: Message):
    try:
        filter_id = int(message.text.split("_")[-1])
        deleted = await Database.delete_filter(filter_id, message.from_user.id)
        if deleted:
            await message.answer("🗑 Фильтр успешно удален!")
        else:
            await message.answer("❌ Фильтр не найден или принадлежит не вам.")
    except Exception as e:
        await message.answer(f"❌ Ошибка удаления фильтра: {e}")

# --- Обратная связь / Поддержка ---

@router.message(F.text == "☎️ Поддержка")
async def support_start(message: Message, state: FSMContext):
    await message.answer(
        "💬 <b>Напишите ваш вопрос или сообщение для поддержки:</b>\n\n"
        "Администратор рассмотрит обращение и ответит прямо в этот чат.",
        reply_markup=get_cancel_keyboard(),
        parse_mode="HTML"
    )
    await state.set_state(SupportStates.entering_question)

@router.message(SupportStates.entering_question)
async def support_entered(message: Message, state: FSMContext, bot: Bot):
    question = message.text.strip()
    admin_id = get_admin_id()
    
    if not admin_id:
        await message.answer(
            "❌ Поддержка временно недоступна (не настроен ID администратора).",
            reply_markup=get_main_keyboard(message.from_user.id)
        )
        await state.clear()
        return
        
    user_info = f"@{message.from_user.username}" if message.from_user.username else f"ID: {message.from_user.id}"
    support_text = (
        f"🆘 <b>Новое обращение в поддержку!</b>\n\n"
        f"👤 Отправитель: {user_info} (ID: <code>{message.from_user.id}</code>)\n"
        f"💬 Сообщение:\n{question}"
    )
    
    try:
        # Сохраняем обращение в базе данных
        await Database.add_support_ticket(
            user_id=message.from_user.id,
            username=message.from_user.username or '',
            message=question
        )
        await bot.send_message(admin_id, support_text, parse_mode="HTML")
        await message.answer(
            "✅ Ваше сообщение успешно отправлено администратору. Ожидайте ответа!",
            reply_markup=get_main_keyboard(message.from_user.id)
        )
    except Exception as e:
        logger.error(f"Ошибка отправки сообщения поддержке: {e}")
        await message.answer(
            "❌ Не удалось отправить сообщение администратору. Попробуйте позже.",
            reply_markup=get_main_keyboard(message.from_user.id)
        )
    await state.clear()

# Хэндлер для ответов админа на обращения (reply-to-message)
@router.message(F.reply_to_message)
async def admin_reply_handler(message: Message, bot: Bot):
    admin_id = get_admin_id()
    if not admin_id or str(message.chat.id) != str(admin_id):
        return # Это не админ чат
        
    orig = message.reply_to_message
    if orig.from_user.id != bot.id:
        return # Ответили не на сообщение бота
        
    # Ищем ID пользователя в тексте оригинального сообщения
    match = re.search(r'ID: <code>(\d+)</code>', orig.html_text or orig.text or '')
    if not match:
        match = re.search(r'ID:\s*(\d+)', orig.text or '')
        
    if match:
        user_id = int(match.group(1))
        reply_text = (
            f"✉️ <b>Ответ от администратора:</b>\n\n"
            f"{message.text}"
        )
        try:
            await bot.send_message(user_id, reply_text, parse_mode="HTML")
            await message.answer("✅ Ответ успешно доставлен пользователю.")
            # Помечаем обращение в БД как решенное
            await Database.resolve_last_ticket_from_user(user_id)
        except Exception as e:
            await message.answer(f"❌ Не удалось отправить ответ пользователю: {e}")
    else:
        # Не логируем ошибку для всех реплаев на обычные сообщения бота
        pass

# ==================== АДМИН-ПАНЕЛЬ В TELEGRAM ====================

@router.message(F.text == "👑 Админ-панель")
async def admin_panel_start(message: Message):
    admin_id = get_admin_id()
    if not admin_id or str(message.from_user.id) != str(admin_id):
        return
        
    stats = await Database.get_system_stats()
    text = (
        f"👑 <b>Панель администратора</b>\n\n"
        f"📊 <b>Статистика системы:</b>\n"
        f"👥 всего пользователей: <b>{stats['users']}</b>\n"
        f"  ├ 🆓 Бесплатный тариф: <b>{stats['free_users']}</b>\n"
        f"  ├ 👥 Реферальный тариф: <b>{stats['referral_users']}</b>\n"
        f"  └ 👑 Премиум тариф: <b>{stats['premium_users']}</b>\n\n"
        f"📂 Активных фильтров: <b>{stats['filters']}</b>\n"
        f"🆘 Нерешённых обращений: <b>{stats['tickets']}</b>"
    )
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"🆘 Заявки поддержки ({stats['tickets']})", callback_data="admin_support_list")],
        [InlineKeyboardButton(text="👥 Пользователи", callback_data="admin_users_list")],
        [InlineKeyboardButton(text="🔄 Обновить", callback_data="admin_refresh_panel")]
    ])
    
    await message.answer(text, reply_markup=keyboard, parse_mode="HTML")

@router.callback_query(F.data == "admin_refresh_panel")
async def admin_refresh_panel_cb(callback: CallbackQuery):
    admin_id = get_admin_id()
    if not admin_id or str(callback.from_user.id) != str(admin_id):
        await callback.answer("Отказано в доступе.")
        return
        
    stats = await Database.get_system_stats()
    text = (
        f"👑 <b>Панель администратора</b>\n\n"
        f"📊 <b>Статистика системы:</b>\n"
        f"👥 Всего пользователей: <b>{stats['users']}</b>\n"
        f"  ├ 🆓 Бесплатный тариф: <b>{stats['free_users']}</b>\n"
        f"  ├ 👥 Реферальный тариф: <b>{stats['referral_users']}</b>\n"
        f"  └ 👑 Премиум тариф: <b>{stats['premium_users']}</b>\n\n"
        f"📂 Активных фильтров: <b>{stats['filters']}</b>\n"
        f"🆘 Нерешённых обращений: <b>{stats['tickets']}</b>"
    )
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"🆘 Заявки поддержки ({stats['tickets']})", callback_data="admin_support_list")],
        [InlineKeyboardButton(text="👥 Пользователи", callback_data="admin_users_list")],
        [InlineKeyboardButton(text="🔄 Обновить", callback_data="admin_refresh_panel")]
    ])
    
    try:
        await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="HTML")
        await callback.answer("Обновлено.")
    except Exception:
        await callback.answer()

@router.callback_query(F.data == "admin_support_list")
async def admin_support_list_cb(callback: CallbackQuery):
    admin_id = get_admin_id()
    if not admin_id or str(callback.from_user.id) != str(admin_id):
        await callback.answer("Отказано в доступе.")
        return
        
    tickets = await Database.get_active_support_tickets()
    if not tickets:
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="⬅️ Назад в админку", callback_data="admin_refresh_panel")]
        ])
        await callback.message.edit_text("✅ <b>Активных обращений в поддержку нет! Все вопросы решены.</b>", reply_markup=keyboard, parse_mode="HTML")
        return
        
    buttons = []
    for t in tickets:
        user_str = f"@{t['username']}" if t['username'] else f"ID {t['user_id']}"
        preview = t['message'][:25] + ("..." if len(t['message']) > 25 else "")
        buttons.append([InlineKeyboardButton(text=f"🆘 {user_str}: {preview}", callback_data=f"adm_tkt_{t['id']}")])
        
    buttons.append([InlineKeyboardButton(text="⬅️ Назад в админку", callback_data="admin_refresh_panel")])
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
    await callback.message.edit_text("🆘 <b>Список активных обращений в поддержку:</b>\nВыберите обращение для просмотра и ответа:", reply_markup=keyboard, parse_mode="HTML")

@router.callback_query(F.data.startswith("adm_tkt_"))
async def admin_view_ticket_cb(callback: CallbackQuery):
    admin_id = get_admin_id()
    if not admin_id or str(callback.from_user.id) != str(admin_id):
        await callback.answer("Отказано в доступе.")
        return
        
    ticket_id = int(callback.data.split("_")[2])
    ticket = await Database.get_support_ticket(ticket_id)
    if not ticket:
        await callback.answer("Обращение не найдено или уже закрыто.")
        return
        
    user_str = f"@{ticket['username']}" if ticket['username'] else f"ID: {ticket['user_id']}"
    text = (
        f"🆘 <b>Обращение №{ticket['id']}</b>\n\n"
        f"👤 <b>Отправитель:</b> {user_str} (ID: <code>{ticket['user_id']}</code>)\n"
        f"📅 <b>Дата:</b> {ticket['created_at']}\n"
        f"Статус: <b>{ticket['status'].upper()}</b>\n\n"
        f"💬 <b>Текст обращения:</b>\n{ticket['message']}"
    )
    
    buttons = [
        [InlineKeyboardButton(text="✉️ Ответить пользователю", callback_data=f"adm_reply_{ticket['id']}")],
        [InlineKeyboardButton(text="✅ Пометить как решённое", callback_data=f"adm_resolve_{ticket['id']}")],
        [InlineKeyboardButton(text="⬅️ К списку тикетов", callback_data="admin_support_list")]
    ]
    
    await callback.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons), parse_mode="HTML")

@router.callback_query(F.data.startswith("adm_resolve_"))
async def admin_resolve_ticket_cb(callback: CallbackQuery):
    admin_id = get_admin_id()
    if not admin_id or str(callback.from_user.id) != str(admin_id):
        await callback.answer("Отказано в доступе.")
        return
        
    ticket_id = int(callback.data.split("_")[2])
    await Database.resolve_support_ticket(ticket_id)
    await callback.answer("Тикет закрыт.")
    await admin_support_list_cb(callback)

@router.callback_query(F.data.startswith("adm_reply_"))
async def admin_start_reply_cb(callback: CallbackQuery, state: FSMContext):
    admin_id = get_admin_id()
    if not admin_id or str(callback.from_user.id) != str(admin_id):
        await callback.answer("Отказано в доступе.")
        return
        
    ticket_id = int(callback.data.split("_")[2])
    ticket = await Database.get_support_ticket(ticket_id)
    if not ticket:
        await callback.answer("Обращение не найдено.")
        return
        
    await state.update_data(ticket_id=ticket_id, reply_user_id=ticket['user_id'], reply_username=ticket['username'])
    await state.set_state(AdminStates.entering_ticket_reply)
    
    user_str = f"@{ticket['username']}" if ticket['username'] else f"ID: {ticket['user_id']}"
    await callback.message.answer(
        f"✍️ <b>Введите текст ответа для пользователя {user_str}:</b>\n\n"
        f"Ответ будет моментально доставлен пользователю в Telegram.",
        reply_markup=get_cancel_keyboard(),
        parse_mode="HTML"
    )
    await callback.answer()

@router.message(AdminStates.entering_ticket_reply)
async def admin_send_ticket_reply(message: Message, state: FSMContext, bot: Bot):
    reply_text = message.text.strip()
    data = await state.get_data()
    
    ticket_id = data.get("ticket_id")
    user_id = data.get("reply_user_id")
    user_str = f"@{data.get('reply_username')}" if data.get('reply_username') else f"ID: {user_id}"
    
    formatted_reply = (
        f"✉️ <b>Ответ от администратора:</b>\n\n"
        f"{reply_text}"
    )
    
    try:
        await bot.send_message(user_id, formatted_reply, parse_mode="HTML")
        if ticket_id:
            await Database.resolve_support_ticket(ticket_id)
        await message.answer(
            f"✅ Ответ успешно доставлен пользователю {user_str}, а тикет закрыт!",
            reply_markup=get_main_keyboard(message.from_user.id)
        )
    except Exception as e:
        logger.error(f"Ошибка при отправке ответа пользователю: {e}")
        await message.answer(
            f"❌ Не удалось отправить сообщение пользователю: {e}",
            reply_markup=get_main_keyboard(message.from_user.id)
        )
        
    await state.clear()

@router.callback_query(F.data == "admin_users_list")
async def admin_users_list_cb(callback: CallbackQuery):
    admin_id = get_admin_id()
    if not admin_id or str(callback.from_user.id) != str(admin_id):
        await callback.answer("Отказано в доступе.")
        return
        
    users = await Database.get_all_users()
    text = f"👥 <b>Список пользователей ({len(users)}):</b>\n\n"
    for u in users[:20]:
        u_name = f"@{u['username']}" if u['username'] else f"ID {u['tg_id']}"
        text += f"• {u_name} | Тариф: <b>{u['tier'].upper()}</b> | Реф: {u['referral_count']}\n"
        
    if len(users) > 20:
        text += f"\n<i>...и еще {len(users) - 20} пользователей (смотрите в веб-панели).</i>"
        
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⬅️ Назад в админку", callback_data="admin_refresh_panel")]
    ])
    
    await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="HTML")

# --- Обработчики Избранного, AI-Откликов, Ночного режима и Аналитики ---

@router.callback_query(F.data.startswith("bm_add_"))
async def add_bookmark_cb(callback: CallbackQuery):
    from core.dispatcher import LISTING_CACHE
    key = callback.data.replace("bm_add_", "")
    listing = LISTING_CACHE.get(key)
    
    if listing:
        await Database.add_bookmark(
            user_id=callback.from_user.id,
            platform=listing.get('platform', 'unknown'),
            title=listing.get('title', 'Без названия'),
            url=listing.get('url', ''),
            budget=listing.get('budget', 'Договорная'),
            category_name=listing.get('category_id', '')
        )
        await callback.answer("⭐ Сохранено в Избранное!", show_alert=True)
    else:
        await callback.answer("⭐ Заказ сохранен в список Избранного!", show_alert=True)

@router.message(F.text == "⭐ Избранное")
async def show_bookmarks_handler(message: Message):
    bookmarks = await Database.get_user_bookmarks(message.from_user.id)
    if not bookmarks:
        await message.answer("⭐ <b>Ваша папка Избранное пуста.</b>\n\nНажимайте кнопку «⭐ В избранное» под новыми уведомлениями, чтобы сохранять нужные заказы!", parse_mode="HTML")
        return
        
    await message.answer(f"⭐ <b>Сохраненные заказы ({len(bookmarks)}):</b>", parse_mode="HTML")
    for bm in bookmarks[:10]:
        text = (
            f"📌 <b>{bm['title']}</b>\n"
            f"🔌 Платформа: <b>{bm['platform'].upper()}</b>\n"
            f"💰 Цена: <b>{bm['budget']}</b>"
        )
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(text="🔗 Открыть", url=bm['url']),
                InlineKeyboardButton(text="❌ Удалить", callback_data=f"bm_del_{bm['id']}")
            ]
        ])
        await message.answer(text, reply_markup=keyboard, parse_mode="HTML")

@router.callback_query(F.data.startswith("bm_del_"))
async def delete_bookmark_cb(callback: CallbackQuery):
    bm_id = int(callback.data.replace("bm_del_", ""))
    await Database.delete_bookmark(bm_id, callback.from_user.id)
    try:
        await callback.message.delete()
    except Exception:
        pass
    await callback.answer("Удалено из Избранного")

@router.message(F.text == "⚙️ Настройки")
async def settings_handler(message: Message):
    user = await Database.get_user(message.from_user.id)
    quiet_val = user.get('quiet_hours', 0) if user else 0
    status_str = "🟢 Включен (23:00 - 08:00)" if quiet_val == 1 else "🔴 Выключен"
    
    text = (
        "<b>⚙️ Настройки и Параметры:</b>\n\n"
        f"🌙 <b>Ночной режим:</b> {status_str}\n"
        "<i>В ночном режиме звуковые уведомления с 23:00 до 08:00 объединяются в утреннюю сводку.</i>"
    )
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text=f"🌙 Ночной режим: {'ВКЛ 🟢' if quiet_val == 1 else 'ВЫКЛ 🔴'}",
            callback_data="toggle_quiet_hours_cb"
        )],
        [InlineKeyboardButton(text="📊 Персональная аналитика", callback_data="show_user_analytics_cb")],
        [InlineKeyboardButton(text="☎️ Служба поддержки", callback_data="open_support_cb")]
    ])
    
    await message.answer(text, reply_markup=keyboard, parse_mode="HTML")

@router.callback_query(F.data == "toggle_quiet_hours_cb")
async def toggle_quiet_hours_inline_cb(callback: CallbackQuery):
    new_val = await Database.toggle_quiet_hours(callback.from_user.id)
    status_str = "🟢 Включен (23:00 - 08:00)" if new_val == 1 else "🔴 Выключен"
    
    text = (
        "<b>⚙️ Настройки и Параметры:</b>\n\n"
        f"🌙 <b>Ночной режим:</b> {status_str}\n"
        "<i>В ночном режиме звуковые уведомления с 23:00 до 08:00 объединяются в утреннюю сводку.</i>"
    )
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text=f"🌙 Ночной режим: {'ВКЛ 🟢' if new_val == 1 else 'ВЫКЛ 🔴'}",
            callback_data="toggle_quiet_hours_cb"
        )],
        [InlineKeyboardButton(text="📊 Персональная аналитика", callback_data="show_user_analytics_cb")],
        [InlineKeyboardButton(text="☎️ Служба поддержки", callback_data="open_support_cb")]
    ])
    
    try:
        await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="HTML")
    except Exception:
        pass
    await callback.answer(f"Ночной режим {'включен' if new_val == 1 else 'выключен'}")

@router.callback_query(F.data == "show_user_analytics_cb")
async def user_analytics_inline_cb(callback: CallbackQuery):
    stats = await Database.get_user_analytics(callback.from_user.id)
    text = (
        "<b>📊 Ваша персональная аналитика:</b>\n\n"
        f"⚙️ Активных фильтров: <b>{stats['filters_count']}</b>\n"
        f"⭐ Сохранено лотов в Избранное: <b>{stats['bookmarks_count']}</b>\n"
        f"🔍 Всего обработано заказов системой: <b>{stats['total_seen_listings']}</b>\n\n"
        "💡 <i>Настраивайте разные категории для максимального охвата рынка!</i>"
    )
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⬅️ Назад в настройки", callback_data="back_to_settings_cb")]
    ])
    await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="HTML")
    await callback.answer()

@router.callback_query(F.data == "back_to_settings_cb")
async def back_to_settings_inline_cb(callback: CallbackQuery):
    user = await Database.get_user(callback.from_user.id)
    quiet_val = user.get('quiet_hours', 0) if user else 0
    status_str = "🟢 Включен (23:00 - 08:00)" if quiet_val == 1 else "🔴 Выключен"
    
    text = (
        "<b>⚙️ Настройки и Параметры:</b>\n\n"
        f"🌙 <b>Ночной режим:</b> {status_str}\n"
        "<i>В ночном режиме звуковые уведомления с 23:00 до 08:00 объединяются в утреннюю сводку.</i>"
    )
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text=f"🌙 Ночной режим: {'ВКЛ 🟢' if quiet_val == 1 else 'ВЫКЛ 🔴'}",
            callback_data="toggle_quiet_hours_cb"
        )],
        [InlineKeyboardButton(text="📊 Персональная аналитика", callback_data="show_user_analytics_cb")],
        [InlineKeyboardButton(text="☎️ Служба поддержки", callback_data="open_support_cb")]
    ])
    await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="HTML")
    await callback.answer()

@router.callback_query(F.data == "open_support_cb")
async def open_support_cb(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await callback.message.answer(
        "☎️ <b>Напишите ваш вопрос или сообщение для поддержки.</b>\n"
        "Администраторы ответят вам прямо в бот!",
        reply_markup=get_cancel_keyboard(),
        parse_mode="HTML"
    )
    await state.set_state(SupportStates.entering_question)
