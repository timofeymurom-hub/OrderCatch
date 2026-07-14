import asyncio
import json
import tls_requests
from core.base_module import BaseModule
from core.rate_limiter import ExponentialBackoff
from database import Database

GRAPHQL_URL = "https://playerok.com/graphql"

class PlayerokMonitor(BaseModule):
    def __init__(self, check_interval: float = 60.0):
        super().__init__("playerok")
        self.check_interval = check_interval
        self.backoff = ExponentialBackoff(base_delay=15.0, max_delay=300.0)
        self.uuid_cache = {} # Маппинг "game_slug/category_slug" -> "UUID"

    def _resolve_category_uuid(self, game_slug: str, category_slug: str) -> str:
        cache_key = f"{game_slug}/{category_slug}"
        if cache_key in self.uuid_cache:
            return self.uuid_cache[cache_key]

        headers = {
            'accept': '*/*',
            'accept-language': 'ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7',
            'access-control-allow-headers': 'sentry-trace, baggage',
            'apollo-require-preflight': 'true',
            'apollographql-client-name': 'web',
            'content-type': 'application/json',
            'origin': 'https://playerok.com',
            'referer': f'https://playerok.com/games/{game_slug}/{category_slug}',
            'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/135.0.0.0 Safari/537.36',
            'x-gql-op': 'GamePageCategory',
            'x-gql-path': f'/games/{game_slug}/{category_slug}',
            'x-timezone-offset': '-180',
        }
        
        payload = {
            "operationName": "GamePageCategory",
            "variables": {
                "id": None,
                "gameId": None,
                "slug": category_slug
            },
            "extensions": {
                "persistedQuery": {
                    "version": 1,
                    "sha256Hash": "7759f743651176ddad6afefb5f2e889ec9984cae08a015281879cd61e94bdb60"
                }
            }
        }
        
        response = tls_requests.post(GRAPHQL_URL, headers=headers, json=payload, timeout=15)
        response.raise_for_status()
        
        data = response.json()
        uuid = data.get('data', {}).get('gameCategory', {}).get('id')
        if uuid:
            self.uuid_cache[cache_key] = uuid
            self.logger.info(f"Резолвинг категории {cache_key} -> {uuid}")
            return uuid
        raise ValueError(f"Не удалось получить UUID категории для {cache_key}")

    def _parse_category_sync(self, game_slug: str, category_slug: str, uuid: str):
        headers = {
            'accept': '*/*',
            'accept-language': 'ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7',
            'access-control-allow-headers': 'sentry-trace, baggage',
            'apollo-require-preflight': 'true',
            'apollographql-client-name': 'web',
            'content-type': 'application/json',
            'origin': 'https://playerok.com',
            'referer': f'https://playerok.com/games/{game_slug}/{category_slug}',
            'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/135.0.0.0 Safari/537.36',
            'x-gql-op': 'items',
            'x-gql-path': f'/games/{game_slug}/{category_slug}',
            'x-timezone-offset': '-180',
        }
        
        payload = {
            "operationName": "items",
            "variables": {
                "pagination": {
                    "first": 24,
                    "after": None
                },
                "filter": {
                    "gameCategoryId": uuid,
                    "status": ["APPROVED"]
                }
            },
            "extensions": {
                "persistedQuery": {
                    "version": 1,
                    "sha256Hash": "63eefcfd813442882ad846360d925279bc376e8bc85a577ebefbee0f9c78b557"
                }
            }
        }
        
        response = tls_requests.post(GRAPHQL_URL, headers=headers, json=payload, timeout=15)
        response.raise_for_status()
        
        data = response.json()
        edges = data.get('data', {}).get('items', {}).get('edges', [])
        
        listings = []
        for edge in edges:
            node = edge.get('node', {})
            if not node:
                continue
                
            listing_id = node.get('id')
            title = node.get('name', 'Без названия')
            price = node.get('price', 0.0)
            slug = node.get('slug', '')
            desc = node.get('description', '')
            
            listings.append({
                'id': str(listing_id),
                'title': title,
                'url': f"https://playerok.com/products/{slug}" if slug else "https://playerok.com",
                'budget': f"{price} ₽",
                'budget_val': float(price),
                'description': desc,
                'category_id': f"{game_slug}/{category_slug}"
            })
            
        return listings

    async def run(self):
        while self.is_running:
            if not self.active_categories:
                await asyncio.sleep(5)
                continue

            self.logger.info(f"Начало проверки Playerok. Категорий: {len(self.active_categories)}")
            
            success = False
            for cat_str in list(self.active_categories):
                # Поддержка полных URL-адресов
                if 'playerok.com' in cat_str:
                    path = cat_str.split('playerok.com/')[-1].strip('/')
                    if path.startswith('games/'):
                        path = path[6:]
                    parts = path.split('/')
                    if len(parts) >= 2:
                        game_slug = parts[0]
                        category_slug = parts[1]
                    else:
                        game_slug = parts[0]
                        category_slug = "offers"
                else:
                    if '/' not in cat_str:
                        self.logger.warning(f"Категория {cat_str} не соответствует формату game_slug/category_slug")
                        continue
                    game_slug, category_slug = cat_str.split('/', 1)
                
                try:
                    # Резолвим UUID категории
                    uuid = await asyncio.to_thread(self._resolve_category_uuid, game_slug, category_slug)
                    
                    # Получаем лоты
                    listings = await asyncio.to_thread(self._parse_category_sync, game_slug, category_slug, uuid)
                    if listings:
                        for listing in listings:
                            if not await Database.is_listing_seen(self.name, listing['id']):
                                await Database.add_seen_listing(self.name, listing['id'])
                                await self.trigger_new_listing(listing)
                    success = True
                    await asyncio.sleep(3)
                except Exception as e:
                    self.logger.error(f"Ошибка проверки категории {cat_str} на Playerok: {e}", exc_info=True)
                    success = False

            if success:
                self.backoff.success()
                await self.backoff.sleep(self.check_interval)
            else:
                await self.backoff.failure()
