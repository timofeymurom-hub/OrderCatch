import asyncio
import json
import logging
import os
import sys
import uvicorn
from contextlib import asynccontextmanager
from fastapi import FastAPI
from aiogram import Bot, Dispatcher as AiogramDispatcher
from aiogram.fsm.storage.memory import MemoryStorage

# Импортируем наши модули
from database import init_db
from core.dispatcher import Dispatcher
from core.base_module import BaseModule
from modules.kwork.monitor import KworkMonitor
from modules.funpay.monitor import FunPayMonitor
from modules.playerok.monitor import PlayerokMonitor
from modules.avito.monitor import AvitoMonitor
from modules.freelancehunt.monitor import FreelancehuntMonitor
from modules.freelance_ru.monitor import FreelanceRuMonitor
from modules.fl.monitor import FlMonitor
from modules.freelancer.monitor import FreelancerMonitor
from modules.habr.monitor import HabrFreelanceMonitor
from modules.workzilla.monitor import WorkZillaMonitor
from modules.youla.monitor import YoulaMonitor
from modules.profi.monitor import ProfiMonitor
from modules.yandex_services.monitor import YandexServicesMonitor
from modules.guru.monitor import GuruMonitor
from modules.plati.monitor import PlatiMonitor
from modules.g2g.monitor import G2GMonitor
from modules.olx.monitor import OlxMonitor
from modules.kufar.monitor import KufarMonitor
from modules.peopleperhour.monitor import PeoplePerHourMonitor
from modules.eldorado.monitor import EldoradoMonitor
from modules.kadrof.monitor import KadrofMonitor
from modules.g2a.monitor import G2AMonitor
from bot.telegram_bot import router as bot_router
import web.app as web_app

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("app.log", encoding="utf-8")
    ]
)
logger = logging.getLogger("main")

# Глобальные объекты управления
bot = None
aiogram_dp = None
system_dispatcher = None
polling_task = None
is_restarting = False

def load_system_config():
    if os.path.exists("config.json"):
        with open("config.json", "r", encoding="utf-8") as f:
            return json.load(f)
    return {
        "TG_BOT_TOKEN": "",
        "KWORK_COOKIE": "",
        "CHECK_INTERVAL": 120,
        "WEB_PORT": 5000,
        "ADMIN_PASS": "admin"
    }

