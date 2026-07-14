import asyncio
import hashlib
import tls_requests
from bs4 import BeautifulSoup
import re
from core.base_module import BaseModule
from core.rate_limiter import ExponentialBackoff
from database import Database

class G2AMonitor(BaseModule):
    def __init__(self, check_interval: float = 120.0):
        super().__init__("g2a")
        self.check_interval = check_interval
        self.backoff = ExponentialBackoff(base_delay=20.0, max_delay=300.0)

    def _parse_category_sync(self, category_id: str):
        if category_id.startswith('http'):
            url = category_id
        else:
            url = f"https://www.g2a.com/category/{category_id}" if category_id and category_id != "0" else "https://www.g2a.com/best-offers"

        headers = {
            'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36',
            'accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'accept-language': 'en-US,en;q=0.9',
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
        cards = soup.select('ul[class*="products"] > li') or soup.select('div[class*="Card"]') or soup.select('article') or soup.select('.item')

        listings = []
        for idx, card in enumerate(cards):
            title_el = card.select_one('h3') or card.select_one('h2') or card.select_one('a')
            if not title_el:
                continue
                
            title = title_el.get_text(strip=True)
            href = title_el.get('href', '') if title_el.name == 'a' else (title_el.find('a')['href'] if title_el.find('a') else '')
            if href and not href.startswith('http'):
                href = f"https://www.g2a.com{href}"
            elif not href:
                href = url
                
            match = re.search(r'i(\d+)', href) or re.search(r'/(\d+)', href)
            job_id = match.group(1) if match else hashlib.md5(f"{title}_{idx}".encode('utf-8')).hexdigest()[:12]
            
            price_el = card.select_one('[class*="price"]') or card.select_one('.price')
            price_str = price_el.get_text(strip=True) if price_el else "Цена по запросу"
            
            try:
                clean_price = re.sub(r'[^\d.]', '', price_str)
                eur_val = float(clean_price) if clean_price else 0.0
                price_val = eur_val * 98.0 if ('€' in price_str or '$' in price_str) else eur_val
            except Exception:
                price_val = 0.0

            listings.append({
                'id': str(job_id),
                'title': title,
                'url': href,
                'budget': price_str,
                'budget_val': price_val,
                'description': title,
                'category_id': category_id,
                'platform': 'g2a'
            })
            
        return listings

    async def run(self):
        while self.is_running:
            categories = list(self.active_categories) if self.active_categories else ["0"]
            self.logger.info(f"Начало проверки G2A. Категорий: {len(categories)}")
            
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
                    self.logger.error(f"Ошибка проверки G2A: {e}")
                    success = False

            if success:
                self.backoff.success()
                await self.backoff.sleep(self.check_interval)
            else:
                await self.backoff.failure()
