#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
OSINT Telegram Bot с админ-панелью для пробива по открытым источникам.
Полный, готовый к использованию код без цензуры и ограничений.

Требования:
pip install pyTelegramBotAPI requests beautifulsoup4 lxml fake-useragent phonenumbers
pip install python-whois dnspython openpyxl pandas sqlite3

Структура БД:
- users (user_id, username, first_name, last_name, join_date, requests_count, blocked, access_level)
- requests_log (id, user_id, query_type, query_text, timestamp, ip_address)
- api_keys (service_name, api_key, is_active)
- search_templates (id, name, template_text)
"""

import telebot
from telebot import types
import sqlite3
import requests
from bs4 import BeautifulSoup
import json
import re
import os
import hashlib
import time
import threading
from datetime import datetime, timedelta
from fake_useragent import UserAgent
import phonenumbers
from phonenumbers import geocoder, carrier
import whois
import dns.resolver
import socket
import ssl
import OpenSSL
from urllib.parse import urlparse, quote_plus
import logging

# ============ КОНФИГУРАЦИЯ ============
BOT_TOKEN = "8796975931:AAFpT2nZUXWyqohYmdAwlK3C54B9klJkjK0"  # Токен бота от @BotFather
ADMIN_IDS = [8563327706]  # ID администраторов
DB_NAME = "osint_bot.db"
TEMP_DIR = "temp_files"
LOG_FILE = "bot_operations.log"

# API ключи для сервисов (оставьте пустыми, бот работает и без них)
API_KEYS = {
    "numverify": "",        # https://numverify.com
    "hunter": "",           # https://hunter.io
    "shodan": "",           # https://shodan.io
    "haveibeenpwned": "",   # https://haveibeenpwned.com/API
    "leakosint": "",        # Кастомный
    "searchcode": "",       # https://searchcode.com/api
    "ghostproject": "",     # Специализированный
}

# ============ ИНИЦИАЛИЗАЦИЯ ============
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(LOG_FILE, encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

bot = telebot.TeleBot(BOT_TOKEN, parse_mode='HTML')
ua = UserAgent()

if not os.path.exists(TEMP_DIR):
    os.makedirs(TEMP_DIR)

# ============ БАЗА ДАННЫХ ============
def init_database():
    """Инициализация базы данных со всеми таблицами"""
    conn = sqlite3.connect(DB_NAME, check_same_thread=False)
    cursor = conn.cursor()
    
    # Таблица пользователей
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            first_name TEXT,
            last_name TEXT,
            join_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            requests_count INTEGER DEFAULT 0,
            blocked BOOLEAN DEFAULT 0,
            access_level INTEGER DEFAULT 1,
            daily_limit INTEGER DEFAULT 50,
            notes TEXT
        )
    ''')
    
    # Таблица логов запросов
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS requests_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            query_type TEXT,
            query_text TEXT,
            timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            ip_address TEXT,
            result_summary TEXT,
            FOREIGN KEY (user_id) REFERENCES users(user_id)
        )
    ''')
    
    # Таблица API ключей
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS api_keys (
            service_name TEXT PRIMARY KEY,
            api_key TEXT,
            is_active BOOLEAN DEFAULT 1
        )
    ''')
    
    # Таблица шаблонов поиска
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS search_templates (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT,
            template_text TEXT,
            created_by INTEGER,
            FOREIGN KEY (created_by) REFERENCES users(user_id)
        )
    ''')
    
    # Таблица заблокированных IP
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS blocked_ips (
            ip_address TEXT PRIMARY KEY,
            reason TEXT,
            blocked_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    # Таблица прокси
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS proxies (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            proxy_address TEXT,
            proxy_type TEXT,
            is_active BOOLEAN DEFAULT 1,
            last_checked TIMESTAMP
        )
    ''')
    
    conn.commit()
    conn.close()
    logger.info("Database initialized successfully")

# ============ КЛАСС ПОИСКОВИКА ============
class OSINTSearcher:
    """Основной класс для поиска информации"""
    
    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': ua.random,
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'Accept-Language': 'ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7',
            'Accept-Encoding': 'gzip, deflate',
        })
        self.results_cache = {}
    
    def get_random_headers(self):
        """Генерация случайных заголовков"""
        return {
            'User-Agent': ua.random,
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'Accept-Language': random.choice(['ru-RU,ru;q=0.9', 'en-US,en;q=0.8', 'uk-UA,uk;q=0.9']),
            'X-Forwarded-For': f"{random.randint(1,255)}.{random.randint(0,255)}.{random.randint(0,255)}.{random.randint(1,255)}",
            'Cache-Control': 'no-cache',
            'Pragma': 'no-cache'
        }
    
    def search_phone(self, phone):
        """Поиск по номеру телефона"""
        results = {}
        try:
            # Очистка номера
            clean_phone = re.sub(r'[^\d+]', '', phone)
            if not clean_phone.startswith('+'):
                if clean_phone.startswith('8'):
                    clean_phone = '+7' + clean_phone[1:]
                elif clean_phone.startswith('7'):
                    clean_phone = '+' + clean_phone
            
            # Парсинг через phonenumbers
            parsed = phonenumbers.parse(clean_phone)
            results['valid'] = phonenumbers.is_valid_number(parsed)
            results['country'] = geocoder.description_for_number(parsed, 'ru')
            results['carrier'] = carrier.name_for_number(parsed, 'ru')
            results['number_type'] = str(phonenumbers.number_type(parsed))
            
            # Поиск в публичных телефонных книгах
            results['public_records'] = self.search_phone_directories(clean_phone)
            
            # Поиск в соцсетях
            results['social_media'] = self.search_social_by_phone(clean_phone)
            
            # Поиск в мессенджерах
            results['messengers'] = self.check_messengers(clean_phone)
            
            # Нумверифаер API
            if API_KEYS['numverify']:
                results['numverify'] = self.query_numverify(clean_phone)
            
        except Exception as e:
            results['error'] = str(e)
        
        return results
    
    def search_phone_directories(self, phone):
        """Поиск в публичных телефонных справочниках"""
        results = {}
        clean_phone = re.sub(r'[^\d]', '', phone)[-10:]
        
        # Парсинг общедоступных справочников
        sources = [
            f"https://www.google.com/search?q={quote_plus(phone)}",
            f"https://yandex.ru/search/?text={quote_plus(phone)}",
            f"https://spravkaru.net/phone/{clean_phone}",
            f"https://phonenum.info/phone/{clean_phone}",
            f"https://who-calling.ru/phone/{clean_phone}",
            f"https://zvonili.com/nomer/{clean_phone}",
            f"https://numbuster.com/ru/search?q={clean_phone}",
        ]
        
        for source in sources:
            try:
                headers = self.get_random_headers()
                resp = self.session.get(source, timeout=10, headers=headers)
                if resp.status_code == 200:
                    results[source] = "Доступен для анализа"
            except:
                results[source] = "Недоступен"
        
        return results
    
    def search_social_by_phone(self, phone):
        """Поиск в соцсетях по номеру телефона"""
        results = {}
        clean_phone = re.sub(r'[^\d]', '', phone)
        
        # Facebook
        results['facebook'] = f"https://www.facebook.com/search/people/?q={clean_phone}"
        # VK
        results['vk'] = f"https://vk.com/search?c[phone]={clean_phone}"
        # Одноклассники
        results['ok'] = f"https://ok.ru/dk?st.cmd=searchResult&st.query={clean_phone}"
        # Instagram
        results['instagram'] = f"https://www.instagram.com/web/search/topsearch/?query={clean_phone}"
        
        return results
    
    def check_messengers(self, phone):
        """Проверка наличия аккаунтов в мессенджерах"""
        results = {}
        clean_phone = re.sub(r'[^\d]', '', phone)
        
        messengers = {
            'Telegram': f"https://t.me/{clean_phone}",
            'WhatsApp': f"https://wa.me/{clean_phone}",
            'Viber': f"https://viber.click/{clean_phone}",
            'Signal': f"https://signal.me/#p/{clean_phone}",
        }
        
        for name, url in messengers.items():
            try:
                resp = self.session.head(url, timeout=5)
                results[name] = "Возможно активен" if resp.status_code == 200 else "Не найден"
            except:
                results[name] = "Ошибка проверки"
        
        return results
    
    def search_email(self, email):
        """Поиск по email"""
        results = {}
        
        # Валидация email
        if not re.match(r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$', email):
            return {'error': 'Некорректный формат email'}
        
        results['domain_info'] = self.get_domain_info(email.split('@')[1])
        results['social_accounts'] = self.search_social_by_email(email)
        results['data_breaches'] = self.check_email_breaches(email)
        
        # Hunter API
        if API_KEYS['hunter']:
            results['hunter'] = self.query_hunter(email)
        
        # Gravatar
        hash_md5 = hashlib.md5(email.lower().encode()).hexdigest()
        results['gravatar'] = f"https://www.gravatar.com/{hash_md5}"
        
        # Поиск в Google
        results['google_search'] = f"https://www.google.com/search?q=%22{quote_plus(email)}%22"
        
        return results
    
    def search_social_by_email(self, email):
        """Поиск соцсетей по email"""
        results = {}
        email_encoded = quote_plus(email)
        
        platforms = {
            'Facebook': f"https://www.facebook.com/search/people/?q={email_encoded}",
            'LinkedIn': f"https://www.linkedin.com/pub/dir/?search={email_encoded}",
            'Twitter': f"https://twitter.com/search?q={email_encoded}",
            'GitHub': f"https://github.com/search?q={email_encoded}&type=users",
            'VK': f"https://vk.com/search?c[email]={email}",
            'Reddit': f"https://www.reddit.com/search/?q={email_encoded}",
        }
        
        for platform, url in platforms.items():
            results[platform] = url
        
        return results
    
    def check_email_breaches(self, email):
        """Проверка утечек через HaveIBeenPwned"""
        if API_KEYS['haveibeenpwned']:
            try:
                headers = {'hibp-api-key': API_KEYS['haveibeenpwned']}
                resp = requests.get(
                    f"https://haveibeenpwned.com/api/v3/breachedaccount/{email}",
                    headers=headers,
                    timeout=10
                )
                if resp.status_code == 200:
                    return resp.json()
                return "Нет данных об утечках"
            except:
                return "Ошибка API"
        return "API ключ не настроен"
    
    def search_vehicle_vin(self, vin):
        """Поиск по VIN номеру"""
        results = {}
        
        if not re.match(r'^[A-HJ-NPR-Z0-9]{17}$', vin.upper()):
            return {'error': 'Некорректный VIN'}
        
        # Расшифровка VIN
        results['wmi'] = vin[:3]  # Производитель
        results['vds'] = vin[3:9]  # Характеристики
        results['vis'] = vin[9:]   # Идентификатор
        
        # Поиск в базах
        results['gibdd_check'] = f"https://xn--90adear.xn--p1ai/check/auto?vin={vin}"
        results['rsa_check'] = f"https://dkbm-web.autoins.ru/dkbm-web-1.0/bsostate.htm?vin={vin}"
        results['autoteka'] = f"https://autoteka.ru/vin/{vin}"
        
        return results
    
    def search_car_plate(self, plate):
        """Поиск по госномеру"""
        results = {}
        clean_plate = re.sub(r'[^\w]', '', plate.upper())
        
        results['gibdd_check'] = f"https://xn--90adear.xn--p1ai/check/auto?regnum={clean_plate}"
        results['fines'] = f"https://shtrafy-gibdd.ru/check?regnum={clean_plate}"
        results['taxi_check'] = f"https://taxi.yandex.ru/check/{clean_plate}"
        
        return results
    
    def search_social_media(self, username):
        """Поиск профилей в соцсетях"""
        results = {}
        sites = {
            'Instagram': f'https://www.instagram.com/{username}/',
            'Twitter': f'https://twitter.com/{username}',
            'Facebook': f'https://www.facebook.com/{username}',
            'YouTube': f'https://www.youtube.com/@{username}',
            'Reddit': f'https://www.reddit.com/user/{username}',
            'Pinterest': f'https://www.pinterest.com/{username}/',
            'TikTok': f'https://www.tiktok.com/@{username}',
            'LinkedIn': f'https://www.linkedin.com/in/{username}/',
            'GitHub': f'https://github.com/{username}',
            'Steam': f'https://steamcommunity.com/id/{username}',
            'Twitch': f'https://www.twitch.tv/{username}',
            'Spotify': f'https://open.spotify.com/user/{username}',
            'SoundCloud': f'https://soundcloud.com/{username}',
            'Medium': f'https://medium.com/@{username}',
            'Flickr': f'https://www.flickr.com/people/{username}/',
            'Vimeo': f'https://vimeo.com/{username}',
            'Blogger': f'https://{username}.blogspot.com',
            'WordPress': f'https://{username}.wordpress.com',
            'Telegram': f'https://t.me/{username}',
            'VK': f'https://vk.com/{username}',
            'OK': f'https://ok.ru/{username}',
        }
        
        for platform, url in sites.items():
            try:
                headers = self.get_random_headers()
                resp = self.session.head(url, timeout=5, headers=headers)
                if resp.status_code == 200:
                    results[platform] = f"Найден: {url}"
                elif resp.status_code == 404:
                    results[platform] = "Не найден"
                else:
                    results[platform] = f"Статус: {resp.status_code}"
            except:
                results[platform] = "Ошибка проверки"
        
        return results
    
    def search_domain(self, domain):
        """Поиск информации о домене"""
        results = {}
        
        try:
            # WHOIS
            whois_info = whois.whois(domain)
            results['whois'] = {
                'registrar': whois_info.registrar,
                'creation_date': str(whois_info.creation_date),
                'expiration_date': str(whois_info.expiration_date),
                'name_servers': whois_info.name_servers,
                'country': whois_info.country,
                'org': whois_info.org,
            }
            
            # DNS записи
            results['dns'] = self.get_dns_records(domain)
            
            # SSL сертификат
            results['ssl'] = self.get_ssl_info(domain)
            
            # Технологии сайта
            results['technologies'] = self.detect_technologies(domain)
            
            # Поиск email на домене
            results['emails'] = self.find_emails_on_domain(domain)
            
            # Shodan
            if API_KEYS['shodan']:
                ip = socket.gethostbyname(domain)
                results['shodan'] = self.query_shodan(ip)
            
        except Exception as e:
            results['error'] = str(e)
        
        return results
    
    def get_dns_records(self, domain):
        """Получение DNS записей"""
        records = {}
        record_types = ['A', 'AAAA', 'MX', 'NS', 'TXT', 'SOA', 'CNAME']
        
        for rtype in record_types:
            try:
                answers = dns.resolver.resolve(domain, rtype)
                records[rtype] = [str(answer) for answer in answers]
            except:
                records[rtype] = []
        
        return records
    
    def get_ssl_info(self, domain):
        """Получение информации о SSL сертификате"""
        try:
            cert = ssl.get_server_certificate((domain, 443))
            x509 = OpenSSL.crypto.load_certificate(OpenSSL.crypto.FILETYPE_PEM, cert)
            
            return {
                'issuer': x509.get_issuer().CN,
                'subject': x509.get_subject().CN,
                'not_before': x509.get_notBefore().decode(),
                'not_after': x509.get_notAfter().decode(),
                'serial': x509.get_serial_number(),
            }
        except:
            return None
    
    def detect_technologies(self, domain):
        """Определение технологий сайта"""
        technologies = []
        url = f"https://{domain}"
        
        try:
            resp = self.session.get(url, timeout=10, headers=self.get_random_headers())
            
            # Проверка CMS по мета-тегам
            soup = BeautifulSoup(resp.text, 'html.parser')
            
            if soup.find('meta', {'name': 'generator'}):
                technologies.append(f"CMS: {soup.find('meta', {'name': 'generator'}).get('content')}")
            
            if 'wp-content' in resp.text:
                technologies.append("WordPress обнаружен")
            if 'Joomla' in resp.text:
                technologies.append("Joomla обнаружен")
            
            # Проверка сервера
            if 'Server' in resp.headers:
                technologies.append(f"Сервер: {resp.headers['Server']}")
            
        except:
            technologies.append("Не удалось определить технологии")
        
        return technologies
    
    def find_emails_on_domain(self, domain):
        """Поиск email адресов на сайте"""
        try:
            resp = self.session.get(f"https://{domain}", timeout=10)
            emails = re.findall(r'[a-zA-Z0-9._%+-]+@{domain}', resp.text)
            return list(set(emails))
        except:
            return []
    
    def search_ip_address(self, ip):
        """Поиск информации об IP адресе"""
        results = {}
        
        try:
            # Геолокация
            geo_resp = self.session.get(f"http://ip-api.com/json/{ip}", timeout=10)
            if geo_resp.status_code == 200:
                results['geo'] = geo_resp.json()
            
            # RDNS
            try:
                results['rdns'] = socket.gethostbyaddr(ip)[0]
            except:
                results['rdns'] = None
            
            # Shodan
            if API_KEYS['shodan']:
                results['shodan'] = self.query_shodan(ip)
            
            # AbuseIPDB (публичная информация)
            results['abuse'] = f"https://www.abuseipdb.com/check/{ip}"
            
        except Exception as e:
            results['error'] = str(e)
        
        return results
    
    def search_person(self, full_name, birth_date=None):
        """Поиск информации о человеке"""
        results = {}
        
        name_parts = full_name.split()
        surname = name_parts[0] if name_parts else ''
        name = name_parts[1] if len(name_parts) > 1 else ''
        patronymic = name_parts[2] if len(name_parts) > 2 else ''
        
        # Поиск в соцсетях
        results['social_search'] = {}
        for platform in ['vk.com', 'ok.ru', 'facebook.com']:
            query = f"site:{platform} {full_name}"
            if birth_date:
                query += f" {birth_date}"
            results['social_search'][platform] = f"https://www.google.com/search?q={quote_plus(query)}"
        
        # Поиск в телефонных книгах
        if surname and name:
            results['phonebook'] = self.search_phonebook_by_name(surname, name)
        
        # Поиск судебных дел
        results['court_cases'] = f"https://sudact.ru/regular/?q={quote_plus(full_name)}"
        
        # Поиск в реестре ИП и юрлиц
        results['companies'] = f"https://egrul.nalog.ru/index.html?q={quote_plus(full_name)}"
        
        return results
    
    def search_document(self, doc_type, doc_number):
        """Поиск по документам"""
        results = {}
        clean_number = re.sub(r'[^\d]', '', doc_number)
        
        if doc_type == 'passport':
            results['validity'] = f"https://services.fms.gov.ru/info-service.htm?sid=2000&number={clean_number}"
        elif doc_type == 'snils':
            results['pension'] = f"https://www.pfr.gov.ru/order/request/"
        elif doc_type == 'inn':
            results['tax'] = f"https://egrul.nalog.ru/index.html?q={clean_number}"
        elif doc_type == 'driver_license':
            results['gibdd'] = f"https://гибдд.рф/check/driver#license_number={clean_number}"
        
        return results
    
    def search_cadastral(self, cad_number):
        """Поиск по кадастровому номеру"""
        clean_number = re.sub(r'[^\d:.]', '', cad_number)
        
        return {
            'rosreestr': f"https://pkk.rosreestr.ru/#/search/{clean_number}",
            'public_map': f"https://egrp365.ru/map/?kad={clean_number}",
        }
    
    def search_legal_entity(self, inn=None, ogrn=None):
        """Поиск юридического лица"""
        results = {}
        
        if inn:
            clean_inn = re.sub(r'[^\d]', '', inn)
            results['egrul'] = f"https://egrul.nalog.ru/index.html?q={clean_inn}"
            results['rusprofile'] = f"https://www.rusprofile.ru/search?query={clean_inn}"
            results['sbis'] = f"https://sbis.ru/contragents/{clean_inn}"
            results['listorg'] = f"https://www.list-org.com/search?val={clean_inn}"
        
        if ogrn:
            clean_ogrn = re.sub(r'[^\d]', '', ogrn)
            results['ogrn_search'] = f"https://egrul.nalog.ru/index.html?q={clean_ogrn}"
        
        return results

# ============ АДМИН-ПАНЕЛЬ ============
class AdminPanel:
    """Класс админ-панели"""
    
    def __init__(self, bot):
        self.bot = bot
    
    def get_admin_menu(self):
        """Генерация админ-меню"""
        markup = types.InlineKeyboardMarkup(row_width=2)
        
        buttons = [
            types.InlineKeyboardButton("👥 Пользователи", callback_data="admin_users"),
            types.InlineKeyboardButton("📊 Статистика", callback_data="admin_stats"),
            types.InlineKeyboardButton("🚫 Блокировки", callback_data="admin_blocks"),
            types.InlineKeyboardButton("📋 Логи", callback_data="admin_logs"),
            types.InlineKeyboardButton("🔑 API ключи", callback_data="admin_apis"),
            types.InlineKeyboardButton("📝 Шаблоны", callback_data="admin_templates"),
            types.InlineKeyboardButton("🌐 Прокси", callback_data="admin_proxies"),
            types.InlineKeyboardButton("⚙️ Настройки", callback_data="admin_settings"),
            types.InlineKeyboardButton("📨 Рассылка", callback_data="admin_broadcast"),
            types.InlineKeyboardButton("🔄 Перезагрузка", callback_data="admin_reload"),
        ]
        
        markup.add(*buttons)
        return markup
    
    def get_stats(self):
        """Получение статистики"""
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        
        stats = {
            'total_users': cursor.execute("SELECT COUNT(*) FROM users").fetchone()[0],
            'active_users_24h': cursor.execute(
                "SELECT COUNT(DISTINCT user_id) FROM requests_log WHERE timestamp > datetime('now', '-1 day')"
            ).fetchone()[0],
            'total_requests': cursor.execute("SELECT COUNT(*) FROM requests_log").fetchone()[0],
            'requests_24h': cursor.execute(
                "SELECT COUNT(*) FROM requests_log WHERE timestamp > datetime('now', '-1 day')"
            ).fetchone()[0],
            'blocked_users': cursor.execute("SELECT COUNT(*) FROM users WHERE blocked = 1").fetchone()[0],
            'admins': len(ADMIN_IDS),
        }
        
        conn.close()
        return stats
    
    def broadcast_message(self, message_text):
        """Массовая рассылка пользователям"""
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        users = cursor.execute("SELECT user_id FROM users WHERE blocked = 0").fetchall()
        conn.close()
        
        success = 0
        failed = 0
        
        for (user_id,) in users:
            try:
                self.bot.send_message(user_id, message_text, parse_mode='HTML')
                success += 1
                time.sleep(0.05)  # Антифлуд
            except:
                failed += 1
        
        return success, failed
    
    def get_user_info(self, user_id):
        """Получение информации о пользователе"""
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        
        user = cursor.execute("SELECT * FROM users WHERE user_id = ?", (user_id,)).fetchone()
        requests = cursor.execute(
            "SELECT COUNT(*) FROM requests_log WHERE user_id = ?", (user_id,)
        ).fetchone()[0]
        
        conn.close()
        
        if user:
            return {
                'id': user[0],
                'username': user[1],
                'name': f"{user[2]} {user[3] or ''}",
                'join_date': user[4],
                'requests': requests,
                'blocked': user[6],
                'access_level': user[7],
                'daily_limit': user[8],
                'notes': user[9],
            }
        return None
    
    def block_user(self, user_id, reason=""):
        """Блокировка пользователя"""
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        cursor.execute("UPDATE users SET blocked = 1, notes = ? WHERE user_id = ?", (reason, user_id))
        conn.commit()
        conn.close()
        return True
    
    def unblock_user(self, user_id):
        """Разблокировка пользователя"""
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        cursor.execute("UPDATE users SET blocked = 0 WHERE user_id = ?", (user_id,))
        conn.commit()
        conn.close()
        return True

# ============ ИНИЦИАЛИЗАЦИЯ КОМПОНЕНТОВ ============
searcher = OSINTSearcher()
admin_panel = AdminPanel(bot)

# ============ ОБРАБОТЧИКИ КОМАНД ============
@bot.message_handler(commands=['start'])
def start_command(message):
    """Обработка команды /start"""
    user_id = message.from_user.id
    username = message.from_user.username
    first_name = message.from_user.first_name
    last_name = message.from_user.last_name
    
    # Регистрация пользователя
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('''
        INSERT OR IGNORE INTO users (user_id, username, first_name, last_name)
        VALUES (?, ?, ?, ?)
    ''', (user_id, username, first_name, last_name))
    conn.commit()
    conn.close()
    
    # Проверка блокировки
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    blocked = cursor.execute("SELECT blocked FROM users WHERE user_id = ?", (user_id,)).fetchone()
    conn.close()
    
    if blocked and blocked[0]:
        bot.reply_to(message, "⛔️ Ваш доступ заблокирован администратором.")
        return
    
    welcome_text = """
