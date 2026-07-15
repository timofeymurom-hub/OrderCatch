import asyncio
import hashlib
import tls_requests
from bs4 import BeautifulSoup
from core.base_module import BaseModule
from core.rate_limiter import ExponentialBackoff
from database import Database

class FunPayMonitor(BaseModule):
    def __init__(self, check_interval: float = 60.0):
        super().__init__("funpay")
        self.check_interval = check_interval
        self.backoff = ExponentialBackoff(base_delay=15.0, max_delay=300.0)

    def _parse_category_sync(self, category_id: str):
        url = f"https://funpay.com/lots/{category_id}/"
        headers = {
            'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/135.0.0.0 Safari/537.36',
            'accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8',
            'accept-language': 'ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7',
            'cookie': 'cy=rub; locale=ru', # Принудительно заставляем сайт отдавать цены в рублях
        }
        
        response = tls_requests.get(url, headers=headers, timeout=20)
        response.raise_for_status()
        
        soup = BeautifulSoup(response.text, 'html.parser')
        items = soup.select('.tc-item')
        
        listings = []
        for item in items:
            # Описание
            desc_el = item.select_one('.tc-desc-text')
            if not desc_el:
                desc_el = item.select_one('.tc-desc')
            description = desc_el.get_text(strip=True) if desc_el else "Без описания"
            
            # Цена
            price_el = item.select_one('.tc-price')
            price_str = price_el.get_text(strip=True) if price_el else "0"
            
            # Парсим число цены
            try:
                price_val = float(''.join(c for c in price_str if c.isdigit() or c == '.'))
            except Exception:
                price_val = 0.0
                
            # Если FunPay отдал цену в другой валюте, сконвертируем для фильтрации в рубли
            price_lower = price_str.lower()
            if '$' in price_lower or 'usd' in price_lower:
                price_val = price_val * 90.0 # Конвертация USD -> RUB
            elif '€' in price_lower or 'eur' in price_lower:
                price_val = price_val * 100.0 # Конвертация EUR -> RUB
                
            # Формируем красивую строку бюджета
            # Если в строке уже указан символ валюты, оставляем как есть, иначе добавляем ₽
            if any(char in price_str for char in ['₽', '$', '€', 'грн', 'zł', 'Br']):
                budget = price_str
            else:
                budget = f"{price_str} ₽"
            
            # Ссылка
            href = item.get('href', '')
            if href and not href.startswith('http'):
                href = f"https://funpay.com{href}"
            
            # Продавец
            user_el = item.select_one('.media-user-name')
            if not user_el:
                user_el = item.select_one('.tc-user')
            username = user_el.get_text(strip=True) if user_el else "Неизвестно"
            
            # Генерация уникального ID лота (так как нет явного ID)
            unique_str = f"{href}_{price_str}_{description}"
            listing_id = hashlib.md5(unique_str.encode('utf-8')).hexdigest()
            
            listings.append({
                'id': listing_id,
                'title': f"{description[:100]}...",
                'url': href,
                'budget': budget,
                'budget_val': price_val,
                'description': f"Продавец: {username}\n{description}",
                'category_id': category_id
            })
            
        return listings

    async def run(self):
        while self.is_running:
            if not self.active_categories:
                await asyncio.sleep(5)
                continue

            self.logger.info(f"Начало проверки FunPay. Категорий: {len(self.active_categories)}")
            
            success = False
            for cat_id in list(self.active_categories):
                try:
                    listings = await asyncio.to_thread(self._parse_category_sync, cat_id)
                    if listings:
                        for listing in listings:
                            await self.process_listing(cat_id, listing)
                        self.mark_category_seeded(cat_id)
                    success = True
                    # Задержка между категориями, чтобы не спамить
                    await asyncio.sleep(3)
                except Exception as e:
                    self.logger.error(f"Ошибка проверки категории {cat_id} на FunPay: {e}", exc_info=True)
                    success = False

            if success:
                self.backoff.success()
                await self.backoff.sleep(self.check_interval)
            else:
                await self.backoff.failure()
