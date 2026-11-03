import os
import sys
import re
import time
import math
import sqlite3
import threading
import requests
import hmac
import hashlib
import base64
from decimal import Decimal, ROUND_DOWN
from datetime import datetime
from urllib.parse import urlencode

# --- Kivy Framework Imports ---
from kivy.app import App
from kivy.clock import Clock
from kivy.metrics import dp, sp
from kivy.core.window import Window
from kivy.core.text import LabelBase
from kivy.graphics import Color, RoundedRectangle, Rectangle, Line
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.gridlayout import GridLayout
from kivy.uix.scrollview import ScrollView
from kivy.uix.label import Label
from kivy.uix.button import Button
from kivy.uix.textinput import TextInput
from kivy.uix.checkbox import CheckBox
from kivy.uix.spinner import Spinner
from kivy.uix.popup import Popup
from kivy.uix.tabbedpanel import TabbedPanel, TabbedPanelItem
from kivy.uix.widget import Widget

# --- دعم تشكيل النصوص العربية في Kivy (مع بديل تلقائي مدمج) ---
try:
    import arabic_reshaper
    from bidi.algorithm import get_display

    def ar_text(text):
        if text is None:
            return ""
        s = str(text)
        if not re.search(r"[\u0600-\u06FF]", s):
            return s
        try:
            return get_display(arabic_reshaper.reshape(s))
        except Exception:
            return s
except ImportError:
    def ar_text(text):
        return "" if text is None else str(text)


def register_arabic_font():
    """تسجيل خط يدعم العربية والرموز على أنظمة Android / Linux / Windows."""
    candidate_paths = [
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "NotoSansArabic-Regular.ttf"),
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "DejaVuSans.ttf"),
        "/system/fonts/NotoNaskhArabic-Regular.ttf",
        "/system/fonts/NotoSansArabic-Regular.ttf",
        "/system/fonts/DroidSansArabic.ttf",
        "/usr/share/fonts/truetype/noto/NotoSansArabic-Regular.ttf",
        "/usr/share/fonts/truetype/noto/NotoKufiArabic-Regular.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "C:\\Windows\\Fonts\\tahoma.ttf",
        "C:\\Windows\\Fonts\\arial.ttf",
        "C:\\Windows\\Fonts\\segoeui.ttf",
    ]
    for path in candidate_paths:
        if os.path.exists(path):
            try:
                LabelBase.register(name="Roboto", fn_regular=path, fn_bold=path)
                break
            except Exception:
                continue


register_arabic_font()

# --- إدارة قاعدة البيانات والذاكرة المؤقتة ---
DB_NAME = "bot_data.db"
SYMBOL_RULES_CACHE = {}
BLACKLISTED_SYMBOLS = set()
SPOT_WHITELIST_CACHE = {"symbols": set(), "timestamp": 0.0}
SERVER_TIME_OFFSET_MS = 0
SERVER_TIME_SYNC_AT = 0.0

# العملات المستقرة والرموز المرفوعة مالياً أو الأسهم المرمّزة غير المناسبة للسكالبينج الفوري
EXCLUDED_BASE_ASSETS = {
    "USDC", "FDUSD", "USDE", "TUSD", "DAI", "USDP", "BUSD", "PYUSD",
    "USDD", "USD1", "USDS", "USDX", "USDJ", "USDR", "USN", "UST", "USTC",
    "FRAX", "LUSD", "GUSD", "MIM", "CRVUSD", "GHO", "USDB", "USDY", "USD0",
    "USYC", "USDTB", "USDQ", "USDV", "EURC", "EURS", "EURI", "EUROC",
    "EUR", "GBP", "EURT", "AEUR", "WBTC", "WETH", "STETH", "CBETH", "BETH"
}

# لواحق الرموز ذات الرافعة المالية (ETFs)
EXCLUDED_SUFFIXES = ("3L", "3S", "5L", "5S", "2L", "2S", "4L", "4S", "UP", "DOWN", "BULL", "BEAR")

# قائمة الأسهم المرمّزة على MEXC (Ondo Tokenized Stocks التي ترفض أوامر Market عبر Spot API)
TOKENIZED_STOCK_BASES = {
    "REGNON", "TXNON", "XOMON", "AAPLON", "TSLAON", "NVDAON", "MSFTON",
    "AMZNON", "METAON", "GOOGLON", "AMDON", "NFLXON", "COINON", "MSTRON",
    "SPYON", "QQQON", "BRKAON", "LLYON", "AVGOON", "JPMON", "VON", "WMTON",
    "KOON", "PEPON", "PGON", "JNJON", "UNHON", "HDON", "MAON", "DISON"
}


def get_db_path():
    """تحديد مسار قاعدة البيانات بما يتوافق مع أندرويد (Kivy user_data_dir) وسطح المكتب."""
    try:
        app = App.get_running_app()
        if app and getattr(app, "user_data_dir", None):
            os.makedirs(app.user_data_dir, exist_ok=True)
            return os.path.join(app.user_data_dir, DB_NAME)
    except Exception:
        pass
    return DB_NAME


def get_mexc_timestamp():
    """استخدام ساعة خادم MEXC لتقليل أخطاء Timestamp خارج recvWindow."""
    global SERVER_TIME_OFFSET_MS, SERVER_TIME_SYNC_AT
    now = time.time()
    if now - SERVER_TIME_SYNC_AT > 60:
        try:
            response = requests.get("https://api.mexc.com/api/v3/time", timeout=3)
            if response.status_code == 200:
                server_ms = int(response.json().get("serverTime", 0))
                if server_ms > 0:
                    SERVER_TIME_OFFSET_MS = server_ms - int(time.time() * 1000)
                    SERVER_TIME_SYNC_AT = now
        except Exception:
            pass
    return int(time.time() * 1000) + SERVER_TIME_OFFSET_MS


def format_price(price):
    """تنسيق السعر ديناميكياً لدعم العملات ذات الأسعار الصغيرة والكبيرة."""
    try:
        val = float(price)
    except (TypeError, ValueError):
        return "0.0000"
    if val == 0:
        return "0.0000"
    if val < 0.001:
        return f"{val:.8f}"
    if val < 1.0:
        return f"{val:.6f}"
    return f"{val:.4f}"


def init_db():
    db_file = get_db_path()
    with sqlite3.connect(db_file) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS active_position (
                id INTEGER PRIMARY KEY,
                symbol TEXT,
                entry_price REAL,
                amount REAL,
                tp_percent REAL,
                sl_percent REAL,
                highest_price REAL DEFAULT 0.0,
                dynamic_sl_percent REAL DEFAULT -0.8,
                tp1_done INTEGER DEFAULT 0,
                be_active INTEGER DEFAULT 0,
                trailing_active INTEGER DEFAULT 0,
                trade_mode TEXT DEFAULT 'LIVE',
                base_qty REAL DEFAULT 0.0
            )
        """)
        existing_cols = [row[1] for row in cursor.execute("PRAGMA table_info(active_position)").fetchall()]
        migrations = [
            ("highest_price", "REAL DEFAULT 0.0"),
            ("dynamic_sl_percent", "REAL DEFAULT -0.8"),
            ("tp1_done", "INTEGER DEFAULT 0"),
            ("be_active", "INTEGER DEFAULT 0"),
            ("trailing_active", "INTEGER DEFAULT 0"),
            ("trade_mode", "TEXT DEFAULT 'LIVE'"),
            ("base_qty", "REAL DEFAULT 0.0"),
        ]
        for col_name, col_def in migrations:
            if col_name not in existing_cols:
                cursor.execute(f"ALTER TABLE active_position ADD COLUMN {col_name} {col_def}")

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS closed_trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT,
                entry_price REAL,
                exit_price REAL,
                amount REAL,
                pnl_usd REAL,
                pnl_percent REAL,
                reason TEXT,
                timestamp TEXT
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS blacklisted_symbols (
                symbol TEXT PRIMARY KEY,
                reason TEXT,
                timestamp TEXT
            )
        """)
        conn.commit()

    load_blacklist_from_db()


def load_blacklist_from_db():
    global BLACKLISTED_SYMBOLS
    try:
        with sqlite3.connect(get_db_path()) as conn:
            cursor = conn.cursor()
            rows = cursor.execute("SELECT symbol FROM blacklisted_symbols").fetchall()
            BLACKLISTED_SYMBOLS = {r[0] for r in rows}
    except Exception:
        BLACKLISTED_SYMBOLS = set()


def add_symbol_to_blacklist(symbol, reason=""):
    formatted = symbol.replace("/", "").upper()
    BLACKLISTED_SYMBOLS.add(formatted)
    SPOT_WHITELIST_CACHE["symbols"].discard(formatted)
    try:
        with sqlite3.connect(get_db_path()) as conn:
            cursor = conn.cursor()
            now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            cursor.execute(
                "INSERT OR REPLACE INTO blacklisted_symbols (symbol, reason, timestamp) VALUES (?, ?, ?)",
                (formatted, str(reason), now_str)
            )
            conn.commit()
    except Exception as e:
        print(f"خطأ في حفظ القائمة السوداء: {e}")


def clear_blacklist_db():
    global BLACKLISTED_SYMBOLS
    BLACKLISTED_SYMBOLS.clear()
    SPOT_WHITELIST_CACHE["timestamp"] = 0.0
    try:
        with sqlite3.connect(get_db_path()) as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM blacklisted_symbols")
            conn.commit()
    except Exception:
        pass


def clear_closed_trades_db():
    """مسح جميع سجلات الصفقات المنتهية والجزئية من قاعدة البيانات."""
    try:
        with sqlite3.connect(get_db_path()) as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM closed_trades")
            conn.commit()
    except Exception as e:
        print(f"خطأ في مسح سجل الصفقات: {e}")


def _protect_secret(value):
    """حماية مفاتيح API عبر Windows DPAPI مع بديل آمن عند الفشل أو على Android/Linux."""
    if value is None or str(value) == "":
        return ""
    raw = str(value).encode("utf-8")
    if os.name != "nt":
        return "plain:" + str(value)
    try:
        import ctypes
        from ctypes import wintypes

        class DATA_BLOB(ctypes.Structure):
            _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_byte))]

        source = ctypes.create_string_buffer(raw)
        in_blob = DATA_BLOB(len(raw), ctypes.cast(source, ctypes.POINTER(ctypes.c_byte)))
        out_blob = DATA_BLOB()
        if not ctypes.windll.crypt32.CryptProtectData(
            ctypes.byref(in_blob), None, None, None, None, 0, ctypes.byref(out_blob)
        ):
            raise OSError("CryptProtectData failed")
        try:
            encrypted = ctypes.string_at(out_blob.pbData, out_blob.cbData)
        finally:
            ctypes.windll.kernel32.LocalFree(out_blob.pbData)
        return "dpapi:" + base64.b64encode(encrypted).decode("ascii")
    except Exception as exc:
        print(f"تحذير في تشفير DPAPI (سيتم استخدام التخزين الاحتياطي): {exc}")
        return "plain:" + str(value)


def _unprotect_secret(value):
    if value is None or str(value) == "":
        return ""
    value = str(value)
    if value.startswith("plain:"):
        return value[6:]
    if not value.startswith("dpapi:"):
        return value
    if os.name != "nt":
        return ""
    try:
        import ctypes
        from ctypes import wintypes

        class DATA_BLOB(ctypes.Structure):
            _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_byte))]

        encrypted = base64.b64decode(value[6:])
        if not encrypted:
            return ""
        source = ctypes.create_string_buffer(encrypted)
        in_blob = DATA_BLOB(len(encrypted), ctypes.cast(source, ctypes.POINTER(ctypes.c_byte)))
        out_blob = DATA_BLOB()
        if not ctypes.windll.crypt32.CryptUnprotectData(
            ctypes.byref(in_blob), None, None, None, None, 0, ctypes.byref(out_blob)
        ):
            raise OSError("CryptUnprotectData failed")
        try:
            return ctypes.string_at(out_blob.pbData, out_blob.cbData).decode("utf-8")
        finally:
            ctypes.windll.kernel32.LocalFree(out_blob.pbData)
    except Exception as exc:
        print(f"تحذير في فك تشفير DPAPI: {exc}")
        return ""


def save_setting(key, value):
    try:
        stored = _protect_secret(value) if key in ("api_key", "secret_key") else str(value)
        with sqlite3.connect(get_db_path()) as conn:
            cursor = conn.cursor()
            cursor.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, stored))
            conn.commit()
    except Exception as exc:
        print(f"خطأ في حفظ الإعداد {key}: {exc}")


def get_setting(key, default=""):
    try:
        with sqlite3.connect(get_db_path()) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT value FROM settings WHERE key=?", (key,))
            row = cursor.fetchone()
    except Exception:
        return default
    if not row or row[0] is None:
        return default
    stored = str(row[0])
    if key in ("api_key", "secret_key"):
        plain_value = _unprotect_secret(stored)
        if plain_value and not stored.startswith(("dpapi:", "plain:")):
            save_setting(key, plain_value)
        return plain_value
    return stored


