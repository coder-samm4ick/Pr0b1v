# app.py - Основной файл ебучего сайта
#!/usr/bin/env python3
"""
PIDORI OSINT WEB - Поиск по слитым базам
Веб-интерфейс для пробива по всем критериям
"""

from flask import Flask, render_template, request, jsonify, session, redirect, url_for, send_file
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from functools import wraps
import sqlite3
import os
import re
import json
import csv
import hashlib
import time
from datetime import datetime, timedelta
from fuzzywuzzy import fuzz
import phonenumbers
from phonenumbers import geocoder, carrier
import requests
from bs4 import BeautifulSoup
from fake_useragent import UserAgent
import threading
import queue
import pandas as pd

# ============ КОНФИГУРАЦИЯ ============
app = Flask(__name__)
app.secret_key = 'PIDORI_OSINT_SECRET_KEY_MOTHERFUCKER'
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///osint_users.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)
login_manager = LoginManager(app)
login_manager.login_view = 'login'

ua = UserAgent()
DATA_DIR = 'leaked_databases'
os.makedirs(DATA_DIR, exist_ok=True)

# ============ МОДЕЛИ БД ============
class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password = db.Column(db.String(200), nullable=False)
    is_admin = db.Column(db.Boolean, default=False)
    is_blocked = db.Column(db.Boolean, default=False)
    requests_today = db.Column(db.Integer, default=0)
    total_requests = db.Column(db.Integer, default=0)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    last_request = db.Column(db.DateTime)

class SearchLog(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    query_type = db.Column(db.String(50))
    query_text = db.Column(db.String(500))
    result_count = db.Column(db.Integer)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)
    ip_address = db.Column(db.String(50))

# ============ ИНИЦИАЛИЗАЦИЯ ============
@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not current_user.is_authenticated or not current_user.is_admin:
            return jsonify({'error': 'Доступ запрещен'}), 403
        return f(*args, **kwargs)
    return decorated_function

