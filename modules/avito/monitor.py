import asyncio
import hashlib
import tls_requests
from bs4 import BeautifulSoup
import re
from core.base_module import BaseModule
from core.rate_limiter import ExponentialBackoff
from database import Database

class AvitoMonitor(BaseModule):
    def __init__(self, check_interval: float = 300.0):
        super().__init__("avito")
        self.check_interval = check_interval
        self.backoff = ExponentialBackoff(base_delay=30.0, max_delay=600.0)

    def _parse_category_sync(self, category_url: str):
        if not category_url.startswith('http'):
            # Если передали не ссылку, а поисковый запрос или категории
            if category_url == "0" or category_url == "it":
                url = "https://www.avito.ru/all/predlozheniya_uslug/it_frilans-ASgBAgICAUSYC8CfAQ"
            elif category_url == "design":
                url = "https://www.avito.ru/all/predlozheniya_uslug/dizayn-ASgBAgICAUSYC7yfAQ"
            elif category_url == "text":
                url = "https://www.avito.ru/all/predlozheniya_uslug/teksty_perevody-ASgBAgICAUSYC8CfAQ"
            else:
                url = f"https://www.avito.ru/all/predlozheniya_uslug?q={category_url}"
        else:
            url = category_url

        headers = {
            'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36',
            'accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8',
            'accept-language': 'ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7',
            'referer': 'https://www.avito.ru/'
        }
        
        try:
            response = tls_requests.get(url, headers=headers, timeout=20)
            html_text = response.text
        except Exception:
            import requests
            r = requests.get(url, headers=headers, timeout=20)
            r.raise_for_status()
            html_text = r.text
        
        soup = BeautifulSoup(html_text, 'html.parser')
        
        cards = soup.select('div[data-marker="item"]')
        if not cards:
            cards = soup.select('.iva-item-root')
        if not cards:
            cards = soup.select('div[itemtype="http://schema.org/Product"]')

        listings = []
        for idx, card in enumerate(cards):
            title_el = card.select_one('a[data-marker="item-title"]') or card.select_one('h3') or card.find('a', href=re.compile(r'/[a-zA-Z0-9_\-]+/\d+'))
            if not title_el:
                continue
                
            title = title_el.get_text(strip=True)
            href = title_el.get('href', '')
            if href and not href.startswith('http'):
                href = f"https://www.avito.ru{href}"
                
            # Идентификатор объявления из ссылки
            match = re.search(r'_(\d+)\??', href) or re.search(r'/(\d+)\??', href)
            item_id = match.group(1) if match else card.get('data-item-id') or str(idx)
            
            price_el = card.select_one('meta[itemprop="price"]') or card.select_one('span[data-marker="item-price"]') or card.select_one('[class*="price"]')
            price_str = price_el.get('content', '') if price_el and price_el.get('content') else (price_el.get_text(strip=True) if price_el else "По договоренности")
            
            try:
                clean_price = re.sub(r'[^\d]', '', price_str)
                price_val = float(clean_price) if clean_price else 0.0
            except Exception:
                price_val = 0.0

            desc_el = card.select_one('div[data-marker="item-description"]') or card.select_one('[class*="description"]')
            description = desc_el.get_text(strip=True) if desc_el else title
            description = re.sub(r'\s+', ' ', description).strip()
            
            listings.append({
                'id': str(item_id),
                'title': title,
                'url': href,
                'budget': price_str if price_str != "0" else "По договоренности",
                'budget_val': price_val,
                'description': description,
                'category_id': category_url,
                'platform': 'avito'
            })
            
        return listings

    async def run(self):
        while self.is_running:
            categories = list(self.active_categories) if self.active_categories else ["it"]
            self.logger.info(f"Начало проверки Avito. Категорий/ссылок: {len(categories)}")
            
            success = False
            for cat_url in categories:
                try:
                    listings = await asyncio.to_thread(self._parse_category_sync, cat_url)
                    if listings:
                        for listing in listings:
                            if not await Database.is_listing_seen(self.name, listing['id']):
                                await Database.add_seen_listing(self.name, listing['id'])
                                await self.trigger_new_listing(listing)
                    success = True
                    await asyncio.sleep(4)
                except Exception as e:
                    self.logger.error(f"Ошибка проверки Avito ({cat_url}): {e}")
                    success = False

            if success:
                self.backoff.success()
                await self.backoff.sleep(self.check_interval)
            else:
                await self.backoff.failure()
