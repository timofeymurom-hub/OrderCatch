import aiosqlite
import json
import os
from datetime import datetime

DB_FILE = "database.db"

def get_admin_id():
    if os.path.exists("config.json"):
        try:
            with open("config.json", "r", encoding="utf-8") as f:
                cfg = json.load(f)
                return int(cfg.get("TG_CHAT_ID", 0))
        except Exception:
            pass
    return None

SEEN_CACHE = set()

async def init_db():
    async with aiosqlite.connect(DB_FILE) as db:
        await db.execute("PRAGMA journal_mode=WAL;")
        await db.execute("PRAGMA synchronous=NORMAL;")
        
        # Таблица пользователей
        await db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                tg_id INTEGER PRIMARY KEY,
                username TEXT,
                tier TEXT DEFAULT 'free', -- 'free', 'referral', 'premium'
                referred_by INTEGER,
                referral_code TEXT UNIQUE,
                referral_count INTEGER DEFAULT 0,
                max_filters INTEGER DEFAULT 3,
                quiet_hours INTEGER DEFAULT 0, -- 0 = Выкл, 1 = Вкл (23:00 - 08:00)
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # Миграция: добавим quiet_hours и premium_until если таблица уже создана
        try:
            await db.execute("ALTER TABLE users ADD COLUMN quiet_hours INTEGER DEFAULT 0")
        except Exception:
            pass
        try:
            await db.execute("ALTER TABLE users ADD COLUMN premium_until TIMESTAMP")
        except Exception:
            pass
        
        # Таблица фильтров
        await db.execute("""
            CREATE TABLE IF NOT EXISTS filters (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                platform TEXT, -- 'kwork', 'funpay', 'playerok', 'avito'
                category_id TEXT, -- ID или URL категории
                category_name TEXT, -- Человеческое название
                keywords TEXT, -- JSON-массив ключевых слов
                min_price REAL,
                max_price REAL,
                is_active INTEGER DEFAULT 1,
                FOREIGN KEY(user_id) REFERENCES users(tg_id) ON DELETE CASCADE
            )
        """)
        await db.execute("CREATE INDEX IF NOT EXISTS idx_filters_platform ON filters(platform, is_active);")
        
        # Таблица просмотренных объявлений
        await db.execute("""
            CREATE TABLE IF NOT EXISTS seen_listings (
                platform TEXT,
                listing_id TEXT,
                discovered_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (platform, listing_id)
            )
        """)
        await db.execute("CREATE INDEX IF NOT EXISTS idx_seen_plat_item ON seen_listings(platform, listing_id);")
        
        # Предзагрузка кэша просмотренных объявлений для мнгновенной работы без лагов
        async with db.execute("SELECT platform, listing_id FROM seen_listings") as cursor:
            rows = await cursor.fetchall()
            for r in rows:
                SEEN_CACHE.add(f"{r[0]}:{r[1]}")
        
        # Таблица сохраненных лотов (Избранное)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS saved_bookmarks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                platform TEXT,
                title TEXT,
                url TEXT,
                budget TEXT,
                category_name TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # Таблица обращений в поддержку
        await db.execute("""
            CREATE TABLE IF NOT EXISTS support_tickets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                username TEXT,
                message TEXT,
                status TEXT DEFAULT 'open', -- 'open', 'resolved'
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        await db.commit()

    # Автоматически пропишем админа как Premium, если он есть в базе
    admin_id = get_admin_id()
    if admin_id:
        try:
            async with aiosqlite.connect(DB_FILE) as db:
                async with db.execute("SELECT tg_id FROM users WHERE tg_id = ?", (admin_id,)) as cursor:
                    exists = await cursor.fetchone()
                if exists:
                    await db.execute("""
                        UPDATE users 
                        SET tier = 'premium', max_filters = 99999 
                        WHERE tg_id = ?
                    """, (admin_id,))
                    await db.commit()
        except Exception:
            pass

class Database:
    @staticmethod
    async def add_user(tg_id: int, username: str, referred_by: int = None) -> bool:
        from datetime import datetime, timedelta
        ref_code = f"ref_{tg_id}"
        async with aiosqlite.connect(DB_FILE) as db:
            # Проверим, существует ли пользователь
            async with db.execute("SELECT tg_id FROM users WHERE tg_id = ?", (tg_id,)) as cursor:
                if await cursor.fetchone():
                    return False
            
            # Если есть реферер, проверим его и начислим ему реферала
            if referred_by and referred_by != tg_id:
                async with db.execute("SELECT tg_id FROM users WHERE tg_id = ?", (referred_by,)) as cursor:
                    ref_exists = await cursor.fetchone()
                if ref_exists:
                    await db.execute("""
                        UPDATE users 
                        SET referral_count = referral_count + 1 
                        WHERE tg_id = ?
                    """, (referred_by,))
                    
                    # Обновим тир рефереру, если у него стало >= 3 рефералов
                    async with db.execute("SELECT referral_count, tier FROM users WHERE tg_id = ?", (referred_by,)) as cursor:
                        row = await cursor.fetchone()
                        if row and row[0] >= 3 and row[1] == 'free':
                            await db.execute("""
                                UPDATE users 
                                SET tier = 'referral', max_filters = 10 
                                WHERE tg_id = ?
                            """, (referred_by,))
            
            admin_id = get_admin_id()
            if admin_id and tg_id == admin_id:
                user_tier = 'premium'
                max_filters = 99999
                prem_until = None
            else:
                user_tier = 'premium' # Пробный Premium 3 дня всем новым
                max_filters = 10
                prem_until = (datetime.now() + timedelta(days=3)).isoformat()

            await db.execute("""
                INSERT INTO users (tg_id, username, referred_by, referral_code, tier, max_filters, premium_until) 
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (tg_id, username, referred_by, ref_code, user_tier, max_filters, prem_until))
            await db.commit()
            return True

    @staticmethod
    async def get_user(tg_id: int) -> dict:
        from datetime import datetime
        async with aiosqlite.connect(DB_FILE) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute("SELECT * FROM users WHERE tg_id = ?", (tg_id,)) as cursor:
                row = await cursor.fetchone()
                if row:
                    u = dict(row)
                    admin_id = get_admin_id()
                    if admin_id and tg_id == admin_id:
                        u['tier'] = 'premium'
                        u['max_filters'] = 99999
                        return u
                    
                    # Проверяем не истек ли пробный Premium
                    prem_until = u.get('premium_until')
                    if prem_until:
                        try:
                            until_dt = datetime.fromisoformat(prem_until)
                            if datetime.now() > until_dt:
                                u['tier'] = 'free'
                                u['max_filters'] = 3
                                u['premium_until'] = None
                                await db.execute("UPDATE users SET tier = 'free', max_filters = 3, premium_until = NULL WHERE tg_id = ?", (tg_id,))
                                await db.commit()
                        except Exception:
                            pass
                    return u
                return None

    @staticmethod
    async def get_all_users() -> list:
        async with aiosqlite.connect(DB_FILE) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute("SELECT * FROM users") as cursor:
                rows = await cursor.fetchall()
                res = []
                admin_id = get_admin_id()
                for r in rows:
                    u = dict(r)
                    if admin_id and u['tg_id'] == admin_id:
                        u['tier'] = 'premium'
                        u['max_filters'] = 99999
                    res.append(u)
                return res

    @staticmethod
    async def set_user_tier(tg_id: int, tier: str) -> None:
        admin_id = get_admin_id()
        if admin_id and tg_id == admin_id:
            tier = 'premium'
            max_filters = 99999
        else:
            max_filters = 3 if tier == 'free' else (10 if tier == 'referral' else 99999)
            
        async with aiosqlite.connect(DB_FILE) as db:
            await db.execute("""
                UPDATE users 
                SET tier = ?, max_filters = ? 
                WHERE tg_id = ?
            """, (tier, max_filters, tg_id))
            await db.commit()

    @staticmethod
    async def add_filter(user_id: int, platform: str, category_id: str, category_name: str, 
                         keywords: list, min_price: float = None, max_price: float = None) -> int:
        async with aiosqlite.connect(DB_FILE) as db:
            keywords_json = json.dumps(keywords, ensure_ascii=False)
            cursor = await db.execute("""
                INSERT INTO filters (user_id, platform, category_id, category_name, keywords, min_price, max_price) 
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (user_id, platform, category_id, category_name, keywords_json, min_price, max_price))
            await db.commit()
            return cursor.lastrowid

    @staticmethod
    async def get_user_filters(user_id: int) -> list:
        async with aiosqlite.connect(DB_FILE) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute("SELECT * FROM filters WHERE user_id = ?", (user_id,)) as cursor:
                rows = await cursor.fetchall()
                res = []
                for r in rows:
                    d = dict(r)
                    d['keywords'] = json.loads(d['keywords']) if d['keywords'] else []
                    res.append(d)
                return res

    @staticmethod
    async def delete_filter(filter_id: int, user_id: int) -> bool:
        async with aiosqlite.connect(DB_FILE) as db:
            cursor = await db.execute("DELETE FROM filters WHERE id = ? AND user_id = ?", (filter_id, user_id))
            await db.commit()
            return cursor.rowcount > 0

    @staticmethod
    async def delete_all_user_filters(user_id: int) -> int:
        async with aiosqlite.connect(DB_FILE) as db:
            cursor = await db.execute("DELETE FROM filters WHERE user_id = ?", (user_id,))
            await db.commit()
            return cursor.rowcount

    @staticmethod
    async def get_active_filters_for_platform(platform: str) -> list:
        async with aiosqlite.connect(DB_FILE) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute("""
                SELECT f.*, u.tier FROM filters f
                JOIN users u ON f.user_id = u.tg_id
                WHERE f.platform = ? AND f.is_active = 1
            """, (platform,)) as cursor:
                rows = await cursor.fetchall()
                res = []
                for r in rows:
                    d = dict(r)
                    d['keywords'] = json.loads(d['keywords']) if d['keywords'] else []
                    res.append(d)
                return res

    @staticmethod
    async def is_listing_seen(platform: str, listing_id: str) -> bool:
        key = f"{platform}:{listing_id}"
        if key in SEEN_CACHE:
            return True
        async with aiosqlite.connect(DB_FILE) as db:
            async with db.execute("""
                SELECT 1 FROM seen_listings 
                WHERE platform = ? AND listing_id = ?
            """, (platform, str(listing_id))) as cursor:
                seen = await cursor.fetchone() is not None
                if seen:
                    SEEN_CACHE.add(key)
                return seen

    @staticmethod
    async def add_seen_listing(platform: str, listing_id: str) -> None:
        key = f"{platform}:{listing_id}"
        SEEN_CACHE.add(key)
        if len(SEEN_CACHE) > 3000:
            SEEN_CACHE.clear()
        async with aiosqlite.connect(DB_FILE) as db:
            await db.execute("""
                INSERT OR IGNORE INTO seen_listings (platform, listing_id) 
                VALUES (?, ?)
            """, (platform, str(listing_id)))
            await db.commit()
            
    @staticmethod
    def prune_seen_cache() -> None:
        global SEEN_CACHE
        SEEN_CACHE.clear()

    @staticmethod
    async def clear_old_seen_listings(days: int = 3) -> None:
        async with aiosqlite.connect(DB_FILE) as db:
            await db.execute("""
                DELETE FROM seen_listings 
                WHERE datetime(discovered_at) < datetime('now', ?)
            """, (f'-{days} days',))
            await db.execute("PRAGMA shrink_memory;")
            await db.commit()

    @staticmethod
    async def add_support_ticket(user_id: int, username: str, message: str) -> int:
        async with aiosqlite.connect(DB_FILE) as db:
            cursor = await db.execute("""
                INSERT INTO support_tickets (user_id, username, message) 
                VALUES (?, ?, ?)
            """, (user_id, username, message))
            await db.commit()
            return cursor.lastrowid

    @staticmethod
    async def get_active_support_tickets() -> list:
        async with aiosqlite.connect(DB_FILE) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute("SELECT * FROM support_tickets WHERE status = 'open' ORDER BY created_at DESC") as cursor:
                rows = await cursor.fetchall()
                return [dict(r) for r in rows]

    @staticmethod
    async def resolve_support_ticket(ticket_id: int) -> None:
        async with aiosqlite.connect(DB_FILE) as db:
            await db.execute("UPDATE support_tickets SET status = 'resolved' WHERE id = ?", (ticket_id,))
            await db.commit()

    @staticmethod
    async def resolve_last_ticket_from_user(user_id: int) -> None:
        async with aiosqlite.connect(DB_FILE) as db:
            await db.execute("""
                UPDATE support_tickets 
                SET status = 'resolved' 
                WHERE id = (
                    SELECT id FROM support_tickets 
                    WHERE user_id = ? AND status = 'open' 
                    ORDER BY created_at DESC LIMIT 1
                )
            """, (user_id,))
            await db.commit()

    @staticmethod
    async def get_support_ticket(ticket_id: int) -> dict:
        async with aiosqlite.connect(DB_FILE) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute("SELECT * FROM support_tickets WHERE id = ?", (ticket_id,)) as cursor:
                row = await cursor.fetchone()
                return dict(row) if row else None

    @staticmethod
    async def toggle_quiet_hours(user_id: int) -> int:
        async with aiosqlite.connect(DB_FILE) as db:
            async with db.execute("SELECT quiet_hours FROM users WHERE tg_id = ?", (user_id,)) as cursor:
                row = await cursor.fetchone()
                current = row[0] if row and row[0] is not None else 0
            new_val = 0 if current == 1 else 1
            await db.execute("UPDATE users SET quiet_hours = ? WHERE tg_id = ?", (new_val, user_id))
            await db.commit()
            return new_val

    @staticmethod
    async def add_bookmark(user_id: int, platform: str, title: str, url: str, budget: str, category_name: str) -> int:
        async with aiosqlite.connect(DB_FILE) as db:
            cursor = await db.execute("""
                INSERT INTO saved_bookmarks (user_id, platform, title, url, budget, category_name)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (user_id, platform, title, url, budget, category_name))
            await db.commit()
            return cursor.lastrowid

    @staticmethod
    async def get_user_bookmarks(user_id: int) -> list:
        async with aiosqlite.connect(DB_FILE) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute("SELECT * FROM saved_bookmarks WHERE user_id = ? ORDER BY created_at DESC", (user_id,)) as cursor:
                rows = await cursor.fetchall()
                return [dict(r) for r in rows]

    @staticmethod
    async def delete_bookmark(bookmark_id: int, user_id: int) -> bool:
        async with aiosqlite.connect(DB_FILE) as db:
            cursor = await db.execute("DELETE FROM saved_bookmarks WHERE id = ? AND user_id = ?", (bookmark_id, user_id))
            await db.commit()
            return cursor.rowcount > 0

    @staticmethod
    async def get_user_analytics(user_id: int) -> dict:
        async with aiosqlite.connect(DB_FILE) as db:
            filters = await Database.get_user_filters(user_id)
            filter_count = len(filters)
            
            async with db.execute("SELECT COUNT(*) FROM saved_bookmarks WHERE user_id = ?", (user_id,)) as c1:
                bookmarks_count = (await c1.fetchone())[0]
                
            async with db.execute("SELECT COUNT(*) FROM seen_listings") as c2:
                total_seen = (await c2.fetchone())[0]
                
            return {
                "filters_count": filter_count,
                "bookmarks_count": bookmarks_count,
                "total_seen_listings": total_seen
            }

    @staticmethod
    async def get_system_stats() -> dict:
        async with aiosqlite.connect(DB_FILE) as db:
            async with db.execute("SELECT COUNT(*) FROM users") as c1:
                users_count = (await c1.fetchone())[0]
            async with db.execute("SELECT COUNT(*) FROM filters WHERE is_active = 1") as c2:
                filters_count = (await c2.fetchone())[0]
            async with db.execute("SELECT COUNT(*) FROM support_tickets WHERE status = 'open'") as c3:
                tickets_count = (await c3.fetchone())[0]
            async with db.execute("SELECT COUNT(*) FROM users WHERE tier = 'free'") as c_free:
                free_users = (await c_free.fetchone())[0]
            async with db.execute("SELECT COUNT(*) FROM users WHERE tier = 'referral'") as c_ref:
                ref_users = (await c_ref.fetchone())[0]
            async with db.execute("SELECT COUNT(*) FROM users WHERE tier = 'premium'") as c_prem:
                premium_users = (await c_prem.fetchone())[0]
                
            return {
                "users": users_count,
                "filters": filters_count,
                "tickets": tickets_count,
                "free_users": free_users,
                "referral_users": ref_users,
                "premium_users": premium_users
            }
