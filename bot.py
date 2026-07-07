#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
ENIGMA SEARCH PARSER v2.0
ТОКЕН: 8796975931:AAFpT2nZUXWyqohYmdAwlK3C54B9klJkjK0
Лимит: 100 запросов/день
"""

import asyncio
import aiohttp
import json
import re
import sqlite3
from datetime import datetime, date, timedelta
from typing import Dict, List, Optional, Tuple
import logging
from collections import defaultdict

# ========== КОНФИГУРАЦИЯ ==========
MAX_REQUESTS_PER_DAY = 100
DB_PATH = "enigma_parser.db"
BOT_TOKEN = "8796975931:AAFpT2nZUXWyqohYmdAwlK3C54B9klJkjK0"  # <--- ТВОЙ ТОКЕН

# ========== НАСТРОЙКА ЛОГГИНГА ==========
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# ========== БАЗА ДАННЫХ ==========

def init_db():
    """Инициализация базы данных"""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    
    # Таблица запросов (для лимита)
    c.execute('''CREATE TABLE IF NOT EXISTS requests (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        query TEXT NOT NULL,
        query_type TEXT NOT NULL,
        timestamp TEXT NOT NULL,
        success BOOLEAN DEFAULT 0,
        report_id TEXT
    )''')
    
    c.execute('CREATE INDEX IF NOT EXISTS idx_timestamp ON requests(timestamp)')
    c.execute('CREATE INDEX IF NOT EXISTS idx_query ON requests(query)')
    
    # Таблица для хранения спарсенных данных
    c.execute('''CREATE TABLE IF NOT EXISTS parsed_data (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        report_id TEXT UNIQUE,
        phone TEXT,
        operator TEXT,
        region TEXT,
        personalities TEXT,
        emails TEXT,
        snils TEXT,
        inn TEXT,
        addresses TEXT,
        card TEXT,
        telegram TEXT,
        other_data TEXT,
        parsed_at TEXT
    )''')
    
    # Таблица статистики
    c.execute('''CREATE TABLE IF NOT EXISTS stats (
        stat_key TEXT PRIMARY KEY,
        stat_value TEXT
    )''')
    
    c.execute("INSERT OR IGNORE INTO stats (stat_key, stat_value) VALUES (?, ?)",
              ("total_parsed", "0"))
    c.execute("INSERT OR IGNORE INTO stats (stat_key, stat_value) VALUES (?, ?)",
              ("last_reset", datetime.now().isoformat()))
    
    conn.commit()
    conn.close()
    logger.info("Database initialized")

# ========== КЛАСС ДЛЯ ЛИМИТА ==========

class RateLimiter:
    """Лимит 100 запросов в день"""
    
    def __init__(self, db_path: str = DB_PATH):
        self.db_path = db_path
        self.max_requests = MAX_REQUESTS_PER_DAY
    
    def get_today_requests(self) -> int:
        today = date.today().isoformat()
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()
        c.execute("SELECT COUNT(*) FROM requests WHERE date(timestamp) = ?", (today,))
        count = c.fetchone()[0]
        conn.close()
        return count
    
    def can_make_request(self) -> Tuple[bool, int, int]:
        today_requests = self.get_today_requests()
        remaining = self.max_requests - today_requests
        if remaining <= 0:
            return False, today_requests, remaining
        return True, today_requests, remaining
    
    def log_request(self, query: str, query_type: str, success: bool = False, report_id: str = None):
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()
        c.execute(
            """INSERT INTO requests (query, query_type, timestamp, success, report_id)
               VALUES (?, ?, ?, ?, ?)""",
            (query, query_type, datetime.now().isoformat(), success, report_id)
        )
        conn.commit()
        conn.close()

# ========== ОСНОВНОЙ ПАРСЕР ==========

class EnigmaParser:
    def __init__(self):
        self.session = None
        self.base_url = "https://enigmasearch.org"
        self.rate_limiter = RateLimiter()
    
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
    
    async def fetch_report(self, report_id: str) -> Dict:
        can, used, remaining = self.rate_limiter.can_make_request()
        if not can:
            return {
                "error": f"Лимит превышен! Использовано {used}/{MAX_REQUESTS_PER_DAY}",
                "limit_reached": True,
                "used": used,
                "max": MAX_REQUESTS_PER_DAY
            }
        
        logger.info(f"Fetching report: {report_id} (осталось: {remaining})")
        url = f"{self.base_url}/report/{report_id}"
        
        try:
            async with self.session.get(url) as resp:
                if resp.status == 200:
                    html = await resp.text()
                    result = self.parse_html(html, report_id)
                    self.rate_limiter.log_request(report_id, "report_id", success=True, report_id=report_id)
                    self.save_to_db(result)
                    return result
                else:
                    self.rate_limiter.log_request(report_id, "report_id", success=False)
                    return {"error": f"HTTP {resp.status}", "report_id": report_id}
        except Exception as e:
            self.rate_limiter.log_request(report_id, "report_id", success=False)
            return {"error": str(e), "report_id": report_id}
    
    def parse_html(self, html: str, report_id: str) -> Dict:
        result = {
            "report_id": report_id,
            "url": f"https://enigmasearch.org/report/{report_id}",
            "timestamp": datetime.now().isoformat(),
            "data": {}
        }
        
        # Телефон
        phone_match = re.search(r'Телефон:\s*(\+?\d{10,15})', html)
        if phone_match:
            result["data"]["phone"] = phone_match.group(1)
        
        # Оператор
        operator_match = re.search(r'Оператор связи:\s*([А-Яа-я\s\-]+)', html)
        if operator_match:
            result["data"]["operator"] = operator_match.group(1).strip()
        
        # Регион
        region_match = re.search(r'Регион:\s*([А-Яа-я\s\-]+)', html)
        if region_match:
            result["data"]["region"] = region_match.group(1).strip()
        
        # Личности
        personalities = []
        matches = re.findall(r'\[(\d+)\]\s*([А-Я][а-я]+\s[А-Я][а-я]+\s[А-Я][а-я]+)', html)
        for count, name in matches:
            personalities.append({"count": int(count), "name": name.strip()})
        if personalities:
            result["data"]["personalities"] = personalities
        
        # Email
        emails = re.findall(r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}', html)
        if emails:
            result["data"]["emails"] = list(set(emails))
        
        # СНИЛС
        snils = re.findall(r'СНИЛС:\s*\[?(\d{11})\]?', html)
        if snils:
            result["data"]["snils"] = list(set(snils))
        
        # ИНН
        inns = re.findall(r'ИНН:\s*\[?(\d{10,12})\]?', html)
        if inns:
            result["data"]["inn"] = list(set(inns))
        
        # Адреса
        addresses = re.findall(r'(\d{6}[,.]?\s*[Рр]осси[яи].*?(?:\n|$))', html)
        if addresses:
            result["data"]["addresses"] = [addr.strip() for addr in addresses[:5]]
        
        # Банковские карты
        cards = re.findall(r'Номер карты:\s*(\d{4}\*{4,8}\d{4})', html)
        if cards:
            result["data"]["cards"] = list(set(cards))
        
        # Telegram
        tg_links = re.findall(r'https://t\.me/([^\s\'"]+)', html)
        if tg_links:
            result["data"]["telegram_links"] = [f"https://t.me/{t}" for t in set(tg_links)]
        
        # Статистика
        result["statistics"] = {
            "total_personalities": len(result["data"].get("personalities", [])),
            "total_emails": len(result["data"].get("emails", [])),
            "total_addresses": len(result["data"].get("addresses", []))
        }
        
        return result
    
    def save_to_db(self, data: Dict):
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        d = data.get("data", {})
        c.execute(
            """INSERT OR REPLACE INTO parsed_data 
               (report_id, phone, operator, region, personalities, emails, 
                snils, inn, addresses, card, telegram, other_data, parsed_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                data.get("report_id"),
                d.get("phone"),
                d.get("operator"),
                d.get("region"),
                json.dumps(d.get("personalities", [])),
                json.dumps(d.get("emails", [])),
                json.dumps(d.get("snils", [])),
                json.dumps(d.get("inn", [])),
                json.dumps(d.get("addresses", [])),
                json.dumps(d.get("cards", [])),
                json.dumps(d.get("telegram_links", [])),
                json.dumps({k: v for k, v in d.items() if k not in [
                    'phone', 'operator', 'region', 'personalities', 'emails',
                    'snils', 'inn', 'addresses', 'cards', 'telegram_links'
                ]}),
                data.get("timestamp")
            )
        )
        conn.commit()
        conn.close()