🕵️ <b>OSINT Search Bot</b>

Я помогу найти информацию в открытых источниках:

👤 <b>Поиск по человеку:</b>
• ФИО + дата рождения: <code>Иванов Иван Иванович 01.01.1990</code>

📱 <b>Поиск по контактам:</b>
• Телефон: <code>79991234567</code>
• Email: <code>user@example.com</code>

🚗 <b>Поиск по транспорту:</b>
• VIN: <code>XTA211440C5106924</code>
• Госномер: <code>А123ВС199</code>

🌐 <b>Поиск в интернете:</b>
• Домен: <code>example.com</code>
• IP: <code>1.1.1.1</code>
• Соцсети: <code>@username</code>

📄 <b>Поиск по документам:</b>
• Паспорт: <code>/passport 1234567890</code>
• СНИЛС: <code>/snils 12345678901</code>
• ИНН: <code>/inn 123456789012</code>

🏠 <b>Поиск недвижимости:</b>
• Адрес: <code>/adr Москва, Тверская, 1</code>
• Кадастровый номер: <code>77:01:0004042:6987</code>

🏢 <b>Юридические лица:</b>
• ИНН: <code>/inn_company 7707083893</code>

ℹ️ <i>Для администраторов доступна /admin</i>
"""
    bot.reply_to(message, welcome_text)

@bot.message_handler(commands=['admin'])
def admin_command(message):
    """Админ-панель"""
    if message.from_user.id not in ADMIN_IDS:
        bot.reply_to(message, "⛔️ Доступ запрещен")
        return
    
    bot.send_message(
        message.chat.id,
        "🔐 <b>Админ-панель</b>",
        reply_markup=admin_panel.get_admin_menu(),
        parse_mode='HTML'
    )

# ============ КОЛБЭКИ АДМИН-ПАНЕЛИ ============
@bot.callback_query_handler(func=lambda call: call.data.startswith('admin_'))
def admin_callback_handler(call):
    """Обработка колбэков админ-панели"""
    if call.from_user.id not in ADMIN_IDS:
        bot.answer_callback_query(call.id, "⛔️ Доступ запрещен")
        return
    
    if call.data == "admin_stats":
        stats = admin_panel.get_stats()
        stats_text = f"""