def save_active_position(
    symbol, entry_price, amount, tp_percent, sl_percent,
    highest_price=None, dynamic_sl_percent=None, tp1_done=0, be_active=0,
    trailing_active=0, trade_mode="LIVE", base_qty=0.0
):
    if highest_price is None:
        highest_price = entry_price
    if dynamic_sl_percent is None:
        dynamic_sl_percent = -abs(float(sl_percent))

    with sqlite3.connect(get_db_path()) as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM active_position")
        cursor.execute("""
            INSERT INTO active_position (
                id, symbol, entry_price, amount, tp_percent, sl_percent,
                highest_price, dynamic_sl_percent, tp1_done, be_active, trailing_active, trade_mode, base_qty
            )
            VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            symbol, entry_price, amount, tp_percent, sl_percent,
            highest_price, dynamic_sl_percent, int(tp1_done), int(be_active),
            int(trailing_active), str(trade_mode).upper(), float(base_qty or 0.0)
        ))
        conn.commit()


def update_active_position_state(
    amount, highest_price, dynamic_sl_percent, tp1_done, be_active, trailing_active, base_qty=None
):
    with sqlite3.connect(get_db_path()) as conn:
        cursor = conn.cursor()
        if base_qty is not None:
            cursor.execute("""
                UPDATE active_position
                SET amount=?, highest_price=?, dynamic_sl_percent=?, tp1_done=?, be_active=?, trailing_active=?, base_qty=?
                WHERE id=1
            """, (
                float(amount), float(highest_price), float(dynamic_sl_percent),
                int(tp1_done), int(be_active), int(trailing_active), float(base_qty)
            ))
        else:
            cursor.execute("""
                UPDATE active_position
                SET amount=?, highest_price=?, dynamic_sl_percent=?, tp1_done=?, be_active=?, trailing_active=?
                WHERE id=1
            """, (
                float(amount), float(highest_price), float(dynamic_sl_percent),
                int(tp1_done), int(be_active), int(trailing_active)
            ))
        conn.commit()


def clear_active_position():
    with sqlite3.connect(get_db_path()) as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM active_position")
        conn.commit()


def get_active_position():
    with sqlite3.connect(get_db_path()) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT symbol, entry_price, amount, tp_percent, sl_percent,
                   highest_price, dynamic_sl_percent, tp1_done, be_active, trailing_active, trade_mode, base_qty
            FROM active_position WHERE id=1
        """)
        row = cursor.fetchone()
        if row:
            entry_p = float(row[1])
            amount_val = float(row[2])
            sl_pct = float(row[4])
            raw_base_qty = float(row[11] or 0.0)
            if raw_base_qty <= 0 and entry_p > 0 and amount_val > 0:
                raw_base_qty = amount_val / entry_p
            return {
                "symbol": row[0],
                "entry_price": entry_p,
                "amount": amount_val,
                "tp_percent": float(row[3]),
                "sl_percent": sl_pct,
                "highest_price": float(row[5]) if row[5] and row[5] > 0 else entry_p,
                "dynamic_sl_percent": float(row[6]) if row[6] is not None else -abs(sl_pct),
                "tp1_done": bool(row[7]),
                "be_active": bool(row[8]),
                "trailing_active": bool(row[9]),
                "trade_mode": str(row[10] or "LIVE").upper(),
                "base_qty": raw_base_qty,
            }
        return None


