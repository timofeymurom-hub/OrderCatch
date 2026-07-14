import asyncio
import hashlib
import tls_requests
from bs4 import BeautifulSoup
import re
from core.base_module import BaseModule
from core.rate_limiter import ExponentialBackoff
from database import Database

class HabrFreelanceMonitor(BaseModule):
    def __init__(self, check_interval: float = 90.0):
        super().__init__("habr")
        self.check_interval = check_interval
        self.backoff = ExponentialBackoff(base_delay=15.0, max_delay=300.0)

    def _parse_category_sync(self, category_id: str):
        if category_id.startswith('http'):
            url = category_id
        else:
            url = f"https://freelance.habr.com/tasks?category={category_id}" if category_id and category_id != "0" else "https://freelance.habr.com/tasks"

        headers = {
            'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'accept-language': 'ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7',
        }
        
        try:
            response = tls_requests.get(url, headers=headers, timeout=20)
            html_content = response.text
        except Exception:
            import requests
            r = requests.get(url, headers=headers, timeout=20)
            r.raise_for_status()
            html_content = r.text
        
        soup = BeautifulSoup(html_content, 'html.parser')
        
        cards = soup.select('article.task')
        if not cards:
            cards = soup.select('li.content-list__item')
        if not cards:
            cards = soup.select('.task_card')

        listings = []
        for idx, card in enumerate(cards):
            title_el = card.select_one('.task__title a') or card.find('a', href=re.compile(r'/tasks/\d+'))
            if not title_el:
                continue
                
            title = title_el.get_text(strip=True)
            href = title_el.get('href', '')
            if href and not href.startswith('http'):
                href = f"https://freelance.habr.com{href}"
                
            match = re.search(r'/tasks/(\d+)', href)
            project_id = match.group(1) if match else str(idx)
            
            price_el = card.select_one('.task__price') or card.select_one('.price')
            price_str = price_el.get_text(strip=True) if price_el else "Договорная"
            
            try:
                clean_price = re.sub(r'[^\d]', '', price_str)
                price_val = float(clean_price) if clean_price else 0.0
            except Exception:
                price_val = 0.0

            if '$' in price_str or 'usd' in price_str.lower():
                price_val = price_val * 90.0
            elif '€' in price_str or 'eur' in price_str.lower():
                price_val = price_val * 100.0
                
            desc_el = card.select_one('.task__description') or card.select_one('.description')
            description = desc_el.get_text(strip=True) if desc_el else title
            description = re.sub(r'\s+', ' ', description).strip()
            
            listings.append({
                'id': project_id,
                'title': title,
                'url': href,
                'budget': price_str,
                'budget_val': price_val,
                'description': description,
                'category_id': category_id,
                'platform': 'habr'
            })
            
        return listings

    async def run(self):
        while self.is_running:
            categories = list(self.active_categories) if self.active_categories else ["0"]
            self.logger.info(f"Начало проверки Habr Freelance. Категорий/лент: {len(categories)}")
            
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
                    self.logger.error(f"Ошибка проверки категории {cat_id} на Habr Freelance: {e}", exc_info=True)
                    success = False

            if success:
                self.backoff.success()
                await self.backoff.sleep(self.check_interval)
            else:
                await self.backoff.failure()
