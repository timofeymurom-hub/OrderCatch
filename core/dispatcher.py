import asyncio
import logging
from datetime import datetime, timedelta
from database import Database
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

# Глобальный кэш лотов для создания откликов и добавления в избранное
LISTING_CACHE = {}

class Dispatcher:
    def __init__(self, bot_instance=None):
        self.bot = bot_instance
        self.modules = {}
        self.logger = logging.getLogger("dispatcher")
        self.is_running = False
        
        # Очередь для отправки мгновенных сообщений (Premium / Referral)
        self.instant_queue = asyncio.Queue()
        
        # Накопитель для бесплатных пользователей (Free batching)
        # Формат: {tg_id: [list of listings]}
        self.free_batches = {}
        
        # Блокировка для доступа к free_batches
        self.batch_lock = asyncio.Lock()

    def register_module(self, module):
        self.modules[module.name] = module
        module.on_new_listing(self.on_new_listing_received)
        self.logger.info(f"Модуль {module.name} зарегистрирован в диспетчере.")

    async def on_new_listing_received(self, platform: str, listing: dict):
        """Колбэк, вызываемый модулями при обнаружении нового лота."""
        self.logger.info(f"Получен новый лот от {platform}: ID={listing['id']}, Бюджет={listing['budget']}")
        
        # Кэшируем лот для колбэков бота
        cache_key = f"{platform}_{listing['id']}"
        LISTING_CACHE[cache_key] = listing
        # Ограничим размер кэша
        if len(LISTING_CACHE) > 1000:
            keys_to_del = list(LISTING_CACHE.keys())[:200]
            for k in keys_to_del:
                LISTING_CACHE.pop(k, None)

        # 1. Получаем все активные фильтры для данной платформы
        filters = await Database.get_active_filters_for_platform(platform)
        
        now_hour = datetime.now().hour
        is_night = (now_hour >= 23 or now_hour < 8)

        for f in filters:
            if self.match_listing(listing, f):
                # Нашли совпадение! Создаем копию лота с информацией о категории фильтра
                matched_item = dict(listing)
                matched_item['platform'] = item_platform = matched_item.get('platform') or platform
                matched_item['matched_category_name'] = f.get('category_name') or f.get('category_id') or 'Не указана'
                
                tg_id = f['user_id']
                tier = f['tier'] # 'free', 'referral', 'premium'
                
                # Проверяем "Ночной режим" у пользователя
                user = await Database.get_user(tg_id)
                quiet = user.get('quiet_hours', 0) if user else 0
                
                if quiet and is_night:
                    # В ночном режиме откладываем даже для premium в накопитель
                    async with self.batch_lock:
                        if tg_id not in self.free_batches:
                            self.free_batches[tg_id] = []
                        self.free_batches[tg_id].append(matched_item)
                        self.logger.info(f"Лот {listing['id']} отложен в ночной батч пользователя {tg_id}.")
                elif tier in ('premium', 'referral'):
                    # Шлем мгновенно
                    await self.instant_queue.put((tg_id, matched_item))
                else:
                    # Добавляем в батч для бесплатной отправки
                    async with self.batch_lock:
                        if tg_id not in self.free_batches:
                            self.free_batches[tg_id] = []
                        self.free_batches[tg_id].append(matched_item)
                        self.logger.info(f"Лот {listing['id']} добавлен в батч бесплатного пользователя {tg_id}.")

    def match_listing(self, listing: dict, filter_item: dict) -> bool:
        # 1. Проверка категории
        if str(listing.get('category_id')) != str(filter_item.get('category_id')):
            return False

        # 2. Проверка цены
        price = listing.get('budget_val', 0.0)
        min_p = filter_item.get('min_price')
        max_p = filter_item.get('max_price')
        if min_p is not None and price < min_p:
            return False
        if max_p is not None and price > max_p:
            return False

        # 3. Проверка ключевых слов
        keywords = filter_item.get('keywords', [])
        if keywords:
            text = f"{listing.get('title', '')} {listing.get('description', '')}".lower()
            
            # Разделим на плюс-слова и минус-слова
            plus_words = [w.lower() for w in keywords if not w.startswith('-')]
            minus_words = [w[1:].lower() for w in keywords if w.startswith('-')]
            
            # Если есть минус-слова и хоть одно нашлось — не подходит
            for mw in minus_words:
                if mw in text:
                    return False
                    
            # Если есть плюс-слова, то хотя бы одно должно быть в тексте
            if plus_words:
                found = False
                for pw in plus_words:
                    if pw in text:
                        found = True
                        break
                if not found:
                    return False
                    
        return True

    async def _update_categories_loop(self):
        """Задача обновления активных категорий для парсеров каждые 30 секунд."""
        while self.is_running:
            try:
                for platform, module in self.modules.items():
                    filters = await Database.get_active_filters_for_platform(platform)
                    # Вытаскиваем уникальные category_id
                    cat_ids = list(set(str(f['category_id']) for f in filters))
                    module.set_categories(cat_ids)
            except Exception as e:
                self.logger.error(f"Ошибка в цикле обновления категорий: {e}", exc_info=True)
            await asyncio.sleep(30)

    async def _instant_delivery_loop(self):
        """Задача для мгновенной отправки уведомлений premium-пользователям."""
        while self.is_running:
            try:
                tg_id, listing = await self.instant_queue.get()
                if self.bot:
                    await self.send_listing_notification(tg_id, listing)
                self.instant_queue.task_done()
            except Exception as e:
                self.logger.error(f"Ошибка в цикле мгновенной доставки: {e}", exc_info=True)
                await asyncio.sleep(1)

    async def _batch_delivery_loop(self, check_interval_minutes: int = 5):
        """Задача для батчинговой отправки уведомлений бесплатным пользователям."""
        platform_emojis = {
            'kwork': '🟢 Kwork',
            'funpay': '🔵 FunPay',
            'playerok': '🟣 Playerok',
            'freelancehunt': '🦫 Freelancehunt',
            'freelance_ru': '🔴 Freelance.ru',
            'fl': '🔵 FL.ru',
            'freelancer': '🔵 Freelancer.com',
            'avito': '🟡 Avito',
            'habr': '💻 Habr Freelance',
            'workzilla': '🟢 Work-Zilla',
            'youla': '🟣 Юла',
            'profi': '🔵 Profi.ru',
            'yandex_services': '🔴 Яндекс Услуги',
            'guru': '🌐 Guru.com',
            'plati': '🟢 Plati.market / GGSEL',
            'g2g': '🎮 G2G.com',
            'olx': '🟡 OLX',
            'kufar': '🟢 Kufar.by'
        }
        
        while self.is_running:
            await asyncio.sleep(check_interval_minutes * 60)
            try:
                async with self.batch_lock:
                    batches_to_send = self.free_batches.copy()
                    self.free_batches.clear()
                
                if not batches_to_send:
                    continue
                    
                self.logger.info(f"Запуск отправки накопленных батчей для {len(batches_to_send)} пользователей.")
                for tg_id, listings in batches_to_send.items():
                    if not listings or not self.bot:
                        continue
                    
                    # Группируем по 5 штук в одном сообщении
                    chunk_size = 5
                    for i in range(0, len(listings), chunk_size):
                        chunk = listings[i:i+chunk_size]
                        text = f"<b>📦 Сводка новых лотов (Батч раз в {check_interval_minutes} мин):</b>\n\n"
                        for idx, item in enumerate(chunk):
                            raw_platform = item.get('platform', '') or ''
                            p_name = platform_emojis.get(raw_platform.lower(), raw_platform.upper())
                            cat_name = item.get('matched_category_name') or item.get('category_id') or ''
                            cat_part = f" | 📂 {cat_name}" if cat_name else ""
                            
                            text += (
                                f"{idx+1}. [{p_name}{cat_part}]\n"
                                f"<b>{item['title']}</b>\n"
                                f"Цена: <b>{item['budget']}</b>\n"
                                f"Ссылка: <a href='{item['url']}'>Перейти</a>\n\n"
                            )
                        try:
                            # Отправляем через aiogram бот
                            await self.bot.send_message(tg_id, text, parse_mode="HTML", disable_web_page_preview=True)
                            await asyncio.sleep(0.1) # Защита от флуда
                        except Exception as send_err:
                            self.logger.error(f"Ошибка отправки батча пользователю {tg_id}: {send_err}")
            except Exception as e:
                self.logger.error(f"Ошибка в цикле батчинговой доставки: {e}", exc_info=True)

    async def send_listing_notification(self, tg_id: int, item: dict):
        """Форматирование и отправка мгновенного уведомления о лоте с красивыми Inline-кнопками."""
        platform_emojis = {
            'kwork': '🟢 Kwork',
            'funpay': '🔵 FunPay',
            'playerok': '🟣 Playerok',
            'freelancehunt': '🦫 Freelancehunt',
            'freelance_ru': '🔴 Freelance.ru',
            'fl': '🔵 FL.ru',
            'freelancer': '🌐 Freelancer.com',
            'avito': '🟡 Avito',
            'habr': '💻 Habr Freelance',
            'workzilla': '🟢 Work-Zilla',
            'youla': '🟣 Юла (Youla)',
            'profi': '🔵 Profi.ru',
            'yandex_services': '🔴 Яндекс Услуги',
            'guru': '🌐 Guru.com',
            'plati': '🟢 Plati.market',
            'g2g': '🎮 G2G.com',
            'olx': '🟡 OLX',
            'kufar': '🟢 Kufar.by',
            'peopleperhour': '💼 PeoplePerHour',
            'eldorado': '🏆 Eldorado.gg',
            'kadrof': '📝 Kadrof.ru',
            'g2a': '🎁 G2A Goods'
        }
        
        raw_platform = item.get('platform', '') or ''
        platform_name = platform_emojis.get(raw_platform.lower(), f"🌐 {raw_platform.upper()}" if raw_platform else "🌐 Неизвестная площадка")
        category_name = str(item.get('matched_category_name') or item.get('category_id') or 'Общая')
        
        if category_name.startswith('http'):
            cat_display = f"<a href='{category_name}'>Открыть раздел</a>"
        else:
            cat_display = f"<code>{category_name}</code>"
        
        try:
            import pytz
            now_msk = datetime.now(pytz.timezone('Europe/Moscow'))
        except Exception:
            now_msk = datetime.utcnow() + timedelta(hours=3)
            
        detected_time = now_msk.strftime("%H:%M (МСК)")
        
        text = (
            f"🏢 <b>Площадка:</b> {platform_name}\n"
            f"📁 <b>Категория:</b> {cat_display}\n"
            f"⏱ <b>Обнаружен:</b> {detected_time}\n\n"
            f"🔔 <b>{item['title']}</b>\n\n"
            f"💰 <b>Бюджет / Цена:</b> {item['budget']}\n\n"
            f"📝 <b>Описание:</b>\n{item['description'][:400]}"
            f"{'...' if len(item['description']) > 400 else ''}"
        )
        
        listing_key = f"{raw_platform}_{item['id']}"
        
        # Интерактивные inline-кнопки
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔗 Открыть заказ", url=item['url'])],
            [InlineKeyboardButton(text="⭐ В избранное", callback_data=f"bm_add_{listing_key[:45]}")]
        ])
        
        try:
            await self.bot.send_message(tg_id, text, reply_markup=keyboard, parse_mode="HTML", disable_web_page_preview=True)
        except Exception as e:
            self.logger.error(f"Не удалось отправить сообщение {tg_id}: {e}")

    async def start(self):
        self.is_running = True
        self.logger.info("Диспетчер запускается...")
        
        # Инициализируем БД
        from database import init_db
        await init_db()
        
        # Создаем задачи
        self.update_task = asyncio.create_task(self._update_categories_loop())
        self.instant_task = asyncio.create_task(self._instant_delivery_loop())
        self.batch_task = asyncio.create_task(self._batch_delivery_loop())
        
        # Запуск всех модулей
        self.module_tasks = []
        for name, module in self.modules.items():
            self.module_tasks.append(asyncio.create_task(module.start()))
            
        self.logger.info("Диспетчер и все модули запущены.")

    async def stop(self):
        self.is_running = False
        self.logger.info("Остановка диспетчера...")
        
        # Останавливаем модули
        for name, module in self.modules.items():
            await module.stop()
            
        # Отменяем задачи
        self.update_task.cancel()
        self.instant_task.cancel()
        self.batch_task.cancel()
        for task in self.module_tasks:
            task.cancel()
            
        await asyncio.gather(
            self.update_task, self.instant_task, self.batch_task, *self.module_tasks,
            return_exceptions=True
        )
        self.logger.info("Диспетчер остановлен.")

# Псевдоним для совместимости импорта
dispatcher = Dispatcher
