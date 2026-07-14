from abc import ABC, abstractmethod
import logging
import asyncio

class BaseModule(ABC):
    def __init__(self, name: str):
        self.name = name
        self.callbacks = []
        self.active_categories = set()
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
            self.logger.info(f"Список категорий обновлен: {self.active_categories}")

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
