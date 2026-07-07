#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
ENIGMA SEARCH PARSER v3.0 - РЕАЛЬНЫЙ ПАРСИНГ ПО НОМЕРУ
Ищет отчёты через Google и парсит их автоматически
"""

import asyncio
import aiohttp
import json
import re
import sqlite3
from datetime import datetime, date
from typing import Dict, List, Optional, Tuple
import logging
from urllib.parse import quote_plus

# ========== КОНФИГУРАЦИЯ ==========
MAX_REQUESTS_PER_DAY = 1000
DB_PATH = "enigma_parser.db"
BOT_TOKEN = "8796975931:AAFpT2nZUXWyqohYmdAwlK3C54B9klJkjK0"

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# ========== БАЗА ДАННЫХ ==========

def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS requests (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        query TEXT NOT NULL,
        timestamp TEXT NOT NULL,
        success BOOLEAN DEFAULT 0,
        report_id TEXT
    )''')
    c.execute('CREATE INDEX IF NOT EXISTS idx_timestamp ON requests(timestamp)')
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
        parsed_at TEXT
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS stats (
        stat_key TEXT PRIMARY KEY,
        stat_value TEXT
    )''')
    c.execute("INSERT OR IGNORE INTO stats (stat_key, stat_value) VALUES (?, ?)",
              ("total_parsed", "0"))
    conn.commit()
    conn.close()
    logger.info("Database initialized")

# ========== ЛИМИТЕР ==========

class RateLimiter:
    def __init__(self):
        self.max_requests = MAX_REQUESTS_PER_DAY
    
    def get_today_requests(self) -> int:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("SELECT COUNT(*) FROM requests WHERE date(timestamp) = ?", (date.today().isoformat(),))
        count = c.fetchone()[0]
        conn.close()
        return count
    
    def can_make_request(self) -> Tuple[bool, int, int]:
        used = self.get_today_requests()
        remaining = self.max_requests - used
        return remaining > 0, used, remaining
    
    def log_request(self, query: str, success: bool = False, report_id: str = None):
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("INSERT INTO requests (query, timestamp, success, report_id) VALUES (?, ?, ?, ?)",
                  (query, datetime.now().isoformat(), success, report_id))
        conn.commit()
        conn.close()

# ========== ПАРСЕР ==========

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
    
    async def search_by_phone(self, phone: str) -> Dict:
        """ПОИСК ОТЧЁТА ПО НОМЕРУ ЧЕРЕЗ GOOGLE"""
        clean_phone = re.sub(r'\D', '', phone)
        
        # Проверяем, есть ли уже в БД
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("SELECT report_id, phone FROM parsed_data WHERE phone LIKE ?", (f"%{clean_phone}%",))
        existing = c.fetchone()
        conn.close()
        
        if existing:
            logger.info(f"Found existing data for {clean_phone}")
            return await self.fetch_report(existing[0])
        
        # Ищем через Google
        search_queries = [
            f'site:enigmasearch.org "{clean_phone}"',
            f'"{clean_phone}" enigmasearch',
            f'"{clean_phone}" report enigma'
        ]
        
        for query in search_queries:
            google_url = f"https://www.google.com/search?q={quote_plus(query)}"
            
            try:
                async with self.session.get(google_url) as resp:
                    if resp.status == 200:
                        html = await resp.text()
                        # Ищем ссылки на отчёты EnigmaSearch
                        report_links = re.findall(
                            r'https://enigmasearch\.org/report/[0-9a-f-]+',
                            html
                        )
                        if report_links:
                            report_id = report_links[0].split('/')[-1]
                            logger.info(f"Found report: {report_id} for phone {clean_phone}")
                            return await self.fetch_report(report_id)
            except Exception as e:
                logger.error(f"Search error: {e}")
                continue
        
        return {
            "error": "Отчёт не найден",
            "phone": clean_phone,
            "search_links": [
                f"https://google.com/search?q={clean_phone}+enigmasearch",
                f"https://enigmasearch.org/search?q={clean_phone}"
            ]
        }
    
    async def fetch_report(self, report_id: str) -> Dict:
        """ПАРСИНГ ОТЧЁТА ПО ID"""
        
        can, used, remaining = self.rate_limiter.can_make_request()
        if not can:
            return {"error": f"Лимит! {used}/{MAX_REQUESTS_PER_DAY}"}
        
        url = f"{self.base_url}/report/{report_id}"
        logger.info(f"Fetching: {url}")
        
        try:
            async with self.session.get(url) as resp:
                if resp.status != 200:
                    self.rate_limiter.log_request(report_id, success=False)
                    return {"error": f"HTTP {resp.status}"}
                
                html = await resp.text()
                result = self.parse_html(html, report_id)
                self.rate_limiter.log_request(report_id, success=True, report_id=report_id)
                self.save_to_db(result)
                return result
                
        except Exception as e:
            self.rate_limiter.log_request(report_id, success=False)
            return {"error": str(e)}
    
    def parse_html(self, html: str, report_id: str) -> Dict:
        """ПАРСИНГ ВСЕЙ ИНФЫ ИЗ HTML"""
        
        result = {
            "report_id": report_id,
            "url": f"https://enigmasearch.org/report/{report_id}",
            "timestamp": datetime.now().isoformat(),
            "data": {}
        }
        
        d = result["data"]
        
        # Телефон
        phone_match = re.search(r'Телефон:\s*(\+?\d{10,15})', html)
        if phone_match:
            d["phone"] = phone_match.group(1)
        
        # Оператор
        op_match = re.search(r'Оператор связи:\s*([А-Яа-я\s\-]+)', html)
        if op_match:
            d["operator"] = op_match.group(1).strip()
        
        # Регион
        reg_match = re.search(r'Регион:\s*([А-Яа-я\s\-]+)', html)
        if reg_match:
            d["region"] = reg_match.group(1).strip()
        
        # Личности (ФИО)
        personalities = []
        # Ищем в формате [10] Залиева Наталья Владимировна
        for match in re.finditer(r'\[(\d+)\]\s*([А-Я][а-я]+\s[А-Я][а-я]+\s[А-Я][а-я]+)', html):
            personalities.append({
                "count": int(match.group(1)),
                "name": match.group(2).strip()
            })
        if personalities:
            d["personalities"] = personalities
        
        # Email
        emails = re.findall(r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}', html)
        if emails:
            d["emails"] = list(set(emails))
        
        # СНИЛС
        snils = re.findall(r'СНИЛС:\s*\[?(\d{11})\]?', html)
        if snils:
            d["snils"] = list(set(snils))
        
        # ИНН
        inns = re.findall(r'ИНН:\s*\[?(\d{10,12})\]?', html)
        if inns:
            d["inn"] = list(set(inns))
        
        # Адреса
        addresses = []
        for addr in re.findall(r'(\d{6}[,.]?\s*[Рр]осси[яи][^\n]{0,200})', html):
            addr = addr.strip()
            if addr and len(addr) > 10 and addr not in addresses:
                addresses.append(addr)
        if addresses:
            d["addresses"] = addresses[:5]
        
        # Карты
        cards = re.findall(r'Номер карты:\s*(\d{4}\*{4,8}\d{4})', html)
        if cards:
            d["cards"] = list(set(cards))
        
        # Telegram
        tg_links = re.findall(r'https://t\.me/([^\s\'"]+)', html)
        if tg_links:
            d["telegram_links"] = [f"https://t.me/{t}" for t in set(tg_links)]
        
        # Дополнительная инфа
        if re.search(r'СНИЛС:', html):
            d["has_snils"] = True
        if re.search(r'ИНН:', html):
            d["has_inn"] = True
        
        # Статистика
        result["statistics"] = {
            "personalities": len(d.get("personalities", [])),
            "emails": len(d.get("emails", [])),
            "addresses": len(d.get("addresses", [])),
            "snils": len(d.get("snils", [])),
            "inn": len(d.get("inn", []))
        }
        
        return result
    
    def save_to_db(self, data: Dict):
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        d = data.get("data", {})
        
        c.execute(
            """INSERT OR REPLACE INTO parsed_data 
               (report_id, phone, operator, region, personalities, emails, 
                snils, inn, addresses, card, telegram, parsed_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
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
                data.get("timestamp")
            )
        )
        conn.commit()
        conn.close()
        logger.info(f"Saved report: {data.get('report_id')}")