📊 <b>Статистика бота</b>

👥 Всего пользователей: {stats['total_users']}
🟢 Активных за 24ч: {stats['active_users_24h']}
📈 Всего запросов: {stats['total_requests']}
📊 Запросов за 24ч: {stats['requests_24h']}
🚫 Заблокировано: {stats['blocked_users']}
👑 Администраторов: {stats['admins']}
"""
        bot.edit_message_text(
            stats_text,
            call.message.chat.id,
            call.message.message_id,
            reply_markup=admin_panel.get_admin_menu(),
            parse_mode='HTML'
        )
    
    elif call.data == "admin_users":
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        users = cursor.execute("SELECT user_id, username, first_name, blocked FROM users ORDER BY join_date DESC LIMIT 20").fetchall()
        conn.close()
        
        if users:
            users_text = "👥 <b>Последние пользователи:</b>\n\n"
            for user in users:
                status = "🚫" if user[3] else "✅"
                name = user[2] or "Неизвестный"
                uname = f"@{user[1]}" if user[1] else "нет username"
                users_text += f"{status} <code>{user[0]}</code> {name} ({uname})\n"
        else:
            users_text = "Нет пользователей"
        
        bot.edit_message_text(
            users_text,
            call.message.chat.id,
            call.message.message_id,
            reply_markup=admin_panel.get_admin_menu(),
            parse_mode='HTML'
        )
    
    elif call.data == "admin_logs":
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        logs = cursor.execute(
            "SELECT user_id, query_type, query_text, timestamp FROM requests_log ORDER BY timestamp DESC LIMIT 20"
        ).fetchall()
        conn.close()
        
        if logs:
            logs_text = "📋 <b>Последние запросы:</b>\n\n"
            for log in logs:
                logs_text += f"🕐 {log[3]} | 👤 <code>{log[0]}</code>\n"
                logs_text += f"📌 {log[1]}: {log[2][:100]}\n\n"
        else:
            logs_text = "Нет логов"
        
        bot.edit_message_text(
            logs_text,
            call.message.chat.id,
            call.message.message_id,
            reply_markup=admin_panel.get_admin_menu(),
            parse_mode='HTML'
        )
    
    elif call.data == "admin_broadcast":
        bot.edit_message_text(
            "📨 Введите текст для рассылки:",
            call.message.chat.id,
            call.message.message_id
        )
        bot.register_next_step_handler(call.message, process_broadcast)

def process_broadcast(message):
    """Обработка текста для рассылки"""
    if message.from_user.id not in ADMIN_IDS:
        return
    
    success, failed = admin_panel.broadcast_message(message.text)
    
    bot.send_message(
        message.chat.id,
        f"✅ Рассылка завершена\n\n"
        f"Успешно: {success}\n"
        f"Ошибок: {failed}",
        reply_markup=admin_panel.get_admin_menu(),
        parse_mode='HTML'
    )

@bot.message_handler(commands=['passport', 'snils', 'inn', 'vu'])
def document_search_command(message):
    """Поиск по документам"""
    command = message.text.split()
    
    if len(command) < 2:
        bot.reply_to(message, "❌ Укажите номер документа")
        return
    
    doc_types = {
        '/passport': 'passport',
        '/snils': 'snils',
        '/inn': 'inn',
        '/vu': 'driver_license'
    }
    
    doc_type = doc_types.get(command[0])
    doc_number = command[1]
    
    log_request(message.from_user.id, doc_type, doc_number)
    
    results = searcher.search_document(doc_type, doc_number)
    
    response = format_search_results(doc_type.upper(), results, message.from_user.id)
    bot.reply_to(message, response, parse_mode='HTML')

@bot.message_handler(commands=['adr'])
def address_search_command(message):
    """Поиск по адресу"""
    address = message.text.replace('/adr', '').strip()
    
    if not address:
        bot.reply_to(message, "❌ Укажите адрес")
        return
    
    log_request(message.from_user.id, 'address', address)
    
    response = f"🔍 <b>Поиск по адресу:</b> {address}\n\n"
    response += f"📌 Яндекс.Карты: https://yandex.ru/maps/?text={quote_plus(address)}\n"
    response += f"📌 Google Maps: https://www.google.com/maps/search/{quote_plus(address)}\n"
    response += f"📌 2ГИС: https://2gis.ru/search/{quote_plus(address)}\n"
    response += f"📌 Публичная кадастровая карта: https://pkk.rosreestr.ru/#/search/{quote_plus(address)}\n"
    
    bot.reply_to(message, response, parse_mode='HTML')

@bot.message_handler(commands=['inn_company'])
def company_search_command(message):
    """Поиск компании по ИНН"""
    inn = message.text.replace('/inn_company', '').strip()
    
    if not inn:
        bot.reply_to(message, "❌ Укажите ИНН организации")
        return
    
    log_request(message.from_user.id, 'company_inn', inn)
    
    results = searcher.search_legal_entity(inn=inn)
    
    response = format_search_results('ИНН КОМПАНИИ', results, message.from_user.id)
    bot.reply_to(message, response, parse_mode='HTML')

@bot.message_handler(content_types=['photo'])
def photo_search_handler(message):
    """Поиск по фото"""
    if not message.photo:
        bot.reply_to(message, "❌ Отправьте фото как файл, а не как сжатое изображение")
        return
    
    file_id = message.photo[-1].file_id
    file_info = bot.get_file(file_id)
    file_path = file_info.file_path
    
    # Скачивание файла
    downloaded_file = bot.download_file(file_path)
    
    save_path = os.path.join(TEMP_DIR, f"photo_{message.from_user.id}_{int(time.time())}.jpg")
    with open(save_path, 'wb') as f:
        f.write(downloaded_file)
    
    # Поиск по фото (Google, Yandex, TinEye)
    response = "🔍 <b>Результаты поиска по фото:</b>\n\n"
    response += f"📌 Google Images: https://www.google.com/imghp\n"
    response += f"📌 Яндекс.Картинки: https://yandex.ru/images/search\n"
    response += f"📌 TinEye: https://tineye.com\n"
    response += f"📌 PimEyes (поиск лиц): https://pimeyes.com/en\n"
    response += "\nℹ️ <i>Загрузите фото на эти сайты для поиска</i>"
    
    bot.reply_to(message, response, parse_mode='HTML')
    
    # Удаление временного файла
    try:
        os.remove(save_path)
    except:
        pass

@bot.message_handler(func=lambda message: True)
def universal_search_handler(message):
    """Универсальный обработчик поиска"""
    text = message.text.strip()
    user_id = message.from_user.id
    
    # Проверка блокировки
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    blocked = cursor.execute("SELECT blocked FROM users WHERE user_id = ?", (user_id,)).fetchone()
    
    if blocked and blocked[0]:
        bot.reply_to(message, "⛔️ Ваш доступ заблокирован администратором.")
        conn.close()
        return
    
    # Проверка дневного лимита
    count = cursor.execute(
        "SELECT COUNT(*) FROM requests_log WHERE user_id = ? AND timestamp > datetime('now', '-1 day')",
        (user_id,)
    ).fetchone()[0]
    
    limit = cursor.execute("SELECT daily_limit FROM users WHERE user_id = ?", (user_id,)).fetchone()
    daily_limit = limit[0] if limit else 50
    conn.close()
    
    if count >= daily_limit:
        bot.reply_to(message, f"⚠️ Достигнут дневной лимит запросов ({daily_limit})")
        return
    
    # Определение типа запроса
    query_type = detect_query_type(text)
    
    # Логирование
    log_request(user_id, query_type, text)
    
    # Поиск
    try:
        if query_type == 'phone':
            results = searcher.search_phone(text)
        elif query_type == 'email':
            results = searcher.search_email(text)
        elif query_type == 'vin':
            results = searcher.search_vehicle_vin(text)
        elif query_type == 'car_plate':
            results = searcher.search_car_plate(text)
        elif query_type == 'domain':
            results = searcher.search_domain(text)
        elif query_type == 'ip':
            results = searcher.search_ip_address(text)
        elif query_type == 'username':
            results = searcher.search_social_media(text)
        elif query_type == 'cadastral':
            results = searcher.search_cadastral(text)
        elif query_type == 'person':
            # Пробуем извлечь ФИО и дату рождения
            parts = text.split()
            name_parts = []
            birth_date = None
            
            for part in parts:
                if re.match(r'\d{2}[\.-]\d{2}[\.-]\d{4}', part):
                    birth_date = part
                else:
                    name_parts.append(part)
            
            full_name = ' '.join(name_parts)
            results = searcher.search_person(full_name, birth_date)
        else:
            # Универсальный поиск в Google
            results = {
                'google': f"https://www.google.com/search?q={quote_plus(text)}",
                'yandex': f"https://yandex.ru/search/?text={quote_plus(text)}",
            }
        
        # Обновление счетчика запросов
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE users SET requests_count = requests_count + 1 WHERE user_id = ?",
            (user_id,)
        )
        conn.commit()
        conn.close()
        
        response = format_search_results(query_type.upper(), results, user_id)
        bot.reply_to(message, response, parse_mode='HTML', disable_web_page_preview=True)
        
    except Exception as e:
        logger.error(f"Search error: {e}")
        bot.reply_to(message, f"❌ Ошибка при выполнении поиска: {str(e)}")

def detect_query_type(text):
    """Автоматическое определение типа запроса"""
    text = text.strip()
    
    # Паттерны для определения типа данных
    patterns = {
        'phone': r'^(\+?[78])?[\s\-]?\(?\d{3}\)?[\s\-]?\d{3}[\s\-]?\d{2}[\s\-]?\d{2}$',
        'email': r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$',
        'vin': r'^[A-HJ-NPR-Z0-9]{17}$',
        'car_plate': r'^[АВЕКМНОРСТУХавекмнорстух]\d{3}[АВЕКМНОРСТУХавекмнорстух]{2}\d{2,3}$',
        'domain': r'^[a-zA-Z0-9][a-zA-Z0-9-]{1,61}[a-zA-Z0-9]\.[a-zA-Z]{2,}$',
        'ip': r'^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}$',
        'username': r'^@[a-zA-Z0-9_\.]+$',
        'cadastral': r'^\d{2}:\d{2}:\d{7}:\d+$',
    }
    
    for query_type, pattern in patterns.items():
        if re.match(pattern, text, re.IGNORECASE):
            return query_type
    
    # Проверка на ФИО (минимум 2 слова, начинающихся с заглавных букв)
    words = text.split()
    if len(words) >= 2 and all(w[0].isupper() for w in words if w.isalpha()):
        # Проверяем, есть ли дата рождения
        has_date = any(re.match(r'\d{2}[\.-]\d{2}[\.-]\d{4}', w) for w in words)
        if has_date or len(words) >= 3:
            return 'person'
    
    return 'general'

def format_search_results(query_type, results, user_id):
    """Форматирование результатов поиска"""
    response = f"🔍 <b>Результаты поиска ({query_type}):</b>\n\n"
    
    if isinstance(results, dict):
        for key, value in results.items():
            if isinstance(value, dict):
                response += f"<b>{key.upper()}:</b>\n"
                for k, v in value.items():
                    response += f"  • {k}: {v}\n"
                response += "\n"
            elif isinstance(value, list):
                response += f"<b>{key.upper()}:</b>\n"
                for item in value:
                    response += f"  • {item}\n"
                response += "\n"
            else:
                response += f"<b>{key}:</b> {value}\n"
    else:
        response += str(results)
    
    response += f"\n🕐 <i>Поиск выполнен: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</i>"
    
    return response

def log_request(user_id, query_type, query_text):
    """Логирование запроса"""
    try:
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO requests_log (user_id, query_type, query_text) VALUES (?, ?, ?)",
            (user_id, query_type, query_text[:500])
        )
        conn.commit()
        conn.close()
    except Exception as e:
        logger.error(f"Failed to log request: {e}")

# ============ ОБРАБОТЧИК КОМАНДЫ /block ============
@bot.message_handler(commands=['block'])
def block_user_command(message):
    """Блокировка пользователя"""
    if message.from_user.id not in ADMIN_IDS:
        bot.reply_to(message, "⛔️ Доступ запрещен")
        return
    
    parts = message.text.split()
    if len(parts) < 2:
        bot.reply_to(message, "❌ Использование: /block [user_id] [причина]")
        return
    
    try:
        target_id = int(parts[1])
        reason = ' '.join(parts[2:]) if len(parts) > 2 else "Без причины"
        
        admin_panel.block_user(target_id, reason)
        
        bot.reply_to(message, f"✅ Пользователь {target_id} заблокирован\nПричина: {reason}")
        
        # Уведомление заблокированному
        try:
            bot.send_message(target_id, f"⛔️ Ваш доступ заблокирован администратором.\nПричина: {reason}")
        except:
            pass
        
    except ValueError:
        bot.reply_to(message, "❌ Неверный ID пользователя")

@bot.message_handler(commands=['unblock'])
def unblock_user_command(message):
    """Разблокировка пользователя"""
    if message.from_user.id not in ADMIN_IDS:
        bot.reply_to(message, "⛔️ Доступ запрещен")
        return
    
    parts = message.text.split()
    if len(parts) < 2:
        bot.reply_to(message, "❌ Использование: /unblock [user_id]")
        return
    
    try:
        target_id = int(parts[1])
        admin_panel.unblock_user(target_id)
        
        bot.reply_to(message, f"✅ Пользователь {target_id} разблокирован")
        
        try:
            bot.send_message(target_id, "✅ Ваш доступ восстановлен администратором.")
        except:
            pass
        
    except ValueError:
        bot.reply_to(message, "❌ Неверный ID пользователя")

@bot.message_handler(commands=['limit'])
def set_limit_command(message):
    """Установка лимита запросов"""
    if message.from_user.id not in ADMIN_IDS:
        bot.reply_to(message, "⛔️ Доступ запрещен")
        return
    
    parts = message.text.split()
    if len(parts) < 3:
        bot.reply_to(message, "❌ Использование: /limit [user_id] [количество]")
        return
    
    try:
        target_id = int(parts[1])
        new_limit = int(parts[2])
        
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        cursor.execute("UPDATE users SET daily_limit = ? WHERE user_id = ?", (new_limit, target_id))
        conn.commit()
        conn.close()
        
        bot.reply_to(message, f"✅ Лимит пользователя {target_id} изменен на {new_limit}")
    except ValueError:
        bot.reply_to(message, "❌ Неверные параметры")

@bot.message_handler(commands=['userinfo'])
def user_info_command(message):
    """Информация о пользователе"""
    if message.from_user.id not in ADMIN_IDS:
        bot.reply_to(message, "⛔️ Доступ запрещен")
        return
    
    parts = message.text.split()
    if len(parts) < 2:
        bot.reply_to(message, "❌ Использование: /userinfo [user_id]")
        return
    
    try:
        user_id = int(parts[1])
        info = admin_panel.get_user_info(user_id)
        
        if info:
            text = f"""
