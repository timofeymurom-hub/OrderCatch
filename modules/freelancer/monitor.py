import asyncio
import tls_requests
from core.base_module import BaseModule
from core.rate_limiter import ExponentialBackoff
from database import Database

class FreelancerMonitor(BaseModule):
    def __init__(self, check_interval: float = 120.0):
        super().__init__("freelancer")
        self.check_interval = check_interval
        self.backoff = ExponentialBackoff(base_delay=15.0, max_delay=300.0)
        
        # Курсы валют по умолчанию для конвертации в рубли
        self.currency_rates = {
            'RUB': 1.0,
            'USD': 90.0,
            'EUR': 98.0,
            'GBP': 114.0,
            'CAD': 66.0,
            'AUD': 60.0,
            'INR': 1.1,
            'UAH': 2.2,
            'NZD': 55.0,
            'SGD': 67.0,
            'HKD': 11.5,
        }

    def _parse_category_sync(self, category_id: str):
        url = "https://www.freelancer.com/api/projects/0.1/projects/active/"
        params = {
            'limit': 15,
            'compact': 'false'
        }
        
        # Если передан ID навыка/работы, добавляем его в параметры
        if category_id and category_id != "0":
            if category_id.startswith('jobs[]='):
                # Извлекаем только число из jobs[]=3
                job_id = category_id.replace('jobs[]=', '')
                params['jobs[]'] = job_id
            else:
                params['jobs[]'] = category_id

        headers = {
            'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/135.0.0.0 Safari/537.36',
            'accept': 'application/json',
        }
        
        response = tls_requests.get(url, params=params, headers=headers, timeout=20)
        response.raise_for_status()
        
        data = response.json()
        projects = data.get('result', {}).get('projects', [])
        
        listings = []
        for proj in projects:
            proj_id = str(proj.get('id'))
            title = proj.get('title', 'Без названия')
            seo_url = proj.get('seo_url', '')
            href = f"https://www.freelancer.com/projects/{seo_url}" if seo_url else f"https://www.freelancer.com/projects/project-{proj_id}.html"
            
            # Описание
            description = proj.get('preview_description', 'Без описания')
            
            # Бюджет и валюта
            curr_info = proj.get('currency', {})
            curr_code = curr_info.get('code', 'USD').upper()
            curr_sign = curr_info.get('sign', '$')
            
            budget_info = proj.get('budget', {})
            min_b = budget_info.get('minimum')
            max_b = budget_info.get('maximum')
            
            # Расчет бюджета
            price_val = 0.0
            price_str = "Договорная"
            
            if min_b is not None:
                # Берем максимальный бюджет для фильтрации, если есть
                raw_price = max_b if max_b is not None else min_b
                rate = self.currency_rates.get(curr_code, 90.0)
                price_val = float(raw_price) * rate
                
                if max_b is not None:
                    price_str = f"{min_b} - {max_b} {curr_sign} (~{int(price_val)} руб)"
                else:
                    price_str = f"от {min_b} {curr_sign} (~{int(price_val)} руб)"
            
            listings.append({
                'id': proj_id,
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
            
            self.logger.info(f"Начало проверки Freelancer.com. Категорий/лент: {len(categories)}")
            
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
                    self.logger.error(f"Ошибка проверки категории {cat_id} на Freelancer.com: {e}", exc_info=True)
                    success = False

            if success:
                self.backoff.success()
                await self.backoff.sleep(self.check_interval)
            else:
                await self.backoff.failure()
