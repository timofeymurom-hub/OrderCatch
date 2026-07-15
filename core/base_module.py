from abc import ABC, abstractmethod
import logging
import asyncio

class BaseModule(ABC):
    def __init__(self, name: str):
        self.name = name
        self.callbacks = []
        self.active_categories = set()
        self.seeded_categories = set()
        self.is_running = False
        self.logger = logging.getLogger(f"module.{name}")
        
    def on_new_listing(self, callback):
        """Регистрация колбэка для обработки новых лотов."""
        self.callbacks.append(callback)
        
    async def trigger_new_listing(self, listing: dict):
        """Запуск всех колбэков при появлении нового лота."""
        for callback in self.callbacks:
            try:
                if asyncio.iscoroutinefunction(callback):
                    await callback(self.name, listing)
                else:
                    callback(self.name, listing)
            except Exception as e:
                self.logger.error(f"Ошибка в колбэке: {e}", exc_info=True)

    def set_categories(self, categories: list):
        """Обновление списка категорий, которые нужно сканировать."""
        old_cats = self.active_categories.copy()
        self.active_categories = set(categories)
        if old_cats != self.active_categories:
            self.seeded_categories = self.seeded_categories.intersection(self.active_categories)
            self.logger.info(f"Список категорий обновлен: {self.active_categories}")

    async def process_listing(self, category_key: str, listing: dict):
        """
        Проверка и обработка лота.
        При первичном сканировании категории после перезапуска бота,
        существующие старые лоты помечаются просмотренными БЕЗ отправки повторных уведомлений.
        """
        from database import Database
        is_first_scan = (category_key not in self.seeded_categories)
        listing_id = str(listing['id'])
        
        if not await Database.is_listing_seen(self.name, listing_id):
            await Database.add_seen_listing(self.name, listing_id)
            if not is_first_scan:
                await self.trigger_new_listing(listing)

    def mark_category_seeded(self, category_key: str):
        self.seeded_categories.add(category_key)

    @abstractmethod
    async def run(self):
        """Основной асинхронный цикл сканирования."""
        pass
        
    async def start(self):
        self.is_running = True
        self.logger.info(f"Модуль {self.name} запущен.")
        try:
            await self.run()
        except asyncio.CancelledError:
            self.logger.info(f"Модуль {self.name} остановлен по запросу.")
        except Exception as e:
            self.logger.error(f"Критическая ошибка в работе модуля {self.name}: {e}", exc_info=True)
        finally:
            self.is_running = False

    async def stop(self):
        self.is_running = False
        self.logger.info(f"Запрос на остановку модуля {self.name} получен.")