# ========== ТЕЛЕГРАМ БОТ ==========

class EnigmaBot:
    def __init__(self, token: str):
        self.token = token
        self.parser = EnigmaParser()
    
    async def start_command(self, update, context):
        used = RateLimiter().get_today_requests()
        remaining = MAX_REQUESTS_PER_DAY - used
        await update.message.reply_text(
            f"🔍 **ENIGMA SEARCH BOT v2.0**\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"📊 Лимит: {MAX_REQUESTS_PER_DAY} запросов/день\n"
            f"✅ Использовано: {used}\n"
            f"⏳ Осталось: {remaining}\n\n"
            f"Команды:\n"
            f"🔎 /search <номер> - поиск по номеру\n"
            f"📄 /report <id> - получить отчёт по ID\n"
            f"📊 /stats - статистика\n\n"
            f"Пример:\n"
            f"/search 79114649118\n"
            f"/report 019f3a2a-2744-70b0-9a93-f63c67f50388"
        )
    
    async def search_command(self, update, context):
        if not context.args:
            await update.message.reply_text("❌ Введите номер: /search 79114649118")
            return
        
        phone = context.args[0]
        can, used, remaining = RateLimiter().can_make_request()
        if not can:
            await update.message.reply_text(f"❌ Лимит! {used}/{MAX_REQUESTS_PER_DAY}")
            return
        
        await update.message.reply_text(f"🔍 Поиск по номеру {phone}...\nОсталось: {remaining}")
        
        # Показываем ссылки для поиска
        clean_phone = re.sub(r'\D', '', phone)
        response = f"🔍 **Поиск по номеру:** {clean_phone}\n\n"
        response += "📌 **Ссылки для поиска:**\n"
        response += f"• https://google.com/search?q={clean_phone}+enigmasearch\n"
        response += f"• https://enigmasearch.org/search?q={clean_phone}\n"
        response += f"• https://www.google.com/search?q=%22{clean_phone}%22+site:enigmasearch.org\n"
        response += "\n⚠️ EnigmaSearch не имеет открытого API для поиска.\n"
        response += "Найди отчёт вручную и используй /report <id>"
        
        await update.message.reply_text(response)
    
    async def report_command(self, update, context):
        if not context.args:
            await update.message.reply_text("❌ Введите ID: /report 019f3a2a-2744-70b0-9a93-f63c67f50388")
            return
        
        report_id = context.args[0]
        can, used, remaining = RateLimiter().can_make_request()
        if not can:
            await update.message.reply_text(f"❌ Лимит! {used}/{MAX_REQUESTS_PER_DAY}")
            return
        
        await update.message.reply_text(f"📄 Парсинг отчёта {report_id}...\nОсталось: {remaining}")
        
        await self.parser.init_session()
        result = await self.parser.fetch_report(report_id)
        await self.parser.close_session()
        
        if "error" in result:
            await update.message.reply_text(f"❌ {result['error']}")
            return
        
        # Форматируем ответ
        response = "📋 **ОТЧЁТ ENIGMA SEARCH**\n"
        response += "━━━━━━━━━━━━━━━━━━━━━━━\n"
        d = result.get("data", {})
        
        if d.get("phone"):
            response += f"📱 **Телефон:** `{d['phone']}`\n"
        if d.get("operator") or d.get("region"):
            response += f"📍 {d.get('operator', '')} | {d.get('region', '')}\n"
        
        if d.get("personalities"):
            response += "\n👤 **Личности:**\n"
            for p in d["personalities"][:5]:
                response += f"  • {p['name']} ({p['count']} совп.)\n"
        
        if d.get("emails"):
            response += "\n📧 **Email:**\n"
            for email in d["emails"][:5]:
                response += f"  • `{email}`\n"
        
        if d.get("snils"):
            response += "\n🆔 **СНИЛС:**\n"
            for s in d["snils"][:3]:
                response += f"  • `{s}`\n"
        
        if d.get("inn"):
            response += "\n🆔 **ИНН:**\n"
            for i in d["inn"][:3]:
                response += f"  • `{i}`\n"
        
        if d.get("addresses"):
            response += "\n📍 **Адреса:**\n"
            for addr in d["addresses"][:3]:
                response += f"  • {addr[:60]}...\n"
        
        if d.get("cards"):
            response += "\n💳 **Карты:**\n"
            for card in d["cards"][:3]:
                response += f"  • `{card}`\n"
        
        if d.get("telegram_links"):
            response += "\n✈️ **Telegram:**\n"
            for tg in d["telegram_links"][:3]:
                response += f"  • {tg}\n"
        
        response += "\n━━━━━━━━━━━━━━━━━━━━━━━\n"
        response += f"🔗 [Открыть в браузере]({result.get('url', '')})"
        
        await update.message.reply_text(response)
        
        # Сохраняем JSON
        with open(f"report_{report_id[:8]}.json", "w", encoding="utf-8") as f:
            json.dump(result, f, indent=2, ensure_ascii=False)
    
    async def stats_command(self, update, context):
        used = RateLimiter().get_today_requests()
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("SELECT COUNT(*) FROM parsed_data")
        total_parsed = c.fetchone()[0]
        conn.close()
        
        await update.message.reply_text(
            f"📊 **СТАТИСТИКА**\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"📅 Сегодня: {used}/{MAX_REQUESTS_PER_DAY}\n"
            f"⏳ Осталось: {MAX_REQUESTS_PER_DAY - used}\n"
            f"📦 Всего отчётов: {total_parsed}"
        )
    
    def run(self):
        from telegram.ext import Application, CommandHandler
        
        app = Application.builder().token(self.token).build()
        app.add_handler(CommandHandler("start", self.start_command))
        app.add_handler(CommandHandler("search", self.search_command))
        app.add_handler(CommandHandler("report", self.report_command))
        app.add_handler(CommandHandler("stats", self.stats_command))
        
        logger.info("Bot started!")
        app.run_polling()

# ========== ЗАПУСК ==========

if __name__ == "__main__":
    init_db()
    
    # ТВОЙ ТОКЕН УЖЕ ВСТАВЛЕН
    bot = EnigmaBot(BOT_TOKEN)
    bot.run()
