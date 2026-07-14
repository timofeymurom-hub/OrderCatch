import asyncio
import tls_requests
from bs4 import BeautifulSoup
import re
from core.base_module import BaseModule
from core.rate_limiter import ExponentialBackoff
from database import Database

class FreelanceRuMonitor(BaseModule):
    def __init__(self, check_interval: float = 120.0):
        super().__init__("freelance_ru")
        self.check_interval = check_interval
        self.backoff = ExponentialBackoff(base_delay=15.0, max_delay=300.0)

    def _parse_category_sync(self, category_id: str):
        # Если это ссылка, используем её
        if category_id.startswith('http'):
            url = category_id
        else:
            # Иначе подставляем как фильтр категорий в query-параметры
            # На Freelance.ru используется параметр c[]
            url = f"https://freelance.ru/task?c%5B%5D={category_id}" if category_id and category_id != "0" else "https://freelance.ru/task"

        headers = {
            'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/135.0.0.0 Safari/537.36',
            'accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8',
            'accept-language': 'ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7',
        }
        
        response = tls_requests.get(url, headers=headers, timeout=20)
        response.raise_for_status()
        
        soup = BeautifulSoup(response.text, 'html.parser')
        cards = soup.select('article.task-card')
        
        listings = []
        for idx, card in enumerate(cards):
            title_el = card.select_one('a.task-card__title-link')
            if not title_el:
                continue
                
            title = title_el.get_text(strip=True)
            href = title_el.get('href', '')
            if href and not href.startswith('http'):
                href = f"https://freelance.ru{href}"
                
            # Идентификатор
            project_id = ""
            match = re.search(r'/view/(\d+)', href)
            if match:
                project_id = match.group(1)
            else:
                project_id = str(hash(href))
                
            # Бюджет
            price_el = card.select_one('.task-card__budget')
            price_str = "Договорная"
            price_val = 0.0
            
            if price_el:
                price_str = price_el.get_text(strip=True)
                if "Обсуждается" in price_str or "индивидуально" in price_str:
                    price_str = "Договорная"
                else:
                    # Очищаем от валюты и пробелов
                    clean_price = re.sub(r'[^\d]', '', price_str)
                    if clean_price:
                        price_val = float(clean_price)
                        
            # Описание
            desc_el = card.select_one('.task-card__desc')
            description = desc_el.get_text(strip=True) if desc_el else "Без описания"
            description = re.sub(r'\s+', ' ', description).strip()
            
            listings.append({
                'id': project_id,
                'title': title,
                'url': href,
                'budget': price_str,
                'budget_val': price_val,
                'description': description,
                'category_id': category_id
            })
            
        return listings

    async def run(self):
        while self.is_running:
            categories = list(self.active_categories) if self.active_categories else [""]
            
            self.logger.info(f"Начало проверки Freelance.ru. Категорий/лент: {len(categories)}")
            
            success = False
            for cat_id in categories:
                try:
                    listings = await asyncio.to_thread(self._parse_category_sync, cat_id)
                    if listings:
                        for listing in listings:
                            if not await Database.is_listing_seen(self.name, listing['id']):
                                await Database.add_seen_listing(self.name, listing['id'])
                                await self.trigger_new_listing(listing)
                    success = True
                    await asyncio.sleep(3)
                except Exception as e:
                    self.logger.error(f"Ошибка проверки категории {cat_id} на Freelance.ru: {e}", exc_info=True)
                    success = False

            if success:
                self.backoff.success()
                await self.backoff.sleep(self.check_interval)
            else:
                await self.backoff.failure()
