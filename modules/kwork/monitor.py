import asyncio
import re
from tls_requests import requests
from urllib.parse import urlparse, parse_qs
from bs4 import BeautifulSoup
from core.base_module import BaseModule
from core.rate_limiter import ExponentialBackoff
from database import Database

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"

class KworkMonitor(BaseModule):
    def __init__(self, check_interval: float = 120.0):
        super().__init__("kwork")
        self.check_interval = check_interval
        self.backoff = ExponentialBackoff(base_delay=10.0, max_delay=300.0)
        self.cookie = ""

    def set_cookie(self, cookie: str):
        self.cookie = cookie

    def _parse_category_sync(self, category_id: str):
        # Формируем URL
        url = f"https://kwork.ru/projects?c={category_id}"
        
        # Готовим куки с принудительной рублевой валютой parent_currency=rub
        cookie_str = self.cookie.strip()
        if cookie_str:
            if "parent_currency" not in cookie_str:
                cookie_str = f"parent_currency=rub; {cookie_str}".strip("; ")
        else:
            cookie_str = "parent_currency=rub"

        # 1. AJAX JSON-эндпоинт
        try:
            post_data = {"a": "1", "c": category_id}
            ajax_headers = {
                "User-Agent": USER_AGENT,
                "X-Requested-With": "XMLHttpRequest",
                "Accept": "application/json, text/plain, */*",
                "Cookie": cookie_str
            }
                
            ajax_url = "https://kwork.ru/projects"
            ajax_resp = requests.post(ajax_url, headers=ajax_headers, data=post_data, timeout=15)
            
            if ajax_resp.status_code == 200 and "application/json" in ajax_resp.headers.get("Content-Type", ""):
                res_json = ajax_resp.json()
                if res_json.get("success") is True:
                    items = res_json.get("data", {}).get("pagination", {}).get("data", [])
                    orders = []
                    for item in items:
                        project_id = str(item.get("id"))
                        title = item.get("name", "").strip()
                        description = item.get("description", "").strip()
                        
                        price_min = int(float(item.get("priceLimit", 0)))
                        price_max = item.get("possiblePriceLimit")
                        budget_val = price_min
                        if price_max and price_max > price_min:
                            budget = f"{price_min} - {price_max} ₽"
                            budget_val = price_max
                        elif price_min:
                            budget = f"{price_min} ₽"
                        else:
                            budget = "Не указан"
                            budget_val = 0
                            
                        full_url = f"https://kwork.ru/projects/{project_id}"
                        
                        orders.append({
                            'id': project_id,
                            'title': title,
                            'url': full_url,
                            'budget': budget,
                            'budget_val': budget_val,
                            'description': description,
                            'category_id': category_id
                        })
                    if orders:
                        return orders
        except Exception as e:
            self.logger.warning(f"Ошибка парсинга AJAX для Kwork категории {category_id}: {e}")

        # 2. Фолбек на HTML
        headers = {
            "User-Agent": USER_AGENT,
            "Cookie": cookie_str
        }

        response = requests.get(url, headers=headers, timeout=15)
        response.raise_for_status()

        soup = BeautifulSoup(response.text, 'html.parser')
        cards = soup.select('.want-card')
        if not cards:
            cards = [el for el in soup.find_all(class_=True) if any('want-card' in c for c in el.get('class'))]

        orders = []
        for card in cards:
            title_el = card.select_one('.wants-card__header-title a')
            if not title_el:
                title_header = card.select_one('.wants-card__header-title')
                if title_header:
                    title_el = title_header.find('a')
            if not title_el:
                title_el = card.find('a', href=re.compile(r'/projects/\d+'))
            if not title_el:
                continue

            title = title_el.get_text(strip=True)
            url_path = title_el.get('href', '')
            if not url_path:
                continue

            full_url = "https://kwork.ru" + url_path if url_path.startswith('/') else url_path
            match = re.search(r'/projects/(\d+)', url_path)
            if not match:
                continue
            project_id = match.group(1)

            price_el = card.select_one('.wants-card__price')
            if not price_el:
                price_el = card.select_one('.wants-card__header-price')
            
            budget = price_el.get_text(strip=True) if price_el else "Не указан"
            budget_val = 0
            price_digits = re.findall(r'\d+', budget.replace(' ', ''))
            if price_digits:
                budget_val = float(price_digits[-1])
                
                # Защита от евро/долларов при европейском IP
                budget_lower = budget.lower()
                if '$' in budget_lower or 'usd' in budget_lower:
                    budget_val = budget_val * 90.0
                elif '€' in budget_lower or 'eur' in budget_lower:
                    budget_val = budget_val * 100.0

            desc_el = card.select_one('.wants-card__description-text')
            if desc_el:
                inner_desc = desc_el.select_one('.breakwords > .d-inline')
                description = inner_desc.get_text(strip=True) if inner_desc else desc_el.get_text(strip=True)
            else:
                description = ""
            description = re.sub(r'\s+', ' ', description).strip()

            orders.append({
                'id': project_id,
                'title': title,
                'url': full_url,
                'budget': budget,
                'budget_val': budget_val,
                'description': description,
                'category_id': category_id
            })

        return orders

    async def run(self):
        while self.is_running:
            if not self.active_categories:
                await asyncio.sleep(5)
                continue

            self.logger.info(f"Начало проверки Kwork. Категорий: {len(self.active_categories)}")
            
            success = False
            for cat_id in list(self.active_categories):
                try:
                    # Запускаем синхронный парсинг в отдельном потоке
                    orders = await asyncio.to_thread(self._parse_category_sync, cat_id)
                    if orders:
                        for order in orders:
                            await self.process_listing(cat_id, order)
                        self.mark_category_seeded(cat_id)
                    success = True
                except Exception as e:
                    self.logger.error(f"Ошибка проверки категории {cat_id} на Kwork: {e}", exc_info=True)
                    success = False

            if success:
                self.backoff.success()
                await self.backoff.sleep(self.check_interval)
            else:
                await self.backoff.failure()
