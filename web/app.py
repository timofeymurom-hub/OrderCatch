import asyncio
import json
import os
from fastapi import FastAPI, Request, Form, Depends, HTTPException, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from database import Database, DB_FILE

app = FastAPI(title="Kwork & Multiplatform Parser Web Admin")
security = HTTPBasic()

CONFIG_FILE = "config.json"
dispatcher_instance = None

def load_config():
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {
        "TG_BOT_TOKEN": "",
        "KWORK_COOKIE": "",
        "CHECK_INTERVAL": 120,
        "WEB_PORT": 5000,
        "ADMIN_PASS": "admin"
    }

def save_config(cfg):
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=4, ensure_ascii=False)

# Простая Basic Auth авторизация для админа
def get_current_username(credentials: HTTPBasicCredentials = Depends(security)):
    cfg = load_config()
    correct_password = cfg.get("ADMIN_PASS", "admin")
    if credentials.username != "admin" or credentials.password != correct_password:
        raise HTTPException(
            status_code=status.HTTP_411_LENGTH_REQUIRED, # Для простоты
            detail="Неверный логин или пароль",
            headers={"WWW-Authenticate": "Basic"},
        )
    return credentials.username

HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Панель управления парсером</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <style>
        body {
            background-color: #0f172a;
            color: #e2e8f0;
            font-family: 'Inter', sans-serif;
        }
        .glass {
            background: rgba(30, 41, 59, 0.7);
            backdrop-filter: blur(12px);
            border: 1px solid rgba(255, 255, 255, 0.05);
        }
    </style>
</head>
<body class="p-6 md:p-12">
    <div class="max-w-6xl mx-auto space-y-8">
        <!-- Header -->
        <div class="flex flex-col md:flex-row justify-between items-center glass p-6 rounded-2xl shadow-xl">
            <div>
                <h1 class="text-3xl font-extrabold text-transparent bg-clip-text bg-gradient-to-r from-teal-400 to-blue-500">
                    Parser Control Panel
                </h1>
                <p class="text-gray-400 text-sm mt-1">Панель управления асинхронным мультиплатформенным ботом-парсером</p>
            </div>
            <div class="mt-4 md:mt-0 flex space-x-3">
                <span class="inline-flex items-center px-4 py-2 rounded-full text-xs font-semibold bg-emerald-500/10 text-emerald-400 border border-emerald-500/20">
                    🟢 Бот: Активен
                </span>
                <span class="inline-flex items-center px-4 py-2 rounded-full text-xs font-semibold bg-blue-500/10 text-blue-400 border border-blue-500/20">
                    ⚡ FastAPI + asyncio loop
                </span>
            </div>
        </div>

        <!-- Main Grid -->
        <div class="grid grid-cols-1 lg:grid-cols-3 gap-8">
            <!-- Left Column: Config Form -->
            <div class="lg:col-span-1 glass p-6 rounded-2xl shadow-lg space-y-6">
                <h2 class="text-xl font-bold text-white border-b border-gray-700 pb-3">⚙️ Настройки бота</h2>
                <form action="/save-settings" method="post" class="space-y-4">
                    <div>
                        <label class="block text-xs font-semibold text-gray-400 uppercase tracking-wider mb-2">Telegram Bot Token</label>
                        <input type="text" name="bot_token" value="{bot_token}" class="w-full bg-slate-900 border border-slate-700 rounded-lg px-3 py-2 text-sm focus:outline-none focus:border-teal-500 text-white">
                    </div>
                    <div>
                        <label class="block text-xs font-semibold text-gray-400 uppercase tracking-wider mb-2">Kwork Cookie (авторизация)</label>
                        <textarea name="kwork_cookie" rows="3" class="w-full bg-slate-900 border border-slate-700 rounded-lg px-3 py-2 text-sm focus:outline-none focus:border-teal-500 text-white text-xs">{kwork_cookie}</textarea>
                    </div>
                    <div class="grid grid-cols-2 gap-4">
                        <div>
                            <label class="block text-xs font-semibold text-gray-400 uppercase tracking-wider mb-2">Интервал (сек)</label>
                            <input type="number" name="interval" value="{interval}" class="w-full bg-slate-900 border border-slate-700 rounded-lg px-3 py-2 text-sm focus:outline-none focus:border-teal-500 text-white">
                        </div>
                        <div>
                            <label class="block text-xs font-semibold text-gray-400 uppercase tracking-wider mb-2">Пароль админа</label>
                            <input type="text" name="admin_pass" value="{admin_pass}" class="w-full bg-slate-900 border border-slate-700 rounded-lg px-3 py-2 text-sm focus:outline-none focus:border-teal-500 text-white">
                        </div>
                    </div>
                    <button type="submit" class="w-full py-2.5 bg-gradient-to-r from-teal-500 to-blue-600 hover:from-teal-600 hover:to-blue-700 text-white font-bold rounded-lg text-sm transition shadow-lg shadow-teal-500/20">
                        Сохранить и Перезапустить
                    </button>
                </form>
            </div>

            <!-- Middle/Right Column: System info and users -->
            <div class="lg:col-span-2 space-y-8">
                <!-- Users list -->
                <div class="glass p-6 rounded-2xl shadow-lg space-y-4">
                    <h2 class="text-xl font-bold text-white border-b border-gray-700 pb-3">👤 Зарегистрированные пользователи</h2>
                    <div class="overflow-x-auto">
                        <table class="w-full text-left border-collapse text-sm">
                            <thead>
                                <tr class="text-gray-400 border-b border-gray-800">
                                    <th class="py-2">TG ID</th>
                                    <th class="py-2">Никнейм</th>
                                    <th class="py-2">Тариф</th>
                                    <th class="py-2">Рефералов</th>
                                    <th class="py-2">Фильтры (макс)</th>
                                </tr>
                            </thead>
                            <tbody class="divide-y divide-gray-800">
                                {users_rows}
                            </tbody>
                        </table>
                    </div>
                </div>

                <!-- Support tickets list -->
                <div class="glass p-6 rounded-2xl shadow-lg space-y-4">
                    <h2 class="text-xl font-bold text-white border-b border-gray-700 pb-3">🆘 Активные обращения в поддержку</h2>
                    <div class="overflow-x-auto">
                        <table class="w-full text-left border-collapse text-sm">
                            <thead>
                                <tr class="text-gray-400 border-b border-gray-800">
                                    <th class="py-2">Пользователь / Дата</th>
                                    <th class="py-2">Сообщение</th>
                                    <th class="py-2">Ответ</th>
                                </tr>
                            </thead>
                            <tbody class="divide-y divide-gray-800">
                                {tickets_rows}
                            </tbody>
                        </table>
                    </div>
                </div>

                <!-- Logs -->
                <div class="glass p-6 rounded-2xl shadow-lg space-y-4">
                    <div class="flex justify-between items-center border-b border-gray-700 pb-3">
                        <h2 class="text-xl font-bold text-white">📄 Логи системы</h2>
                        <a href="/clear-logs" class="text-xs text-rose-400 hover:underline">Очистить лог-файл</a>
                    </div>
                    <pre class="bg-slate-950 p-4 rounded-xl text-xs font-mono text-emerald-400 overflow-y-auto max-h-72 whitespace-pre-wrap">{logs}</pre>
                </div>
            </div>
        </div>
    </div>
