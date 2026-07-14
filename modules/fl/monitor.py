import asyncio
import tls_requests
from bs4 import BeautifulSoup
import re
from core.base_module import BaseModule
from core.rate_limiter import ExponentialBackoff
from database import Database

class FlMonitor(BaseModule):
    def __init__(self, check_interval: float = 120.0):
        super().__init__("fl")
        self.check_interval = check_interval
        self.backoff = ExponentialBackoff(base_delay=15.0, max_delay=300.0)

    def _parse_category_sync(self, category_slug: str):
        # Если это полная ссылка, то используем её
        if category_slug.startswith('http'):
            url = category_slug
        elif category_slug and category_slug != "0":
            # Иначе подставляем как категорию
            # На FL.ru категории имеют вид /projects/category/slug/
            slug = category_slug.strip('/')
            if not slug.startswith('category/'):
                url = f"https://www.fl.ru/projects/category/{slug}/"
            else:
                url = f"https://www.fl.ru/projects/{slug}/"
        else:
            url = "https://www.fl.ru/projects/"

        headers = {
            'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/135.0.0.0 Safari/537.36',
            'accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8',
            'accept-language': 'ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7',
        }
        
        response = tls_requests.get(url, headers=headers, timeout=20)
        response.raise_for_status()
        
        soup = BeautifulSoup(response.text, 'html.parser')
        
        # Находим все блоки b-post
        posts = soup.select('.b-post')
        
        listings = []
        for post in posts:
            title_el = post.select_one('h2.b-post__title a')
            if not title_el:
                continue
                
            title = title_el.get_text(strip=True)
            href = title_el.get('href', '')
            if href and not href.startswith('http'):
                href = f"https://www.fl.ru{href}"
                
            # Идентификатор проекта
            project_id = title_el.get('data-disposable-project-id')
            if not project_id:
                match = re.search(r'/projects/(\d+)', href)
                if match:
                    project_id = match.group(1)
                else:
                    project_id = str(hash(href))
                    
            # Бюджет
            price_el = post.select_one('.b-post__price')
            price_str = "Договорная"
            price_val = 0.0
            
            if price_el:
                price_str = price_el.get_text(strip=True)
                # Убираем лишние пробелы и переносы
                price_str = re.sub(r'\s+', ' ', price_str).strip()
                if any(x in price_str.lower() for x in ["договоренности", "собеседования", "конкурс"]):
                    price_str = "Договорная"
                else:
                    # Извлекаем максимальное число как бюджет для фильтрации
                    digits = re.findall(r'\d+', price_str.replace('\xa0', '').replace(' ', ''))
                    if digits:
                        # Если указан диапазон, возьмем верхнюю границу, если от - нижнюю
                        price_val = float(digits[-1])
                        
            # Описание
            desc_el = post.select_one('.b-post__body .b-post__txt')
            description = desc_el.get_text(strip=True) if desc_el else "Без описания"
            description = re.sub(r'\s+', ' ', description).strip()
            
            listings.append({
                'id': project_id,
                'title': title,
                'url': href,
                'budget': price_str,
                'budget_val': price_val,
                'description': description,
                'category_id': category_slug
            })
            
        return listings

    async def run(self):
        while self.is_running:
            categories = list(self.active_categories) if self.active_categories else [""]
            
            self.logger.info(f"Начало проверки FL.ru. Категорий/лент: {len(categories)}")
            
            success = False
            for cat_slug in categories:
                try:
                    listings = await asyncio.to_thread(self._parse_category_sync, cat_slug)
                    if listings:
                        for listing in listings:
                            if not await Database.is_listing_seen(self.name, listing['id']):
                                await Database.add_seen_listing(self.name, listing['id'])
                                await self.trigger_new_listing(listing)
                    success = True
                    await asyncio.sleep(3)
                except Exception as e:
                    self.logger.error(f"Ошибка проверки категории {cat_slug} на FL.ru: {e}", exc_info=True)
                    success = False

            if success:
                self.backoff.success()
                await self.backoff.sleep(self.check_interval)
            else:
                await self.backoff.failure()
