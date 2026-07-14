import asyncio
import hashlib
import tls_requests
from bs4 import BeautifulSoup
import re
from core.base_module import BaseModule
from core.rate_limiter import ExponentialBackoff
from database import Database

class FreelancehuntMonitor(BaseModule):
    def __init__(self, check_interval: float = 90.0):
        super().__init__("freelancehunt")
        self.check_interval = check_interval
        self.backoff = ExponentialBackoff(base_delay=15.0, max_delay=300.0)

    def _parse_category_sync(self, category_id: str):
        # Если категория - это полноценная ссылка, используем её
        if category_id.startswith('http'):
            url = category_id
        else:
            # Иначе подставляем как параметр категории
            url = f"https://freelancehunt.com/projects?c={category_id}" if category_id else "https://freelancehunt.com/projects"

        headers = {
            'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/135.0.0.0 Safari/537.36',
            'accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8',
            'accept-language': 'ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7',
        }
        
        response = tls_requests.get(url, headers=headers, timeout=20)
        response.raise_for_status()
        
        soup = BeautifulSoup(response.text, 'html.parser')
        
        # Поиск строк таблицы проектов
        rows = soup.select('tr[data-project-id]')
        if not rows:
            rows = soup.select('.project-list > tr')
        if not rows:
            rows = soup.find_all(class_=re.compile(r'project-card|project-list-item'))
            
        listings = []
        for idx, row in enumerate(rows):
            # Ссылка и название
            title_el = row.select_one('a.visiting')
            if not title_el:
                title_el = row.find('a', href=re.compile(r'/project/.*\.html'))
                
            if not title_el:
                continue
                
            title = title_el.get_text(strip=True)
            href = title_el.get('href', '')
            if href and not href.startswith('http'):
                href = f"https://freelancehunt.com{href}"
                
            # Идентификатор проекта из ссылки или data-project-id
            project_id = row.get('data-project-id')
            if not project_id:
                match = re.search(r'/(\d+)\.html$', href)
                project_id = match.group(1) if match else str(idx)
                
            # Бюджет
            price_el = row.select_one('.price')
            price_str = price_el.get_text(strip=True) if price_el else "Договорная"
            
            # Парсим число цены
            try:
                clean_price = re.sub(r'[^\d]', '', price_str)
                price_val = float(clean_price) if clean_price else 0.0
            except Exception:
                price_val = 0.0
                
            # Конвертируем валюты для фильтрации в рубли
            price_lower = price_str.lower()
            if '₴' in price_lower or 'грн' in price_lower or 'uah' in price_lower:
                price_val = price_val * 2.4 # 1 UAH -> 2.4 RUB
            elif '$' in price_lower or 'usd' in price_lower:
                price_val = price_val * 90.0
            elif '€' in price_lower or 'eur' in price_lower:
                price_val = price_val * 100.0
                
            # Описание
            desc_el = row.select_one('.description')
            if not desc_el:
                desc_el = row.select_one('p')
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
            # Если активных категорий нет, сканируем общую ленту проектов
            categories = list(self.active_categories) if self.active_categories else [""]
            
            self.logger.info(f"Начало проверки Freelancehunt. Категорий/лент: {len(categories)}")
            
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
                    # Задержка между запросами
                    await asyncio.sleep(3)
                except Exception as e:
                    self.logger.error(f"Ошибка проверки категории {cat_id} на Freelancehunt: {e}", exc_info=True)
                    success = False

            if success:
                self.backoff.success()
                await self.backoff.sleep(self.check_interval)
            else:
                await self.backoff.failure()