# ========== БОТ ==========

class EnigmaBot:
    def __init__(self, token: str):
        self.token = token
        self.parser = EnigmaParser()
    
    async def start_command(self, update, context):
        used = RateLimiter().get_today_requests()
        await update.message.reply_text(
            f"🔍 **ENIGMA SEARCH BOT v3.0**\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"📊 Лимит: {MAX_REQUESTS_PER_DAY}/день\n"
            f"✅ Сегодня: {used}\n"
            f"⏳ Осталось: {MAX_REQUESTS_PER_DAY - used}\n\n"
            f"📌 Команды:\n"
            f"/search <номер> - поиск и парсинг\n"
            f"/report <id> - парсинг по ID\n"
            f"/stats - статистика\n\n"
            f"Пример:\n"
            f"/search 79114649118"
        )
    
    async def search_command(self, update, context):
        if not context.args:
            await update.message.reply_text("❌ Введи номер: /search 79114649118")
            return
        
        phone = context.args[0]
        clean_phone = re.sub(r'\D', '', phone)
        
        if len(clean_phone) < 10:
            await update.message.reply_text(f"❌ Номер слишком короткий: {clean_phone}\nВведи полный номер, например: 79114649118")
            return
        
        await update.message.reply_text(f"🔍 Ищу информацию по номеру {clean_phone}...")
        
        await self.parser.init_session()
        result = await self.parser.search_by_phone(clean_phone)
        await self.parser.close_session()
        
        if "error" in result:
            # Показываем ссылки для ручного поиска
            response = f"❌ **{result['error']}**\n\n"
            if result.get("search_links"):
                response += "📌 **Ссылки для поиска:**\n"
                for link in result["search_links"]:
                    response += f"• {link}\n"
            await update.message.reply_text(response)
            return
        
        # Форматируем результат
        response = self.format_report(result)
        await update.message.reply_text(response)
        
        # Сохраняем JSON
        with open(f"report_{result['report_id'][:8]}.json", "w", encoding="utf-8") as f:
            json.dump(result, f, indent=2, ensure_ascii=False)
    
    async def report_command(self, update, context):
        if not context.args:
            await update.message.reply_text("❌ Введи ID: /report 019f3a2a-2744-70b0-9a93-f63c67f50388")
            return
        
        report_id = context.args[0]
        await update.message.reply_text(f"📄 Парсинг отчёта {report_id}...")
        
        await self.parser.init_session()
        result = await self.parser.fetch_report(report_id)
        await self.parser.close_session()
        
        if "error" in result:
            await update.message.reply_text(f"❌ {result['error']}")
            return
        
        response = self.format_report(result)
        await update.message.reply_text(response)
    
    def format_report(self, result: Dict) -> str:
        """Форматирование отчёта"""
        d = result.get("data", {})
        
        response = "📋 **ОТЧЁТ ENIGMA SEARCH**\n"
        response += "━━━━━━━━━━━━━━━━━━━━━━━\n"
        
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
        
        # Статистика
        if result.get("statistics"):
            stats = result["statistics"]
            response += "\n📊 **Найдено:**\n"
            response += f"  • Личностей: {stats.get('personalities', 0)}\n"
            response += f"  • Email: {stats.get('emails', 0)}\n"
            response += f"  • Адресов: {stats.get('addresses', 0)}\n"
        
        response += "\n━━━━━━━━━━━━━━━━━━━━━━━\n"
        response += f"🔗 [Открыть в браузере]({result.get('url', '')})"
        
        return response
    
    async def stats_command(self, update, context):
        used = RateLimiter().get_today_requests()
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("SELECT COUNT(*) FROM parsed_data")
        total = c.fetchone()[0]
        conn.close()
        
        await update.message.reply_text(
            f"📊 **СТАТИСТИКА**\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"📅 Сегодня: {used}/{MAX_REQUESTS_PER_DAY}\n"
            f"⏳ Осталось: {MAX_REQUESTS_PER_DAY - used}\n"
            f"📦 В БД: {total} отчётов"
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
    bot = EnigmaBot(BOT_TOKEN)
    bot.run()
