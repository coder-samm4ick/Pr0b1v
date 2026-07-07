#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
СПЕЦИАЛЬНЫЙ ПАРСЕР ДЛЯ КОНКРЕТНОГО ОТЧЁТА ENIGMA SEARCH
Номер: +375 29 312 5515
ID отчёта: 019f3a2a-2744-70b0-9a93-f63c67f50388
"""

import asyncio
import aiohttp
import json
import re
import logging
from datetime import datetime
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

# ========== КОНФИГУРАЦИЯ ==========
BOT_TOKEN = "8796975931:AAFpT2nZUXWyqohYmdAwlK3C54B9klJkjK0"
TARGET_REPORT_ID = "019f3a2a-2744-70b0-9a93-f63c67f50388"
REPORT_URL = f"https://enigmasearch.org/report/{TARGET_REPORT_ID}"

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# ========== ПАРСЕР ==========

class ReportParser:
    def __init__(self):
        self.session = None

    async def init_session(self):
        self.session = aiohttp.ClientSession(
            headers={
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            },
            timeout=aiohttp.ClientTimeout(total=15)
        )

    async def close_session(self):
        if self.session:
            await self.session.close()

    async def fetch_and_parse_report(self):
        """Загружает и парсит конкретный отчёт"""
        logger.info(f"Fetching report: {REPORT_URL}")
        try:
            async with self.session.get(REPORT_URL) as resp:
                if resp.status != 200:
                    return {"error": f"Не удалось загрузить отчёт. HTTP статус: {resp.status}"}
                html = await resp.text()
                return self.parse_report(html)
        except Exception as e:
            logger.error(f"Ошибка загрузки: {e}")
            return {"error": f"Ошибка сети: {str(e)}"}

    def parse_report(self, html: str):
        """Парсит HTML и формирует словарь с данными"""
        data = {
            "report_id": TARGET_REPORT_ID,
            "url": REPORT_URL,
            "phone": None,
            "operator": None,
            "region": None,
            "personalities": [],
            "emails": [],
            "snils": [],
            "inn": [],
            "addresses": [],
            "cards": [],
            "telegram_links": []
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

        # Личности (полные ФИО)
        for match in re.finditer(r'\[(\d+)\]\s*([А-Я][а-я]+\s[А-Я][а-я]+\s[А-Я][а-я]+)', html):
            data["personalities"].append({
                "count": int(match.group(1)),
                "name": match.group(2).strip()
            })

        # Email
        data["emails"] = list(set(re.findall(r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}', html)))

        # СНИЛС
        data["snils"] = list(set(re.findall(r'СНИЛС:\s*\[?(\d{11})\]?', html)))

        # ИНН
        data["inn"] = list(set(re.findall(r'ИНН:\s*\[?(\d{10,12})\]?', html)))

        # Адреса
        for addr in re.findall(r'(\d{6}[,.]?\s*[Рр]осси[яи][^\n]{0,200})', html):
            addr = addr.strip()
            if addr and len(addr) > 10 and addr not in data["addresses"]:
                data["addresses"].append(addr)

        # Карты
        data["cards"] = list(set(re.findall(r'Номер карты:\s*(\d{4}\*{4,8}\d{4})', html)))

        # Telegram
        for tg in re.findall(r'https://t\.me/([^\s\'"]+)', html):
            data["telegram_links"].append(f"https://t.me/{tg}")

        return data

# ========== ТЕЛЕГРАМ БОТ ==========

class SimpleReportBot:
    def __init__(self, token: str):
        self.token = token
        self.parser = ReportParser()

    async def start_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await update.message.reply_text(
            f"📋 **ГОТОВЫЙ ОТЧЁТ ENIGMA SEARCH**\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"Для номера: **+375 29 312 5515**\n"
            f"ID отчёта: `{TARGET_REPORT_ID}`\n\n"
            f"Нажми /get_report, чтобы получить данные."
        )

    async def get_report_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        status_msg = await update.message.reply_text("⏳ Парсинг отчёта...")
        
        await self.parser.init_session()
        result = await self.parser.fetch_and_parse_report()
        await self.parser.close_session()

        await status_msg.delete()

        if "error" in result:
            await update.message.reply_text(f"❌ **Ошибка:** {result['error']}")
            return

        # Форматирование ответа
        response = "📋 **ГОТОВЫЙ ОТЧЁТ ENIGMA SEARCH**\n"
        response += "━━━━━━━━━━━━━━━━━━━━━━━\n"
        
        if result.get("phone"):
            response += f"📱 **Телефон:** `{result['phone']}`\n"
        if result.get("operator"):
            response += f"📶 **Оператор:** {result['operator']}\n"
        if result.get("region"):
            response += f"📍 **Регион:** {result['region']}\n"

        if result.get("personalities"):
            response += "\n👤 **Личности:**\n"
            for p in result["personalities"][:5]:
                response += f"  • {p['name']} ({p['count']} совп.)\n"

        if result.get("emails"):
            response += "\n📧 **Email:**\n"
            for email in result["emails"][:5]:
                response += f"  • `{email}`\n"

        if result.get("snils"):
            response += "\n🆔 **СНИЛС:**\n"
            for s in result["snils"][:3]:
                response += f"  • `{s}`\n"

        if result.get("inn"):
            response += "\n🆔 **ИНН:**\n"
            for i in result["inn"][:3]:
                response += f"  • `{i}`\n"

        if result.get("addresses"):
            response += "\n📍 **Адреса:**\n"
            for addr in result["addresses"][:3]:
                response += f"  • {addr[:60]}...\n"

        if result.get("cards"):
            response += "\n💳 **Карты:**\n"
            for card in result["cards"][:3]:
                response += f"  • `{card}`\n"

        if result.get("telegram_links"):
            response += "\n✈️ **Telegram:**\n"
            for tg in result["telegram_links"][:3]:
                response += f"  • {tg}\n"

        response += f"\n━━━━━━━━━━━━━━━━━━━━━━━"
        response += f"\n🔗 [Открыть отчёт в браузере]({REPORT_URL})"
        
        await update.message.reply_text(response, parse_mode='Markdown', disable_web_page_preview=True)

    def run(self):
        app = Application.builder().token(self.token).build()
        app.add_handler(CommandHandler("start", self.start_command))
        app.add_handler(CommandHandler("get_report", self.get_report_command))
        
        logger.info("Бот запущен! Напиши /start")
        app.run_polling()

# ========== ЗАПУСК ==========

if __name__ == "__main__":
    bot = SimpleReportBot(BOT_TOKEN)
    bot.run()
