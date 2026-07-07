#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
АНАЛОГ БОТА @P5UcbA_y4Mg_bot
Лимит: 100 запросов/день
Полный поиск информации по номеру телефона
Версия: 3.0
"""

import asyncio
import aiohttp
import json
import re
import sqlite3
from datetime import datetime, date, timedelta
from typing import Dict, List, Optional, Tuple
import logging
from urllib.parse import quote_plus
import hashlib

# ========== КОНФИГУРАЦИЯ ==========
MAX_REQUESTS_PER_DAY = 100
DB_PATH = "phone_bot.db"
BOT_TOKEN = "8796975931:AAFpT2nZUXWyqohYmdAwlK3C54B9klJkjK0"  # ЗАМЕНИ НА СВОЙ ТОКЕН

# ========== НАСТРОЙКА ЛОГГИНГА ==========
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler("phone_bot.log", encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# ========== БАЗА ДАННЫХ ==========

def init_db():
    """Инициализация БД"""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    
    # Таблица запросов (лимит)
    c.execute('''CREATE TABLE IF NOT EXISTS requests (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        query TEXT NOT NULL,
        timestamp TEXT NOT NULL,
        success BOOLEAN DEFAULT 0,
        result_id TEXT
    )''')
    
    c.execute('CREATE INDEX IF NOT EXISTS idx_requests_date ON requests(date(timestamp))')
    c.execute('CREATE INDEX IF NOT EXISTS idx_requests_user ON requests(user_id)')
    
    # Таблица результатов
    c.execute('''CREATE TABLE IF NOT EXISTS results (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        query_hash TEXT UNIQUE,
        phone TEXT,
        data TEXT,
        created_at TEXT,
        expires_at TEXT
    )''')
    
    c.execute('CREATE INDEX IF NOT EXISTS idx_results_hash ON results(query_hash)')
    c.execute('CREATE INDEX IF NOT EXISTS idx_results_phone ON results(phone)')
    
    # Таблица статистики
    c.execute('''CREATE TABLE IF NOT EXISTS stats (
        stat_key TEXT PRIMARY KEY,
        stat_value TEXT
    )''')
    
    c.execute("INSERT OR IGNORE INTO stats (stat_key, stat_value) VALUES (?, ?)",
              ("total_requests", "0"))
    c.execute("INSERT OR IGNORE INTO stats (stat_key, stat_value) VALUES (?, ?)",
              ("total_success", "0"))
    
    conn.commit()
    conn.close()
    logger.info("Database initialized")

# ========== ЛИМИТЕР ==========

class RateLimiter:
    """Лимит 100 запросов в день на пользователя"""
    
    def __init__(self):
        self.max_requests = MAX_REQUESTS_PER_DAY
    
    def get_user_today_requests(self, user_id: int) -> int:
        """Сколько запросов сделал пользователь сегодня"""
        today = date.today().isoformat()
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute(
            "SELECT COUNT(*) FROM requests WHERE user_id = ? AND date(timestamp) = ?",
            (user_id, today)
        )
        count = c.fetchone()[0]
        conn.close()
        return count
    
    def can_make_request(self, user_id: int) -> Tuple[bool, int, int]:
        """Может ли пользователь сделать запрос"""
        used = self.get_user_today_requests(user_id)
        remaining = self.max_requests - used
        return remaining > 0, used, remaining
    
    def log_request(self, user_id: int, query: str, success: bool = False, result_id: str = None):
        """Логирование запроса"""
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute(
            "INSERT INTO requests (user_id, query, timestamp, success, result_id) VALUES (?, ?, ?, ?, ?)",
            (user_id, query, datetime.now().isoformat(), success, result_id)
        )
        conn.commit()
        conn.close()

# ========== ОСНОВНОЙ ДВИЖОК ==========

class PhoneSearchEngine:
    """Поиск информации по номеру телефона"""
    
    def __init__(self):
        self.session = None
        self.rate_limiter = RateLimiter()
        self.cache = {}
        
    async def init_session(self):
        self.session = aiohttp.ClientSession(
            headers={
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
                'Accept-Language': 'ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7'
            },
            timeout=aiohttp.ClientTimeout(total=30)
        )
    
    async def close_session(self):
        if self.session:
            await self.session.close()
    
    async def search(self, phone: str) -> Dict:
        """Полный поиск по номеру"""
        
        clean_phone = re.sub(r'\D', '', phone)
        if len(clean_phone) < 10:
            return {"error": "Номер слишком короткий"}
        
        # Проверяем кэш
        cache_key = hashlib.md5(clean_phone.encode()).hexdigest()
        cached = self.get_cached(cache_key)
        if cached:
            logger.info(f"Returning cached data for {clean_phone}")
            return cached
        
        # Ищем информацию
        result = {
            "phone": clean_phone,
            "timestamp": datetime.now().isoformat(),
            "sources": {}
        }
        
        # 1. Поиск в публичных базах
        await self.search_public_breaches(clean_phone, result)
        
        # 2. Поиск в социальных сетях
        await self.search_social_media(clean_phone, result)
        
        # 3. Поиск через EnigmaSearch
        await self.search_enigma(clean_phone, result)
        
        # 4. Поиск через Google
        await self.search_google(clean_phone, result)
        
        # Сохраняем в кэш
        self.save_cache(cache_key, result)
        
        return result
    
    async def search_public_breaches(self, phone: str, result: Dict):
        """Поиск в публичных базах утечек"""
        # Здесь можно интегрировать API HaveIBeenPwned, Dehashed и др.
        # Для демонстрации - мок-данные
        result["sources"]["breaches"] = {
            "status": "checking",
            "note": "Для реальных данных нужен API ключ"
        }
    
    async def search_social_media(self, phone: str, result: Dict):
        """Поиск в соцсетях"""
        # Проверяем популярные соцсети
        social_networks = [
            ("telegram", f"https://t.me/+{phone}"),
            ("whatsapp", f"https://wa.me/{phone}"),
            ("viber", f"viber://chat?number={phone}")
        ]
        
        for name, url in social_networks:
            result["sources"][name] = {
                "url": url,
                "note": "Проверь вручную"
            }
    
    async def search_enigma(self, phone: str, result: Dict):
        """Поиск через EnigmaSearch"""
        try:
            # Пробуем найти через Google
            search_url = f"https://www.google.com/search?q={phone}+site:enigmasearch.org"
            async with self.session.get(search_url) as resp:
                if resp.status == 200:
                    html = await resp.text()
                    report_links = re.findall(
                        r'https://enigmasearch\.org/report/[0-9a-f-]+',
                        html
                    )
                    if report_links:
                        result["sources"]["enigma"] = {
                            "found": True,
                            "links": report_links[:3],
                            "report_id": report_links[0].split('/')[-1]
                        }
                        # Парсим отчёт
                        await self.parse_enigma_report(
                            report_links[0].split('/')[-1],
                            result
                        )
                    else:
                        result["sources"]["enigma"] = {
                            "found": False,
                            "note": "Отчёт не найден"
                        }
        except Exception as e:
            result["sources"]["enigma"] = {"error": str(e)}
    
    async def parse_enigma_report(self, report_id: str, result: Dict):
        """Парсинг отчёта EnigmaSearch"""
        url = f"https://enigmasearch.org/report/{report_id}"
        
        try:
            async with self.session.get(url) as resp:
                if resp.status == 200:
                    html = await resp.text()
                    result["sources"]["enigma_report"] = self.parse_enigma_html(html, report_id)
        except Exception as e:
            logger.error(f"Enigma parse error: {e}")
    
    def parse_enigma_html(self, html: str, report_id: str) -> Dict:
        """Парсинг HTML EnigmaSearch"""
        data = {
            "report_id": report_id,
            "url": f"https://enigmasearch.org/report/{report_id}"
        }
        
        # Телефон
        phone_match = re.search(r'Телефон:\s*(\+?\d{10,15})', html)
        if phone_match:
            data["phone"] = phone_match.group(1)
        
        # Оператор
        op_match = re.search(r'Оператор связи:\s*([А-Яа-я\s\-]+)', html)
        if op_match:
            data["operator"] = op_match.group(1).strip()
        
        # Регион
        reg_match = re.search(r'Регион:\s*([А-Яа-я\s\-]+)', html)
        if reg_match:
            data["region"] = reg_match.group(1).strip()
        
        # Личности
        personalities = []
        for match in re.finditer(r'\[(\d+)\]\s*([А-Я][а-я]+\s[А-Я][а-я]+\s[А-Я][а-я]+)', html):
            personalities.append({
                "count": int(match.group(1)),
                "name": match.group(2).strip()
            })
        if personalities:
            data["personalities"] = personalities
        
        # Email
        emails = re.findall(r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}', html)
        if emails:
            data["emails"] = list(set(emails))
        
        # СНИЛС
        snils = re.findall(r'СНИЛС:\s*\[?(\d{11})\]?', html)
        if snils:
            data["snils"] = list(set(snils))
        
        # ИНН
        inns = re.findall(r'ИНН:\s*\[?(\d{10,12})\]?', html)
        if inns:
            data["inn"] = list(set(inns))
        
        # Адреса
        addresses = []
        for addr in re.findall(r'(\d{6}[,.]?\s*[Рр]осси[яи][^\n]{0,200})', html):
            addr = addr.strip()
            if addr and len(addr) > 10 and addr not in addresses:
                addresses.append(addr)
        if addresses:
            data["addresses"] = addresses[:5]
        
        # Карты
        cards = re.findall(r'Номер карты:\s*(\d{4}\*{4,8}\d{4})', html)
        if cards:
            data["cards"] = list(set(cards))
        
        # Telegram
        tg_links = re.findall(r'https://t\.me/([^\s\'"]+)', html)
        if tg_links:
            data["telegram_links"] = [f"https://t.me/{t}" for t in set(tg_links)]
        
        return data
    
    async def search_google(self, phone: str, result: Dict):
        """Поиск через Google"""
        search_url = f"https://www.google.com/search?q={quote_plus(phone)}"
        
        try:
            async with self.session.get(search_url) as resp:
                if resp.status == 200:
                    html = await resp.text()
                    # Ищем ссылки
                    links = re.findall(r'<a[^>]+href="([^"]+)"', html)
                    relevant_links = [
                        link for link in links[:10]
                        if any(site in link for site in ['vk.com', 'facebook.com', 'instagram.com'])
                    ]
                    if relevant_links:
                        result["sources"]["google"] = {
                            "found": True,
                            "links": relevant_links[:5]
                        }
        except Exception as e:
            result["sources"]["google"] = {"error": str(e)}
    
    def get_cached(self, key: str) -> Optional[Dict]:
        """Получение из кэша"""
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute(
            "SELECT data FROM results WHERE query_hash = ? AND datetime(expires_at) > datetime('now')",
            (key,)
        )
        row = c.fetchone()
        conn.close()
        
        if row:
            try:
                return json.loads(row[0])
            except:
                return None
        return None
    
    def save_cache(self, key: str, data: Dict):
        """Сохранение в кэш"""
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        expires = (datetime.now() + timedelta(days=7)).isoformat()
        c.execute(
            "INSERT OR REPLACE INTO results (query_hash, phone, data, created_at, expires_at) VALUES (?, ?, ?, ?, ?)",
            (key, data.get("phone"), json.dumps(data, ensure_ascii=False), datetime.now().isoformat(), expires)
        )
        conn.commit()
        conn.close()

# ========== ТЕЛЕГРАМ БОТ ==========

class PhoneBot:
    """Телеграм бот для поиска по номеру"""
    
    def __init__(self, token: str):
        self.token = token
        self.engine = PhoneSearchEngine()
        self.rate_limiter = RateLimiter()
    
    async def start_command(self, update, context):
        user_id = update.effective_user.id
        used = self.rate_limiter.get_user_today_requests(user_id)
        remaining = MAX_REQUESTS_PER_DAY - used
        
        await update.message.reply_text(
            f"📱 **PHONE SEARCH BOT v3.0**\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"🔍 Поиск информации по номеру телефона\n"
            f"📊 Лимит: {MAX_REQUESTS_PER_DAY} запросов/день\n"
            f"✅ Сегодня: {used}\n"
            f"⏳ Осталось: {remaining}\n\n"
            f"📌 Как использовать:\n"
            f"Отправь номер в любом формате:\n"
            f"• +375291234567\n"
            f"• 8(029)123-45-67\n"
            f"• 79112345678\n\n"
            f"📋 Команды:\n"
            f"/stats - статистика\n"
            f"/help - помощь\n"
            f"/clear - очистить мои данные"
        )
    
    async def handle_phone(self, update, context):
        """Обработка номера телефона"""
        user_id = update.effective_user.id
        phone = update.message.text.strip()
        
        # Очищаем номер
        clean_phone = re.sub(r'\D', '', phone)
        
        if len(clean_phone) < 7:
            await update.message.reply_text(
                "❌ Слишком короткий номер!\n"
                "Введи полный номер, например: +375291234567"
            )
            return
        
        # Проверка лимита
        can, used, remaining = self.rate_limiter.can_make_request(user_id)
        if not can:
            reset_time = (date.today() + timedelta(days=1)).strftime('%d.%m.%Y')
            await update.message.reply_text(
                f"❌ **Лимит исчерпан!**\n"
                f"📊 Использовано: {used}/{MAX_REQUESTS_PER_DAY}\n"
                f"🔄 Сброс: {reset_time}\n\n"
                f"💳 Купи премиум для безлимита (шутка, я бесплатный)"
            )
            return
        
        # Отправляем статус
        status_msg = await update.message.reply_text(
            f"🔍 Ищу информацию по номеру **{clean_phone}**...\n"
            f"⏳ Осталось запросов: {remaining}\n"
            f"🔄 Это может занять до 30 секунд"
        )
        
        # Запускаем поиск
        await self.engine.init_session()
        result = await self.engine.search(clean_phone)
        await self.engine.close_session()
        
        # Логируем запрос
        self.rate_limiter.log_request(
            user_id,
            clean_phone,
            success="error" not in result,
            result_id=hashlib.md5(clean_phone.encode()).hexdigest()
        )
        
        # Удаляем статус
        await status_msg.delete()
        
        # Отправляем результат
        if "error" in result:
            await update.message.reply_text(f"❌ **Ошибка:** {result['error']}")
            return
        
        # Форматируем ответ
        response = self.format_result(result, clean_phone, remaining)
        await update.message.reply_text(response, parse_mode='Markdown', disable_web_page_preview=True)
        
        # Сохраняем в файл
        try:
            with open(f"result_{clean_phone}.json", "w", encoding="utf-8") as f:
                json.dump(result, f, indent=2, ensure_ascii=False)
        except:
            pass
    
    def format_result(self, result: Dict, phone: str, remaining: int) -> str:
        """Форматирование результата"""
        response = "📋 **РЕЗУЛЬТАТ ПОИСКА**\n"
        response += f"📱 **Номер:** `{phone}`\n"
        response += "━━━━━━━━━━━━━━━━━━━━━━━\n"
        
        # Данные из Enigma
        if "sources" in result and "enigma_report" in result["sources"]:
            data = result["sources"]["enigma_report"]
            
            if data.get("operator"):
                response += f"📶 **Оператор:** {data['operator']}\n"
            if data.get("region"):
                response += f"📍 **Регион:** {data['region']}\n"
            
            if data.get("personalities"):
                response += "\n👤 **Личности:**\n"
                for p in data["personalities"][:5]:
                    response += f"  • {p['name']} ({p['count']} совп.)\n"
            
            if data.get("emails"):
                response += "\n📧 **Email:**\n"
                for email in data["emails"][:5]:
                    response += f"  • `{email}`\n"
            
            if data.get("snils"):
                response += "\n🆔 **СНИЛС:**\n"
                for s in data["snils"][:3]:
                    response += f"  • `{s}`\n"
            
            if data.get("inn"):
                response += "\n🆔 **ИНН:**\n"
                for i in data["inn"][:3]:
                    response += f"  • `{i}`\n"
            
            if data.get("addresses"):
                response += "\n📍 **Адреса:**\n"
                for addr in data["addresses"][:3]:
                    response += f"  • {addr[:60]}...\n"
            
            if data.get("cards"):
                response += "\n💳 **Карты:**\n"
                for card in data["cards"][:3]:
                    response += f"  • `{card}`\n"
            
            if data.get("telegram_links"):
                response += "\n✈️ **Telegram:**\n"
                for tg in data["telegram_links"][:3]:
                    response += f"  • {tg}\n"
            
            if data.get("url"):
                response += f"\n🔗 [Открыть отчёт]({data['url']})"
        
        # Enigma ссылки
        if "sources" in result and "enigma" in result["sources"]:
            enigma = result["sources"]["enigma"]
            if enigma.get("links"):
                response += "\n\n📌 **Найдено отчётов Enigma:**"
                for link in enigma["links"][:3]:
                    response += f"\n  • {link}"
        
        # Соцсети
        if "sources" in result:
            social = result["sources"]
            if social.get("telegram") or social.get("whatsapp") or social.get("viber"):
                response += "\n\n💬 **Мессенджеры:**"
                if social.get("telegram"):
                    response += f"\n  • [Telegram]({social['telegram']['url']})"
                if social.get("whatsapp"):
                    response += f"\n  • [WhatsApp]({social['whatsapp']['url']})"
                if social.get("viber"):
                    response += f"\n  • [Viber]({social['viber']['url']})"
        
        response += f"\n\n━━━━━━━━━━━━━━━━━━━━━━━"
        response += f"\n📊 Осталось запросов: {remaining}"
        response += f"\n🕐 {datetime.now().strftime('%H:%M:%S %d.%m.%Y')}"
        
        return response
    
    async def stats_command(self, update, context):
        user_id = update.effective_user.id
        used = self.rate_limiter.get_user_today_requests(user_id)
        remaining = MAX_REQUESTS_PER_DAY - used
        
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("SELECT COUNT(*) FROM requests")
        total = c.fetchone()[0]
        c.execute("SELECT COUNT(*) FROM results")
        cached = c.fetchone()[0]
        conn.close()
        
        await update.message.reply_text(
            f"📊 **МОЯ СТАТИСТИКА**\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"👤 Твои запросы сегодня: {used}/{MAX_REQUESTS_PER_DAY}\n"
            f"⏳ Тебе осталось: {remaining}\n\n"
            f"🌍 Всего запросов: {total}\n"
            f"📦 В кэше: {cached}\n"
            f"🔄 Сброс лимита: {(date.today() + timedelta(days=1)).strftime('%d.%m.%Y')}"
        )
    
    async def clear_command(self, update, context):
        user_id = update.effective_user.id
        
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("DELETE FROM requests WHERE user_id = ?", (user_id,))
        conn.commit()
        conn.close()
        
        await update.message.reply_text(
            "✅ **Твои данные очищены!**\n"
            "Лимит сброшен, можно пользоваться дальше."
        )
    
    async def help_command(self, update, context):
        await self.start_command(update, context)
    
    async def error_handler(self, update, context):
        logger.error(f"Update {update} caused error {context.error}")
        await update.message.reply_text(
            "❌ **Ошибка!**\n"
            "Что-то пошло не так. Попробуй позже."
        )
    
    def run(self):
        from telegram.ext import Application, CommandHandler, MessageHandler, filters
        
        app = Application.builder().token(self.token).build()
        
        # Команды
        app.add_handler(CommandHandler("start", self.start_command))
        app.add_handler(CommandHandler("help", self.help_command))
        app.add_handler(CommandHandler("stats", self.stats_command))
        app.add_handler(CommandHandler("clear", self.clear_command))
        
        # Обработка сообщений (номера телефонов)
        app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_phone))
        
        # Обработка ошибок
        app.add_error_handler(self.error_handler)
        
        logger.info("Bot started!")
        app.run_polling()

# ========== ЗАПУСК ==========

if __name__ == "__main__":
    init_db()
    
    # ЗАМЕНИ ТОКЕН НА СВОЙ
    TOKEN = "YOUR_BOT_TOKEN_HERE"  # <--- СЮДА ВСТАВЬ ТОКЕН
    
    bot = PhoneBot(TOKEN)
    bot.run()
