#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
OSINT Telegram Bot с реальным парсингом данных.
Парсит публичные источники и возвращает найденную информацию.
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
from phonenumbers import geocoder, carrier, timezone
import whois
import dns.resolver
import socket
import ssl
import logging
from urllib.parse import urlparse, quote_plus, unquote
import random

# ============ КОНФИГУРАЦИЯ ============
BOT_TOKEN = "8796975931:AAFpT2nZUXWyqohYmdAwlK3C54B9klJkjK0"
ADMIN_IDS = [8563327706]
DB_NAME = "osint_bot.db"
TEMP_DIR = "temp_files"
LOG_FILE = "bot_operations.log"

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[logging.FileHandler(LOG_FILE, encoding='utf-8'), logging.StreamHandler()]
)
logger = logging.getLogger(__name__)

bot = telebot.TeleBot(BOT_TOKEN, parse_mode='HTML')
ua = UserAgent()

if not os.path.exists(TEMP_DIR):
    os.makedirs(TEMP_DIR)

# ============ БАЗА ДАННЫХ ============
def init_database():
    conn = sqlite3.connect(DB_NAME, check_same_thread=False)
    cursor = conn.cursor()
    
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
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS requests_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            query_type TEXT,
            query_text TEXT,
            timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            result_summary TEXT,
            FOREIGN KEY (user_id) REFERENCES users(user_id)
        )
    ''')
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS search_cache (
            query_hash TEXT PRIMARY KEY,
            query_type TEXT,
            query_text TEXT,
            result_data TEXT,
            cached_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    conn.commit()
    conn.close()

# ============ ОСНОВНОЙ КЛАСС ПОИСКА ============
class OSINTSearcher:
    def __init__(self):
        self.session = requests.Session()
        self.cache = {}
    
    def _get_headers(self):
        return {
            'User-Agent': ua.random,
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'Accept-Language': 'ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7',
        }
    
    def _safe_request(self, url, timeout=10, retries=2):
        for attempt in range(retries):
            try:
                resp = self.session.get(url, headers=self._get_headers(), timeout=timeout)
                return resp
            except Exception as e:
                if attempt == retries - 1:
                    return None
                time.sleep(1)
        return None
    
    def search_phone(self, phone):
        """Реальный поиск по номеру телефона"""
        results = {}
        
        clean_phone = re.sub(r'[^\d]', '', phone)
        if len(clean_phone) == 11 and clean_phone.startswith('8'):
            clean_phone = '7' + clean_phone[1:]
        if len(clean_phone) == 10 and clean_phone.startswith('9'):
            clean_phone = '7' + clean_phone
        
        try:
            parsed = phonenumbers.parse('+' + clean_phone, 'RU')
            results['Страна'] = geocoder.description_for_number(parsed, 'ru')
            results['Регион'] = geocoder.description_for_number(parsed, 'ru')
            results['Оператор'] = carrier.name_for_number(parsed, 'ru')
            results['Часовой пояс'] = ', '.join(timezone.time_zones_for_number(parsed))
            results['Тип номера'] = 'Мобильный' if phonenumbers.number_type(parsed) == 1 else 'Стационарный'
            results['Валидный'] = 'Да' if phonenumbers.is_valid_number(parsed) else 'Нет'
        except:
            results['Ошибка'] = 'Не удалось определить'
        
        # Поиск в телефонных справочниках
        code = clean_phone[1:4] if len(clean_phone) > 3 else ''
        number = clean_phone[4:] if len(clean_phone) > 4 else clean_phone
        
        # Парсинг who-calling.ru
        try:
            resp = self._safe_request(f'https://who-calling.ru/nomer/{clean_phone}')
            if resp and resp.status_code == 200:
                soup = BeautifulSoup(resp.text, 'html.parser')
                comments = soup.find_all('div', class_='comment-text')
                if comments:
                    results['Отзывы (who-calling)'] = [c.text.strip()[:200] for c in comments[:5]]
                category = soup.find('span', class_='label-category')
                if category:
                    results['Категория номера'] = category.text.strip()
        except:
            pass
        
        # Поиск в Google (первые результаты)
        try:
            resp = self._safe_request(f'https://www.google.com/search?q={quote_plus("+" + clean_phone)}&hl=ru')
            if resp and resp.status_code == 200:
                soup = BeautifulSoup(resp.text, 'html.parser')
                snippets = soup.find_all('div', class_='VwiC3b')
                if snippets:
                    results['Упоминания в Google'] = [s.text[:150] for s in snippets[:5]]
        except:
            pass
        
        results['Telegram'] = f'https://t.me/+{clean_phone}'
        results['WhatsApp'] = f'https://wa.me/{clean_phone}'
        
        return results
    
    def search_email(self, email):
        """Поиск по email"""
        results = {}
        
        if not re.match(r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$', email):
            return {'Ошибка': 'Некорректный email'}
        
        domain = email.split('@')[1]
        username = email.split('@')[0]
        
        # WHOIS домена
        try:
            w = whois.whois(domain)
            results['Домен зарегистрирован'] = str(w.creation_date)
            results['Регистратор'] = w.registrar or 'Не указан'
            results['Страна'] = w.country or 'Не указана'
        except:
            results['WHOIS'] = 'Не удалось получить'
        
        # Проверка утечек через публичное API
        try:
            resp = self._safe_request(f'https://haveibeenpwned.com/api/v3/breachedaccount/{email}?truncateResponse=true')
            if resp and resp.status_code == 200:
                breaches = resp.json()
                results['Найден в утечках'] = [b['Name'] for b in breaches[:10]]
            elif resp and resp.status_code == 404:
                results['Утечки'] = 'Не найден в известных утечках'
        except:
            results['Утечки'] = 'Не удалось проверить'
        
        # Gravatar
        hash_md5 = hashlib.md5(email.lower().encode()).hexdigest()
        results['Gravatar'] = f'https://www.gravatar.com/{hash_md5}'
        
        # Поиск в соцсетях
        results['Facebook'] = f'https://www.facebook.com/search/people/?q={quote_plus(email)}'
        results['LinkedIn'] = f'https://www.linkedin.com/search/results/people/?keywords={quote_plus(email)}'
        results['VK'] = f'https://vk.com/search?c[email]={email}'
        
        # Проверка домена email
        try:
            mx_records = dns.resolver.resolve(domain, 'MX')
            results['MX записи'] = [str(mx) for mx in mx_records]
        except:
            results['MX записи'] = 'Не найдены'
        
        return results
    
    def search_vin(self, vin):
        """Поиск по VIN номеру"""
        results = {}
        clean_vin = vin.upper().strip()
        
        if not re.match(r'^[A-HJ-NPR-Z0-9]{17}$', clean_vin):
            return {'Ошибка': 'Некорректный VIN'}
        
        # Расшифровка VIN
        wmi = clean_vin[:3]
        vds = clean_vin[3:9]
        vis = clean_vin[9:17]
        
        # WMI справочник
        wmi_dict = {
            'XTA': 'LADA (АвтоВАЗ)',
            'XTB': 'АЗЛК/Москвич',
            'XTC': 'КамАЗ',
            'XTD': 'ЗАЗ',
            'XTE': 'ГАЗ',
            'XTH': 'АЗЛК',
            'XTT': 'УАЗ',
            'XTY': 'ИжМаш',
            'ZAA': 'Alfa Romeo',
            'ZFF': 'Ferrari',
            'ZFA': 'Fiat',
            'VF1': 'Renault',
            'VF3': 'Peugeot',
            'VF7': 'Citroen',
            'WBA': 'BMW',
            'WBS': 'BMW M',
            'WDB': 'Mercedes-Benz',
            'WDD': 'Mercedes-Benz',
            'WAU': 'Audi',
            'WVW': 'Volkswagen',
            'WVG': 'Volkswagen',
            '1HG': 'Honda USA',
            'JHM': 'Honda Japan',
            '1FT': 'Ford Truck',
            '2FM': 'Ford Canada',
            'JM1': 'Mazda',
            'JN1': 'Nissan',
            'JT2': 'Toyota',
            'JS1': 'Suzuki',
            'KMH': 'Hyundai',
            'KNA': 'Kia',
        }
        
        results['Производитель (WMI)'] = wmi_dict.get(wmi, f'Неизвестный код: {wmi}')
        results['Модельный год'] = vis[0] if len(vis) > 0 else 'Не определен'
        results['Завод-изготовитель'] = vis[1] if len(vis) > 1 else 'Не определен'
        results['Серийный номер'] = vis[2:] if len(vis) > 2 else 'Не определен'
        
        # Год выпуска по VIN (10-й символ)
        year_codes = {
            'A': 2010, 'B': 2011, 'C': 2012, 'D': 2013, 'E': 2014,
            'F': 2015, 'G': 2016, 'H': 2017, 'J': 2018, 'K': 2019,
            'L': 2020, 'M': 2021, 'N': 2022, 'P': 2023, 'R': 2024,
            'S': 2025, 'T': 2026, 'V': 2027, 'W': 2028, 'X': 2029
        }
        model_year_code = clean_vin[9] if len(clean_vin) > 9 else None
        if model_year_code and model_year_code in year_codes:
            results['Год выпуска'] = year_codes[model_year_code]
        
        results['Проверка ГИБДД'] = f'https://xn--90adear.xn--p1ai/check/auto?vin={clean_vin}'
        results['Автотека'] = f'https://autoteka.ru/vin/{clean_vin}'
        
        return results
    
    def search_car_plate(self, plate):
        """Поиск по госномеру"""
        results = {}
        clean_plate = re.sub(r'[^\w]', '', plate.upper())
        
        # Определение региона
        region_match = re.search(r'(\d{2,3})$', clean_plate)
        if region_match:
            region_code = region_match.group(1)
            regions = {
                '77': 'Москва', '78': 'Санкт-Петербург', '50': 'Московская область',
                '47': 'Ленинградская область', '23': 'Краснодарский край',
                '16': 'Республика Татарстан', '66': 'Свердловская область',
                '54': 'Новосибирская область', '61': 'Ростовская область',
                '59': 'Пермский край', '74': 'Челябинская область',
                '52': 'Нижегородская область', '63': 'Самарская область',
                '55': 'Омская область', '34': 'Волгоградская область',
                '02': 'Башкортостан', '116': 'Татарстан (новый)',
                '799': 'Москва (новый)', '797': 'Москва (новый)',
            }
            results['Регион регистрации'] = regions.get(str(region_code), f'Код: {region_code}')
        
        results['Проверка ГИБДД'] = f'https://xn--90adear.xn--p1ai/check/auto?regnum={clean_plate}'
        results['Штрафы ГИБДД'] = f'https://гибдд.рф/check/fines?regnum={clean_plate}'
        results['Автокод'] = f'https://avtokod.mos.ru/Login?returnUrl=%2f'
        
        return results
    
    def search_social_media(self, username):
        """Поиск профилей в соцсетях"""
        username = username.replace('@', '').strip()
        results = {}
        
        sites = {
            'Instagram': {
                'url': f'https://www.instagram.com/{username}/',
                'check': True
            },
            'Twitter/X': {
                'url': f'https://twitter.com/{username}',
                'check': True
            },
            'GitHub': {
                'url': f'https://github.com/{username}',
                'check': True
            },
            'Reddit': {
                'url': f'https://www.reddit.com/user/{username}',
                'check': True
            },
            'TikTok': {
                'url': f'https://www.tiktok.com/@{username}',
                'check': True
            },
            'VK': {
                'url': f'https://vk.com/{username}',
                'check': True
            },
            'Telegram': {
                'url': f'https://t.me/{username}',
                'check': True
            },
            'YouTube': {
                'url': f'https://www.youtube.com/@{username}',
                'check': True
            },
            'Twitch': {
                'url': f'https://www.twitch.tv/{username}',
                'check': True
            },
            'Pinterest': {
                'url': f'https://www.pinterest.com/{username}/',
                'check': True
            },
            'Steam': {
                'url': f'https://steamcommunity.com/id/{username}',
                'check': True
            },
            'Spotify': {
                'url': f'https://open.spotify.com/user/{username}',
                'check': False
            },
        }
        
        for platform, data in sites.items():
            if data['check']:
                try:
                    resp = self._safe_request(data['url'], timeout=5)
                    if resp:
                        if resp.status_code == 200:
                            results[platform] = f'✅ Найден: {data["url"]}'
                        elif resp.status_code == 404:
                            results[platform] = '❌ Не найден'
                        else:
                            results[platform] = f'⚠️ Статус: {resp.status_code}'
                except:
                    results[platform] = '⚠️ Ошибка проверки'
            else:
                results[platform] = f'🔗 {data["url"]}'
        
        return results
    
    def search_domain(self, domain):
        """Поиск информации о домене"""
        results = {}
        domain = domain.strip().lower()
        
        # WHOIS
        try:
            w = whois.whois(domain)
            results['Регистратор'] = w.registrar or 'Не указан'
            results['Дата создания'] = str(w.creation_date) if w.creation_date else 'Не указана'
            results['Дата окончания'] = str(w.expiration_date) if w.expiration_date else 'Не указана'
            results['Владелец'] = w.org or 'Скрыт'
            results['Страна'] = w.country or 'Не указана'
            results['NS серверы'] = ', '.join(w.name_servers) if w.name_servers else 'Не указаны'
        except Exception as e:
            results['WHOIS'] = f'Ошибка: {str(e)[:100]}'
        
        # DNS записи
        record_types = ['A', 'AAAA', 'MX', 'NS', 'TXT', 'SOA']
        for rtype in record_types:
            try:
                answers = dns.resolver.resolve(domain, rtype)
                records = [str(a) for a in answers]
                results[f'DNS {rtype}'] = ', '.join(records[:5])
            except:
                pass
        
        # IP адрес
        try:
            ip = socket.gethostbyname(domain)
            results['IP адрес'] = ip
            
            # Геолокация IP
            try:
                geo_resp = self._safe_request(f'http://ip-api.com/json/{ip}')
                if geo_resp and geo_resp.status_code == 200:
                    geo_data = geo_resp.json()
                    results['Страна IP'] = geo_data.get('country', '?')
                    results['Город'] = geo_data.get('city', '?')
                    results['Провайдер'] = geo_data.get('isp', '?')
            except:
                pass
        except:
            results['IP адрес'] = 'Не удалось определить'
        
        # SSL сертификат
        try:
            cert = ssl.get_server_certificate((domain, 443))
            import OpenSSL
            x509 = OpenSSL.crypto.load_certificate(OpenSSL.crypto.FILETYPE_PEM, cert)
            results['SSL выдан'] = x509.get_issuer().CN
            results['SSL для'] = x509.get_subject().CN
            results['SSL действителен до'] = x509.get_notAfter().decode()[:8]
        except:
            results['SSL'] = 'Не удалось проверить'
        
        # Заголовки сервера
        try:
            resp = self._safe_request(f'https://{domain}', timeout=5)
            if resp:
                server = resp.headers.get('Server', '')
                if server:
                    results['Сервер'] = server
                powered = resp.headers.get('X-Powered-By', '')
                if powered:
                    results['Технология'] = powered
        except:
            pass
        
        return results
    
    def search_ip(self, ip):
        """Поиск информации об IP"""
        results = {}
        
        # Геолокация
        try:
            geo_resp = self._safe_request(f'http://ip-api.com/json/{ip}')
            if geo_resp and geo_resp.status_code == 200:
                geo = geo_resp.json()
                results['Страна'] = geo.get('country', '?')
                results['Город'] = geo.get('city', '?')
                results['Регион'] = geo.get('regionName', '?')
                results['Провайдер'] = geo.get('isp', '?')
                results['Организация'] = geo.get('org', '?')
                results['Координаты'] = f"{geo.get('lat', '?')}, {geo.get('lon', '?')}"
                results['Часовой пояс'] = geo.get('timezone', '?')
        except:
            pass
        
        # Reverse DNS
        try:
            rdns = socket.gethostbyaddr(ip)
            results['RDNS'] = rdns[0]
        except:
            results['RDNS'] = 'Не определен'
        
        # Проверка черных списков
        try:
            reversed_ip = '.'.join(reversed(ip.split('.')))
            bl_resp = self._safe_request(f'https://{reversed_ip}.zen.spamhaus.org', timeout=3)
            if bl_resp:
                results['Spamhaus'] = '🚫 В черном списке!'
            else:
                results['Spamhaus'] = '✅ Чистый'
        except:
            pass
        
        return results
    
    def search_person(self, full_name, birth_date=None):
        """Поиск информации о человеке"""
        results = {}
        
        parts = full_name.split()
        surname = parts[0] if parts else ''
        name = parts[1] if len(parts) > 1 else ''
        
        results['Поисковый запрос'] = full_name
        
        # Поиск в Google
        query = f'"{full_name}"'
        if birth_date:
            query += f' "{birth_date}"'
        
        try:
            resp = self._safe_request(f'https://www.google.com/search?q={quote_plus(query)}&hl=ru')
            if resp and resp.status_code == 200:
                soup = BeautifulSoup(resp.text, 'html.parser')
                snippets = soup.find_all('div', class_='VwiC3b')
                if snippets:
                    results['Упоминания в Google'] = [s.text[:200] for s in snippets[:5]]
        except:
            pass
        
        # Ссылки на соцсети
        if surname and name:
            results['VK поиск'] = f'https://vk.com/search?c[name]=1&c[q]={quote_plus(full_name)}'
            results['OK поиск'] = f'https://ok.ru/dk?st.cmd=searchResult&st.query={quote_plus(full_name)}'
            results['Facebook поиск'] = f'https://www.facebook.com/search/people/?q={quote_plus(full_name)}'
        
        # Судебные дела
        results['Судебные дела'] = f'https://sudact.ru/regular/?q={quote_plus(full_name)}'
        
        # Исполнительные производства
        results['ФССП'] = f'https://fssp.gov.ru/iss/ip/search?query={quote_plus(full_name)}'
        
        # ЕГРЮЛ/ЕГРИП
        results['Налоговая'] = f'https://egrul.nalog.ru/index.html?q={quote_plus(surname)}+{quote_plus(name) if name else ""}'
        
        return results
    
    def search_document(self, doc_type, doc_number):
        """Поиск по документам"""
        results = {}
        clean_number = re.sub(r'[^\d]', '', doc_number)
        
        if doc_type == 'passport':
            results['Тип документа'] = 'Паспорт РФ'
            results['Серия'] = clean_number[:4] if len(clean_number) >= 4 else clean_number
            results['Номер'] = clean_number[4:] if len(clean_number) > 4 else ''
            results['Проверка ФМС'] = f'https://services.fms.gov.ru/info-service.htm?sid=2000&number={clean_number}'
        elif doc_type == 'snils':
            results['Тип документа'] = 'СНИЛС'
            results['Номер'] = f'{clean_number[:3]}-{clean_number[3:6]}-{clean_number[6:9]} {clean_number[9:11]}' if len(clean_number) >= 11 else clean_number
            results['ПФР'] = 'https://www.pfr.gov.ru/order/request/'
        elif doc_type == 'inn':
            results['Тип документа'] = 'ИНН'
            results['Номер'] = clean_number
            if len(clean_number) == 12:
                results['Тип'] = 'ИНН физического лица'
            elif len(clean_number) == 10:
                results['Тип'] = 'ИНН юридического лица'
            results['Проверка ФНС'] = f'https://egrul.nalog.ru/index.html?q={clean_number}'
        elif doc_type == 'driver_license':
            results['Тип документа'] = 'Водительское удостоверение'
            results['Номер'] = clean_number
            results['Проверка ГИБДД'] = f'https://гибдд.рф/check/driver#license_number={clean_number}'
        
        return results
    
    def search_company(self, inn):
        """Поиск компании по ИНН"""
        results = {}
        clean_inn = re.sub(r'[^\d]', '', inn)
        
        results['ИНН'] = clean_inn
        
        # Парсинг rusprofile.ru
        try:
            resp = self._safe_request(f'https://www.rusprofile.ru/search?query={clean_inn}')
            if resp and resp.status_code == 200:
                soup = BeautifulSoup(resp.text, 'html.parser')
                # Название компании
                title = soup.find('h1', class_='company-name')
                if title:
                    results['Название'] = title.text.strip()
                
                # Директор
                director = soup.find('div', class_='company-director')
                if director:
                    results['Руководитель'] = director.text.strip().replace('Руководитель', '').strip()
                
                # Адрес
                address = soup.find('div', class_='company-address')
                if address:
                    results['Адрес'] = address.text.strip()
                
                # Статус
                status = soup.find('div', class_='company-status')
                if status:
                    results['Статус'] = status.text.strip()
        except:
            pass
        
        # Альтернативные источники
        results['СБИС'] = f'https://sbis.ru/contragents/{clean_inn}'
        results['List-Org'] = f'https://www.list-org.com/search?val={clean_inn}'
        results['ЕГРЮЛ'] = f'https://egrul.nalog.ru/index.html?q={clean_inn}'
        
        return results
    
    def search_cadastral(self, cad_number):
        """Поиск по кадастровому номеру"""
        results = {}
        clean_number = re.sub(r'[^\d:.]', '', cad_number)
        
        parts = clean_number.split(':')
        if len(parts) >= 2:
            results['Кадастровый округ'] = parts[0]
            results['Кадастровый район'] = parts[1] if len(parts) > 1 else '?'
            results['Кадастровый квартал'] = parts[2] if len(parts) > 2 else '?'
            results['Номер участка'] = parts[3] if len(parts) > 3 else '?'
        
        results['Публичная кадастровая карта'] = f'https://pkk.rosreestr.ru/#/search/{clean_number}'
        results['ЕГРП 365'] = f'https://egrp365.ru/map/?kad={clean_number}'
        
        return results

# ============ ФУНКЦИИ БОТА ============
searcher = OSINTSearcher()

def log_request(user_id, query_type, query_text, result_summary=""):
    try:
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO requests_log (user_id, query_type, query_text, result_summary) VALUES (?, ?, ?, ?)",
            (user_id, query_type, query_text[:500], result_summary[:500])
        )
        cursor.execute(
            "UPDATE users SET requests_count = requests_count + 1 WHERE user_id = ?",
            (user_id,)
        )
        conn.commit()
        conn.close()
    except Exception as e:
        logger.error(f"Log error: {e}")

def format_results(results):
    """Форматирование результатов для Telegram"""
    text = ""
    for key, value in results.items():
        if isinstance(value, list):
            text += f"\n<b>{key}:</b>\n"
            for item in value[:5]:
                text += f"  • {item}\n"
        elif isinstance(value, str) and value.startswith('http'):
            text += f"🔗 <a href='{value}'>{key}</a>\n"
        else:
            text += f"<b>{key}:</b> {value}\n"
    return text

def detect_query_type(text):
    """Определение типа запроса"""
    text = text.strip()
    
    patterns = [
        ('phone', r'^(\+?[78])?[\s\-]?\(?\d{3}\)?[\s\-]?\d{3}[\s\-]?\d{2}[\s\-]?\d{2}$'),
        ('email', r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'),
        ('vin', r'^[A-HJ-NPR-Z0-9]{17}$'),
        ('car_plate', r'^[АВЕКМНОРСТУХ]\d{3}[АВЕКМНОРСТУХ]{2}\d{2,3}$'),
        ('domain', r'^[a-zA-Z0-9][a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'),
        ('ip', r'^(\d{1,3}\.){3}\d{1,3}$'),
        ('cadastral', r'^\d{2}:\d{2}:\d{7}:\d+$'),
        ('username', r'^@[a-zA-Z0-9_\.]+$'),
    ]
    
    for qtype, pattern in patterns:
        if re.match(pattern, text, re.IGNORECASE):
            return qtype
    
    words = text.split()
    if len(words) >= 2 and all(w[0].isupper() for w in words if w.isalpha()):
        has_date = any(re.match(r'\d{2}[\.-]\d{2}[\.-]\d{4}', w) for w in words)
        if has_date or len(words) >= 3:
            return 'person'
    
    return 'general'

@bot.message_handler(commands=['start'])
def start_command(message):
    user_id = message.from_user.id
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute(
        "INSERT OR IGNORE INTO users (user_id, username, first_name, last_name) VALUES (?, ?, ?, ?)",
        (user_id, message.from_user.username, message.from_user.first_name, message.from_user.last_name)
    )
    
    blocked = cursor.execute("SELECT blocked FROM users WHERE user_id = ?", (user_id,)).fetchone()
    conn.commit()
    conn.close()
    
    if blocked and blocked[0]:
        bot.reply_to(message, "⛔️ Доступ заблокирован.")
        return
    
    welcome = """
🕵️ <b>OSINT Search Bot</b> — поиск информации в открытых источниках.

<b>Что можно искать:</b>

👤 <b>Человек:</b> <code>Фамилия Имя Отчество ДД.ММ.ГГГГ</code>

📱 <b>Телефон:</b> <code>79991234567</code>
📧 <b>Email:</b> <code>user@example.com</code>

🚗 <b>Авто:</b>
• VIN: <code>XTA211440C5106924</code>
• Номер: <code>А123ВС199</code>

🌐 <b>Интернет:</b>
• Домен: <code>example.com</code>
• IP: <code>1.1.1.1</code>
• Профиль: <code>@username</code>

📄 <b>Документы:</b>
• Паспорт: <code>/passport 1234567890</code>
• СНИЛС: <code>/snils 12345678901</code>
• ИНН: <code>/inn 123456789012</code>
• Права: <code>/vu 1234567890</code>

🏢 <b>Компания:</b> <code>/company 7707083893</code>
🏠 <b>Кадастр:</b> <code>77:01:0004042:6987</code>

/admin — панель администратора
"""
    bot.reply_to(message, welcome)

@bot.message_handler(commands=['admin'])
def admin_command(message):
    if message.from_user.id not in ADMIN_IDS:
        return
    
    markup = types.InlineKeyboardMarkup(row_width=2)
    markup.add(
        types.InlineKeyboardButton("📊 Статистика", callback_data="admin_stats"),
        types.InlineKeyboardButton("👥 Пользователи", callback_data="admin_users"),
        types.InlineKeyboardButton("📋 Логи", callback_data="admin_logs"),
        types.InlineKeyboardButton("🚫 Блокировки", callback_data="admin_blocks"),
    )
    bot.send_message(message.chat.id, "🔐 <b>Админ-панель</b>", reply_markup=markup)

@bot.callback_query_handler(func=lambda call: call.data.startswith('admin_'))
def admin_callback(call):
    if call.from_user.id not in ADMIN_IDS:
        return
    
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    
    if call.data == "admin_stats":
        total_users = cursor.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        total_requests = cursor.execute("SELECT COUNT(*) FROM requests_log").fetchone()[0]
        blocked = cursor.execute("SELECT COUNT(*) FROM users WHERE blocked = 1").fetchone()[0]
        active_24h = cursor.execute(
            "SELECT COUNT(DISTINCT user_id) FROM requests_log WHERE timestamp > datetime('now', '-1 day')"
        ).fetchone()[0]
        
        stats = f"""
📊 <b>Статистика:</b>
👥 Пользователей: {total_users}
🟢 Активных за 24ч: {active_24h}
📈 Всего запросов: {total_requests}
🚫 Заблокировано: {blocked}
"""
        bot.edit_message_text(stats, call.message.chat.id, call.message.message_id, reply_markup=call.message.reply_markup, parse_mode='HTML')
    
    elif call.data == "admin_users":
        users = cursor.execute("SELECT user_id, username, first_name, requests_count, blocked FROM users ORDER BY requests_count DESC LIMIT 20").fetchall()
        text = "👥 <b>Топ пользователей:</b>\n\n"
        for u in users:
            status = "🚫" if u[4] else "✅"
            text += f"{status} <code>{u[0]}</code> {u[2] or '?'} (@{u[1] or 'нет'}) — {u[3]} запросов\n"
        bot.edit_message_text(text, call.message.chat.id, call.message.message_id, reply_markup=call.message.reply_markup, parse_mode='HTML')
    
    elif call.data == "admin_logs":
        logs = cursor.execute(
            "SELECT user_id, query_type, query_text, timestamp FROM requests_log ORDER BY timestamp DESC LIMIT 15"
        ).fetchall()
        text = "📋 <b>Последние запросы:</b>\n\n"
        for log in logs:
            text += f"🕐 {log[3][:16]} | 👤 {log[0]} | {log[1]}: {log[2][:80]}\n"
        bot.edit_message_text(text, call.message.chat.id, call.message.message_id, reply_markup=call.message.reply_markup, parse_mode='HTML')
    
    elif call.data == "admin_blocks":
        blocked_users = cursor.execute("SELECT user_id, username, notes FROM users WHERE blocked = 1").fetchall()
        if blocked_users:
            text = "🚫 <b>Заблокированные:</b>\n\n"
            for u in blocked_users:
                text += f"<code>{u[0]}</code> @{u[1] or 'нет'} — {u[2] or 'без причины'}\n"
        else:
            text = "Нет заблокированных пользователей"
        bot.edit_message_text(text, call.message.chat.id, call.message.message_id, reply_markup=call.message.reply_markup, parse_mode='HTML')
    
    conn.close()

@bot.message_handler(commands=['passport', 'snils', 'inn', 'vu'])
def doc_commands(message):
    cmd = message.text.split()
    if len(cmd) < 2:
        bot.reply_to(message, "❌ Укажите номер документа")
        return
    
    doc_map = {'/passport': 'passport', '/snils': 'snils', '/inn': 'inn', '/vu': 'driver_license'}
    doc_type = doc_map.get(cmd[0])
    doc_num = cmd[1]
    
    log_request(message.from_user.id, doc_type, doc_num)
    
    bot.send_chat_action(message.chat.id, 'typing')
    results = searcher.search_document(doc_type, doc_num)
    response = f"📄 <b>Результаты поиска ({doc_type.upper()}):</b>\n{format_results(results)}"
    bot.reply_to(message, response, disable_web_page_preview=True)

@bot.message_handler(commands=['company'])
def company_command(message):
    inn = message.text.replace('/company', '').strip()
    if not inn:
        bot.reply_to(message, "❌ Укажите ИНН компании")
        return
    
    log_request(message.from_user.id, 'company', inn)
    
    bot.send_chat_action(message.chat.id, 'typing')
    results = searcher.search_company(inn)
    response = f"🏢 <b>Результаты поиска компании:</b>\n{format_results(results)}"
    bot.reply_to(message, response, disable_web_page_preview=True)

@bot.message_handler(content_types=['photo'])
def photo_handler(message):
    bot.reply_to(message, """
🔍 <b>Поиск по фото:</b>

Загрузите фото на эти сервисы:

📌 <a href='https://pimeyes.com'>PimEyes</a> — поиск лиц
📌 <a href='https://tineye.com'>TinEye</a> — обратный поиск изображений
📌 <a href='https://yandex.ru/images/search'>Яндекс.Картинки</a>
📌 <a href='https://images.google.com'>Google Images</a>
""", disable_web_page_preview=True)

@bot.message_handler(func=lambda m: True)
def universal_handler(message):
    text = message.text.strip()
    user_id = message.from_user.id
    
    # Проверка блокировки
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    blocked = cursor.execute("SELECT blocked FROM users WHERE user_id = ?", (user_id,)).fetchone()
    
    if blocked and blocked[0]:
        bot.reply_to(message, "⛔️ Доступ заблокирован")
        conn.close()
        return
    
    # Проверка лимита
    count = cursor.execute(
        "SELECT COUNT(*) FROM requests_log WHERE user_id = ? AND timestamp > datetime('now', '-1 day')",
        (user_id,)
    ).fetchone()[0]
    
    limit = cursor.execute("SELECT daily_limit FROM users WHERE user_id = ?", (user_id,)).fetchone()
    daily_limit = limit[0] if limit else 50
    conn.close()
    
    if count >= daily_limit:
        bot.reply_to(message, f"⚠️ Дневной лимит ({daily_limit}) исчерпан")
        return
    
    query_type = detect_query_type(text)
    
    bot.send_chat_action(message.chat.id, 'typing')
    
    search_map = {
        'phone': searcher.search_phone,
        'email': searcher.search_email,
        'vin': searcher.search_vin,
        'car_plate': searcher.search_car_plate,
        'domain': searcher.search_domain,
        'ip': searcher.search_ip,
        'username': searcher.search_social_media,
        'cadastral': searcher.search_cadastral,
    }
    
    if query_type == 'person':
        parts = text.split()
        name_parts = []
        birth_date = None
        for part in parts:
            if re.match(r'\d{2}[\.-]\d{2}[\.-]\d{4}', part):
                birth_date = part
            else:
                name_parts.append(part)
        results = searcher.search_person(' '.join(name_parts), birth_date)
    elif query_type in search_map:
        results = search_map[query_type](text)
    else:
        results = {
            'Google': f'https://www.google.com/search?q={quote_plus(text)}',
            'Яндекс': f'https://yandex.ru/search/?text={quote_plus(text)}',
        }
    
    log_request(user_id, query_type, text, str(results)[:500])
    
    type_names = {
        'phone': '📱 ТЕЛЕФОН', 'email': '📧 EMAIL', 'vin': '🚗 VIN',
        'car_plate': '🚘 ГОСНОМЕР', 'domain': '🌐 ДОМЕН', 'ip': '🔢 IP АДРЕС',
        'username': '👤 СОЦСЕТИ', 'cadastral': '🏠 КАДАСТР', 'person': '👤 ЧЕЛОВЕК',
        'general': '🔍 ПОИСК'
    }
    
    response = f"<b>Результаты поиска ({type_names.get(query_type, query_type.upper())}):</b>\n{format_results(results)}"
    response += f"\n<i>🕐 {datetime.now().strftime('%H:%M:%S')}</i>"
    
    bot.reply_to(message, response, disable_web_page_preview=True)

# ============ АДМИН КОМАНДЫ ============
@bot.message_handler(commands=['block'])
def block_user(message):
    if message.from_user.id not in ADMIN_IDS:
        return
    parts = message.text.split()
    if len(parts) < 2:
        bot.reply_to(message, "❌ /block [user_id]")
        return
    try:
        uid = int(parts[1])
        conn = sqlite3.connect(DB_NAME)
        conn.execute("UPDATE users SET blocked = 1 WHERE user_id = ?", (uid,))
        conn.commit()
        conn.close()
        bot.reply_to(message, f"✅ Пользователь {uid} заблокирован")
    except:
        bot.reply_to(message, "❌ Ошибка")

@bot.message_handler(commands=['unblock'])
def unblock_user(message):
    if message.from_user.id not in ADMIN_IDS:
        return
    parts = message.text.split()
    if len(parts) < 2:
        bot.reply_to(message, "❌ /unblock [user_id]")
        return
    try:
        uid = int(parts[1])
        conn = sqlite3.connect(DB_NAME)
        conn.execute("UPDATE users SET blocked = 0 WHERE user_id = ?", (uid,))
        conn.commit()
        conn.close()
        bot.reply_to(message, f"✅ Пользователь {uid} разблокирован")
    except:
        bot.reply_to(message, "❌ Ошибка")

@bot.message_handler(commands=['limit'])
def set_limit(message):
    if message.from_user.id not in ADMIN_IDS:
        return
    parts = message.text.split()
    if len(parts) < 3:
        bot.reply_to(message, "❌ /limit [user_id] [число]")
        return
    try:
        uid = int(parts[1])
        lim = int(parts[2])
        conn = sqlite3.connect(DB_NAME)
        conn.execute("UPDATE users SET daily_limit = ? WHERE user_id = ?", (lim, uid))
        conn.commit()
        conn.close()
        bot.reply_to(message, f"✅ Лимит пользователя {uid} = {lim}")
    except:
        bot.reply_to(message, "❌ Ошибка")

# ============ ЗАПУСК ============
if __name__ == "__main__":
    print("🕵️ OSINT BOT STARTING...")
    init_database()
    
    while True:
        try:
            bot.polling(none_stop=True, interval=0, timeout=60)
        except Exception as e:
            logger.error(f"Polling error: {e}")
            time.sleep(15)