def check_rate_limit(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if current_user.is_authenticated:
            today = datetime.utcnow().date()
            if current_user.last_request and current_user.last_request.date() != today:
                current_user.requests_today = 0
            
            limit = 100 if current_user.is_admin else 50
            if current_user.requests_today >= limit:
                return jsonify({'error': f'Лимит запросов ({limit}) исчерпан'}), 429
            
            current_user.requests_today += 1
            current_user.total_requests += 1
            current_user.last_request = datetime.utcnow()
            db.session.commit()
        return f(*args, **kwargs)
    return decorated_function

# ============ РАБОТА С БАЗАМИ ============
class DatabaseManager:
    """Менеджер слитых баз данных"""
    
    def __init__(self):
        self.databases = {}
        self.load_all_databases()
    
    def load_all_databases(self):
        """Загрузка всех баз из папки"""
        for filename in os.listdir(DATA_DIR):
            if filename.endswith(('.db', '.sqlite', '.sqlite3')):
                self.load_sqlite(filename)
            elif filename.endswith('.csv'):
                self.load_csv(filename)
    
    def load_sqlite(self, filename):
        """Загрузка SQLite базы"""
        try:
            path = os.path.join(DATA_DIR, filename)
            conn = sqlite3.connect(path)
            cursor = conn.cursor()
            
            # Получаем все таблицы
            tables = cursor.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
            
            for (table,) in tables:
                # Получаем структуру таблицы
                columns = [col[1] for col in cursor.execute(f"PRAGMA table_info({table})").fetchall()]
                
                db_key = f"{filename}_{table}"
                self.databases[db_key] = {
                    'type': 'sqlite',
                    'path': path,
                    'table': table,
                    'columns': columns,
                    'conn': conn
                }
        except Exception as e:
            print(f"Ошибка загрузки {filename}: {e}")
    
    def load_csv(self, filename):
        """Загрузка CSV базы"""
        try:
            path = os.path.join(DATA_DIR, filename)
            df = pd.read_csv(path, encoding='utf-8-sig', low_memory=False)
            
            db_key = filename.replace('.csv', '')
            self.databases[db_key] = {
                'type': 'csv',
                'path': path,
                'dataframe': df,
                'columns': df.columns.tolist()
            }
        except Exception as e:
            print(f"Ошибка загрузки {filename}: {e}")
    
    def search_all(self, query, search_type='auto'):
        """Поиск по всем базам"""
        results = []
        
        for db_name, db_info in self.databases.items():
            if db_info['type'] == 'sqlite':
                results.extend(self.search_sqlite(db_info, query, search_type))
            elif db_info['type'] == 'csv':
                results.extend(self.search_csv(db_info, query, search_type))
        
        return results
    
    def search_sqlite(self, db_info, query, search_type):
        """Поиск в SQLite базе"""
        results = []
        conn = db_info['conn']
        table = db_info['table']
        columns = db_info['columns']
        
        # Определяем колонки для поиска
        search_columns = self.get_search_columns(columns, search_type)
        
        for col in search_columns:
            try:
                # Прямой поиск
                sql = f"SELECT * FROM {table} WHERE CAST({col} AS TEXT) LIKE ? LIMIT 50"
                rows = conn.execute(sql, (f'%{query}%',)).fetchall()
                
                for row in rows:
                    result = dict(zip(columns, row))
                    result['source'] = f"{db_info['path']}::{table}::{col}"
                    results.append(result)
            except:
                continue
        
        return results
    
    def search_csv(self, db_info, query, search_type):
        """Поиск в CSV базе"""
        results = []
        df = db_info['dataframe']
        columns = db_info['columns']
        
        search_columns = self.get_search_columns(columns, search_type)
        
        for col in search_columns:
            try:
                # Поиск по колонке
                mask = df[col].astype(str).str.contains(query, case=False, na=False)
                matches = df[mask].head(50)
                
                for _, row in matches.iterrows():
                    result = row.to_dict()
                    result['source'] = f"{db_info['path']}::{col}"
                    results.append(result)
            except:
                continue
        
        return results
    
    def get_search_columns(self, columns, search_type):
        """Определение колонок для поиска по типу данных"""
        column_patterns = {
            'phone': ['phone', 'tel', 'mobile', 'телефон', 'мобильный', 'номер'],
            'email': ['email', 'mail', 'почта', 'e-mail'],
            'name': ['name', 'fio', 'фио', 'fullname', 'имя', 'фамилия'],
            'passport': ['passport', 'паспорт', 'doc', 'document'],
            'snils': ['snils', 'снилс'],
            'inn': ['inn', 'инн'],
            'address': ['address', 'адрес', 'city', 'город'],
            'vin': ['vin', 'вин'],
            'car': ['car', 'auto', 'авто', 'госномер', 'plate'],
            'telegram': ['telegram', 'tg', 'ник', 'username'],
        }
        
        if search_type == 'auto':
            # Все возможные колонки
            matched = []
            for patterns in column_patterns.values():
                for col in columns:
                    col_lower = col.lower()
                    if any(p in col_lower for p in patterns):
                        matched.append(col)
            return list(set(matched)) if matched else columns
        else:
            patterns = column_patterns.get(search_type, [])
            return [col for col in columns if any(p in col.lower() for p in patterns)]

# ============ ОСНОВНОЙ ПОИСКОВИК ============
class OSINTSearcher:
    """Главный поисковый движок"""
    
    def __init__(self):
        self.db_manager = DatabaseManager()
    
    def search(self, query, query_type='auto'):
        """Универсальный поиск"""
        results = {
            'local_db': [],
            'osint': {},
            'social': {},
            'summary': ''
        }
        
        # 1. Поиск в локальных слитых базах
        results['local_db'] = self.db_manager.search_all(query, query_type)
        
        # 2. OSINT поиск
        if query_type == 'phone' or self.detect_phone(query):
            results['osint'] = self.search_phone(query)
        elif query_type == 'email' or self.detect_email(query):
            results['osint'] = self.search_email(query)
        elif query_type == 'vin' or self.detect_vin(query):
            results['osint'] = self.search_vin(query)
        elif query_type == 'car_plate' or self.detect_car_plate(query):
            results['osint'] = self.search_car_plate(query)
        elif query_type == 'domain' or self.detect_domain(query):
            results['osint'] = self.search_domain(query)
        
        # 3. Соцсети
        if query_type == 'username' or query.startswith('@'):
            results['social'] = self.search_social(query)
        
        # 4. Сводка
        results['summary'] = self.generate_summary(results)
        
        return results
    
    def detect_phone(self, text):
        return bool(re.match(r'^(\+?[78])?[\s\-]?\(?\d{3}\)?[\s\-]?\d{3}[\s\-]?\d{2}[\s\-]?\d{2}$', text.strip()))
    
    def detect_email(self, text):
        return bool(re.match(r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$', text.strip()))
    
    def detect_vin(self, text):
        return bool(re.match(r'^[A-HJ-NPR-Z0-9]{17}$', text.strip().upper()))
    
    def detect_car_plate(self, text):
        return bool(re.match(r'^[АВЕКМНОРСТУХ]\d{3}[АВЕКМНОРСТУХ]{2}\d{2,3}$', text.strip().upper()))
    
    def detect_domain(self, text):
        return bool(re.match(r'^[a-zA-Z0-9][a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$', text.strip()))
    
    def search_phone(self, phone):
        """Поиск по телефону"""
        clean = re.sub(r'[^\d]', '', phone)
        info = {}
        
        try:
            parsed = phonenumbers.parse(f'+{clean}' if len(clean) > 10 else f'+7{clean[-10:]}', 'RU')
            info['Страна'] = geocoder.description_for_number(parsed, 'ru')
            info['Оператор'] = carrier.name_for_number(parsed, 'ru')
            info['Тип'] = 'Мобильный' if phonenumbers.number_type(parsed) == 1 else 'Стационарный'
        except:
            pass
        
        return info
    
    def search_email(self, email):
        """Поиск по email"""
        info = {}
        domain = email.split('@')[1]
        
        try:
            # Проверка утечек
            resp = requests.get(
                f'https://haveibeenpwned.com/api/v3/breachedaccount/{email}?truncateResponse=true',
                headers={'User-Agent': ua.random},
                timeout=10
            )
            if resp.status_code == 200:
                info['Утечки'] = [b['Name'] for b in resp.json()[:10]]
            elif resp.status_code == 404:
                info['Утечки'] = 'Не найден'
        except:
            info['Утечки'] = 'Ошибка проверки'
        
        return info
    
    def search_vin(self, vin):
        """Расшифровка VIN"""
        vin = vin.upper()
        info = {}
        
        wmi_dict = {
            'XTA': 'LADA (АвтоВАЗ)', 'XTE': 'ГАЗ', 'XTT': 'УАЗ',
            'WBA': 'BMW', 'WDB': 'Mercedes-Benz', 'WAU': 'Audi',
            'WVW': 'Volkswagen', 'JHM': 'Honda', 'JT2': 'Toyota',
            'KMH': 'Hyundai', 'KNA': 'Kia', 'VF1': 'Renault',
        }
        
        info['Производитель'] = wmi_dict.get(vin[:3], f'Код: {vin[:3]}')
        info['Год'] = self.decode_vin_year(vin[9]) if len(vin) > 9 else '?'
        
        return info
    
    def decode_vin_year(self, code):
        years = {'A': 2010, 'B': 2011, 'C': 2012, 'D': 2013, 'E': 2014,
                 'F': 2015, 'G': 2016, 'H': 2017, 'J': 2018, 'K': 2019,
                 'L': 2020, 'M': 2021, 'N': 2022, 'P': 2023, 'R': 2024}
        return years.get(code.upper(), 'Неизвестно')
    
    def search_car_plate(self, plate):
        """Информация о госномере"""
        info = {}
        clean = re.sub(r'[^\w]', '', plate.upper())
        
        regions = {
            '77': 'Москва', '78': 'СПб', '50': 'МО', '23': 'Краснодар',
            '16': 'Татарстан', '02': 'Башкортостан', '66': 'Свердловская',
        }
        
        match = re.search(r'(\d{2,3})$', clean)
        if match:
            info['Регион'] = regions.get(match.group(1), f'Код: {match.group(1)}')
        
        return info
    
    def search_domain(self, domain):
        """Информация о домене"""
        info = {}
        
        try:
            import whois
            w = whois.whois(domain)
            info['Регистратор'] = w.registrar or '?'
            info['Создан'] = str(w.creation_date)[:10] if w.creation_date else '?'
            info['Истекает'] = str(w.expiration_date)[:10] if w.expiration_date else '?'
            info['Владелец'] = w.org or 'Скрыт'
        except:
            pass
        
        try:
            import socket
            info['IP'] = socket.gethostbyname(domain)
        except:
            pass
        
        return info
    
    def search_social(self, username):
        """Поиск в соцсетях"""
        username = username.replace('@', '').strip()
        results = {}
        
        sites = {
            'VK': f'https://vk.com/{username}',
            'Telegram': f'https://t.me/{username}',
            'Instagram': f'https://instagram.com/{username}',
            'Twitter': f'https://twitter.com/{username}',
            'GitHub': f'https://github.com/{username}',
            'TikTok': f'https://tiktok.com/@{username}',
            'Reddit': f'https://reddit.com/user/{username}',
        }
        
        for platform, url in sites.items():
            try:
                resp = requests.head(url, headers={'User-Agent': ua.random}, timeout=5)
                if resp.status_code == 200:
                    results[platform] = f'✅ Найден'
                elif resp.status_code == 404:
                    results[platform] = '❌ Не найден'
            except:
                results[platform] = '⚠️ Ошибка'
        
        return results
    
    def generate_summary(self, results):
        """Генерация сводки"""
        summary_parts = []
        
        # Локальные базы
        local_count = len(results.get('local_db', []))
        if local_count > 0:
            summary_parts.append(f'🔍 В локальных базах: {local_count} записей')
            
            # Собираем ключевую информацию из найденных записей
            for record in results['local_db'][:3]:
                for key in ['ФИО', 'name', 'fio', 'адрес', 'address', 'паспорт', 'passport']:
                    if key in record:
                        summary_parts.append(f'  📌 {record[key]}')
                        break
        
        # OSINT
        if results.get('osint'):
            for key, value in results['osint'].items():
                if isinstance(value, list):
                    summary_parts.append(f'📋 {key}: {len(value)}')
                else:
                    summary_parts.append(f'📋 {key}: {value}')
        
        # Соцсети
        social_found = sum(1 for v in results.get('social', {}).values() if '✅' in str(v))
        if social_found > 0:
            summary_parts.append(f'👤 Профилей найдено: {social_found}')
        
        return '\n'.join(summary_parts) if summary_parts else 'Ничего не найдено'

# ============ ИНИЦИАЛИЗАЦИЯ ПОИСКОВИКА ============
searcher = OSINTSearcher()

# ============ МАРШРУТЫ ============
@app.route('/')
def index():
    if current_user.is_authenticated:
        return render_template('dashboard.html')
    return redirect(url_for('login'))

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        
        user = User.query.filter_by(username=username).first()
        
        if user and check_password_hash(user.password, password):
            if user.is_blocked:
                return render_template('login.html', error='Аккаунт заблокирован')
            login_user(user)
            return redirect(url_for('index'))
        
        return render_template('login.html', error='Неверные данные')
    
    return render_template('login.html')

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        invite_code = request.form.get('invite_code')
        
        # Проверка инвайт-кода (хуй зарегится без него)
        valid_codes = ['PIDORI2024', 'OSINT_ACCESS', 'admin_invite_1337']
        if invite_code not in valid_codes:
            return render_template('register.html', error='Неверный инвайт-код')
        
        if User.query.filter_by(username=username).first():
            return render_template('register.html', error='Пользователь уже существует')
        
        user = User(
            username=username,
            password=generate_password_hash(password),
            is_admin=(invite_code == 'admin_invite_1337')
        )
        
        db.session.add(user)
        db.session.commit()
        
        login_user(user)
        return redirect(url_for('index'))
    
    return render_template('register.html')

@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('login'))

@app.route('/search', methods=['POST'])
@login_required
@check_rate_limit
def search():
    query = request.json.get('query', '').strip()
    query_type = request.json.get('type', 'auto')
    
    if not query:
        return jsonify({'error': 'Введите запрос'}), 400
    
    # Логирование
    log = SearchLog(
        user_id=current_user.id,
        query_type=query_type,
        query_text=query,
        ip_address=request.remote_addr
    )
    
    try:
        results = searcher.search(query, query_type)
        log.result_count = len(results.get('local_db', []))
        log.timestamp = datetime.utcnow()
        
        return jsonify({
            'success': True,
            'query': query,
            'type': query_type,
            'results': results,
            'timestamp': datetime.utcnow().isoformat()
        })
    except Exception as e:
        log.result_count = 0
        return jsonify({'error': f'Ошибка поиска: {str(e)}'}), 500
    finally:
        db.session.add(log)
        db.session.commit()

@app.route('/admin')
@login_required
@admin_required
def admin_panel():
    users = User.query.all()
    logs = SearchLog.query.order_by(SearchLog.timestamp.desc()).limit(100).all()
    
    stats = {
        'total_users': User.query.count(),
        'total_searches': SearchLog.query.count(),
        'blocked_users': User.query.filter_by(is_blocked=True).count(),
        'admins': User.query.filter_by(is_admin=True).count(),
    }
    
    return render_template('admin.html', users=users, logs=logs, stats=stats)

@app.route('/admin/user/<int:user_id>/block', methods=['POST'])
@login_required
@admin_required
def block_user(user_id):
    user = User.query.get(user_id)
    if user:
        user.is_blocked = not user.is_blocked
        db.session.commit()
        return jsonify({'success': True, 'blocked': user.is_blocked})
    return jsonify({'error': 'Пользователь не найден'}), 404

@app.route('/admin/user/<int:user_id>/limit', methods=['POST'])
@login_required
@admin_required
def set_user_limit(user_id):
    user = User.query.get(user_id)
    if user:
        limit = request.json.get('limit', 50)
        # Сохраняем лимит в сессии или БД
        return jsonify({'success': True, 'limit': limit})
    return jsonify({'error': 'Пользователь не найден'}), 404

@app.route('/admin/databases')
@login_required
@admin_required
def list_databases():
    databases = []
    for db_name, db_info in searcher.db_manager.databases.items():
        databases.append({
            'name': db_name,
            'type': db_info['type'],
            'columns': db_info['columns'][:10],
            'size': os.path.getsize(db_info['path']) if 'path' in db_info else 0
        })
    return jsonify(databases)

@app.route('/admin/reload')
@login_required
@admin_required
def reload_databases():
    global searcher
    searcher = OSINTSearcher()
    return jsonify({'success': True, 'message': 'Базы перезагружены'})

@app.route('/api/export/<format>')
@login_required
@admin_required
def export_data(format):
    """Экспорт логов"""
    logs = SearchLog.query.all()
    data = [{
        'user_id': log.user_id,
        'query_type': log.query_type,
        'query_text': log.query_text,
        'timestamp': log.timestamp.isoformat(),
        'result_count': log.result_count
    } for log in logs]
    
    if format == 'json':
        return jsonify(data)
    elif format == 'csv':
        import io
        output = io.StringIO()
        writer = csv.DictWriter(output, fieldnames=data[0].keys())
        writer.writeheader()
        writer.writerows(data)
        return send_file(
            io.BytesIO(output.getvalue().encode('utf-8')),
            mimetype='text/csv',
            as_attachment=True,
            download_name='search_logs.csv'
        )
    
    return jsonify({'error': 'Неверный формат'}), 400

# ============ HTML ШАБЛОНЫ ============
HTML_TEMPLATES = {
    'login.html': '''
<!DOCTYPE html>
<html>
<head>
    <title>PIDORI OSINT - Вход</title>
    <meta charset="utf-8">
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body { background: #0a0a0a; color: #00ff00; font-family: 'Courier New', monospace; display: flex; justify-content: center; align-items: center; height: 100vh; }
        .container { background: #111; border: 2px solid #00ff00; padding: 40px; border-radius: 10px; width: 400px; box-shadow: 0 0 20px rgba(0,255,0,0.3); }
        h1 { text-align: center; margin-bottom: 30px; font-size: 24px; text-shadow: 0 0 10px #00ff00; }
        input { width: 100%; padding: 12px; margin: 10px 0; background: #000; border: 1px solid #00ff00; color: #00ff00; font-family: inherit; border-radius: 5px; }
        button { width: 100%; padding: 12px; background: #00ff00; color: #000; border: none; font-weight: bold; font-family: inherit; cursor: pointer; border-radius: 5px; margin-top: 10px; }
        button:hover { background: #00cc00; }
        .error { color: #ff0000; text-align: center; margin-top: 10px; }
        a { color: #00ff00; text-decoration: none; display: block; text-align: center; margin-top: 15px; }
    </style>
</head>
<body>
    <div class="container">
        <h1>🕵️ PIDORI OSINT</h1>
        <form method="POST">
            <input type="text" name="username" placeholder="Логин" required>
            <input type="password" name="password" placeholder="Пароль" required>
            <button type="submit">ВОЙТИ</button>
        </form>
        {% if error %}
        <div class="error">{{ error }}</div>
        {% endif %}
        <a href="{{ url_for('register') }}">Регистрация</a>
    </div>
</body>
</html>
''',
    
    'register.html': '''
<!DOCTYPE html>
<html>
<head>
    <title>PIDORI OSINT - Регистрация</title>
    <meta charset="utf-8">
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body { background: #0a0a0a; color: #00ff00; font-family: 'Courier New', monospace; display: flex; justify-content: center; align-items: center; height: 100vh; }
        .container { background: #111; border: 2px solid #00ff00; padding: 40px; border-radius: 10px; width: 400px; }
        h1 { text-align: center; margin-bottom: 30px; }
        input { width: 100%; padding: 12px; margin: 10px 0; background: #000; border: 1px solid #00ff00; color: #00ff00; font-family: inherit; border-radius: 5px; }
        button { width: 100%; padding: 12px; background: #00ff00; color: #000; border: none; font-weight: bold; cursor: pointer; border-radius: 5px; }
        .error { color: #ff0000; text-align: center; margin-top: 10px; }
        a { color: #00ff00; text-decoration: none; display: block; text-align: center; margin-top: 15px; }
    </style>
</head>
<body>
    <div class="container">
        <h1>🔐 Регистрация</h1>
        <form method="POST">
            <input type="text" name="username" placeholder="Логин" required>
            <input type="password" name="password" placeholder="Пароль" required>
            <input type="text" name="invite_code" placeholder="Инвайт-код" required>
            <button type="submit">ЗАРЕГИСТРИРОВАТЬСЯ</button>
        </form>
        {% if error %}
        <div class="error">{{ error }}</div>
        {% endif %}
        <a href="{{ url_for('login') }}">Вход</a>
    </div>
</body>
</html>
''',
    
    'dashboard.html': '''
<!DOCTYPE html>
<html>
<head>
    <title>PIDORI OSINT - Поиск</title>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body { background: #0a0a0a; color: #00ff00; font-family: 'Courier New', monospace; min-height: 100vh; }
        .header { background: #111; border-bottom: 2px solid #00ff00; padding: 15px 30px; display: flex; justify-content: space-between; align-items: center; }
        .header h1 { font-size: 20px; }
        .header a { color: #00ff00; text-decoration: none; margin-left: 20px; }
        .container { max-width: 1400px; margin: 20px auto; padding: 0 20px; }
        .search-box { background: #111; border: 2px solid #00ff00; padding: 30px; border-radius: 10px; margin-bottom: 20px; }
        .search-box input { width: 100%; padding: 15px; background: #000; border: 1px solid #00ff00; color: #00ff00; font-size: 16px; font-family: inherit; border-radius: 5px; }
        .search-box select { padding: 15px; background: #000; border: 1px solid #00ff00; color: #00ff00; font-family: inherit; border-radius: 5px; margin-top: 10px; }
        .examples { color: #666; font-size: 12px; margin-top: 10px; }
        .examples code { color: #00cc00; }
        .results { display: grid; grid-template-columns: 1fr 1fr; gap: 20px; }
        .result-panel { background: #111; border: 1px solid #333; padding: 20px; border-radius: 10px; }
        .result-panel h3 { border-bottom: 1px solid #333; padding-bottom: 10px; margin-bottom: 15px; }
        .result-item { padding: 10px; background: #0a0a0a; margin-bottom: 10px; border-left: 3px solid #00ff00; }
        .result-item .key { color: #00cc00; font-weight: bold; }
        .result-item .value { color: #ccc; }
        .summary { background: #111; border: 2px solid #00ff00; padding: 20px; border-radius: 10px; margin-top: 20px; white-space: pre-wrap; }
        .loading { text-align: center; padding: 50px; color: #666; }
        .admin-link { color: #ff0; }
    </style>
</head>
<body>
    <div class="header">
        <h1>🕵️ PIDORI OSINT v2.0</h1>
        <div>
            {% if current_user.is_admin %}
            <a href="{{ url_for('admin_panel') }}" class="admin-link">[АДМИН]</a>
            {% endif %}
            <span>{{ current_user.username }}</span>
            <a href="{{ url_for('logout') }}">[ВЫХОД]</a>
        </div>
    </div>
    
    <div class="container">
        <div class="search-box">
            <input type="text" id="searchInput" placeholder="Введите запрос..." autofocus>
            <select id="searchType">
                <option value="auto">Автоопределение</option>
                <option value="phone">Телефон</option>
                <option value="email">Email</option>
                <option value="name">ФИО</option>
                <option value="passport">Паспорт</option>
                <option value="snils">СНИЛС</option>
                <option value="inn">ИНН</option>
                <option value="vin">VIN</option>
                <option value="car">Авто</option>
                <option value="address">Адрес</option>
                <option value="telegram">Telegram</option>
                <option value="username">Соцсети</option>
                <option value="domain">Домен/IP</option>
            </select>
            <div class="examples">
                <b>Примеры:</b>
                <code>79991234567</code> | 
                <code>user@mail.ru</code> | 
                <code>Иванов Иван 01.01.1990</code> |
                <code>А123ВС199</code> |
                <code>@username</code>
            </div>
        </div>
        
        <div id="results"></div>
    </div>
    
    <script>
        const searchInput = document.getElementById('searchInput');
        const searchType = document.getElementById('searchType');
        let searchTimeout;
        
        searchInput.addEventListener('input', function() {
            clearTimeout(searchTimeout);
            const query = this.value.trim();
            
            if (query.length >= 3) {
                document.getElementById('results').innerHTML = '<div class="loading">🔍 Поиск...</div>';
                
                searchTimeout = setTimeout(() => {
                    performSearch(query, searchType.value);
                }, 500);
            }
        });
        
        searchType.addEventListener('change', function() {
            const query = searchInput.value.trim();
            if (query.length >= 3) {
                performSearch(query, this.value);
            }
        });
        
        function performSearch(query, type) {
            fetch('/search', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({query: query, type: type})
            })
            .then(response => response.json())
            .then(data => {
                if (data.error) {
                    document.getElementById('results').innerHTML = `<div class="loading">❌ ${data.error}</div>`;
                    return;
                }
                displayResults(data);
            })
            .catch(error => {
                document.getElementById('results').innerHTML = `<div class="loading">❌ Ошибка: ${error}</div>`;
            });
        }
        
        function displayResults(data) {
            let html = '<div class="results">';
            
            // Локальная БД
            html += '<div class="result-panel">';
            html += '<h3>🗄️ Локальная база данных</h3>';
            if (data.results.local_db && data.results.local_db.length > 0) {
                data.results.local_db.forEach(record => {
                    html += '<div class="result-item">';
                    html += `<span class="key">Источник:</span> <span class="value">${record.source}</span><br>`;
                    for (const [key, value] of Object.entries(record)) {
                        if (key !== 'source' && value) {
                            html += `<span class="key">${key}:</span> <span class="value">${value}</span><br>`;
                        }
                    }
                    html += '</div>';
                });
            } else {
                html += '<p style="color:#666">Нет данных в локальных базах</p>';
            }
            html += '</div>';
            
            // OSINT
            html += '<div class="result-panel">';
            html += '<h3>🌐 Открытые источники</h3>';
            if (data.results.osint && Object.keys(data.results.osint).length > 0) {
                for (const [key, value] of Object.entries(data.results.osint)) {
                    html += '<div class="result-item">';
                    if (Array.isArray(value)) {
                        html += `<span class="key">${key}:</span><br>`;
                        value.forEach(v => html += `<span class="value">  • ${v}</span><br>`);
                    } else {
                        html += `<span class="key">${key}:</span> <span class="value">${value}</span>`;
                    }
                    html += '</div>';
                }
            } else {
                html += '<p style="color:#666">Нет данных из открытых источников</p>';
            }
            html += '</div>';
            
            // Соцсети
            html += '<div class="result-panel">';
            html += '<h3>👤 Социальные сети</h3>';
            if (data.results.social && Object.keys(data.results.social).length > 0) {
                for (const [platform, status] of Object.entries(data.results.social)) {
                    html += `<div class="result-item"><span class="key">${platform}:</span> <span class="value">${status}</span></div>`;
                }
            } else {
                html += '<p style="color:#666">Нет данных о соцсетях</p>';
            }
            html += '</div>';
            
            html += '</div>';
            
            // Сводка
            if (data.results.summary) {
                html += `<div class="summary">${data.results.summary}</div>`;
            }
            
            document.getElementById('results').innerHTML = html;
        }
    </script>
</body>
</html>
''',
    
    'admin.html': '''
<!DOCTYPE html>
<html>
<head>
    <title>PIDORI OSINT - Админ-панель</title>
    <meta charset="utf-8">
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body { background: #0a0a0a; color: #00ff00; font-family: 'Courier New', monospace; }
        .header { background: #111; border-bottom: 2px solid #ff0; padding: 15px 30px; display: flex; justify-content: space-between; }
        .header a { color: #00ff00; text-decoration: none; }
        .container { max-width: 1400px; margin: 20px auto; padding: 0 20px; }
        .stats { display: grid; grid-template-columns: repeat(4, 1fr); gap: 15px; margin-bottom: 20px; }
        .stat-card { background: #111; border: 1px solid #333; padding: 20px; text-align: center; border-radius: 5px; }
        .stat-card h2 { font-size: 32px; color: #ff0; }
        .panel { background: #111; border: 1px solid #333; padding: 20px; border-radius: 5px; margin-bottom: 20px; }
        table { width: 100%; border-collapse: collapse; }
        th, td { padding: 10px; text-align: left; border-bottom: 1px solid #333; }
        th { color: #ff0; }
        button { padding: 5px 15px; background: #333; color: #00ff00; border: 1px solid #00ff00; cursor: pointer; border-radius: 3px; }
        button:hover { background: #00ff00; color: #000; }
        .blocked { color: #ff0000; }
    </style>
</head>
<body>
    <div class="header">
        <h1>🔐 Админ-панель PIDORI OSINT</h1>
        <a href="{{ url_for('index') }}">[НАЗАД]</a>
    </div>
    
    <div class="container">
        <div class="stats">
            <div class="stat-card">
                <div>Пользователей</div>
                <h2>{{ stats.total_users }}</h2>
            </div>
            <div class="stat-card">
                <div>Поисков</div>
                <h2>{{ stats.total_searches }}</h2>
            </div>
            <div class="stat-card">
                <div>Заблокировано</div>
                <h2>{{ stats.blocked_users }}</h2>
            </div>
            <div class="stat-card">
                <div>Админов</div>
                <h2>{{ stats.admins }}</h2>
            </div>
        </div>
        
        <div class="panel">
            <h3>👥 Пользователи</h3>
            <table>
                <tr>
                    <th>ID</th>
                    <th>Логин</th>
                    <th>Запросов</th>
                    <th>Статус</th>
                    <th>Действия</th>
                </tr>
                {% for user in users %}
                <tr class="{{ 'blocked' if user.is_blocked }}">
                    <td>{{ user.id }}</td>
                    <td>{{ user.username }}</td>
                    <td>{{ user.total_requests }}</td>
                    <td>{{ '🚫 Заблокирован' if user.is_blocked else '✅ Активен' }}</td>
                    <td>
                        <button onclick="toggleBlock({{ user.id }})">
                            {{ 'Разблокировать' if user.is_blocked else 'Заблокировать' }}
                        </button>
                    </td>
                </tr>
                {% endfor %}
            </table>
        </div>
        
        <div class="panel">
            <h3>📋 Последние запросы</h3>
            <table>
                <tr>
                    <th>Время</th>
                    <th>Пользователь</th>
                    <th>Тип</th>
                    <th>Запрос</th>
                    <th>Результатов</th>
                </tr>
                {% for log in logs %}
                <tr>
                    <td>{{ log.timestamp.strftime('%H:%M:%S') }}</td>
                    <td>{{ log.user_id }}</td>
                    <td>{{ log.query_type }}</td>
                    <td>{{ log.query_text[:100] }}</td>
                    <td>{{ log.result_count }}</td>
                </tr>
                {% endfor %}
            </table>
        </div>
        
        <button onclick="reloadDB()">🔄 Перезагрузить базы</button>
        <a href="/api/export/json" style="color:#00ff00">📥 Экспорт JSON</a>
        <a href="/api/export/csv" style="color:#00ff00">📥 Экспорт CSV</a>
    </div>
    
    <script>
        function toggleBlock(userId) {
            fetch(`/admin/user/${userId}/block`, {method: 'POST'})
                .then(r => r.json())
                .then(data => location.reload());
        }
        
        function reloadDB() {
            fetch('/admin/reload')
                .then(r => r.json())
                .then(data => alert(data.message));
        }
    </script>
</body>
</html>
'''
}

# ============ СОЗДАНИЕ ШАБЛОНОВ ============
os.makedirs('templates', exist_ok=True)
for filename, content in HTML_TEMPLATES.items():
    with open(f'templates/{filename}', 'w', encoding='utf-8') as f:
        f.write(content)

# ============ ЗАПУСК ============
if __name__ == '__main__':
    with app.app_context():
        db.create_all()
        
        # Создаем админа если нет
        if not User.query.filter_by(username='admin').first():
            admin = User(
                username='admin',
                password=generate_password_hash('admin123'),
                is_admin=True
            )
            db.session.add(admin)
            db.session.commit()
            print("✅ Админ создан: admin / admin123")
    
    print("""
    ╔══════════════════════════════════════╗
    ║     🕵️ PIDORI OSINT WEB v2.0        ║
    ║     http://localhost:5000            ║
    ║     admin / admin123                 ║
    ╚══════════════════════════════════════╝
    """)
    
    app.run(host='0.0.0.0', port=5000, debug=True)
