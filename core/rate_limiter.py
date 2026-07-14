import asyncio
import logging

class ExponentialBackoff:
    def __init__(self, base_delay: float = 5.0, max_delay: float = 300.0, factor: float = 2.0):
        self.base_delay = base_delay
        self.max_delay = max_delay
        self.factor = factor
        self.current_delay = base_delay
        self.error_count = 0
        self.logger = logging.getLogger("backoff")

    def success(self):
        """Сброс бэк-оффа при успешном запросе."""
        if self.error_count > 0:
            self.logger.info("Успешное восстановление подключения, сброс задержки.")
        self.error_count = 0
        self.current_delay = self.base_delay

    async def failure(self):
        """Увеличение задержки при ошибке и ожидание."""
        self.error_count += 1
        self.logger.warning(f"Ошибка запроса #{self.error_count}. Задержка: {self.current_delay} сек.")
        await asyncio.sleep(self.current_delay)
        self.current_delay = min(self.current_delay * self.factor, self.max_delay)

    async def sleep(self, seconds: float):
        """Обычное ожидание (если всё хорошо)."""
        await asyncio.sleep(seconds)