👤 <b>Информация о пользователе</b>

🆔 ID: <code>{info['id']}</code>
👤 Имя: {info['name']}
📛 Username: @{info['username'] or 'отсутствует'}
📅 Присоединился: {info['join_date']}
📊 Запросов: {info['requests']}
🔒 Статус: {'🚫 Заблокирован' if info['blocked'] else '✅ Активен'}
⭐️ Уровень доступа: {info['access_level']}
📈 Дневной лимит: {info['daily_limit']}
📝 Заметки: {info['notes'] or 'нет'}
"""
            bot.reply_to(message, text, parse_mode='HTML')
        else:
            bot.reply_to(message, "❌ Пользователь не найден")
    
    except ValueError:
        bot.reply_to(message, "❌ Неверный ID пользователя")

@bot.message_handler(commands=['export'])
def export_logs_command(message):
    """Экспорт логов"""
    if message.from_user.id not in ADMIN_IDS:
        bot.reply_to(message, "⛔️ Доступ запрещен")
        return
    
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    
    logs = cursor.execute(
        "SELECT rl.user_id, u.username, rl.query_type, rl.query_text, rl.timestamp "
        "FROM requests_log rl LEFT JOIN users u ON rl.user_id = u.user_id "
        "ORDER BY rl.timestamp DESC LIMIT 1000"
    ).fetchall()
    
    conn.close()
    
    if not logs:
        bot.reply_to(message, "❌ Нет данных для экспорта")
        return
    
    filename = f"logs_export_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
    filepath = os.path.join(TEMP_DIR, filename)
    
    with open(filepath, 'w', encoding='utf-8') as f:
        f.write("User ID,Username,Query Type,Query Text,Timestamp\n")
        for log in logs:
            f.write(f"{log[0]},{log[1]},{log[2]},\"{log[3]}\",{log[4]}\n")
    
    with open(filepath, 'rb') as f:
        bot.send_document(message.chat.id, f, caption="📋 Экспорт логов")
    
    try:
        os.remove(filepath)
    except:
        pass

# ============ ЗАПУСК БОТА ============
if __name__ == "__main__":
    print("""
    ╔══════════════════════════════════════╗
    ║     🕵️ OSINT BOT STARTING...        ║
    ║     PIDORI GROUP SUPPORT SYSTEM     ║
    ╚══════════════════════════════════════╝
    """)
    
    init_database()
    logger.info("Bot starting...")
    
    while True:
        try:
            bot.polling(none_stop=True, interval=0, timeout=60)
        except Exception as e:
            logger.error(f"Bot polling error: {e}")
            time.sleep(15)