def record_closed_trade(symbol, entry_price, exit_price, amount, pnl_usd, pnl_percent, reason):
    with sqlite3.connect(get_db_path()) as conn:
        cursor = conn.cursor()
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        cursor.execute("""
            INSERT INTO closed_trades (symbol, entry_price, exit_price, amount, pnl_usd, pnl_percent, reason, timestamp)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (symbol, entry_price, exit_price, amount, pnl_usd, pnl_percent, reason, now_str))
        conn.commit()


def get_all_closed_trades():
    with sqlite3.connect(get_db_path()) as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT symbol, entry_price, exit_price, amount, pnl_usd, pnl_percent, reason, timestamp "
            "FROM closed_trades ORDER BY id DESC"
        )
        return cursor.fetchall()


# --- فلاتر التحقق الصارم من أزواج Spot API على منصة MEXC ---
def is_clean_spot_crypto_pair(symbol, full_name=""):
    """استبعاد العملات المستقرة، عملات الرافعة المالية، والأسهم المرمّزة."""
    if not symbol.endswith("USDT"):
        return False
    if symbol in BLACKLISTED_SYMBOLS:
        return False

    base = symbol[:-4].upper()
    if not base or base in EXCLUDED_BASE_ASSETS or base in TOKENIZED_STOCK_BASES:
        return False

    if base.endswith(EXCLUDED_SUFFIXES):
        return False

    lower_full = str(full_name).lower()
    if any(k in lower_full for k in ("tokenized", "ondo", "leveraged", "3x", "5x", "etf", "stock", "shares")):
        return False

    if not re.match(r"^[A-Z0-9]+$", base):
        return False

    return True


def fetch_mexc_spot_tradable_whitelist():
    """
    بناء القائمة البيضاء الصارمة لعملات Spot فقط على منصة MEXC:
    1. استدعاء /api/v3/defaultSymbols الذي يُرجع حصرياً أزواج Spot المسموح تداولها عبر الـ API.
    2. مطابقتها مع /api/v3/exchangeInfo للتأكد من صلاحية التداول الفوري وأوامر السوق.
    """
    now = time.time()
    if SPOT_WHITELIST_CACHE["symbols"] and (now - SPOT_WHITELIST_CACHE["timestamp"] < 900):
        return SPOT_WHITELIST_CACHE["symbols"] - BLACKLISTED_SYMBOLS

    default_api_symbols = set()
    for _ in range(2):
        try:
            res_def = requests.get("https://api.mexc.com/api/v3/defaultSymbols", timeout=8)
            if res_def.status_code == 200:
                data_def = res_def.json().get("data", [])
                if isinstance(data_def, list):
                    default_api_symbols = {
                        str(s).upper() for s in data_def
                        if str(s).upper().endswith("USDT") and is_clean_spot_crypto_pair(str(s).upper())
                    }
                    break
        except Exception as e:
            print(f"تحذير في جلب defaultSymbols: {e}")
            time.sleep(0.5)

    verified_spot_symbols = set()
    try:
        res_info = requests.get("https://api.mexc.com/api/v3/exchangeInfo", timeout=20)
        if res_info.status_code == 200:
            symbols_list = res_info.json().get("symbols", [])
            for s_info in symbols_list:
                sym = str(s_info.get("symbol", "")).upper()
                if s_info.get("quoteAsset") != "USDT":
                    continue

                full_name = s_info.get("fullName", "")
                status_ok = str(s_info.get("status", "")).upper() in ("1", "ENABLED")
                spot_allowed = bool(s_info.get("isSpotTradingAllowed", True))
                order_types = [str(ot).upper() for ot in s_info.get("orderTypes", [])]
                market_order_ok = ("MARKET" in order_types) if order_types else True
                permissions = [str(p).upper() for p in s_info.get("permissions", [])]
                perm_ok = ("SPOT" in permissions) if permissions else True

                SYMBOL_RULES_CACHE[sym] = {
                    "baseSizePrecision": s_info.get("baseSizePrecision"),
                    "baseAssetPrecision": int(s_info.get("baseAssetPrecision", 4)),
                    "quoteAmountPrecisionMarket": s_info.get("quoteAmountPrecisionMarket"),
                    "isSpotTradingAllowed": spot_allowed,
                    "marketOrderAllowed": market_order_ok,
                    "statusOk": status_ok,
                    "fullName": full_name,
                }

                if status_ok and spot_allowed and market_order_ok and perm_ok:
                    if not default_api_symbols or sym in default_api_symbols:
                        if is_clean_spot_crypto_pair(sym, full_name=full_name):
                            verified_spot_symbols.add(sym)
    except Exception as e:
        print(f"تحذير في جلب exchangeInfo الكامل: {e}")

    final_whitelist = verified_spot_symbols if verified_spot_symbols else default_api_symbols
    if final_whitelist:
        SPOT_WHITELIST_CACHE["symbols"] = set(final_whitelist)
        SPOT_WHITELIST_CACHE["timestamp"] = now

    return final_whitelist - BLACKLISTED_SYMBOLS


def verify_symbol_tradable_now(symbol):
    """
    فحص فوري مباشر للزوج عبر /api/v3/exchangeInfo?symbol=... ودفتر أوامر Spot
    للتأكد من أنه زوج Spot نشط ويقبل أوامر MARKET قبل الشراء.
    """
    formatted = symbol.replace("/", "").upper()
    if not is_clean_spot_crypto_pair(formatted):
        return False, "الزوج مستبعد (ليس عملة Spot قياسية)"

    try:
        url_info = f"https://api.mexc.com/api/v3/exchangeInfo?symbol={formatted}"
        res_info = requests.get(url_info, timeout=4)
        if res_info.status_code != 200:
            return False, f"فشل التحقق من exchangeInfo (HTTP {res_info.status_code})"
        syms = res_info.json().get("symbols", [])
        if not syms:
            add_symbol_to_blacklist(formatted, "غير موجود في exchangeInfo")
            return False, "الزوج غير مدرج في سوق Spot"
        s_info = syms[0]
        full_name = s_info.get("fullName", "")
        status_ok = str(s_info.get("status", "")).upper() in ("1", "ENABLED")
        spot_allowed = bool(s_info.get("isSpotTradingAllowed", True))
        order_types = [str(ot).upper() for ot in s_info.get("orderTypes", [])]
        market_ok = ("MARKET" in order_types) if order_types else True
        permissions = [str(p).upper() for p in s_info.get("permissions", [])]
        perm_ok = ("SPOT" in permissions) if permissions else True

        SYMBOL_RULES_CACHE[formatted] = {
            "baseSizePrecision": s_info.get("baseSizePrecision"),
            "baseAssetPrecision": int(s_info.get("baseAssetPrecision", 4)),
            "quoteAmountPrecisionMarket": s_info.get("quoteAmountPrecisionMarket"),
            "isSpotTradingAllowed": spot_allowed,
            "marketOrderAllowed": market_ok,
            "statusOk": status_ok,
            "fullName": full_name,
        }

        if not is_clean_spot_crypto_pair(formatted, full_name=full_name):
            add_symbol_to_blacklist(formatted, f"سهم مرمّز أو غير مدعوم ({full_name})")
            return False, f"أصل غير مدعوم للسكالبينج الفوري ({full_name})"

        if not (status_ok and spot_allowed and market_ok and perm_ok):
            add_symbol_to_blacklist(formatted, "مقيد في exchangeInfo ولا يدعم Market Spot API")
            return False, "الزوج لا يدعم أوامر السوق المباشرة عبر Spot API"

        url_depth = f"https://api.mexc.com/api/v3/depth?symbol={formatted}&limit=5"
        res_depth = requests.get(url_depth, timeout=3)
        if res_depth.status_code == 200:
            depth = res_depth.json()
            bids = depth.get("bids", [])
            asks = depth.get("asks", [])
            if not bids or not asks:
                add_symbol_to_blacklist(formatted, "دفتر أوامر Spot فارغ")
                return False, "لا توجد سيولة في دفتر أوامر Spot"
            best_bid = float(bids[0][0])
            best_ask = float(asks[0][0])
            if best_bid > 0:
                spread_pct = ((best_ask - best_bid) / best_bid) * 100
                if spread_pct > 1.2:
                    return False, f"الفارق السعري (Spread) مرتفع ({spread_pct:.2f}%)"
    except Exception as e:
        return False, f"تعذر التحقق من جاهزية الزوج: {e}"

    return True, "متاح للتداول الفوري"


def get_top_200_symbols():
    """
    جلب أعلى 200 عملة سيولة من عملات Spot المعتمدة في fetch_mexc_spot_tradable_whitelist.
    """
    whitelist = fetch_mexc_spot_tradable_whitelist()
    if not whitelist:
        return []

    try:
        url = "https://api.mexc.com/api/v3/ticker/24hr"
        response = requests.get(url, timeout=8)
        if response.status_code == 200:
            data = response.json()
            usdt_pairs = []
            for item in data:
                sym = str(item.get("symbol", "")).upper()
                if sym not in whitelist:
                    continue
                quote_vol = float(item.get("quoteVolume", 0) or 0)
                bid_p = float(item.get("bidPrice", 0) or 0)
                ask_p = float(item.get("askPrice", 0) or 0)
                if quote_vol >= 50000 and bid_p > 0 and ask_p > 0:
                    usdt_pairs.append(item)

            usdt_pairs.sort(key=lambda x: float(x.get("quoteVolume", 0)), reverse=True)
            top_list = [item["symbol"] for item in usdt_pairs[:200]]
            if top_list:
                return top_list
    except Exception as e:
        print(f"خطأ في ترتيب السيولة: {e}")

    return sorted(list(whitelist))[:200]


def get_mexc_real_price(symbol):
    try:
        formatted_symbol = symbol.replace("/", "").upper()
        url = f"https://api.mexc.com/api/v3/ticker/price?symbol={formatted_symbol}"
        response = requests.get(url, timeout=3)
        if response.status_code == 200:
            return float(response.json()["price"])
    except Exception as e:
        print(f"خطأ السعر: {e}")
    return None


def get_symbol_step_rules(symbol):
    """جلب دقة الكمية المسموحة (stepSize / baseAssetPrecision) من منصة MEXC وتخزينها مؤقتاً."""
    formatted_symbol = symbol.replace("/", "").upper()
    if formatted_symbol in SYMBOL_RULES_CACHE and SYMBOL_RULES_CACHE[formatted_symbol].get("baseSizePrecision") is not None:
        return SYMBOL_RULES_CACHE[formatted_symbol]

    try:
        url = f"https://api.mexc.com/api/v3/exchangeInfo?symbol={formatted_symbol}"
        res = requests.get(url, timeout=4)
        if res.status_code == 200:
            data = res.json()
            symbols_list = data.get("symbols", [])
            if symbols_list:
                s_info = symbols_list[0]
                base_size_precision = s_info.get("baseSizePrecision")
                base_asset_precision = s_info.get("baseAssetPrecision", 4)
                rules = {
                    "baseSizePrecision": base_size_precision,
                    "baseAssetPrecision": int(base_asset_precision)
                }
                SYMBOL_RULES_CACHE[formatted_symbol] = rules
                return rules
    except Exception as e:
        print(f"خطأ في جلب إعدادات الزوج {formatted_symbol}: {e}")

    return {"baseSizePrecision": None, "baseAssetPrecision": 4}


def format_quantity_for_symbol(symbol, quantity):
    """قص الكمية (Round Down) لتطابق شروط المنصة دون زيادة عن الرصيد الفعلي."""
    rules = get_symbol_step_rules(symbol)
    qty_dec = Decimal(str(max(0.0, float(quantity))))

    step_str = rules.get("baseSizePrecision")
    if step_str and str(step_str) not in ("0", "0.0"):
        try:
            step_dec = Decimal(str(step_str))
            if step_dec > 0:
                clipped = (qty_dec // step_dec) * step_dec
                return format(clipped.normalize(), "f")
        except Exception:
            pass

    precision = int(rules.get("baseAssetPrecision", 4))
    quantizer = Decimal("1") if precision <= 0 else Decimal("1." + ("0" * precision))
    clipped = qty_dec.quantize(quantizer, rounding=ROUND_DOWN)
    return format(clipped, "f")


def get_symbol_free_balance(symbol, api_key, secret_key):
    """إرجاع الرصيد المتاح كـ float، أو None في حال حدوث خطأ في الشبكة/API لتجنب الإغلاق الوهمي."""
    if not api_key or not secret_key:
        return None
    try:
        asset_name = symbol.replace("USDT", "").replace("/", "").upper()
        url_bal = "https://api.mexc.com/api/v3/account"
        ts = get_mexc_timestamp()
        p_bal = {"timestamp": ts, "recvWindow": 60000}
        q_bal = urlencode(p_bal)
        sig_bal = hmac.new(secret_key.encode("utf-8"), q_bal.encode("utf-8"), hashlib.sha256).hexdigest()
        p_bal["signature"] = sig_bal

        res_bal = requests.get(url_bal, headers={"X-MEXC-APIKEY": api_key}, params=p_bal, timeout=5)
        if res_bal.status_code == 200:
            balances = res_bal.json().get("balances", [])
            for b in balances:
                if b.get("asset") == asset_name:
                    return float(b.get("free", 0.0))
            return 0.0
        else:
            print(f"تحذير في استعلام الرصيد ({res_bal.status_code}): {res_bal.text}")
            return None
    except Exception as e:
        print(f"خطأ الرصيد: {e}")
        return None


def extract_execution_details(order_response):
    """استخراج الكمية والقيمة المنفذتين مع دعم أسماء الحقول البديلة في ردود المنصة."""
    if not isinstance(order_response, dict):
        return 0.0, 0.0

    def number(*keys):
        for key in keys:
            value = order_response.get(key)
            try:
                parsed = float(value)
                if parsed > 0 and math.isfinite(parsed):
                    return parsed
            except (TypeError, ValueError):
                continue
        return 0.0

    qty = number("executedQty", "dealSize", "filledQty", "executed_quantity", "origQty")
    quote = number("cummulativeQuoteQty", "cumulativeQuoteQty", "dealFunds", "quoteQty", "executedQuoteQty", "origQuoteOrderQty")

    if quote <= 0 and qty > 0:
        avg_price = number("avgPrice", "averagePrice", "price")
        if avg_price > 0:
            quote = qty * avg_price
    if (qty <= 0 or quote <= 0) and isinstance(order_response.get("fills"), list):
        fill_qty = 0.0
        fill_quote = 0.0
        for fill in order_response["fills"]:
            try:
                fq = float(fill.get("qty", fill.get("quantity", 0)) or 0)
                fp = float(fill.get("price", 0) or 0)
                if fq > 0 and fp > 0:
                    fill_qty += fq
                    fill_quote += fq * fp
            except (TypeError, ValueError, AttributeError):
                continue
        if fill_qty > 0 and fill_quote > 0:
            qty, quote = fill_qty, fill_quote
    return qty, quote


def extract_execution_price(order_response, symbol):
    """حساب متوسط سعر التنفيذ الفعلي من بيانات تنفيذ الأمر."""
    try:
        qty, quote = extract_execution_details(order_response)
        return quote / qty if qty > 0 and quote > 0 else 0.0
    except (TypeError, ValueError, ZeroDivisionError):
        return 0.0


def get_mexc_order_details(symbol, order_id, api_key, secret_key):
    """قراءة حالة الأمر من MEXC باستخدام orderId بعد قبول الأمر."""
    url = "https://api.mexc.com/api/v3/order"
    params = {
        "symbol": symbol.replace("/", "").upper(),
        "orderId": str(order_id),
        "recvWindow": 60000,
        "timestamp": get_mexc_timestamp(),
    }
    query_string = urlencode(params)
    params["signature"] = hmac.new(
        secret_key.encode("utf-8"), query_string.encode("utf-8"), hashlib.sha256
    ).hexdigest()
    response = requests.get(
        url, headers={"X-MEXC-APIKEY": api_key}, params=params, timeout=8
    )
    try:
        data = response.json()
    except ValueError:
        data = {}
    if response.status_code != 200 or not isinstance(data, dict) or "orderId" not in data:
        raise RuntimeError(
            f"تعذر الاستعلام عن الأمر {order_id} (HTTP {response.status_code}): "
            f"{data or response.text[:300]}"
        )
    return data


def place_mexc_buy_order(symbol, amount_usd, api_key, secret_key):
    if not api_key or not secret_key:
        return False, "لم يتم إدخال مفاتيح API", 0.0, 0.0

    formatted_symbol = symbol.replace("/", "").upper()
    tradable_ok, reason_msg = verify_symbol_tradable_now(formatted_symbol)
    if not tradable_ok:
        return False, reason_msg, 0.0, 0.0

    pre_free_qty = get_symbol_free_balance(formatted_symbol, api_key, secret_key)

    url = "https://api.mexc.com/api/v3/order"
    formatted_amount = f"{float(amount_usd):.2f}"
    params = {
        "symbol": formatted_symbol,
        "side": "BUY",
        "type": "MARKET",
        "quoteOrderQty": formatted_amount,
        "recvWindow": 60000,
        "timestamp": get_mexc_timestamp(),
    }
    query_string = urlencode(params)
    params["signature"] = hmac.new(
        secret_key.encode("utf-8"), query_string.encode("utf-8"), hashlib.sha256
    ).hexdigest()
    headers = {"X-MEXC-APIKEY": api_key, "Content-Type": "application/json"}

    try:
        response = requests.post(url, headers=headers, params=params, timeout=15)
    except requests.exceptions.RequestException as exc:
        return False, (
            f"نتيجة الأمر غير مؤكدة — لا تعاود الشراء قبل مراجعة سجل MEXC. السبب: {exc}"
        ), 0.0, 0.0

    try:
        res_data = response.json()
    except ValueError:
        res_data = {}

    if response.status_code in (200, 201) and isinstance(res_data, dict) and res_data.get("orderId"):
        order_id = str(res_data["orderId"])
        qty, quote = extract_execution_details(res_data)

        if qty <= 0 or quote <= 0:
            for attempt in range(4):
                try:
                    details = get_mexc_order_details(
                        formatted_symbol, order_id, api_key, secret_key
                    )
                    qty, quote = extract_execution_details(details)
                    if qty > 0 and quote > 0:
                        break
                except Exception:
                    pass
                if attempt < 3:
                    time.sleep(0.6)

        if qty <= 0 or quote <= 0:
            post_free_qty = get_symbol_free_balance(formatted_symbol, api_key, secret_key)
            live_price = get_mexc_real_price(formatted_symbol)
            if post_free_qty is not None and pre_free_qty is not None and post_free_qty > pre_free_qty and live_price:
                qty = post_free_qty - pre_free_qty
                quote = float(amount_usd)

        if qty > 0 and quote > 0:
            real_price = quote / qty
            return True, f"Order ID: {order_id} | تم تأكيد التنفيذ", real_price, qty

        return False, (
            f"نتيجة الأمر غير مؤكدة — MEXC قبل الأمر رقم {order_id} لكن لم نتمكن من "
            f"استخراج كمية التنفيذ. راجع سجل الأوامر قبل أي شراء جديد."
        ), 0.0, 0.0

    if response.status_code >= 500 or response.status_code in (200, 201):
        return False, (
            f"نتيجة الأمر غير مؤكدة — رد MEXC غير حاسم (HTTP {response.status_code}). "
            "راجع سجل الأوامر قبل إعادة الشراء."
        ), 0.0, 0.0

    err_msg = (
        res_data.get("msg", str(res_data) if res_data else response.text[:300])
        if isinstance(res_data, dict)
        else response.text[:300]
    )
    lower_err = str(err_msg).lower()
    if any(k in lower_err for k in ("can't place orders", "not support", "symbol is not open", "invalid symbol", "disabled", "assessment")):
        add_symbol_to_blacklist(formatted_symbol, err_msg)
    return False, err_msg, 0.0, 0.0


def place_mexc_sell_order_market(symbol, api_key, secret_key, sell_ratio=1.0, position_quantity=None):
    """
    بيع كامل (sell_ratio=1.0) أو جزئي (مثلاً sell_ratio=0.5) بسعر السوق.
    يرجع: (success, message, execution_price, executed_quantity)
    """
    try:
        formatted_symbol = symbol.replace("/", "").upper()
        free_qty = get_symbol_free_balance(formatted_symbol, api_key, secret_key)
        if free_qty is None:
            return False, "تعذر التحقق من الرصيد بسبب خطأ في الاتصال", None, 0.0
        if free_qty <= 0:
            return False, "الرصيد المتاح صفر؛ إذا أغلقت الصفقة من المنصة اضغط زر (إغلاق إجباري).", None, 0.0

        if position_quantity is not None and float(position_quantity) > 0:
            base_available = min(free_qty, float(position_quantity))
        else:
            base_available = free_qty

        target_qty = base_available * float(sell_ratio)
        formatted_qty = format_quantity_for_symbol(formatted_symbol, target_qty)
        if Decimal(formatted_qty) <= 0:
            if sell_ratio < 1.0:
                return False, "الكمية الجزئية أقل من الحد الأدنى المسموح للبيع", None, 0.0
            return False, "الكمية المتبقية أقل من الحد الأدنى للتداول (Dust)؛ يمكنك استخدام زر (إغلاق إجباري).", None, 0.0

        url_order = "https://api.mexc.com/api/v3/order"
        p_order = {
            "symbol": formatted_symbol,
            "side": "SELL",
            "type": "MARKET",
            "quantity": formatted_qty,
            "recvWindow": 60000,
            "timestamp": get_mexc_timestamp()
        }
        q_order = urlencode(p_order)
        sig_order = hmac.new(secret_key.encode("utf-8"), q_order.encode("utf-8"), hashlib.sha256).hexdigest()
        p_order["signature"] = sig_order

        res_sell = requests.post(url_order, headers={"X-MEXC-APIKEY": api_key}, params=p_order, timeout=10)
        try:
            res_data = res_sell.json()
        except ValueError:
            res_data = {}

        if res_sell.status_code in (200, 201) and isinstance(res_data, dict) and "orderId" in res_data:
            order_id = str(res_data["orderId"])
            executed_qty, quote_qty = extract_execution_details(res_data)

            if executed_qty <= 0 or quote_qty <= 0:
                for attempt in range(4):
                    try:
                        details = get_mexc_order_details(formatted_symbol, order_id, api_key, secret_key)
                        executed_qty, quote_qty = extract_execution_details(details)
                        if executed_qty > 0 and quote_qty > 0:
                            break
                    except Exception:
                        pass
                    if attempt < 3:
                        time.sleep(0.5)

            if executed_qty <= 0:
                executed_qty = float(formatted_qty)
            if quote_qty <= 0:
                fallback_price = get_mexc_real_price(formatted_symbol)
                if fallback_price and fallback_price > 0:
                    quote_qty = executed_qty * fallback_price

            exit_price = (quote_qty / executed_qty) if (executed_qty > 0 and quote_qty > 0) else get_mexc_real_price(formatted_symbol)
            return True, f"تم تنفيذ بيع {executed_qty:g} بسعر السوق (Order ID: {order_id})", exit_price, executed_qty
        else:
            err_msg = res_data.get("msg", "فشل الإغلاق") if isinstance(res_data, dict) else res_sell.text[:200]
            return False, err_msg, None, 0.0

    except Exception as e:
        return False, str(e), None, 0.0


# --- المؤشرات الفنية والشروط (EMA + RSI + MACD + VWAP + OrderBook) ---
def calculate_ema_series(data, period):
    if len(data) < period:
        return []
    ema = []
    multiplier = 2 / (period + 1)
    sma = sum(data[:period]) / period
    ema.append(sma)
    for price in data[period:]:
        ema.append((price - ema[-1]) * multiplier + ema[-1])
    return ema


def calculate_rsi_series(closes, period=14):
    """حساب مؤشر القوة النسبية RSI وفق معادلة Wilder القياسية."""
    if len(closes) < period + 1:
        return []

    gains = []
    losses = []
    for i in range(1, len(closes)):
        delta = closes[i] - closes[i - 1]
        gains.append(max(delta, 0.0))
        losses.append(max(-delta, 0.0))

    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period

    rsi_values = []
    if avg_loss == 0:
        rsi_values.append(100.0)
    else:
        rs = avg_gain / avg_loss
        rsi_values.append(100.0 - (100.0 / (1.0 + rs)))

    for i in range(period, len(gains)):
        avg_gain = ((avg_gain * (period - 1)) + gains[i]) / period
        avg_loss = ((avg_loss * (period - 1)) + losses[i]) / period
        if avg_loss == 0:
            rsi_values.append(100.0)
        else:
            rs = avg_gain / avg_loss
            rsi_values.append(100.0 - (100.0 / (1.0 + rs)))

    return rsi_values


def calculate_macd_series(closes, fast_period=12, slow_period=26, signal_period=9):
    """
    حساب مؤشر MACD القياسي (12, 26, 9):
    يُرجع: (macd_line, signal_line, histogram_series)
    """
    if len(closes) < slow_period + signal_period:
        return [], [], []

    ema_fast = calculate_ema_series(closes, fast_period)
    ema_slow = calculate_ema_series(closes, slow_period)
    if not ema_fast or not ema_slow:
        return [], [], []

    offset = slow_period - fast_period
    aligned_fast = ema_fast[offset:]
    macd_line = [f - s for f, s in zip(aligned_fast, ema_slow)]

    signal_line = calculate_ema_series(macd_line, signal_period)
    if not signal_line:
        return [], [], []

    sig_offset = signal_period - 1
    aligned_macd = macd_line[sig_offset:]
    histogram = [m - s for m, s in zip(aligned_macd, signal_line)]
    return aligned_macd, signal_line, histogram


def fetch_klines(formatted_symbol, interval, limit=500):
    try:
        url = f"https://api.mexc.com/api/v3/klines?symbol={formatted_symbol}&interval={interval}&limit={limit}"
        response = requests.get(url, timeout=4)
        if response.status_code == 200:
            data = response.json()
            return data if isinstance(data, list) else None
    except Exception:
        pass
    return None


def check_ema200_trend(formatted_symbol, interval):
    """تأكيد اتجاه صاعد على إطار أعلى باستخدام آخر شمعة مغلقة وترتيب EMA9/21/200."""
    klines = fetch_klines(formatted_symbol, interval, limit=300)
    if not klines or len(klines) < 202:
        return False
    closed = klines[:-1]
    closes = [float(k[4]) for k in closed]
    e9 = calculate_ema_series(closes, 9)
    e21 = calculate_ema_series(closes, 21)
    e200 = calculate_ema_series(closes, 200)
    if not e9 or not e21 or not e200:
        return False
    return closes[-1] > e200[-1] and e9[-1] > e21[-1] > e200[-1]


def check_order_book_pressure(symbol, depth_limit=20):
    """
    مقارنة القيمة الإجمالية لأوامر البيع والشراء في دفتر MEXC.
    يعيد (allowed, message). عند تعذر قراءة الدفتر يمنع الدخول احتياطياً.
    """
    formatted = symbol.replace("/", "").upper()
    try:
        response = requests.get(
            "https://api.mexc.com/api/v3/depth",
            params={"symbol": formatted, "limit": int(depth_limit)},
            timeout=4,
        )
        if response.status_code != 200:
            return False, f"تعذر قراءة دفتر الأوامر (HTTP {response.status_code})"

        book = response.json()
        bids = book.get("bids") or []
        asks = book.get("asks") or []
        if not bids or not asks:
            return False, "دفتر الأوامر فارغ أو غير مكتمل"

        buy_value = sum(float(level[0]) * float(level[1]) for level in bids)
        sell_value = sum(float(level[0]) * float(level[1]) for level in asks)

        if buy_value <= 0 or sell_value <= 0:
            return False, "قيم دفتر الأوامر غير صالحة"

        if sell_value > buy_value:
            ratio = sell_value / buy_value
            return False, (
                f"ضغط بيع أعلى من الشراء: بيع ${sell_value:,.2f} مقابل "
                f"شراء ${buy_value:,.2f} (نسبة {ratio:.2f}x)"
            )

        ratio = buy_value / sell_value
        return True, (
            f"ضغط الشراء مناسب: شراء ${buy_value:,.2f} مقابل "
            f"بيع ${sell_value:,.2f} (نسبة {ratio:.2f}x)"
        )
    except (ValueError, TypeError, IndexError, KeyError) as exc:
        return False, f"بيانات دفتر الأوامر غير صالحة: {exc}"
    except Exception as exc:
        return False, f"تعذر فحص ضغط دفتر الأوامر: {exc}"


def check_trade_conditions_from_main(
    symbol, vol_multiplier=1.2, enable_low_price_change_filter=True,
    max_price_change_pct=0.60, enable_order_book_filter=True,
    enable_rsi_filter=True, rsi_min=45.0, rsi_max=70.0,
    enable_macd_filter=True
):
    """إشارة شراء من شموع 5m المغلقة مع تأكيد EMA9>EMA21>EMA200 و RSI و MACD و VWAP ودفتر الأوامر."""
    try:
        formatted_symbol = symbol.replace("/", "").upper()
        if formatted_symbol in BLACKLISTED_SYMBOLS or not is_clean_spot_crypto_pair(formatted_symbol):
            return False, 0.0, "الزوج مستبعد"
        klines = fetch_klines(formatted_symbol, "5m", limit=300)
        if not klines or len(klines) < 202:
            return False, 0.0, "تعذر جلب البيانات الكافية"
        closed = klines[:-1]
        opens = [float(k[1]) for k in closed]
        highs = [float(k[2]) for k in closed]
        lows = [float(k[3]) for k in closed]
        closes = [float(k[4]) for k in closed]
        volumes = [float(k[5]) for k in closed]
        price = closes[-1]
        e9 = calculate_ema_series(closes, 9)
        e21 = calculate_ema_series(closes, 21)
        e200 = calculate_ema_series(closes, 200)
        if not (e9 and e21 and e200):
            return False, price, "بيانات EMA غير كافية"
        if not (e9[-1] > e21[-1] > e200[-1] and price > e9[-1]):
            return False, price, "ترتيب EMA9/EMA21/EMA200 غير صاعد على 5m"

        # فحص مؤشر RSI (14) إذا كان مفعلاً من الواجهة
        rsi_tag = ""
        if enable_rsi_filter:
            rsi_series = calculate_rsi_series(closes, period=14)
            if not rsi_series:
                return False, price, "بيانات RSI غير كافية"
            curr_rsi = rsi_series[-1]
            low_bound = min(float(rsi_min), float(rsi_max))
            high_bound = max(float(rsi_min), float(rsi_max))
            if curr_rsi < low_bound:
                return False, price, f"زخم RSI ضعيف ({curr_rsi:.1f} < {low_bound:.0f})"
            if curr_rsi > high_bound:
                return False, price, f"تشبع شرائي في RSI ({curr_rsi:.1f} > {high_bound:.0f})"
            rsi_tag = f" | RSI={curr_rsi:.1f}"

        # فحص مؤشر MACD (12, 26, 9) إذا كان مفعلاً من الواجهة
        macd_tag = ""
        if enable_macd_filter:
            macd_line, sig_line, hist = calculate_macd_series(closes, 12, 26, 9)
            if not hist or len(hist) < 2:
                return False, price, "بيانات MACD غير كافية"
            if not (macd_line[-1] > sig_line[-1] and hist[-1] > 0):
                return False, price, f"إشارة MACD سلبية (MACD={macd_line[-1]:.6f} <= Signal={sig_line[-1]:.6f})"
            macd_tag = " | MACD صاعد"

        total_vol = sum(volumes[-20:])
        vwap = (
            sum(((highs[i] + lows[i] + closes[i]) / 3) * volumes[i] for i in range(-20, 0)) / total_vol
            if total_vol > 0 else price
        )
        avg_vol = sum(volumes[-10:-1]) / 9
        if avg_vol <= 0 or volumes[-1] <= avg_vol * float(vol_multiplier):
            return False, price, f"الفوليوم ضعيف (< {vol_multiplier}x من المتوسط)"
        op = opens[-1]
        prev = closes[-2]
        body_pct = abs(price - op) / op * 100 if op > 0 else 999
        close_pct = abs(price - prev) / prev * 100 if prev > 0 else 999
        range_pct = (highs[-1] - lows[-1]) / lows[-1] * 100 if lows[-1] > 0 else 999
        effective_pct = max(body_pct, close_pct)
        if enable_low_price_change_filter:
            limit_pct = abs(float(max_price_change_pct))
            if effective_pct > limit_pct or range_pct > limit_pct * 1.6:
                return False, price, f"التغير السعري كبير ({effective_pct:.2f}%)"
        if price < vwap:
            return False, price, "السعر دون VWAP"
        if not check_ema200_trend(formatted_symbol, "15m"):
            return False, price, "تأكيد EMA غير صاعد على 15m"
        if not check_ema200_trend(formatted_symbol, "60m"):
            return False, price, "تأكيد EMA غير صاعد على 1h"
        if enable_order_book_filter:
            book_ok, book_msg = check_order_book_pressure(formatted_symbol, depth_limit=20)
            if not book_ok:
                return False, price, book_msg
        tradable, reason = verify_symbol_tradable_now(formatted_symbol)
        if not tradable:
            return False, price, reason
        return True, price, f"EMA صاعد + VWAP + فوليوم {volumes[-1]/avg_vol:.2f}x{rsi_tag}{macd_tag}"
    except Exception as e:
        return False, 0.0, f"خطأ: {e}"


# --- خيوط المعالجة الخلفية المتوافقة مع Kivy Clock ---
class MainScannerThread(threading.Thread):
    def __init__(
        self,
        log_callback,
        opportunity_callback,
        scan_failed_callback,
        start_offset=0,
        vol_multiplier=1.2,
        enable_low_price_change_filter=True,
        max_price_change_pct=0.60,
        enable_order_book_filter=True,
        enable_rsi_filter=True,
        rsi_min=45.0,
        rsi_max=70.0,
        enable_macd_filter=True,
    ):
        super().__init__(daemon=True)
        self.log_callback = log_callback
        self.opportunity_callback = opportunity_callback
        self.scan_failed_callback = scan_failed_callback
        self.is_running = True
        self.start_offset = start_offset
        self.last_scanned_index = 0
        self.vol_multiplier = float(vol_multiplier)
        self.enable_low_price_change_filter = bool(enable_low_price_change_filter)
        self.max_price_change_pct = float(max_price_change_pct)
        self.enable_order_book_filter = bool(enable_order_book_filter)
        self.enable_rsi_filter = bool(enable_rsi_filter)
        self.rsi_min = float(rsi_min)
        self.rsi_max = float(rsi_max)
        self.enable_macd_filter = bool(enable_macd_filter)

    def emit_log(self, msg):
        if self.log_callback:
            Clock.schedule_once(lambda dt: self.log_callback(msg))

    def emit_opportunity(self, symbol, price):
        if self.opportunity_callback:
            Clock.schedule_once(lambda dt: self.opportunity_callback(symbol, price))

    def emit_scan_failed(self):
        if self.scan_failed_callback:
            Clock.schedule_once(lambda dt: self.scan_failed_callback())

    def run(self):
        self.emit_log("🔍 جاري جلب قائمة عملات Spot المعتمدة للتداول الفوري عبر الـ API من MEXC...")
        symbols = get_top_200_symbols()
        total = len(symbols)
        if not symbols:
            self.emit_log("⚠️ تعذر جلب قائمة عملات Spot، يرجى التحقق من اتصال الإنترنت والمحاولة مجدداً.")
            self.emit_scan_failed()
            return

        active_filters = [f"فوليوم > {self.vol_multiplier}x"]
        if self.enable_low_price_change_filter:
            active_filters.append(f"أقصى تغير <= {self.max_price_change_pct:.2f}%")
        if self.enable_rsi_filter:
            active_filters.append(f"RSI({self.rsi_min:.0f}-{self.rsi_max:.0f})")
        if self.enable_macd_filter:
            active_filters.append("MACD صاعد")
        if self.enable_order_book_filter:
            active_filters.append("دفتر الأوامر")

        self.emit_log(f"✅ تم جلب {total} عملة Spot نشطة ({' | '.join(active_filters)}).")
        offset = self.start_offset % total if total > 0 else 0

        while self.is_running:
            ordered_symbols = symbols[offset:] + symbols[:offset]
            for idx, symbol in enumerate(ordered_symbols, 1):
                if not self.is_running:
                    break
                if symbol in BLACKLISTED_SYMBOLS:
                    continue

                self.last_scanned_index = (offset + idx) % total
                is_valid, price, msg = check_trade_conditions_from_main(
                    symbol,
                    vol_multiplier=self.vol_multiplier,
                    enable_low_price_change_filter=self.enable_low_price_change_filter,
                    max_price_change_pct=self.max_price_change_pct,
                    enable_order_book_filter=self.enable_order_book_filter,
                    enable_rsi_filter=self.enable_rsi_filter,
                    rsi_min=self.rsi_min,
                    rsi_max=self.rsi_max,
                    enable_macd_filter=self.enable_macd_filter,
                )
                if is_valid:
                    self.emit_log(f"[🎯 اقتناص مؤكد!] {symbol} | السعر: ${format_price(price)} | {msg}")
                    self.emit_opportunity(symbol, price)
                    self.is_running = False
                    return
                else:
                    self.emit_log(f"[{idx}/{total}] [{symbol}] {msg}")

                time.sleep(0.15)
            offset = 0

    def stop(self):
        self.is_running = False


class RealLiveTickerThread(threading.Thread):
    def __init__(
        self,
        price_update_callback,
        partial_tp_callback,
        auto_close_callback,
        log_event_callback,
        symbol, entry_price, amount_usd, api_key, secret_key,
        tp_percent=2.0, sl_percent=0.8, trade_mode="LIVE", base_qty=0.0,
        enable_be=True, be_trigger_pct=0.7, be_offset_pct=0.15,
        enable_trailing=True, trail_activate_pct=1.1, trail_distance_pct=0.4,
        enable_partial_tp=True, tp1_percent=1.0, tp1_sell_ratio=0.5,
        highest_price=None, dynamic_sl_percent=None, tp1_done=False, be_active=False, trailing_active=False
    ):
        super().__init__(daemon=True)
        self.price_update_callback = price_update_callback
        self.partial_tp_callback = partial_tp_callback
        self.auto_close_callback = auto_close_callback
        self.log_event_callback = log_event_callback

        self.symbol = symbol
        self.entry_price = float(entry_price)
        self.amount_usd = float(amount_usd)
        self.base_qty = float(base_qty or 0.0)
        self.api_key = api_key
        self.secret_key = secret_key
        self.trade_mode = str(trade_mode or "LIVE").upper()

        self.tp_percent = abs(float(tp_percent))
        self.sl_percent = abs(float(sl_percent))

        self.enable_be = bool(enable_be)
        self.be_trigger_pct = abs(float(be_trigger_pct))
        self.be_offset_pct = abs(float(be_offset_pct))

        self.enable_trailing = bool(enable_trailing)
        self.trail_activate_pct = abs(float(trail_activate_pct))
        self.trail_distance_pct = abs(float(trail_distance_pct))

        self.enable_partial_tp = bool(enable_partial_tp)
        self.tp1_percent = abs(float(tp1_percent))
        self.tp1_sell_ratio = max(0.1, min(0.9, float(tp1_sell_ratio)))

        self.highest_price = float(highest_price) if highest_price and highest_price > self.entry_price else self.entry_price
        self.dynamic_sl_percent = float(dynamic_sl_percent) if dynamic_sl_percent is not None else -self.sl_percent
        self.tp1_done = bool(tp1_done)
        self.tp1_pending = False
        self.tp1_retry_after = 0.0
        self.be_active = bool(be_active)
        self.trailing_active = bool(trailing_active)

        self.is_running = True
        self.zero_balance_strikes = 0
        self.has_seen_positive_balance = False
        self.thread_start_time = time.time()

    def emit_log(self, msg):
        if self.log_event_callback:
            Clock.schedule_once(lambda dt: self.log_event_callback(msg))

    def emit_price_update(self, cp, pu, pp, hp, dsl, st):
        if self.price_update_callback:
            Clock.schedule_once(lambda dt: self.price_update_callback(cp, pu, pp, hp, dsl, st))

    def emit_partial_tp(self, ratio, cp):
        if self.partial_tp_callback:
            Clock.schedule_once(lambda dt: self.partial_tp_callback(ratio, cp))

    def emit_auto_close(self, reason):
        if self.auto_close_callback:
            Clock.schedule_once(lambda dt: self.auto_close_callback(reason))

    def get_protection_status_label(self):
        if self.trailing_active:
            return f"🚀 ملاحقة ربح ({self.dynamic_sl_percent:+.2f}%)"
        if self.be_active:
            return f"🔒 مؤمّنة BE ({self.dynamic_sl_percent:+.2f}%)"
        return f"✔ وقف ثابت ({self.dynamic_sl_percent:+.2f}%)"

    def run(self):
        while self.is_running:
            current_price = get_mexc_real_price(self.symbol)
            if current_price and self.entry_price > 0:
                state_changed = False

                if current_price > self.highest_price:
                    self.highest_price = current_price
                    state_changed = True

                pnl_percent = ((current_price - self.entry_price) / self.entry_price) * 100
                peak_pnl_percent = ((self.highest_price - self.entry_price) / self.entry_price) * 100
                pnl_usd = self.amount_usd * (pnl_percent / 100)

                if self.enable_be and not self.be_active and peak_pnl_percent >= self.be_trigger_pct:
                    if self.dynamic_sl_percent < self.be_offset_pct:
                        self.dynamic_sl_percent = self.be_offset_pct
                    self.be_active = True
                    state_changed = True
                    self.emit_log(
                        f"[🔒 تأمين الصفقة Break-Even] {self.symbol} وصل ربحها إلى +{peak_pnl_percent:.2f}% -> "
                        f"تم رفع وقف الخسارة إلى نقطة الدخول (+{self.dynamic_sl_percent:.2f}%)"
                    )

                if self.enable_trailing and peak_pnl_percent >= self.trail_activate_pct:
                    candidate_sl_pct = peak_pnl_percent - self.trail_distance_pct
                    if not self.trailing_active:
                        self.trailing_active = True
                        state_changed = True
                        self.emit_log(
                            f"[🚀 تفعيل الملاحقة Trailing Stop] {self.symbol} تجاوزت +{self.trail_activate_pct:.2f}% -> "
                            f"ملاحقة القمة بمسافة {self.trail_distance_pct:.2f}%"
                        )
                    if candidate_sl_pct > self.dynamic_sl_percent:
                        self.dynamic_sl_percent = candidate_sl_pct
                        state_changed = True

                if (
                    self.enable_partial_tp
                    and not self.tp1_done
                    and not self.tp1_pending
                    and time.time() >= self.tp1_retry_after
                    and pnl_percent >= self.tp1_percent
                ):
                    remaining_usd = self.amount_usd * (1.0 - self.tp1_sell_ratio)
                    if remaining_usd >= 2.0:
                        self.tp1_pending = True
                        self.emit_partial_tp(self.tp1_sell_ratio, current_price)
                    else:
                        self.tp1_done = True
                        self.emit_log(
                            f"[ℹ️ تخطي البيع الجزئي] مبلغ الصفقة (${self.amount_usd:.2f}) صغير للبيع الجزئي؛ "
                            f"تم تأمين الوقف عند +{self.be_offset_pct:.2f}% ومتابعة الهدف الكامل."
                        )
                        if self.dynamic_sl_percent < self.be_offset_pct:
                            self.dynamic_sl_percent = self.be_offset_pct
                            self.be_active = True
                        state_changed = True

                if state_changed:
                    update_active_position_state(
                        self.amount_usd, self.highest_price, self.dynamic_sl_percent,
                        self.tp1_done, self.be_active, self.trailing_active, self.base_qty
                    )

                status_text = self.get_protection_status_label()
                self.emit_price_update(
                    current_price, pnl_usd, pnl_percent,
                    self.highest_price, self.dynamic_sl_percent, status_text
                )

                if self.tp1_pending:
                    time.sleep(1.2)
                    continue

                if pnl_percent >= self.tp_percent:
                    self.emit_auto_close("TP_TARGET")
                    self.is_running = False
                    break

                if pnl_percent <= self.dynamic_sl_percent:
                    if self.trailing_active and self.dynamic_sl_percent > 0:
                        close_reason = "TRAILING_TP"
                    elif self.be_active and self.dynamic_sl_percent >= 0:
                        close_reason = "BREAK_EVEN"
                    else:
                        close_reason = "SL"
                    self.emit_auto_close(close_reason)
                    self.is_running = False
                    break

                elapsed = time.time() - self.thread_start_time
                free_qty = (
                    get_symbol_free_balance(self.symbol, self.api_key, self.secret_key)
                    if self.trade_mode == "LIVE" else None
                )
                if self.trade_mode == "LIVE" and free_qty is not None:
                    formatted_qty = format_quantity_for_symbol(self.symbol, free_qty)
                    if Decimal(formatted_qty) > 0:
                        self.has_seen_positive_balance = True
                        self.zero_balance_strikes = 0
                    elif self.has_seen_positive_balance or elapsed > 20.0:
                        self.zero_balance_strikes += 1
                        if self.zero_balance_strikes >= 3:
                            reason = "EXTERNAL_TP" if pnl_percent >= 0 else "EXTERNAL_SL"
                            self.emit_auto_close(reason)
                            self.is_running = False
                            break

            time.sleep(1.2)

    def stop(self):
        self.is_running = False


# --- مكونات واجهة Kivy المخصصة (الثيم الداكن الاحترافي) ---
class CardBox(BoxLayout):
    """حاوية بطاقة داكنة مع إطار بعنوان مشابه لـ QGroupBox."""
    def __init__(self, title="", **kwargs):
        super().__init__(orientation="vertical", spacing=dp(6), padding=[dp(8), dp(8), dp(8), dp(8)], **kwargs)
        with self.canvas.before:
            Color(0.11, 0.11, 0.11, 1)
            self._bg = RoundedRectangle(pos=self.pos, size=self.size, radius=[dp(6)])
            Color(0.22, 0.22, 0.22, 1)
            self._border = Line(rounded_rectangle=(self.x, self.y, self.width, self.height, dp(6)), width=1)
        self.bind(pos=self._update_rect, size=self._update_rect)

        if title:
            hdr = Label(
                text=ar_text(title),
                color=(0.0, 0.74, 0.83, 1),
                font_size=sp(13),
                bold=True,
                size_hint_y=None,
                height=dp(24),
                halign="right",
                valign="middle",
            )
            hdr.bind(size=lambda inst, val: setattr(inst, "text_size", val))
            self.add_widget(hdr)

    def _update_rect(self, *args):
        self._bg.pos = self.pos
        self._bg.size = self.size
        self._border.rounded_rectangle = (self.x, self.y, self.width, self.height, dp(6))


def make_label(text, color=(0.88, 0.88, 0.88, 1), font_size=12, bold=False, halign="right", size_hint_x=1.0, height=dp(30)):
    lbl = Label(
        text=ar_text(text),
        color=color,
        font_size=sp(font_size),
        bold=bold,
        halign=halign,
        valign="middle",
        size_hint_x=size_hint_x,
        size_hint_y=None if height else 1.0,
        height=height if height else dp(30),
    )
    lbl.bind(size=lambda inst, val: setattr(inst, "text_size", val))
    return lbl


def make_input(text="", password=False, hint="", on_finish=None):
    ti = TextInput(
        text=str(text),
        password=password,
        hint_text=ar_text(hint) if hint else "",
        multiline=False,
        background_normal="",
        background_active="",
        background_color=(0.16, 0.16, 0.16, 1),
        foreground_color=(1, 1, 1, 1),
        cursor_color=(0.0, 0.74, 0.83, 1),
        font_size=sp(12),
        padding=[dp(6), dp(6), dp(6), dp(6)],
        size_hint_y=None,
        height=dp(32),
    )
    if on_finish:
        ti.bind(focus=lambda inst, focused: on_finish() if not focused else None)
    return ti


def make_button(text, bg_rgba, text_rgba=(1, 1, 1, 1), callback=None, height=dp(38)):
    btn = Button(
        text=ar_text(text),
        background_normal="",
        background_color=bg_rgba,
        color=text_rgba,
        bold=True,
        font_size=sp(12),
        size_hint_y=None,
        height=height,
    )
    if callback:
        btn.bind(on_release=lambda *a: callback())
    return btn


class CheckRow(BoxLayout):
    """صف يجمع مربع اختيار (CheckBox) مع نص عربي قابل للنقر."""
    def __init__(self, text="", active=True, on_change=None, **kwargs):
        super().__init__(orientation="horizontal", size_hint_y=None, height=dp(30), spacing=dp(4), **kwargs)
        self.chk = CheckBox(active=active, size_hint_x=None, width=dp(32), color=(0.0, 0.74, 0.83, 1))
        self.lbl = make_label(text, bold=True, halign="right", height=dp(30))
        self.add_widget(self.lbl)
        self.add_widget(self.chk)
        if on_change:
            self.chk.bind(active=lambda inst, val: on_change(val))

    @property
    def is_checked(self):
        return bool(self.chk.active)

    def set_checked(self, value):
        self.chk.active = bool(value)


# --- الواجهة الرئيسية للتطبيق (Kivy UI) ---
class MEXCScalpKivyUI(BoxLayout):
    def __init__(self, **kwargs):
        super().__init__(orientation="vertical", padding=dp(6), spacing=dp(6), **kwargs)
        Window.clearcolor = (0.07, 0.07, 0.07, 1)

        init_db()
        self.scanner_thread = None
        self.ticker_thread = None
        self.active_position = None
        self.is_closing_position = False
        self.scan_resume_offset = 0
        self.log_lines = []

        self._build_ui()
        self.load_saved_settings()
        self.load_closed_trades_to_ui()
        self.check_and_recover_position()
        Window.bind(on_resize=self._on_window_resize)
        Clock.schedule_once(lambda dt: self._on_window_resize(Window, Window.width, Window.height), 0.1)

    def _build_ui(self):
        # شريط العنوان العلوي
        header = BoxLayout(size_hint_y=None, height=dp(34), padding=[dp(8), dp(2)])
        with header.canvas.before:
            Color(0.09, 0.13, 0.18, 1)
            self._hdr_bg = RoundedRectangle(pos=header.pos, size=header.size, radius=[dp(5)])
        header.bind(pos=lambda i, v: setattr(self._hdr_bg, "pos", v), size=lambda i, v: setattr(self._hdr_bg, "size", v))
        title_lbl = make_label(
            "⚡ MEXC Pro Scalper — Smart TP & Trailing SL Engine (Kivy Edition)",
            color=(0.0, 0.85, 0.95, 1), font_size=13, bold=True, halign="center", height=dp(30)
        )
        header.add_widget(title_lbl)
        self.add_widget(header)

        # حاوية المحتوى المتجاوبة (أفقي على الشاشات العريضة / عمودي على الهواتف)
        self.body_layout = BoxLayout(orientation="horizontal", spacing=dp(8))

        # --- اللوحة اليسرى: الإعدادات والتحكم داخل ScrollView ---
        self.left_scroll = ScrollView(size_hint_x=0.42, do_scroll_x=False, bar_width=dp(6))
        self.left_container = BoxLayout(orientation="vertical", spacing=dp(6), size_hint_y=None, padding=[dp(2), dp(2)])
        self.left_container.bind(minimum_height=self.left_container.setter("height"))

        self.left_container.add_widget(self._create_api_box())
        self.left_container.add_widget(self._create_trade_config_box())
        self.left_container.add_widget(self._create_smart_tp_sl_box())
        self.left_container.add_widget(self._create_control_box())
        self.left_scroll.add_widget(self.left_container)

        # --- اللوحة اليمنى: بطاقة الربح + جدول الصفقة المفتوحة + سجل الصفقات + السجل المباشر ---
        self.right_panel = BoxLayout(orientation="vertical", spacing=dp(6), size_hint_x=0.58)
        self.right_panel.add_widget(self._create_live_pnl_card())
        self.right_panel.add_widget(self._create_positions_table())
        self.right_panel.add_widget(self._create_history_table())
        self.right_panel.add_widget(self._create_log_box())

        self.body_layout.add_widget(self.left_scroll)
        self.body_layout.add_widget(self.right_panel)
        self.add_widget(self.body_layout)

    def _on_window_resize(self, window, width, height):
        """التبديل التلقائي بين وضع الهاتف العمودي ووضع الشاشة العريضة."""
        if width < dp(760):
            self.body_layout.orientation = "vertical"
            self.left_scroll.size_hint_x = 1.0
            self.left_scroll.size_hint_y = 0.48
            self.right_panel.size_hint_x = 1.0
            self.right_panel.size_hint_y = 0.52
        else:
            self.body_layout.orientation = "horizontal"
            self.left_scroll.size_hint_x = 0.40
            self.left_scroll.size_hint_y = 1.0
            self.right_panel.size_hint_x = 0.60
            self.right_panel.size_hint_y = 1.0

    # --- إنشاء صناديق الإعدادات (مطابقة 100% للنسخة الأصلية) ---
    def _create_api_box(self):
        card = CardBox("🔑 حساب MEXC API", size_hint_y=None, height=dp(146))
        grid = GridLayout(cols=2, spacing=dp(5), size_hint_y=None, height=dp(106))

        self.api_key_input = make_input(
            on_finish=lambda: save_setting("api_key", self.api_key_input.text.strip())
        )
        grid.add_widget(self.api_key_input)
        grid.add_widget(make_label("API Key:", size_hint_x=0.38))

        self.secret_key_input = make_input(
            password=True,
            on_finish=lambda: save_setting("secret_key", self.secret_key_input.text.strip())
        )
        grid.add_widget(self.secret_key_input)
        grid.add_widget(make_label("Secret Key:", size_hint_x=0.38))

        self.trade_mode_spinner = Spinner(
            text=ar_text("تجريبي (Paper)"),
            values=[ar_text("تجريبي (Paper)"), ar_text("حقيقي (Live)")],
            background_normal="",
            background_color=(0.18, 0.22, 0.28, 1),
            color=(1, 1, 1, 1),
            font_size=sp(12),
            size_hint_y=None,
            height=dp(32),
        )
        self.trade_mode_spinner.bind(text=self._on_trade_mode_changed)
        grid.add_widget(self.trade_mode_spinner)
        grid.add_widget(make_label("وضع التداول:", size_hint_x=0.38))

        card.add_widget(grid)
        return card

    def get_current_trade_mode_code(self):
        txt = self.trade_mode_spinner.text
        if "LIVE" in txt.upper() or "حقيقي" in txt or ar_text("حقيقي") in txt:
            return "LIVE"
        return "PAPER"

    def set_trade_mode_by_code(self, mode_code):
        if str(mode_code).upper() == "LIVE":
            self.trade_mode_spinner.text = ar_text("حقيقي (Live)")
        else:
            self.trade_mode_spinner.text = ar_text("تجريبي (Paper)")

    def _on_trade_mode_changed(self, *args):
        if not self.active_position:
            save_setting("trade_mode", self.get_current_trade_mode_code())

    def _create_trade_config_box(self):
        card = CardBox("⚙️ إعدادات الصفقة والمؤشرات الفنية", size_hint_y=None, height=dp(265))

        row1 = GridLayout(cols=4, spacing=dp(5), size_hint_y=None, height=dp(32))
        self.vol_mult_input = make_input("1.2", on_finish=lambda: save_setting("vol_mult", self.vol_mult_input.text.strip()))
        self.amount_input = make_input("15", on_finish=lambda: save_setting("amount", self.amount_input.text.strip()))
        row1.add_widget(self.vol_mult_input)
        row1.add_widget(make_label("معامل الفوليوم (x):"))
        row1.add_widget(self.amount_input)
        row1.add_widget(make_label("المبلغ ($):"))
        card.add_widget(row1)

        row2 = GridLayout(cols=4, spacing=dp(5), size_hint_y=None, height=dp(32))
        self.sl_input = make_input("0.8", on_finish=lambda: save_setting("sl", self.sl_input.text.strip()))
        self.tp_input = make_input("2.2", on_finish=lambda: save_setting("tp", self.tp_input.text.strip()))
        row2.add_widget(self.sl_input)
        row2.add_widget(make_label("الوقف (SL %):"))
        row2.add_widget(self.tp_input)
        row2.add_widget(make_label("الهدف (TP2 %):"))
        card.add_widget(row2)

        row3 = BoxLayout(orientation="horizontal", spacing=dp(5), size_hint_y=None, height=dp(32))
        self.max_price_chg_input = make_input("0.60", on_finish=lambda: save_setting("max_price_chg", self.max_price_chg_input.text.strip()))
        self.max_price_chg_input.size_hint_x = 0.22
        lbl_max_chg = make_label("أقصى تغير (%):", size_hint_x=0.26)
        self.chk_low_price_chg = CheckRow(
            "فلتر التجميع (تغير سعري بسيط)",
            active=True,
            on_change=lambda v: save_setting("enable_low_price_chg", int(bool(v))),
            size_hint_x=0.52,
        )
        row3.add_widget(self.max_price_chg_input)
        row3.add_widget(lbl_max_chg)
        row3.add_widget(self.chk_low_price_chg)
        card.add_widget(row3)

        self.chk_order_book = CheckRow(
            "استبعاد العملة إذا كانت قيمة أوامر البيع أكبر من الشراء",
            active=True,
            on_change=lambda v: save_setting("enable_order_book_filter", int(bool(v))),
        )
        card.add_widget(self.chk_order_book)

        row_rsi = BoxLayout(orientation="horizontal", spacing=dp(5), size_hint_y=None, height=dp(32))
        self.rsi_max_input = make_input("70", hint="إلى (70)", on_finish=lambda: save_setting("rsi_max", self.rsi_max_input.text.strip()))
        self.rsi_max_input.size_hint_x = 0.18
        self.rsi_min_input = make_input("45", hint="من (45)", on_finish=lambda: save_setting("rsi_min", self.rsi_min_input.text.strip()))
        self.rsi_min_input.size_hint_x = 0.18
        self.chk_rsi = CheckRow(
            "تفعيل مؤشر RSI (14) — النطاق:",
            active=True,
            on_change=lambda v: save_setting("enable_rsi_filter", int(bool(v))),
            size_hint_x=0.64,
        )
        row_rsi.add_widget(self.rsi_max_input)
        row_rsi.add_widget(self.rsi_min_input)
        row_rsi.add_widget(self.chk_rsi)
        card.add_widget(row_rsi)

        self.chk_macd = CheckRow(
            "تفعيل مؤشر MACD (تقاطع إيجابي: MACD > Signal & Hist > 0)",
            active=True,
            on_change=lambda v: save_setting("enable_macd_filter", int(bool(v))),
        )
        card.add_widget(self.chk_macd)

        return card

    def _create_smart_tp_sl_box(self):
        card = CardBox("⚡ تحصيل الأرباح والوقف الذكي (Pro TP/SL)", size_hint_y=None, height=dp(220))

        self.chk_be = CheckRow(
            "تفعيل تأمين الصفقة تلقائياً (Break-Even)",
            active=True,
            on_change=lambda v: save_setting("enable_be", int(bool(v))),
        )
        card.add_widget(self.chk_be)

        row_be = GridLayout(cols=4, spacing=dp(5), size_hint_y=None, height=dp(32))
        self.be_offset_input = make_input("0.15", on_finish=lambda: save_setting("be_offset", self.be_offset_input.text.strip()))
        self.be_trigger_input = make_input("0.65", on_finish=lambda: save_setting("be_trigger", self.be_trigger_input.text.strip()))
        row_be.add_widget(self.be_offset_input)
        row_be.add_widget(make_label("هامش التأمين (%):"))
        row_be.add_widget(self.be_trigger_input)
        row_be.add_widget(make_label("تفعيل عند (%):"))
        card.add_widget(row_be)

        self.chk_trailing = CheckRow(
            "تفعيل الوقف المتحرك لملاحقة الأرباح (Trailing SL)",
            active=True,
            on_change=lambda v: save_setting("enable_trailing", int(bool(v))),
        )
        card.add_widget(self.chk_trailing)

        row_tr = GridLayout(cols=4, spacing=dp(5), size_hint_y=None, height=dp(32))
        self.trail_dist_input = make_input("0.35", on_finish=lambda: save_setting("trail_dist", self.trail_dist_input.text.strip()))
        self.trail_activate_input = make_input("1.1", on_finish=lambda: save_setting("trail_activate", self.trail_activate_input.text.strip()))
        row_tr.add_widget(self.trail_dist_input)
        row_tr.add_widget(make_label("مسافة الملاحقة (%):"))
        row_tr.add_widget(self.trail_activate_input)
        row_tr.add_widget(make_label("بدء الملاحقة (%):"))
        card.add_widget(row_tr)

        row_tp1 = BoxLayout(orientation="horizontal", spacing=dp(5), size_hint_y=None, height=dp(32))
        self.tp1_input = make_input("1.0", on_finish=lambda: save_setting("tp1_pct", self.tp1_input.text.strip()))
        self.tp1_input.size_hint_x = 0.24
        lbl_tp1 = make_label("هدف TP1 (%):", size_hint_x=0.26)
        self.chk_partial_tp = CheckRow(
            "بيع جزئي 50% (TP1)",
            active=True,
            on_change=lambda v: save_setting("enable_partial_tp", int(bool(v))),
            size_hint_x=0.50,
        )
        row_tp1.add_widget(self.tp1_input)
        row_tp1.add_widget(lbl_tp1)
        row_tp1.add_widget(self.chk_partial_tp)
        card.add_widget(row_tp1)

        return card

    def _create_control_box(self):
        card = CardBox("🚀 التحكم السريع", size_hint_y=None, height=dp(210))

        row1 = GridLayout(cols=2, spacing=dp(6), size_hint_y=None, height=dp(38))
        self.btn_stop_scan = make_button("⏹ إيقاف البحث", (0.96, 0.49, 0.0, 1), callback=self.stop_scanning)
        self.btn_stop_scan.disabled = True
        self.btn_start = make_button("▶ بدء البحث التلقائي", (0.08, 0.40, 0.75, 1), callback=lambda: self.start_scanning(reset_offset=True))
        row1.add_widget(self.btn_stop_scan)
        row1.add_widget(self.btn_start)
        card.add_widget(row1)

        row2 = GridLayout(cols=2, spacing=dp(6), size_hint_y=None, height=dp(38))
        self.btn_close = make_button("✖ إغلاق الصفقة (Market)", (0.83, 0.18, 0.18, 1), callback=self.close_position_manual)
        self.btn_close.disabled = True
        self.btn_lock_profit = make_button("🔒 تأمين على الدخول (BE)", (0.0, 0.54, 0.48, 1), callback=self.manual_lock_breakeven)
        self.btn_lock_profit.disabled = True
        row2.add_widget(self.btn_close)
        row2.add_widget(self.btn_lock_profit)
        card.add_widget(row2)

        self.btn_force_close = make_button(
            "⚠️ إغلاق إجباري (مسح الصفقة العالقة)",
            (0.42, 0.11, 0.60, 1),
            callback=self.force_close_position_local,
        )
        self.btn_force_close.disabled = True
        card.add_widget(self.btn_force_close)

        row4 = GridLayout(cols=2, spacing=dp(6), size_hint_y=None, height=dp(36))
        self.btn_clear_records = make_button(
            "🗑️ مسح كافة السجلات",
            (0.27, 0.35, 0.39, 1),
            text_rgba=(1.0, 0.80, 0.74, 1),
            callback=self.handle_clear_all_records,
            height=dp(34),
        )
        self.btn_clear_blacklist = make_button(
            "🧹 مسح الأزواج المستبعدة",
            (0.22, 0.28, 0.31, 1),
            callback=self.handle_clear_blacklist,
            height=dp(34),
        )
        row4.add_widget(self.btn_clear_records)
        row4.add_widget(self.btn_clear_blacklist)
        card.add_widget(row4)

        return card

    def _create_live_pnl_card(self):
        card = CardBox("📈 حالة الصفقة والربح المباشر", size_hint_y=None, height=dp(78))
        row = GridLayout(cols=4, spacing=dp(6), size_hint_y=None, height=dp(36))

        self.lbl_pnl_percent = make_label("(0.00%)", font_size=14, bold=True, halign="center")
        self.lbl_pnl_usd = make_label("$0.00", font_size=16, bold=True, halign="center")
        self.lbl_protection_state = make_label("الحماية: --", color=(0.0, 0.74, 0.83, 1), font_size=12, bold=True, halign="center")
        self.lbl_active_symbol = make_label("العملة: --", font_size=13, bold=True, halign="center")

        row.add_widget(self.lbl_pnl_percent)
        row.add_widget(self.lbl_pnl_usd)
        row.add_widget(self.lbl_protection_state)
        row.add_widget(self.lbl_active_symbol)
        card.add_widget(row)
        return card

    def _create_positions_table(self):
        card = CardBox("📊 الصفقة المفتوحة الحالية", size_hint_y=None, height=dp(106))
        headers = ["النسبة (%)", "الربح ($)", "الوقف النشط (SL)", "أعلى قمة", "الحالي", "الدخول", "المبلغ المتبقي", "الزوج"]

        hdr_grid = GridLayout(cols=8, size_hint_y=None, height=dp(26), spacing=dp(2))
        for h in headers:
            hdr_grid.add_widget(make_label(h, color=(0.7, 0.85, 1, 1), font_size=11, bold=True, halign="center", height=dp(24)))
        card.add_widget(hdr_grid)

        self.pos_row_grid = GridLayout(cols=8, size_hint_y=None, height=dp(32), spacing=dp(2))
        self.pos_cells = [make_label("--", font_size=11, halign="center", height=dp(28)) for _ in range(8)]
        for cell in self.pos_cells:
            self.pos_row_grid.add_widget(cell)
        card.add_widget(self.pos_row_grid)
        return card

    def _create_history_table(self):
        card = CardBox("📜 سجل الصفقات المنتهية والجزئية", size_hint_y=0.52)
        headers = ["التاريخ", "السبب", "النسبة (%)", "الربح ($)", "المبلغ", "الخروج", "الدخول", "الزوج"]

        hdr_grid = GridLayout(cols=8, size_hint_y=None, height=dp(26), spacing=dp(2))
        for h in headers:
            hdr_grid.add_widget(make_label(h, color=(0.7, 0.85, 1, 1), font_size=11, bold=True, halign="center", height=dp(24)))
        card.add_widget(hdr_grid)

        self.history_scroll = ScrollView(do_scroll_x=False, bar_width=dp(6))
        self.history_grid = GridLayout(cols=8, spacing=dp(2), size_hint_y=None)
        self.history_grid.bind(minimum_height=self.history_grid.setter("height"))
        self.history_scroll.add_widget(self.history_grid)
        card.add_widget(self.history_scroll)
        return card

    def _create_log_box(self):
        card = CardBox("📝 السجل المباشر", size_hint_y=0.48)
        self.log_input = TextInput(
            text="",
            readonly=True,
            multiline=True,
            background_normal="",
            background_color=(0.08, 0.08, 0.08, 1),
            foreground_color=(0.88, 0.92, 0.95, 1),
            font_size=sp(11.5),
            padding=[dp(6), dp(6), dp(6), dp(6)],
        )
        card.add_widget(self.log_input)
        return card

    # --- نوافذ التنبيه والتأكيد (بديل QMessageBox في Kivy) ---
    def show_alert(self, title, message):
        content = BoxLayout(orientation="vertical", padding=dp(10), spacing=dp(10))
        msg_lbl = make_label(message, font_size=12, halign="center", height=dp(90))
        content.add_widget(msg_lbl)
        btn_ok = make_button("حسناً", (0.08, 0.40, 0.75, 1), height=dp(36))
        content.add_widget(btn_ok)
        popup = Popup(title=ar_text(title), content=content, size_hint=(0.85, None), height=dp(200), auto_dismiss=False)
        btn_ok.bind(on_release=popup.dismiss)
        popup.open()

    def show_confirm(self, title, message, on_yes):
        content = BoxLayout(orientation="vertical", padding=dp(10), spacing=dp(10))
        msg_lbl = make_label(message, font_size=12, halign="center", height=dp(110))
        content.add_widget(msg_lbl)

        btns = GridLayout(cols=2, spacing=dp(8), size_hint_y=None, height=dp(38))
        btn_no = make_button("إلغاء", (0.35, 0.35, 0.35, 1), height=dp(36))
        btn_yes = make_button("نعم، متابعة", (0.83, 0.18, 0.18, 1), height=dp(36))
        btns.add_widget(btn_no)
        btns.add_widget(btn_yes)
        content.add_widget(btns)

        popup = Popup(title=ar_text(title), content=content, size_hint=(0.88, None), height=dp(230), auto_dismiss=False)
        btn_no.bind(on_release=popup.dismiss)

        def _confirm(*a):
            popup.dismiss()
            on_yes()

        btn_yes.bind(on_release=_confirm)
        popup.open()

    def append_log(self, text):
        self.log_lines.append(ar_text(str(text)))
        if len(self.log_lines) > 350:
            self.log_lines = self.log_lines[-350:]
        self.log_input.text = "\n".join(self.log_lines)
        self.log_input.cursor = (0, len(self.log_lines))

    # --- إدارة الإعدادات والسجلات ---
    def load_saved_settings(self):
        mode = get_setting("trade_mode", "PAPER").upper()
        self.set_trade_mode_by_code(mode)
        self.api_key_input.text = get_setting("api_key", "")
        self.secret_key_input.text = get_setting("secret_key", "")
        self.amount_input.text = get_setting("amount", "15")
        self.tp_input.text = get_setting("tp", "2.2")
        self.sl_input.text = get_setting("sl", "0.8")
        self.vol_mult_input.text = get_setting("vol_mult", "1.2")
        self.chk_low_price_chg.set_checked(get_setting("enable_low_price_chg", "1") == "1")
        self.max_price_chg_input.text = get_setting("max_price_chg", "0.60")
        self.chk_order_book.set_checked(get_setting("enable_order_book_filter", "1") == "1")

        self.chk_rsi.set_checked(get_setting("enable_rsi_filter", "1") == "1")
        self.rsi_min_input.text = get_setting("rsi_min", "45")
        self.rsi_max_input.text = get_setting("rsi_max", "70")
        self.chk_macd.set_checked(get_setting("enable_macd_filter", "1") == "1")

        self.chk_be.set_checked(get_setting("enable_be", "1") == "1")
        self.be_trigger_input.text = get_setting("be_trigger", "0.65")
        self.be_offset_input.text = get_setting("be_offset", "0.15")

        self.chk_trailing.set_checked(get_setting("enable_trailing", "1") == "1")
        self.trail_activate_input.text = get_setting("trail_activate", "1.1")
        self.trail_dist_input.text = get_setting("trail_dist", "0.35")

        self.chk_partial_tp.set_checked(get_setting("enable_partial_tp", "1") == "1")
        self.tp1_input.text = get_setting("tp1_pct", "1.0")

    def save_all_settings(self):
        save_setting("trade_mode", self.get_current_trade_mode_code())
        save_setting("api_key", self.api_key_input.text.strip())
        save_setting("secret_key", self.secret_key_input.text.strip())
        save_setting("amount", self.amount_input.text.strip())
        save_setting("tp", self.tp_input.text.strip())
        save_setting("sl", self.sl_input.text.strip())
        save_setting("vol_mult", self.vol_mult_input.text.strip())
        save_setting("enable_low_price_chg", int(self.chk_low_price_chg.is_checked))
        save_setting("max_price_chg", self.max_price_chg_input.text.strip())
        save_setting("enable_order_book_filter", int(self.chk_order_book.is_checked))

        save_setting("enable_rsi_filter", int(self.chk_rsi.is_checked))
        save_setting("rsi_min", self.rsi_min_input.text.strip())
        save_setting("rsi_max", self.rsi_max_input.text.strip())
        save_setting("enable_macd_filter", int(self.chk_macd.is_checked))

        save_setting("enable_be", int(self.chk_be.is_checked))
        save_setting("be_trigger", self.be_trigger_input.text.strip())
        save_setting("be_offset", self.be_offset_input.text.strip())

        save_setting("enable_trailing", int(self.chk_trailing.is_checked))
        save_setting("trail_activate", self.trail_activate_input.text.strip())
        save_setting("trail_dist", self.trail_dist_input.text.strip())

        save_setting("enable_partial_tp", int(self.chk_partial_tp.is_checked))
        save_setting("tp1_pct", self.tp1_input.text.strip())

    def get_risk_parameters(self):
        def safe_float(val, default):
            try:
                return float(val)
            except (ValueError, TypeError):
                return default

        return {
            "amount": safe_float(self.amount_input.text, 15.0),
            "tp": safe_float(self.tp_input.text, 2.2),
            "sl": safe_float(self.sl_input.text, 0.8),
            "vol_mult": safe_float(self.vol_mult_input.text, 1.2),
            "enable_low_price_chg": self.chk_low_price_chg.is_checked,
            "max_price_chg": safe_float(self.max_price_chg_input.text, 0.60),
            "enable_order_book_filter": self.chk_order_book.is_checked,
            "enable_rsi_filter": self.chk_rsi.is_checked,
            "rsi_min": safe_float(self.rsi_min_input.text, 45.0),
            "rsi_max": safe_float(self.rsi_max_input.text, 70.0),
            "enable_macd_filter": self.chk_macd.is_checked,
            "enable_be": self.chk_be.is_checked,
            "be_trigger": safe_float(self.be_trigger_input.text, 0.65),
            "be_offset": safe_float(self.be_offset_input.text, 0.15),
            "enable_trailing": self.chk_trailing.is_checked,
            "trail_activate": safe_float(self.trail_activate_input.text, 1.1),
            "trail_dist": safe_float(self.trail_dist_input.text, 0.35),
            "enable_partial_tp": self.chk_partial_tp.is_checked,
            "tp1_pct": safe_float(self.tp1_input.text, 1.0),
        }

    def _set_pos_row_values(self, symbol="--", amount="--", entry="--", current="--", highest="--", sl_txt="--", pnl_usd="--", pnl_pct="--", pnl_rgba=(0.9, 0.9, 0.9, 1), sl_rgba=(1.0, 0.6, 0.0, 1)):
        # ترتيب الأعمدة من اليسار لليمين (للعرض العربي من اليمين لليسار):
        # [0: النسبة, 1: الربح$, 2: الوقف, 3: أعلى قمة, 4: الحالي, 5: الدخول, 6: المبلغ المتبقي, 7: الزوج]
        values = [pnl_pct, pnl_usd, sl_txt, highest, current, entry, amount, symbol]
        colors = [pnl_rgba, pnl_rgba, sl_rgba, (0.9, 0.9, 0.9, 1), (0.9, 0.9, 0.9, 1), (0.9, 0.9, 0.9, 1), (0.9, 0.9, 0.9, 1), (1, 1, 1, 1)]
        for idx, (val, col) in enumerate(zip(values, colors)):
            self.pos_cells[idx].text = ar_text(str(val))
            self.pos_cells[idx].color = col

    def load_closed_trades_to_ui(self):
        self.history_grid.clear_widgets()
        trades = get_all_closed_trades()
        for trade in trades[:80]:
            symbol, entry, exit_p, amount, pnl_usd, pnl_pct, reason, ts = trade
            pnl_rgba = (0.30, 0.69, 0.31, 1) if pnl_usd >= 0 else (0.96, 0.26, 0.21, 1)
            row_vals = [
                (ts[-8:] if len(str(ts)) >= 8 else str(ts), (0.75, 0.75, 0.75, 1)),
                (str(reason), (0.85, 0.85, 0.85, 1)),
                (f"{'+' if pnl_pct >= 0 else ''}{pnl_pct:.2f}%", pnl_rgba),
                (f"{'+' if pnl_usd >= 0 else ''}${pnl_usd:.2f}", pnl_rgba),
                (f"${float(amount):.2f}", (0.9, 0.9, 0.9, 1)),
                (f"${format_price(exit_p)}", (0.9, 0.9, 0.9, 1)),
                (f"${format_price(entry)}", (0.9, 0.9, 0.9, 1)),
                (str(symbol), (1, 1, 1, 1)),
            ]
            for txt, col in row_vals:
                self.history_grid.add_widget(make_label(txt, color=col, font_size=10.5, halign="center", height=dp(24)))

    # --- التحكم بالعمليات والصفقات ---
    def handle_clear_blacklist(self):
        count = len(BLACKLISTED_SYMBOLS)
        clear_blacklist_db()
        self.append_log(f"[🧹 تنظيف] تم مسح {count} زوج من القائمة المستبعدة.")

    def handle_clear_all_records(self):
        def _do_clear():
            clear_closed_trades_db()
            self.history_grid.clear_widgets()
            self.log_lines.clear()
            self.log_input.text = ""
            self.append_log("[🗑️ تنظيف شامل] تم مسح كافة سجلات الصفقات المنتهية والسجل المباشر بنجاح.")

        self.show_confirm(
            "تأكيد مسح كافة السجلات",
            "هل أنت متأكد من مسح سجل الصفقات المنتهية والجزئية والسجل المباشر بالكامل؟",
            _do_clear,
        )

    def force_close_position_local(self):
        """إغلاق الصفقة إجبارياً داخل التطبيق (عند بيعها مسبقاً على المنصة أو بقاء كمية Dust)."""
        if not self.active_position:
            clear_active_position()
            self.reset_active_position_ui()
            return

        pos = self.active_position
        symbol = pos["symbol"]

        def _do_force():
            self.is_closing_position = True
            if self.ticker_thread and self.ticker_thread.is_alive():
                self.ticker_thread.stop()

            exit_price = get_mexc_real_price(symbol) or pos.get("highest_price") or pos["entry_price"]
            pnl_percent = ((exit_price - pos["entry_price"]) / pos["entry_price"]) * 100 if pos["entry_price"] > 0 else 0.0
            pnl_usd = pos["amount"] * (pnl_percent / 100.0)

            record_closed_trade(symbol, pos["entry_price"], exit_price, pos["amount"], pnl_usd, pnl_percent, "FORCE_CLOSE")
            self.load_closed_trades_to_ui()

            clear_active_position()
            self.active_position = None
            self.reset_active_position_ui()
            self.is_closing_position = False
            self.btn_start.disabled = False
            self.append_log(
                f"[⚠️ إغلاق إجباري] تم مسح الصفقة العالقة {symbol} من التطبيق وتسجيلها بسعر ${format_price(exit_price)}."
            )

        self.show_confirm(
            "تأكيد الإغلاق الإجباري",
            f"سيتم إنهاء وإزالة الصفقة ({symbol}) فوراً من التطبيق دون إرسال أمر بيع إلى منصة MEXC.\n"
            "استخدم هذا الخيار إذا بعت العملة يدوياً من داخل المنصة وبقيت عالقة. هل تريد المتابعة؟",
            _do_force,
        )

    def reset_active_position_ui(self):
        self.trade_mode_spinner.disabled = False
        self.btn_close.disabled = True
        self.btn_force_close.disabled = True
        self.btn_lock_profit.disabled = True
        self._set_pos_row_values()
        self.lbl_active_symbol.text = ar_text("العملة: --")
        self.lbl_protection_state.text = ar_text("الحماية: --")
        self.lbl_pnl_usd.text = "$0.00"
        self.lbl_pnl_usd.color = (0.88, 0.88, 0.88, 1)
        self.lbl_pnl_percent.text = "(0.00%)"
        self.lbl_pnl_percent.color = (0.88, 0.88, 0.88, 1)

    def start_ticker_for_active_position(self):
        if not self.active_position:
            return
        if self.ticker_thread and self.ticker_thread.is_alive():
            self.ticker_thread.stop()

        api_k = self.api_key_input.text.strip()
        sec_k = self.secret_key_input.text.strip()
        pos = self.active_position
        cfg = self.get_risk_parameters()

        self.ticker_thread = RealLiveTickerThread(
            price_update_callback=self.update_live_pnl,
            partial_tp_callback=self.handle_partial_tp,
            auto_close_callback=self.handle_auto_close,
            log_event_callback=self.append_log,
            symbol=pos["symbol"],
            entry_price=pos["entry_price"],
            amount_usd=pos["amount"],
            trade_mode=pos.get("trade_mode", "LIVE"),
            base_qty=pos.get("base_qty", 0.0),
            api_key=api_k,
            secret_key=sec_k,
            tp_percent=pos["tp_percent"],
            sl_percent=pos["sl_percent"],
            enable_be=cfg["enable_be"],
            be_trigger_pct=cfg["be_trigger"],
            be_offset_pct=cfg["be_offset"],
            enable_trailing=cfg["enable_trailing"],
            trail_activate_pct=cfg["trail_activate"],
            trail_distance_pct=cfg["trail_dist"],
            enable_partial_tp=cfg["enable_partial_tp"],
            tp1_percent=cfg["tp1_pct"],
            tp1_sell_ratio=0.5,
            highest_price=pos.get("highest_price", pos["entry_price"]),
            dynamic_sl_percent=pos.get("dynamic_sl_percent", -abs(pos["sl_percent"])),
            tp1_done=pos.get("tp1_done", False),
            be_active=pos.get("be_active", False),
            trailing_active=pos.get("trailing_active", False),
        )
        self.ticker_thread.start()

    def check_and_recover_position(self):
        saved_pos = get_active_position()
        if saved_pos:
            self.append_log(
                f"[استعادة] متابعة الصفقة المفتوحة: {saved_pos['symbol']} | "
                f"الوقف المحفوظ: {saved_pos['dynamic_sl_percent']:+.2f}%"
            )
            self.active_position = saved_pos
            self.set_trade_mode_by_code(saved_pos.get("trade_mode", "LIVE"))
            self.trade_mode_spinner.disabled = True
            self.lbl_active_symbol.text = ar_text(f"العملة: {saved_pos['symbol']}")
            self.btn_close.disabled = False
            self.btn_force_close.disabled = False
            self.btn_lock_profit.disabled = False
            self.btn_start.disabled = True

            self._set_pos_row_values(
                symbol=saved_pos["symbol"],
                amount=f"${saved_pos['amount']:.2f}",
                entry=f"${format_price(saved_pos['entry_price'])}",
                current="--",
                highest=f"${format_price(saved_pos['highest_price'])}",
                sl_txt=f"{saved_pos['dynamic_sl_percent']:+.2f}%",
            )
            self.start_ticker_for_active_position()

    def handle_scan_failed(self):
        self.btn_start.disabled = False
        self.btn_stop_scan.disabled = True

    def start_scanning(self, reset_offset=False):
        self.save_all_settings()
        api = self.api_key_input.text.strip()
        secret = self.secret_key_input.text.strip()
        if self.get_current_trade_mode_code() == "LIVE" and (not api or not secret):
            self.show_alert("تنبيه", "أدخل مفاتيح API لتفعيل التداول الحقيقي.")
            return

        if self.scanner_thread and self.scanner_thread.is_alive():
            return

        if reset_offset:
            self.scan_resume_offset = 0

        cfg = self.get_risk_parameters()
        self.btn_start.disabled = True
        self.btn_stop_scan.disabled = False
        self.append_log("[بدء] البحث عن فرصة جديدة في سوق Spot...")

        self.scanner_thread = MainScannerThread(
            log_callback=self.append_log,
            opportunity_callback=self.execute_snipe,
            scan_failed_callback=self.handle_scan_failed,
            start_offset=self.scan_resume_offset,
            vol_multiplier=cfg["vol_mult"],
            enable_low_price_change_filter=cfg["enable_low_price_chg"],
            max_price_change_pct=cfg["max_price_chg"],
            enable_order_book_filter=cfg["enable_order_book_filter"],
            enable_rsi_filter=cfg["enable_rsi_filter"],
            rsi_min=cfg["rsi_min"],
            rsi_max=cfg["rsi_max"],
            enable_macd_filter=cfg["enable_macd_filter"],
        )
        self.scanner_thread.start()

    def stop_scanning(self):
        if self.scanner_thread and self.scanner_thread.is_alive():
            self.scan_resume_offset = self.scanner_thread.last_scanned_index
            self.scanner_thread.stop()
            self.append_log("[إيقاف] تم إيقاف عملية البحث.")
        self.btn_start.disabled = False
        self.btn_stop_scan.disabled = True

    def execute_snipe(self, symbol, approx_price):
        if self.scanner_thread:
            self.scan_resume_offset = self.scanner_thread.last_scanned_index

        self.btn_stop_scan.disabled = True
        cfg = self.get_risk_parameters()
        amount = cfg["amount"]
        tp = cfg["tp"]
        sl = cfg["sl"]

        api_k = self.api_key_input.text.strip()
        sec_k = self.secret_key_input.text.strip()

        get_symbol_step_rules(symbol)

        trade_mode = self.get_current_trade_mode_code()
        self.trade_mode_spinner.disabled = True
        self.append_log(f"[شراء {trade_mode}] {symbol} بمبلغ ${amount:.2f}...")

        def _buy_worker():
            if trade_mode == "PAPER":
                entry_price = get_mexc_real_price(symbol) or approx_price
                base_qty = (amount / entry_price) if entry_price > 0 else 0.0
                success, order_msg = (entry_price > 0, "محاكاة شراء تجريبية دون إرسال أمر للمنصة")
            else:
                success, order_msg, entry_price, base_qty = place_mexc_buy_order(symbol, amount, api_k, sec_k)

            Clock.schedule_once(lambda dt: self._on_snipe_completed(symbol, amount, tp, sl, trade_mode, success, order_msg, entry_price, base_qty))

        threading.Thread(target=_buy_worker, daemon=True).start()

    def _on_snipe_completed(self, symbol, amount, tp, sl, trade_mode, success, order_msg, entry_price, base_qty):
        if not success or entry_price <= 0:
            if "نتيجة الأمر غير مؤكدة" in str(order_msg):
                self.append_log(f"[⚠️ إيقاف أمان — أمر شراء غير محسوم] {symbol}: {order_msg}")
                if self.scanner_thread and self.scanner_thread.is_alive():
                    self.scanner_thread.stop()
                self.btn_stop_scan.disabled = True
                self.btn_start.disabled = False
                self.trade_mode_spinner.disabled = False
                self.show_alert(
                    "يلزم التحقق من MEXC",
                    "تعذر تأكيد نتيجة أمر الشراء. قد تكون الصفقة نُفذت بالفعل. "
                    "تم إيقاف الشراء الآلي لمنع تكرار الأمر. افحص سجل الأوامر والرصيد في MEXC.",
                )
                return

            self.append_log(f"[🛑 فشل الشراء] {symbol}: {order_msg} — تم استبعاد الزوج ومتابعة الفحص من النقطة الحالية.")
            self.trade_mode_spinner.disabled = False
            self.start_scanning(reset_offset=False)
            return

        initial_sl = -abs(sl)
        self.active_position = {
            "symbol": symbol,
            "entry_price": entry_price,
            "amount": amount,
            "tp_percent": tp,
            "sl_percent": sl,
            "highest_price": entry_price,
            "dynamic_sl_percent": initial_sl,
            "tp1_done": False,
            "be_active": False,
            "trailing_active": False,
            "trade_mode": trade_mode,
            "base_qty": base_qty,
        }

        save_active_position(
            symbol, entry_price, amount, tp, sl,
            highest_price=entry_price, dynamic_sl_percent=initial_sl,
            tp1_done=0, be_active=0, trailing_active=0, trade_mode=trade_mode, base_qty=base_qty
        )

        self.append_log(f"[🎯 تم الشراء] {symbol} | سعر الدخول الفعلي: ${format_price(entry_price)}")
        self.lbl_active_symbol.text = ar_text(f"العملة: {symbol}")
        self.btn_close.disabled = False
        self.btn_force_close.disabled = False
        self.btn_lock_profit.disabled = False

        self._set_pos_row_values(
            symbol=symbol,
            amount=f"${amount:.2f}",
            entry=f"${format_price(entry_price)}",
            current=f"${format_price(entry_price)}",
            highest=f"${format_price(entry_price)}",
            sl_txt=f"{initial_sl:+.2f}%",
        )

        self.start_ticker_for_active_position()

    def update_live_pnl(self, current_price, pnl_usd, pnl_percent, highest_price, dynamic_sl_percent, status_text):
        if self.active_position:
            self.active_position["highest_price"] = highest_price
            self.active_position["dynamic_sl_percent"] = dynamic_sl_percent

        pnl_rgba = (0.30, 0.69, 0.31, 1) if pnl_usd >= 0 else (0.96, 0.26, 0.21, 1)
        self.lbl_pnl_usd.text = f"{'+' if pnl_usd >= 0 else ''}${pnl_usd:.2f}"
        self.lbl_pnl_usd.color = pnl_rgba
        self.lbl_pnl_percent.text = f"({'+' if pnl_percent >= 0 else ''}{pnl_percent:.2f}%)"
        self.lbl_pnl_percent.color = pnl_rgba
        self.lbl_protection_state.text = ar_text(f"الحماية: {status_text}")

        if self.active_position:
            sl_price = self.active_position["entry_price"] * (1.0 + (dynamic_sl_percent / 100.0))
            sl_rgba = (0.30, 0.69, 0.31, 1) if dynamic_sl_percent >= 0 else (1.0, 0.60, 0.0, 1)
            self._set_pos_row_values(
                symbol=self.active_position["symbol"],
                amount=f"${self.active_position['amount']:.2f}",
                entry=f"${format_price(self.active_position['entry_price'])}",
                current=f"${format_price(current_price)}",
                highest=f"${format_price(highest_price)}",
                sl_txt=f"{dynamic_sl_percent:+.2f}% (${format_price(sl_price)})",
                pnl_usd=f"{'+' if pnl_usd >= 0 else ''}${pnl_usd:.2f}",
                pnl_pct=f"{'+' if pnl_percent >= 0 else ''}{pnl_percent:.2f}%",
                pnl_rgba=pnl_rgba,
                sl_rgba=sl_rgba,
            )

    def manual_lock_breakeven(self):
        """زر يدوي لتأمين الصفقة فوراً عند نقطة الدخول + هامش العمولة."""
        if not self.active_position or not self.ticker_thread:
            return
        cfg = self.get_risk_parameters()
        lock_pct = max(cfg["be_offset"], 0.1)
        if self.ticker_thread.dynamic_sl_percent < lock_pct:
            self.ticker_thread.dynamic_sl_percent = lock_pct
            self.ticker_thread.be_active = True
            self.active_position["dynamic_sl_percent"] = lock_pct
            self.active_position["be_active"] = True
            update_active_position_state(
                self.active_position["amount"],
                self.ticker_thread.highest_price,
                lock_pct,
                self.ticker_thread.tp1_done,
                True,
                self.ticker_thread.trailing_active,
                self.active_position.get("base_qty", 0.0),
            )
            self.append_log(f"[🔒 تأمين يدوي] تم رفع وقف الخسارة إلى +{lock_pct:.2f}% فوق سعر الدخول.")

    def handle_partial_tp(self, sell_ratio, trigger_price):
        """تنفيذ البيع الجزئي عند الهدف الأول TP1 وتحديث الرصيد المتبقي للصفقة."""
        if not self.active_position or self.is_closing_position:
            return

        pos = self.active_position
        symbol = pos["symbol"]
        api_k = self.api_key_input.text.strip()
        sec_k = self.secret_key_input.text.strip()

        self.append_log(
            f"[💰 جني ربح جزئي TP1] جاري بيع {int(sell_ratio * 100)}% من {symbol} عند السعر ${format_price(trigger_price)}..."
        )

        def _partial_worker():
            if pos.get("trade_mode", "LIVE") == "PAPER":
                exec_price = get_mexc_real_price(symbol) or trigger_price
                exec_qty = float(pos.get("base_qty", 0.0)) * sell_ratio
                success, sell_msg = True, "بيع تجريبي محاكى"
            else:
                success, sell_msg, exec_price, exec_qty = place_mexc_sell_order_market(
                    symbol, api_k, sec_k, sell_ratio=sell_ratio, position_quantity=pos.get("base_qty", 0.0)
                )
            Clock.schedule_once(lambda dt: self._on_partial_tp_done(sell_ratio, trigger_price, success, sell_msg, exec_price, exec_qty))

        threading.Thread(target=_partial_worker, daemon=True).start()

    def _on_partial_tp_done(self, sell_ratio, trigger_price, success, sell_msg, exec_price, exec_qty):
        if not self.active_position:
            return
        pos = self.active_position
        symbol = pos["symbol"]

        if not success:
            if self.ticker_thread:
                self.ticker_thread.tp1_pending = False
                self.ticker_thread.tp1_retry_after = time.time() + 10.0
            self.append_log(f"[⚠️ تعذر البيع الجزئي] {sell_msg} — ستتم إعادة المحاولة عند استمرار تحقق الهدف.")
            return

        exit_price = exec_price or trigger_price
        sold_amount_usd = pos["amount"] * sell_ratio
        remaining_amount_usd = pos["amount"] - sold_amount_usd
        pnl_percent = ((exit_price - pos["entry_price"]) / pos["entry_price"]) * 100
        pnl_usd = sold_amount_usd * (pnl_percent / 100)

        record_closed_trade(symbol, pos["entry_price"], exit_price, sold_amount_usd, pnl_usd, pnl_percent, "PARTIAL_TP1")
        self.load_closed_trades_to_ui()

        current_base_qty = float(pos.get("base_qty", 0.0))
        if exec_qty > 0 and current_base_qty > 0:
            new_base_qty = max(0.0, current_base_qty - exec_qty)
        else:
            new_base_qty = max(0.0, current_base_qty * (1.0 - sell_ratio))

        pos["amount"] = remaining_amount_usd
        pos["base_qty"] = new_base_qty
        pos["tp1_done"] = True
        pos["be_active"] = True
        if self.ticker_thread:
            self.ticker_thread.amount_usd = remaining_amount_usd
            self.ticker_thread.base_qty = new_base_qty
            self.ticker_thread.tp1_done = True
            self.ticker_thread.be_active = True
            self.ticker_thread.tp1_pending = False

        update_active_position_state(
            remaining_amount_usd,
            pos.get("highest_price", exit_price),
            pos.get("dynamic_sl_percent", 0.15),
            True,
            True,
            pos.get("trailing_active", False),
            new_base_qty,
        )
        self.pos_cells[6].text = f"${remaining_amount_usd:.2f}"
        self.append_log(
            f"[✅ نجاح TP1] تم جني ربح +${pnl_usd:.2f} (+{pnl_percent:.2f}%) | "
            f"المبلغ المتبقي للملاحقة: ${remaining_amount_usd:.2f}"
        )

    def finalize_close_position(self, reason_type, resume_scan=True):
        """إغلاق الصفقة على المنصة والتأكد من نجاح البيع قبل مسحها من البرنامج."""
        if self.is_closing_position or not self.active_position:
            return
        self.is_closing_position = True

        if self.ticker_thread and self.ticker_thread.is_alive():
            self.ticker_thread.stop()

        pos = self.active_position
        symbol = pos["symbol"]
        api_k = self.api_key_input.text.strip()
        sec_k = self.secret_key_input.text.strip()

        def _close_worker():
            if reason_type in ("EXTERNAL_TP", "EXTERNAL_SL"):
                Clock.schedule_once(lambda dt: self.append_log(f"[ℹ️ إغلاق خارجي] تم رصد إغلاق {symbol} من خارج التطبيق ({reason_type})."))
                success, sell_msg, exec_exit_price = True, "تم بيع الرصيد خارجياً", get_mexc_real_price(symbol)
            elif pos.get("trade_mode", "LIVE") == "PAPER":
                Clock.schedule_once(lambda dt: self.append_log(f"[تنفيذ الإغلاق] جاري إغلاق الصفقة التجريبية {symbol} ({reason_type})..."))
                success, sell_msg, exec_exit_price = True, "إغلاق تجريبي محاكى", get_mexc_real_price(symbol)
            else:
                Clock.schedule_once(lambda dt: self.append_log(f"[تنفيذ الإغلاق] جاري إرسال أمر بيع {symbol} ({reason_type}) إلى المنصة..."))
                success, sell_msg, exec_exit_price, _ = place_mexc_sell_order_market(
                    symbol, api_k, sec_k, sell_ratio=1.0, position_quantity=pos.get("base_qty", 0.0)
                )
            Clock.schedule_once(lambda dt: self._on_close_completed(reason_type, resume_scan, success, sell_msg, exec_exit_price))

        threading.Thread(target=_close_worker, daemon=True).start()

    def _on_close_completed(self, reason_type, resume_scan, success, sell_msg, exec_exit_price):
        if not self.active_position:
            self.is_closing_position = False
            return

        pos = self.active_position
        symbol = pos["symbol"]

        if not success:
            self.append_log(
                f"[⚠️ فشل إغلاق الصفقة على المنصة] {sell_msg} — إذا أغلقتها يدوياً من المنصة اضغط زر (⚠️ إغلاق إجباري)."
            )
            self.is_closing_position = False
            self.start_ticker_for_active_position()
            return

        exit_price = exec_exit_price or get_mexc_real_price(symbol) or pos["entry_price"]
        pnl_percent = ((exit_price - pos["entry_price"]) / pos["entry_price"]) * 100
        pnl_usd = pos["amount"] * (pnl_percent / 100)

        record_closed_trade(symbol, pos["entry_price"], exit_price, pos["amount"], pnl_usd, pnl_percent, reason_type)
        self.load_closed_trades_to_ui()

        clear_active_position()
        self.active_position = None
        self.reset_active_position_ui()

        self.append_log(
            f"[✅ تم الإغلاق] {symbol} ({reason_type} | {sell_msg}) | الصافي: ${pnl_usd:.2f} ({pnl_percent:.2f}%)"
        )
        self.is_closing_position = False

        if resume_scan:
            self.append_log("[🔄 إكمال آلي] البحث تلقائياً عن الفرصة التالية...")
            self.start_scanning(reset_offset=False)
        else:
            self.btn_start.disabled = False

    def handle_auto_close(self, reason_type):
        self.finalize_close_position(reason_type, resume_scan=True)

    def close_position_manual(self):
        self.finalize_close_position("MANUAL", resume_scan=False)

    def stop_all_threads(self):
        self.save_all_settings()
        if self.scanner_thread and self.scanner_thread.is_alive():
            self.scanner_thread.stop()
        if self.ticker_thread and self.ticker_thread.is_alive():
            self.ticker_thread.stop()


class MEXCScalpKivyApp(App):
    def build(self):
        self.title = "MEXC Pro Scalper — Smart TP & Trailing SL Engine"
        self.root_widget = MEXCScalpKivyUI()
        return self.root_widget

    def on_stop(self):
        if hasattr(self, "root_widget") and self.root_widget:
            self.root_widget.stop_all_threads()


if __name__ == "__main__":
    MEXCScalpKivyApp().run()