async def start_all():
    global bot, aiogram_dp, system_dispatcher, polling_task
    cfg = load_system_config()
    
    token = cfg.get("TG_BOT_TOKEN", "").strip()
    if not token:
        logger.error("Токен Telegram-бота не задан! Бот не будет запущен. Укажите токен в веб-панели.")
        # Создадим заглушку бота, чтобы диспетчер мог работать (хотя слать сообщения он не сможет)
        bot = None
    else:
        try:
            bot = Bot(token=token)
            aiogram_dp = AiogramDispatcher(storage=MemoryStorage())
            aiogram_dp.include_router(bot_router)
            
            # Запускаем поллинг бота в фоновом режиме
            # Мы передаем bot в start_polling
            polling_task = asyncio.create_task(aiogram_dp.start_polling(bot))
            logger.info("Telegram-бот (aiogram) запущен.")
        except Exception as e:
            logger.error(f"Не удалось запустить Telegram-бот: {e}", exc_info=True)
            bot = None
            
    # Создаем диспетчер мониторингов
    system_dispatcher = Dispatcher(bot_instance=bot)
    web_app.dispatcher_instance = system_dispatcher
    
    # Инициализируем модули
    check_interval = float(cfg.get("CHECK_INTERVAL", 120))
    kwork_mon = KworkMonitor(check_interval=check_interval)
    kwork_mon.set_cookie(cfg.get("KWORK_COOKIE", ""))
    
    funpay_mon = FunPayMonitor(check_interval=max(30.0, check_interval / 2)) # FunPay проверим чаще
    playerok_mon = PlayerokMonitor(check_interval=max(30.0, check_interval / 2))
    avito_mon = AvitoMonitor(check_interval=300.0) # Avito реже
    freelancehunt_mon = FreelancehuntMonitor(check_interval=check_interval)
    freelance_ru_mon = FreelanceRuMonitor(check_interval=check_interval)
    fl_mon = FlMonitor(check_interval=check_interval)
    freelancer_mon = FreelancerMonitor(check_interval=check_interval)
    habr_mon = HabrFreelanceMonitor(check_interval=check_interval)
    workzilla_mon = WorkZillaMonitor(check_interval=check_interval)
    youla_mon = YoulaMonitor(check_interval=180.0)
    profi_mon = ProfiMonitor(check_interval=check_interval)
    yandex_mon = YandexServicesMonitor(check_interval=check_interval)
    guru_mon = GuruMonitor(check_interval=check_interval)
    plati_mon = PlatiMonitor(check_interval=max(30.0, check_interval / 2))
    g2g_mon = G2GMonitor(check_interval=max(30.0, check_interval / 2))
    olx_mon = OlxMonitor(check_interval=180.0)
    kufar_mon = KufarMonitor(check_interval=180.0)
    pph_mon = PeoplePerHourMonitor(check_interval=check_interval)
    eldorado_mon = EldoradoMonitor(check_interval=max(30.0, check_interval / 2))
    kadrof_mon = KadrofMonitor(check_interval=check_interval)
    g2a_mon = G2AMonitor(check_interval=max(30.0, check_interval / 2))
    
    # Регистрируем
    system_dispatcher.register_module(kwork_mon)
    system_dispatcher.register_module(funpay_mon)
    system_dispatcher.register_module(playerok_mon)
    system_dispatcher.register_module(avito_mon)
    system_dispatcher.register_module(freelancehunt_mon)
    system_dispatcher.register_module(freelance_ru_mon)
    system_dispatcher.register_module(fl_mon)
    system_dispatcher.register_module(freelancer_mon)
    system_dispatcher.register_module(habr_mon)
    system_dispatcher.register_module(workzilla_mon)
    system_dispatcher.register_module(youla_mon)
    system_dispatcher.register_module(profi_mon)
    system_dispatcher.register_module(yandex_mon)
    system_dispatcher.register_module(guru_mon)
    system_dispatcher.register_module(plati_mon)
    system_dispatcher.register_module(g2g_mon)
    system_dispatcher.register_module(olx_mon)
    system_dispatcher.register_module(kufar_mon)
    system_dispatcher.register_module(pph_mon)
    system_dispatcher.register_module(eldorado_mon)
    system_dispatcher.register_module(kadrof_mon)
    system_dispatcher.register_module(g2a_mon)
    
    # Запускаем диспетчер
    await system_dispatcher.start()

async def stop_all():
    global bot, aiogram_dp, system_dispatcher, polling_task
    
    logger.info("Остановка всех компонентов...")
    if system_dispatcher:
        await system_dispatcher.stop()
        
    if aiogram_dp and polling_task:
        await aiogram_dp.stop_polling()
        polling_task.cancel()
        try:
            await polling_task
        except asyncio.CancelledError:
            pass
            
    if bot:
        await bot.session.close()
        
    logger.info("Все компоненты успешно остановлены.")

async def perform_restart():
    global is_restarting
    if is_restarting:
        return
    is_restarting = True
    logger.info("--- ПЕРЕЗАПУСК СИСТЕМЫ ---")
    await stop_all()
    await asyncio.sleep(2)
    await start_all()
    is_restarting = False
    logger.info("--- СИСТЕМА ПЕРЕЗАПУЩЕНА ---")

# Связываем функцию перезапуска из веб-панели с нашей функцией
web_app.restart_system = perform_restart

@asynccontextmanager
async def lifespan(app: FastAPI):
    # При старте FastAPI
    await init_db()
    await start_all()
    yield
    # При остановке FastAPI
    await stop_all()

# Подключаем lifespan к FastAPI приложению
web_app.app.router.lifespan_context = lifespan

if __name__ == "__main__":
    cfg = load_system_config()
    port = int(os.environ.get("PORT", cfg.get("WEB_PORT", 5000)))
    logger.info(f"Запуск FastAPI веб-сервера на порту {port}...")
    
    # Запускаем uvicorn
    # На Windows loop может быть SelectorEventLoop
    if sys.platform == 'win32':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
        
    uvicorn.run(web_app.app, host="0.0.0.0", port=port, log_level="warning")