</body>
</html>
"""

@app.get("/", response_class=HTMLResponse)
async def index(request: Request, username: str = Depends(get_current_username)):
    cfg = load_config()
    
    # Загружаем пользователей
    users = await Database.get_all_users()
    users_html = ""
    for u in users:
        tier_color = "text-teal-400" if u['tier'] == 'premium' else ("text-blue-400" if u['tier'] == 'referral' else "text-gray-400")
        users_html += f'''
        <tr class="border-b border-gray-800/50 hover:bg-slate-800/30">
            <td class="py-3 font-mono">{u['tg_id']}</td>
            <td class="py-3">@{u['username']}</td>
            <td class="py-3 font-bold {tier_color}">{u['tier'].upper()}</td>
            <td class="py-3">{u['referral_count']}</td>
            <td class="py-3">{u['max_filters']}</td>
        </tr>
        '''
        
    if not users_html:
        users_html = '<tr><td colspan="5" class="py-4 text-center text-gray-500">Нет зарегистрированных пользователей</td></tr>'

    # Загружаем тикеты
    tickets = await Database.get_active_support_tickets()
    tickets_html = ""
    for t in tickets:
        user_display = f"@{t['username']}" if t['username'] else f"ID: {t['user_id']}"
        tickets_html += f'''
        <tr class="border-b border-gray-800/50 hover:bg-slate-800/30">
            <td class="py-4 align-top">
                <a href="https://t.me/{t['username']}" target="_blank" class="text-teal-400 hover:underline font-medium">{user_display}</a>
                <div class="text-xs text-gray-500 mt-1">{t['created_at']}</div>
            </td>
            <td class="py-4 align-top pr-4">
                <div class="text-white whitespace-pre-wrap text-xs bg-slate-900/50 p-2.5 rounded-lg border border-slate-800">{t['message']}</div>
            </td>
            <td class="py-4 align-top">
                <div class="flex flex-col space-y-2">
                    <form action="/reply-ticket/{t['id']}" method="post" class="flex space-x-2">
                        <input type="text" name="reply_text" placeholder="Написать ответ в Telegram..." required class="flex-1 bg-slate-900 border border-slate-700 rounded-lg px-3 py-1.5 text-xs focus:outline-none focus:border-teal-500 text-white">
                        <button type="submit" class="px-3 py-1.5 bg-gradient-to-r from-teal-500 to-blue-600 hover:from-teal-600 hover:to-blue-700 text-white font-bold rounded-lg text-xs transition shadow-md shadow-teal-500/10">
                            Ответить
                        </button>
                    </form>
                    <div class="flex justify-end">
                        <a href="/resolve-ticket/{t['id']}" class="text-[10px] text-gray-500 hover:text-rose-400 transition">
                            Без ответа (пометить как решено)
                        </a>
                    </div>
                </div>
            </td>
        </tr>
        '''
        
    if not tickets_html:
        tickets_html = '<tr><td colspan="3" class="py-4 text-center text-gray-500">Нет активных обращений</td></tr>'

    # Читаем лог
    log_content = "Лог-файл пуст."
    if os.path.exists("app.log"):
        with open("app.log", "r", encoding="utf-8") as f:
            lines = f.readlines()
            log_content = "".join(lines[-40:]) # Последние 40 строк
            
    return HTML_TEMPLATE.format(
        bot_token=cfg.get("TG_BOT_TOKEN", ""),
        kwork_cookie=cfg.get("KWORK_COOKIE", ""),
        interval=cfg.get("CHECK_INTERVAL", 120),
        admin_pass=cfg.get("ADMIN_PASS", "admin"),
        users_rows=users_html,
        tickets_rows=tickets_html,
        logs=log_content
    )

@app.post("/save-settings")
async def save_settings(
    bot_token: str = Form(...),
    kwork_cookie: str = Form(""),
    interval: int = Form(120),
    admin_pass: str = Form("admin"),
    username: str = Depends(get_current_username)
):
    cfg = load_config()
    cfg["TG_BOT_TOKEN"] = bot_token.strip()
    cfg["KWORK_COOKIE"] = kwork_cookie.strip()
    cfg["CHECK_INTERVAL"] = interval
    cfg["ADMIN_PASS"] = admin_pass.strip()
    save_config(cfg)
    
    # Перезапускаем диспетчер/бот, если применимо
    # Мы можем запустить перезапуск в фоне
    if dispatcher_instance:
        asyncio.create_task(restart_system())
        
    return RedirectResponse(url="/", status_code=status.HTTP_303_SEE_OTHER)

@app.get("/clear-logs")
async def clear_logs(username: str = Depends(get_current_username)):
    if os.path.exists("app.log"):
        with open("app.log", "w", encoding="utf-8") as f:
            f.write("")
    return RedirectResponse(url="/", status_code=status.HTTP_303_SEE_OTHER)

@app.get("/resolve-ticket/{ticket_id}")
async def resolve_ticket(ticket_id: int, username: str = Depends(get_current_username)):
    await Database.resolve_support_ticket(ticket_id)
    return RedirectResponse(url="/", status_code=status.HTTP_303_SEE_OTHER)

@app.post("/reply-ticket/{ticket_id}")
async def reply_ticket(
    ticket_id: int,
    reply_text: str = Form(...),
    username: str = Depends(get_current_username)
):
    ticket = await Database.get_support_ticket(ticket_id)
    if ticket:
        user_id = ticket['user_id']
        reply_text_formatted = (
            f"✉️ <b>Ответ от администратора:</b>\n\n"
            f"{reply_text.strip()}"
        )
        if dispatcher_instance and dispatcher_instance.bot:
            try:
                await dispatcher_instance.bot.send_message(user_id, reply_text_formatted, parse_mode="HTML")
            except Exception as e:
                import logging
                logging.getLogger("web").error(f"Не удалось отправить ответ пользователю {user_id}: {e}")
        
        await Database.resolve_support_ticket(ticket_id)
        
    return RedirectResponse(url="/", status_code=status.HTTP_303_SEE_OTHER)

@app.post("/payment/webhook")
async def payment_webhook(request: Request):
    try:
        data = await request.json()
    except Exception:
        return {"status": "error", "message": "Invalid JSON payload"}
        
    user_id = None
    plan_id = None
    
    # Поддерживаем структуру Yookassa / Robokassa / Tinkoff / Custom Gateway
    if "object" in data and "metadata" in data["object"]:
        user_id = data["object"]["metadata"].get("user_id")
        plan_id = data["object"]["metadata"].get("plan_id")
    elif "user_id" in data:
        user_id = data.get("user_id")
        plan_id = data.get("plan_id")
        
    if user_id:
        try:
            user_id = int(user_id)
            await Database.set_user_tier(user_id, "premium")
            
            # Отправляем мгновенное уведомление в Telegram пользователю
            if dispatcher_instance and dispatcher_instance.bot:
                text = (
                    "🎉 <b>ОПЛАТА ПОДТВЕРЖДЕНА (Авто-платеж)!</b>\n\n"
                    "⭐ Ваш тариф: <b>Premium</b>\n"
                    "🚀 Лимит фильтров успешно увеличен до <b>неограниченного</b>!\n\n"
                    "Спасибо за покупку!"
                )
                await dispatcher_instance.bot.send_message(user_id, text, parse_mode="HTML")
            return {"status": "success", "message": f"User {user_id} upgraded to premium"}
        except Exception as e:
            return {"status": "error", "message": str(e)}
            
    return {"status": "ignored", "message": "No valid user_id in payload"}

async def restart_system():
    # Эта функция будет определена и вызвана из main.py для перезапуска бота
    pass
