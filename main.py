# -*- coding: utf-8 -*-
"""
BEPUL OLOV UZ — Free Fire Universal Bot (bitta fayl)
python-telegram-bot 21.6 | SQLite | Flask keep-alive | Groq / OpenRouter AI

ENV: BOT_TOKEN, ADMIN_IDS (vergul bilan), GROQ_API_KEY, OPENROUTER_API_KEY (ixtiyoriy), DB_PATH (ixtiyoriy)
"""
import os, re, html, time, random, shutil, sqlite3, asyncio, logging, threading, tempfile
from datetime import datetime, timedelta, timezone

import httpx
from flask import Flask
from telegram import (InlineKeyboardButton as IB, InlineKeyboardMarkup as IM,
                      ReplyKeyboardMarkup, KeyboardButton)
from telegram.constants import ParseMode
from telegram.error import RetryAfter
from telegram.ext import (Application, CommandHandler, MessageHandler, CallbackQueryHandler,
                          filters)

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
ENV_ADMINS = {int(x) for x in re.findall(r"\d+", os.getenv("ADMIN_IDS", ""))}
GROQ_KEY = os.getenv("GROQ_API_KEY", "")
OR_KEY = os.getenv("OPENROUTER_API_KEY", "")
DB_PATH = os.getenv("DB_PATH", "bot.db")
DATABASE_URL = os.getenv("DATABASE_URL", "").strip()
IS_PG = DATABASE_URL.startswith("postgres")
if IS_PG:
    import psycopg2
PORT = int(os.getenv("PORT", "8080"))
TZ = timezone(timedelta(hours=5))  # Toshkent
HTML = ParseMode.HTML

logging.basicConfig(format="%(asctime)s %(levelname)s %(message)s", level=logging.INFO)
log = logging.getLogger("bot")
logging.getLogger("httpx").setLevel(logging.WARNING)

# ───────────────────────── DATABASE ─────────────────────────
SCHEMA = """
CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY, username TEXT, name TEXT, phone TEXT,
 verified INTEGER DEFAULT 0, banned INTEGER DEFAULT 0, ref_by INTEGER, balance INTEGER DEFAULT 0,
 earned INTEGER DEFAULT 0, points INTEGER DEFAULT 0, refs INTEGER DEFAULT 0, ff_id TEXT,
 level INTEGER DEFAULT 0, mic TEXT DEFAULT 'Yo''q', bio TEXT DEFAULT '', spin_date TEXT,
 free_used INTEGER DEFAULT 0, paid_used INTEGER DEFAULT 0, bonus_spins INTEGER DEFAULT 0,
 bonus_date TEXT, ai_date TEXT, ai_count INTEGER DEFAULT 0, spins_total INTEGER DEFAULT 0, joined INTEGER);
CREATE TABLE IF NOT EXISTS settings(k TEXT PRIMARY KEY, v TEXT);
CREATE TABLE IF NOT EXISTS admins(id INTEGER PRIMARY KEY);
CREATE TABLE IF NOT EXISTS channels(id INTEGER PRIMARY KEY AUTOINCREMENT, chat_id TEXT, title TEXT, url TEXT);
CREATE TABLE IF NOT EXISTS promos(code TEXT PRIMARY KEY, amount INTEGER, max_uses INTEGER DEFAULT 0,
 used INTEGER DEFAULT 0, expires INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS promo_uses(id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, code TEXT, ts INTEGER);
CREATE TABLE IF NOT EXISTS prizes(id INTEGER PRIMARY KEY AUTOINCREMENT, amount INTEGER, weight REAL);
CREATE TABLE IF NOT EXISTS tasks(id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT, url TEXT, chat_id TEXT);
CREATE TABLE IF NOT EXISTS items(id INTEGER PRIMARY KEY AUTOINCREMENT, cat TEXT, name TEXT, descr TEXT,
 price INTEGER DEFAULT 0, active INTEGER DEFAULT 1);
CREATE TABLE IF NOT EXISTS orders(id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, item_id INTEGER,
 name TEXT, cat TEXT, price INTEGER, ff_id TEXT, note TEXT, status TEXT, ts INTEGER);
CREATE TABLE IF NOT EXISTS withdrawals(id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, amount INTEGER,
 ff_id TEXT, status TEXT, ts INTEGER);
CREATE TABLE IF NOT EXISTS leagues(id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT, emoji TEXT, min_points INTEGER);
CREATE TABLE IF NOT EXISTS partner_ads(user_id INTEGER PRIMARY KEY, mode TEXT, ts INTEGER);
"""
db = None
PGSCHEMA = (SCHEMA.replace("INTEGER PRIMARY KEY AUTOINCREMENT", "BIGSERIAL PRIMARY KEY")
            .replace("INTEGER", "BIGINT").replace("REAL", "DOUBLE PRECISION"))
IDTABLES = {"orders", "withdrawals", "prizes", "tasks", "items", "leagues", "channels", "promo_uses"}


class Row:
    def __init__(self, keys, vals):
        self._v = list(vals)
        self._m = dict(zip(keys, self._v))

    def __getitem__(self, i):
        return self._v[i] if isinstance(i, int) else self._m[i]


class Cur:
    def __init__(self, out=None, lastrowid=None):
        self._r = out or []
        self.lastrowid = lastrowid

    def fetchone(self):
        return self._r[0] if self._r else None

    def fetchall(self):
        return self._r


class PGDB:
    """sqlite3 ga o'xshash Postgres qobig'i (Neon)."""

    def __init__(self, url):
        self.url = url
        self.conn = None
        self._open()

    def _open(self):
        self.conn = psycopg2.connect(self.url, connect_timeout=20)
        self.conn.autocommit = True

    def execute(self, sql, args=()):
        sql = sql.replace("?", "%s")
        m = re.match(r"\s*INSERT INTO (\w+)", sql, re.I)
        ret = bool(m and m.group(1).lower() in IDTABLES and "returning" not in sql.lower())
        if ret:
            sql += " RETURNING id"
        for attempt in (0, 1):
            try:
                cur = self.conn.cursor()
                cur.execute(sql, tuple(args))
                break
            except (psycopg2.OperationalError, psycopg2.InterfaceError):
                if attempt:
                    raise
                try:
                    self.conn.close()
                except Exception:
                    pass
                self._open()
        out = []
        if cur.description:
            keys = [d[0] for d in cur.description]
            out = [Row(keys, r) for r in cur.fetchall()]
        cur.close()
        lid = None
        if ret and out:
            lid, out = out[0]["id"], []
        return Cur(out, lid)

    def commit(self):
        pass

    def close(self):
        try:
            self.conn.close()
        except Exception:
            pass


def connect():
    global db
    if IS_PG:
        if db is None:
            db = PGDB(DATABASE_URL)
        for stmt in PGSCHEMA.split(";"):
            if stmt.strip():
                db.execute(stmt)
    else:
        db = sqlite3.connect(DB_PATH, check_same_thread=False)
        db.row_factory = sqlite3.Row
        db.executescript(SCHEMA)
    if not db.execute("SELECT 1 FROM settings WHERE k='_seeded'").fetchone():
        if not db.execute("SELECT 1 FROM prizes").fetchone():
            for r in [(0, 20), (1, 45), (2, 20), (3, 8), (5, 5), (10, 2)]:
                db.execute("INSERT INTO prizes(amount,weight) VALUES(?,?)", r)
        if not db.execute("SELECT 1 FROM leagues").fetchone():
            for r in [("Bronze", "🥉", 0), ("Silver", "🥈", 50), ("Gold", "🥇", 150), ("Platinum", "💠", 400),
                      ("Diamond", "💎", 800), ("Heroic", "🔥", 1500), ("Grandmaster", "👑", 3000)]:
                db.execute("INSERT INTO leagues(name,emoji,min_points) VALUES(?,?,?)", r)
        if not db.execute("SELECT 1 FROM items").fetchone():
            for r in [
                ("direct", "💎 100 Diamond", "O'yin ichidagi 100 ta diamond FF ID ga yuboriladi.", 100),
                ("direct", "💎 310 Diamond", "O'yin ichidagi 310 ta diamond FF ID ga yuboriladi.", 300),
                ("direct", "💎 520 Diamond", "O'yin ichidagi 520 ta diamond FF ID ga yuboriladi.", 500),
                ("pack", "🎁 Haftalik obuna", "Haftalik Airdrop obunasi.", 250),
                ("pack", "🎁 Oylik obuna", "Oylik Airdrop obunasi.", 800),
                ("emote", "🕺 Emote: Raqs", "Mashhur emotsiya FF ID ga beriladi.", 150),
                ("emote", "🕺 Emote: G'alaba", "G'alaba emotsiyasi.", 200),
                ("skin", "🛡 Stenka: Olov", "Olovli stenka skini.", 120),
                ("skin", "🛡 Skin: Qora Ritsar", "Qimmatbaho kiyim skini.", 400),
                ("service", "📢 Kanalingizni reklama qilish", "Bot foydalanuvchilariga kanalingiz reklamasi.", 100),
                ("service", "🎬 Video montaj", "Free Fire videongiz uchun professional montaj.", 300),
                ("service", "🖼 Banner / Thumbnail dizayn", "YouTube kanal uchun banner va thumbnail.", 150)]:
                db.execute("INSERT INTO items(cat,name,descr,price) VALUES(?,?,?,?)", r)
        db.execute("INSERT INTO settings(k,v) VALUES('_seeded','1')")
    db.commit()


def ex(sql, a=()):
    c = db.execute(sql, a)
    db.commit()
    return c


def one(sql, a=()):
    return db.execute(sql, a).fetchone()


def rows(sql, a=()):
    return db.execute(sql, a).fetchall()


# ───────────────────────── SOZLAMALAR ─────────────────────────
D_WELCOME = ("🔥 <b>Bepul Olov Uz — Free Fire Universal Boti!</b>\n\n"
             "Bu yerda:\n💎 Do'st taklif qilib <b>almaz ishlang</b>\n🎰 Spin aylantirib yuting\n"
             "🤖 AI Pro Coach bilan suhbatlashing\n⚙️ Telefoningizga ideal sensitivity oling\n"
             "🤝 O'yin uchun sherik toping\n🛒 Do'kondan skin va emotsiyalar oling\n\nMenyudan tanlang 👇")
D_EARN = ("💎 <b>Almaz ishlash — o'yin ichidagi boylikka eng tez yo'l!</b>\n\n"
          "Free Fire'da kuchli bo'lishni xohlaysizmi? 🔥\nUnda siz aynan to'g'ri joydasiz! Bu yerda hech qanday "
          "sarmoyasiz, faqat do'stlaringizni taklif qilish orqali <b>almaz ishlab olishingiz</b> mumkin.\n\n"
          "👥 <b>Qanday ishlaydi?</b>\nDo'st taklif qilasiz ➜ U botga kiradi ➜ Captcha + Telefon tasdiqlaydi ➜ "
          "Siz esa darhol <b>{reward} Almaz</b> olasiz! 💎🎉\n\n"
          "🚀 <b>Nima uchun hozir boshlash kerak?</b>\n• Almazlar tez yig'iladi\n• O'yin ichida kuchliroq bo'lasiz\n"
          "• Qimmat skin va itemlarga tez erishasiz\n• Hammasi <b>BEPUL</b> 🎁\n\n"
          "👇 <b>Sizning shaxsiy taklif havolangiz:</b>\n👉 {link}\n\n"
          "🔥 Qancha ko'p do'st — shuncha ko'p Almaz!\nBugunoq boshlang — natija darhol ko'rina boshlaydi! 💎😎")
D_PROMO = ("🎁 <b>PROMO KOD</b>\n\nYouTuberlarimiz va adminlarimiz tomonidan tarqatiladigan maxsus promo kodlarni "
           "shu yerda faollashtiring va bepul Almazlarga ega bo'ling! 💎\n\n"
           "⚡ <i>Ayrim promo kodlar cheklangan miqdorda bo'ladi.</i>\n"
           "⏰ <i>Ayrim promo kodlarning amal qilish muddati bo'ladi.</i>\n\n👇 <b>Promo kodingizni yuboring:</b>")
D_PHONE = ("⚙️ <b>Telefonga mos Free Fire sozlamalari</b>\n\nAI sizning qurilmangiz uchun ideal nastroykani yaratadi:\n"
           "• General / Red Dot / 2X / 4X / AWM\n• DPI tavsiyasi\n• Otish tugmasi o'lchami\n"
           "• Lagni kamaytirish bo'yicha maslahatlar\n• 350+ telefon modeli uchun PRO optimizatsiya 😎\n\n"
           "📱 <b>Telefon modelini kiriting:</b>\nMasalan: <code>Redmi Note 9</code>, <code>Samsung A12</code>, "
           "<code>iPhone 11</code>\n\n❗️ <b>Telefon nomini to'g'ri yozing:</b>\n"
           "<code>Redmi note13pro</code> ❌ — noto'g'ri\n<code>Redmi note 13 pro</code> ✅ — to'g'ri (bo'sh joyga e'tibor bering)\n\n"
           "🔥 Sizga PRO-level Free Fire nastroykani tayyorlab beraman!")
D_AI = ("🤖 <b>Sun'iy Intellekt markazi</b>\n\nBu yerda siz Free Fire bo'yicha eng professional AI xizmatlaridan foydalana olasiz.\n\n"
        "👇 Quyidagi bo'limlardan birini tanlang:\n• 🤖 AI Suhbat — Pro Coach bilan suhbat\n"
        "• 🎭 Personaj Ovozida AI — FF qahramonlari ohangida javob\n• ✨ Nickname Yaratish — stilli nik")
D_GUIDE = ("ℹ️ <b>Sherik topish — qo'llanma</b>\n\n1️⃣ <b>Mening pasportim</b> — FF ID, level, mikrofon va izohni to'ldiring.\n"
           "2️⃣ <b>E'lon berish</b> — rejimni tanlang, e'loningiz {hours} soat faol bo'ladi.\n"
           "3️⃣ <b>Sherik qidirish</b> — e'lonlarni ko'ring, 🤝 taklif yuboring.\n"
           "4️⃣ Taklif qabul qilinsa, ikkalangizga ham bir-biringizning profilingiz yuboriladi.\n\n"
           "⚠️ Haqoratli izohlar uchun ban beriladi.")
D_HOW = ("📈 <b>Darajani qanday oshiramiz?</b>\n\n• 👥 Har bir tasdiqlangan do'st uchun <b>+{pts} ball</b>\n"
         "• 🎁 Promo kod va bonus tasklardan foydalaning\n• 🔥 Faol bo'ling — yangi ball manbalari qo'shiladi!\n\n"
         "Ball oshgan sari liga ko'tariladi va ligalar uchun maxsus bonuslar beriladi. 🚀")
D_SHOP = "🛒 <b>FF Market & Exchange</b>\n\nKategoriyani tanlang:"
D_SERVICE = "🎥 <b>Youtuber bilan aloqa</b>\n\nXizmatlardan birini tanlang:"
D_SUB = "📣 <b>Botdan foydalanish uchun quyidagi kanallarga a'zo bo'ling:</b>"
D_BAN = "🚫 Siz botdan foydalanishdan chetlatilgansiz."
D_MAINT = "🛠 Botda texnik ishlar olib borilmoqda. Birozdan so'ng urinib ko'ring."
D_CHARS = ("Alok|Sho'x va energiyali DJ, musiqa va ritm haqida gapiradi\n"
           "Chrono|Jiddiy strateg, qisqa va aniq gapiradi\n"
           "K|Xotirjam professor, ilmiy tushuntiradi\n"
           "Kelly|Tez va jasur sprinter, shiddatli gapiradi\n"
           "Jota|Hazilkash ovchi, hazil bilan gapiradi\n"
           "Skyler|Baland ruhli, ovozi balandday gapiradi\n"
           "Hayato|Samuray, hurmat va intizom bilan gapiradi\n"
           "Moco|Sirli xaker, jumboqlar bilan gapiradi")
D_SYS = ("Sen Free Fire bo'yicha professional Pro Coach san. Faqat o'zbek tilida (lotin) javob ber. "
         "Qisqa, aniq va amaliy maslahat ber: taktika, qurollar, xarita, rotatsiya, aim, jamoaviy o'yin.")

# key: (default, label, type)  type: int | bool | text
S = {
    "bot_enabled": ("1", "🔌 Bot yoqilgan", "bool"),
    "captcha_on": ("1", "🤖 Captcha", "bool"),
    "phone_on": ("1", "📱 Telefon tasdiqlash", "bool"),
    "phone_prefix": ("998", "🌍 Telefon prefiksi (bo'sh=hammasi)", "text"),
    "ref_reward": ("7", "💎 Taklif uchun almaz", "int"),
    "ref_points": ("10", "⭐ Taklif uchun ball", "int"),
    "start_bonus": ("0", "🎉 Yangi foydalanuvchi bonusi", "int"),
    "free_spins": ("1", "🆓 Kunlik bepul spin", "int"),
    "paid_spins": ("2", "💰 Kunlik pullik spin", "int"),
    "spin_price": ("5", "💎 Pullik spin narxi", "int"),
    "min_withdraw": ("50", "💳 Minimal yechish (almaz)", "int"),
    "ai_limit": ("30", "🤖 Kunlik AI limit", "int"),
    "ad_hours": ("24", "⏰ Sherik e'loni muddati (soat)", "int"),
    "proofs_url": ("", "📜 Isbotlar havolasi (URL)", "text"),
    "proofs_chat": ("", "📣 Isbotlar kanali (@kanal yoki ID)", "text"),
    "ai_model_groq": ("llama-3.3-70b-versatile", "🧠 Groq modeli", "text"),
    "ai_model_or": ("meta-llama/llama-3.3-70b-instruct:free", "🧠 OpenRouter modeli", "text"),
    "ai_system": (D_SYS, "🧠 AI Coach system prompt", "text"),
    "ai_characters": (D_CHARS, "🎭 AI personajlar (Nom|uslub)", "text"),
    "welcome": (D_WELCOME, "📝 Start matni", "text"),
    "earn_text": (D_EARN, "📝 Almaz ishlash matni", "text"),
    "promo_text": (D_PROMO, "📝 Promo matni", "text"),
    "phone_text": (D_PHONE, "📝 Nastroyka matni", "text"),
    "ai_text": (D_AI, "📝 AI markaz matni", "text"),
    "guide_text": (D_GUIDE, "📝 Sherik qo'llanma", "text"),
    "how_text": (D_HOW, "📝 Daraja oshirish matni", "text"),
    "shop_text": (D_SHOP, "📝 Do'kon matni", "text"),
    "service_text": (D_SERVICE, "📝 Xizmatlar matni", "text"),
    "sub_text": (D_SUB, "📝 Obuna matni", "text"),
    "banned_text": (D_BAN, "📝 Ban matni", "text"),
    "maint_text": (D_MAINT, "📝 Texnik ish matni", "text"),
}


def st(k):
    r = one("SELECT v FROM settings WHERE k=?", (k,))
    return r["v"] if r else S[k][0]


def sti(k):
    try:
        return int(float(st(k)))
    except Exception:
        return int(float(S[k][0]))


def stb(k):
    return st(k) == "1"


def set_st(k, v):
    ex("INSERT INTO settings(k,v) VALUES(?,?) ON CONFLICT(k) DO UPDATE SET v=excluded.v", (k, v))


# ───────────────────────── YORDAMCHILAR ─────────────────────────
def now():
    return datetime.now(TZ)


def today():
    return now().strftime("%Y-%m-%d")


def ts():
    return int(time.time())


def esc(s):
    return html.escape(str(s if s is not None else ""))


def C(t, d):
    return IB(t, callback_data=d)


def rk(*rws):
    return ReplyKeyboardMarkup([list(r) for r in rws], resize_keyboard=True)


def admin_ids():
    return set(ENV_ADMINS) | {r["id"] for r in rows("SELECT id FROM admins")}


def is_admin(uid):
    return uid in ENV_ADMINS or bool(one("SELECT 1 FROM admins WHERE id=?", (uid,)))


def get_user(uid):
    return one("SELECT * FROM users WHERE id=?", (uid,))


def upd(uid, **kw):
    ex("UPDATE users SET " + ",".join(f"{k}=?" for k in kw) + " WHERE id=?", (*kw.values(), uid))


def credit(uid, n):
    ex("UPDATE users SET balance=balance+?, earned=earned+? WHERE id=?", (n, max(n, 0), uid))


def tag(r):
    return "@" + esc(r["username"]) if r["username"] else f"ID:{r['id']}"


def mention(r):
    return f'<a href="tg://user?id={r["id"]}">{esc(r["name"] or r["id"])}</a>'


def ensure_user(u, ref=None):
    r = get_user(u.id)
    name = (u.full_name or "")[:60]
    if r is None:
        rb = ref if ref and ref != u.id and get_user(ref) else None
        ex("INSERT INTO users(id,username,name,ref_by,joined) VALUES(?,?,?,?,?)", (u.id, u.username, name, rb, ts()))
    elif r["username"] != u.username or r["name"] != name:
        ex("UPDATE users SET username=?,name=? WHERE id=?", (u.username, name, u.id))
    return get_user(u.id)


async def say(update, text, kb=None, plain=False, preview=False):
    m = update.effective_message
    parts = [text[i:i + 4000] for i in range(0, len(text), 4000)] or ["."]
    for i, p in enumerate(parts):
        k = kb if i == len(parts) - 1 else None
        try:
            await m.reply_text(p, parse_mode=None if plain else HTML, reply_markup=k,
                               disable_web_page_preview=not preview)
        except Exception as e:
            if "parse" in str(e).lower():
                await m.reply_text(p, reply_markup=k, disable_web_page_preview=True)
            else:
                raise


async def aedit(update, text, kb=None):
    q = update.callback_query
    try:
        await q.message.edit_text(text, parse_mode=HTML, reply_markup=kb, disable_web_page_preview=True)
    except Exception as e:
        if "not modified" not in str(e).lower():
            await q.message.reply_text(text, parse_mode=HTML, reply_markup=kb, disable_web_page_preview=True)


def setst(ctx, name, **kw):
    ctx.user_data["st"] = {"n": name, **kw}


def getst(ctx):
    return ctx.user_data.get("st")


def clrst(ctx):
    ctx.user_data.pop("st", None)


async def notify_admins(ctx, text, kb=None):
    for i in admin_ids():
        try:
            await ctx.bot.send_message(i, text, parse_mode=HTML, reply_markup=kb)
        except Exception:
            pass


# ───────────────────────── MENYU ─────────────────────────
B_BACK = "⬅️ Orqaga"
B_AI, B_EARN, B_PROMO = "🤖 Sun'iy Intellekt", "💎 Almaz ishlash", "🎁 Promo kod"
B_SPIN, B_PHONE, B_PARTNER = "🎰 Spin", "⚙️ Telefonga Nastroyka", "🤝 Sherik Topish"
B_TOP, B_PROF, B_LVL = "🏆 Reyting", "📊 Profilim", "🥇 Mening darajam"
B_SHOP, B_YT, B_ADMIN = "🛒 O'yin Do'koni", "🎥 Youtuber Xizmatlari", "👑 Admin panel"
L_SPIN, L_MYSPIN, L_BONUS = "🎰 Spin qilish", "📊 Mening spinlarim", "🎁 Bonus spin olish"
L_PHIST = "📜 Mening promo tarixim"
L_FIND, L_AD, L_PASS, L_GUIDE = "🔍 Sherik Qidirish", "📢 E'lon Berish", "👤 Mening Pasportim", "ℹ️ Qo'llanma"
L_AICHAT, L_AICHAR, L_AINICK = "🤖 AI Suhbat", "🎭 Personaj Ovozida AI", "✨ Nickname Yaratish"
L_SVC, L_MYORD = "📺 Xizmatlar ro'yxati", "📰 Mening Buyurtmalarim"
CATS = {"direct": "💎 To'g'ridan-to'g'ri yechish", "pack": "🎁 Naborlar", "emote": "🕺 Emotsiyalar",
        "skin": "🛡 Stenkalar & Skinlar", "service": "🎥 Youtuber xizmatlari"}
MODES = ["BR-Ranked", "CS-Ranked", "Ultimatum"]


def main_kb(uid):
    r = [[B_AI], [B_EARN, B_PROMO], [B_SPIN, B_PHONE], [B_PARTNER, B_TOP], [B_PROF, B_LVL], [B_SHOP, B_YT]]
    if is_admin(uid):
        r.append([B_ADMIN])
    return ReplyKeyboardMarkup(r, resize_keyboard=True)


async def home(update, ctx):
    clrst(ctx)
    await say(update, "🏠 Bosh menyuga qaytdingiz", main_kb(update.effective_user.id))


# ───────────────────────── TEKSHIRUV (captcha, telefon, obuna) ─────────────────────────
SUBC = {}


async def missing_subs(ctx, uid):
    chs = rows("SELECT * FROM channels")
    if not chs or SUBC.get(uid, 0) > time.time():
        return []
    miss = []
    for c in chs:
        try:
            m = await ctx.bot.get_chat_member(c["chat_id"], uid)
            if m.status in ("left", "kicked"):
                miss.append(c)
        except Exception as e:
            log.warning("sub check %s: %s", c["chat_id"], e)
    if not miss:
        SUBC[uid] = time.time() + 120
    return miss


async def verify_step(update, ctx, r):
    if stb("captcha_on") and not ctx.user_data.get("cap_ok"):
        a, b = random.randint(2, 9), random.randint(2, 9)
        opts = {a + b}
        while len(opts) < 4:
            opts.add(random.randint(4, 18))
        opts = random.sample(sorted(opts), 4)
        ctx.user_data["cap"] = a + b
        await say(update, f"🤖 <b>Captcha</b>\n\nRobot emasligingizni tasdiqlang:\n<b>{a} + {b} = ?</b>",
                  IM([[C(str(o), f"cap:{o}") for o in opts]]))
        return
    if stb("phone_on") and not r["phone"]:
        kb = ReplyKeyboardMarkup([[KeyboardButton("📱 Telefon raqamni yuborish", request_contact=True)]],
                                 resize_keyboard=True, one_time_keyboard=True)
        await say(update, "📱 <b>Telefon raqamingizni tasdiqlang</b>\n\nPastdagi tugmani bosing 👇", kb)
        return
    await finish_verify(update, ctx)


async def gate(update, ctx):
    u = update.effective_user
    r = ensure_user(u)
    if is_admin(u.id):
        return True
    if r["banned"]:
        await say(update, st("banned_text"))
        return False
    if not stb("bot_enabled"):
        await say(update, st("maint_text"))
        return False
    if not r["verified"]:
        await verify_step(update, ctx, r)
        return False
    miss = await missing_subs(ctx, u.id)
    if miss:
        kb = [[IB(f"📣 {c['title']}", url=c["url"])] for c in miss if c["url"]]
        kb.append([C("✅ Tekshirish", "sub:check")])
        await say(update, st("sub_text"), IM(kb))
        return False
    return True


async def finish_verify(update, ctx):
    u = update.effective_user
    r = get_user(u.id)
    if not r["verified"]:
        upd(u.id, verified=1)
        if sti("start_bonus"):
            credit(u.id, sti("start_bonus"))
        ref = r["ref_by"]
        rr = get_user(ref) if ref else None
        if rr and not rr["banned"]:
            credit(ref, sti("ref_reward"))
            ex("UPDATE users SET points=points+?, refs=refs+1 WHERE id=?", (sti("ref_points"), ref))
            try:
                await ctx.bot.send_message(
                    ref, f"🎉 Do'stingiz <b>{esc(u.full_name)}</b> tasdiqlandi!\n💎 +{sti('ref_reward')} Almaz hisobingizga qo'shildi.",
                    parse_mode=HTML)
            except Exception:
                pass
    if await gate(update, ctx):
        await say(update, st("welcome"), main_kb(u.id))


async def start(update, ctx):
    ref = None
    if ctx.args and ctx.args[0].startswith("ref_"):
        try:
            ref = int(ctx.args[0][4:])
        except ValueError:
            pass
    ensure_user(update.effective_user, ref)
    clrst(ctx)
    if await gate(update, ctx):
        await say(update, st("welcome"), main_kb(update.effective_user.id))


async def on_contact(update, ctx):
    u, c = update.effective_user, update.message.contact
    ensure_user(u)
    if c.user_id != u.id:
        return await say(update, "❌ Faqat o'zingizning raqamingizni yuboring.")
    phone = re.sub(r"\D", "", c.phone_number or "")
    pre = re.sub(r"\D", "", st("phone_prefix"))
    if pre and not phone.startswith(pre):
        return await say(update, f"❌ Faqat +{pre} raqamlari qabul qilinadi.")
    if one("SELECT 1 FROM users WHERE phone=? AND id!=?", (phone, u.id)):
        return await say(update, "❌ Bu raqam boshqa akkauntga bog'langan.")
    upd(u.id, phone=phone)
    await finish_verify(update, ctx)


async def cb_cap(update, ctx, a):
    q = update.callback_query
    if str(ctx.user_data.get("cap")) == a[1]:
        ctx.user_data["cap_ok"] = True
        await q.answer("✅ To'g'ri!")
        try:
            await q.message.delete()
        except Exception:
            pass
        await verify_step(update, ctx, get_user(q.from_user.id))
    else:
        await q.answer("❌ Noto'g'ri, qayta urinib ko'ring", show_alert=True)
        try:
            await q.message.delete()
        except Exception:
            pass
        await verify_step(update, ctx, get_user(q.from_user.id))


async def cb_sub(update, ctx, a):
    q = update.callback_query
    SUBC.pop(q.from_user.id, None)
    if await missing_subs(ctx, q.from_user.id):
        return await q.answer("❌ Hali hamma kanalga a'zo bo'lmadingiz", show_alert=True)
    await q.answer("✅ Rahmat!")
    try:
        await q.message.delete()
    except Exception:
        pass
    await say(update, st("welcome"), main_kb(q.from_user.id))


# ───────────────────────── AI ─────────────────────────
async def ask_ai(messages, max_tokens=900):
    async with httpx.AsyncClient(timeout=60) as c:
        for url, key, model in (("https://api.groq.com/openai/v1/chat/completions", GROQ_KEY, st("ai_model_groq")),
                                ("https://openrouter.ai/api/v1/chat/completions", OR_KEY, st("ai_model_or"))):
            if not key:
                continue
            try:
                r = await c.post(url, headers={"Authorization": f"Bearer {key}"},
                                 json={"model": model, "messages": messages, "max_tokens": max_tokens, "temperature": 0.7})
                if r.status_code == 200:
                    return r.json()["choices"][0]["message"]["content"].strip()
                log.warning("AI %s -> %s %s", model, r.status_code, r.text[:200])
            except Exception as e:
                log.warning("AI error: %s", e)
    return None


async def ai_quota(update):
    uid = update.effective_user.id
    if is_admin(uid):
        return True
    r = get_user(uid)
    if r["ai_date"] != today():
        ex("UPDATE users SET ai_date=?, ai_count=0 WHERE id=?", (today(), uid))
        r = get_user(uid)
    if r["ai_count"] >= sti("ai_limit"):
        await say(update, f"⛔ Bugungi AI limitingiz tugadi ({sti('ai_limit')} ta). Ertaga qaytib keling!")
        return False
    ex("UPDATE users SET ai_count=ai_count+1 WHERE id=?", (uid,))
    return True


async def ai_run(update, system, history, text, max_tokens=900):
    await update.effective_chat.send_action("typing")
    msgs = [{"role": "system", "content": system}] + history[-8:] + [{"role": "user", "content": text}]
    ans = await ask_ai(msgs, max_tokens)
    if not ans:
        await say(update, "⚠️ AI hozircha javob bera olmadi. Birozdan so'ng urinib ko'ring.")
        return None
    await say(update, ans, plain=True)
    return ans


async def ai_menu(update, ctx):
    clrst(ctx)
    await say(update, st("ai_text"), rk([L_AICHAT], [L_AICHAR], [L_AINICK], [B_BACK]))


async def ai_chat_start(update, ctx):
    setst(ctx, "ai_chat", h=[])
    await say(update, "🤖 <b>Pro Coach</b> tayyor! Free Fire bo'yicha savolingizni yozing 👇", rk([B_BACK]))


async def st_ai_chat(update, ctx, s, t):
    if not await ai_quota(update):
        return
    ans = await ai_run(update, st("ai_system"), s["h"], t)
    if ans:
        s["h"] += [{"role": "user", "content": t}, {"role": "assistant", "content": ans}]
        s["h"][:] = s["h"][-12:]


def chars():
    return [l.split("|", 1) for l in st("ai_characters").splitlines() if "|" in l]


async def ai_char_start(update, ctx):
    cs = chars()
    if not cs:
        return await say(update, "Hozircha personajlar yo'q.")
    names = [c[0].strip() for c in cs]
    setst(ctx, "ai_charpick")
    kb = [names[i:i + 2] for i in range(0, len(names), 2)] + [[B_BACK]]
    await say(update, "🎭 <b>Qaysi personaj ovozida gaplashamiz?</b>", rk(*kb))


async def st_ai_charpick(update, ctx, s, t):
    for n, style in chars():
        if n.strip() == t:
            sysmsg = (f"Sen Free Fire qahramoni {n.strip()} sifatida gapirasan. Uslubing: {style.strip()}. "
                      "O'zbek tilida (lotin) qisqa va qiziqarli javob ber, personajdan chiqma.")
            setst(ctx, "ai_char", sys=sysmsg, h=[], name=n.strip())
            return await say(update, f"🎭 <b>{esc(n.strip())}</b> ovozida gaplashing! Xabar yozing 👇", rk([B_BACK]))
    await say(update, "Personajni tugmalardan tanlang 👇")


async def st_ai_char(update, ctx, s, t):
    if not await ai_quota(update):
        return
    ans = await ai_run(update, s["sys"], s["h"], t, 500)
    if ans:
        s["h"] += [{"role": "user", "content": t}, {"role": "assistant", "content": ans}]
        s["h"][:] = s["h"][-10:]


async def ai_nick_start(update, ctx):
    setst(ctx, "ai_nick")
    await say(update, "✨ <b>Nickname yaratish</b>\n\nIsmingiz yoki uslubni yozing (masalan: <code>Akmal olov</code>):", rk([B_BACK]))


async def st_ai_nick(update, ctx, s, t):
    if not await ai_quota(update):
        return
    await ai_run(update, "Sen Free Fire uchun stilli nickname yaratuvchisan. Foydalanuvchi so'ziga qarab 10 ta "
                         "noyob, chiroyli nik taklif qil (maxsus belgilar, ramzlar bilan). Faqat ro'yxat ber, har biri alohida qatorda.",
                 [], t, 400)


async def st_phone(update, ctx, s, t):
    if not await ai_quota(update):
        return
    sysmsg = ("Sen Free Fire sensitivity mutaxassisisan. O'zbek tilida (lotin) javob ber. Berilgan telefon modeli uchun "
              "sozlamalarni ber: General, Red Dot, 2X Scope, 4X Scope, AWM Scope (0-100 raqamlarda), DPI tavsiyasi, "
              "otish tugmasi o'lchami (%), grafika va lagni kamaytirish maslahatlari. Oxirida: 'Bu taxminiy sozlama, "
              "o'zingizga moslab sozlang' deb qo'sh. Model noma'lum bo'lsa, shu sinfdagi telefonlar uchun umumiy tavsiya ber.")
    await ai_run(update, sysmsg, [], f"Telefon modeli: {t}", 1000)


# ───────────────────────── ALMAZ ISHLASH ─────────────────────────
async def earn(update, ctx):
    clrst(ctx)
    uid = update.effective_user.id
    link = f"https://t.me/{ctx.bot.username}?start=ref_{uid}"
    text = st("earn_text").replace("{reward}", str(sti("ref_reward"))).replace("{link}", link)
    share = "https://t.me/share/url?url=" + link + "&text=" + "Free%20Fire%20almaz%20bepul!%20%F0%9F%92%8E"
    await say(update, text, IM([[IB("📤 Do'stlarga ulashish", url=share)]]), preview=True)


# ───────────────────────── PROMO ─────────────────────────
async def promo_menu(update, ctx):
    setst(ctx, "promo")
    await say(update, st("promo_text"), rk([L_PHIST], [B_BACK]))


async def promo_hist(update, ctx):
    rs = rows("SELECT u.code,u.ts,p.amount FROM promo_uses u LEFT JOIN promos p ON p.code=u.code "
              "WHERE u.user_id=? ORDER BY u.id DESC LIMIT 15", (update.effective_user.id,))
    if not rs:
        return await say(update, "📜 Siz hali promo kod ishlatmagansiz.")
    ln = [f"• <code>{esc(r['code'])}</code> — 💎 {r['amount'] or '?'} — {datetime.fromtimestamp(r['ts'], TZ):%d.%m.%Y}" for r in rs]
    await say(update, "📜 <b>Promo tarixim:</b>\n\n" + "\n".join(ln))


async def st_promo(update, ctx, s, t):
    uid = update.effective_user.id
    code = t.strip().upper()
    p = one("SELECT * FROM promos WHERE code=?", (code,))
    if not p:
        return await say(update, "❌ Bunday promo kod topilmadi.")
    if p["expires"] and p["expires"] < ts():
        return await say(update, "⏰ Bu promo kodning muddati tugagan.")
    if p["max_uses"] and p["used"] >= p["max_uses"]:
        return await say(update, "😔 Bu promo kod limiti tugagan.")
    if one("SELECT 1 FROM promo_uses WHERE user_id=? AND code=?", (uid, code)):
        return await say(update, "⚠️ Siz bu promo kodni allaqachon ishlatgansiz.")
    ex("UPDATE promos SET used=used+1 WHERE code=?", (code,))
    ex("INSERT INTO promo_uses(user_id,code,ts) VALUES(?,?,?)", (uid, code, ts()))
    credit(uid, p["amount"])
    await say(update, f"🎉 Promo kod faollashtirildi!\n💎 +{p['amount']} Almaz\n💰 Balans: {get_user(uid)['balance']} 💎")


# ───────────────────────── SPIN ─────────────────────────
def spin_info(uid):
    r = get_user(uid)
    if r["spin_date"] != today():
        ex("UPDATE users SET spin_date=?, free_used=0, paid_used=0 WHERE id=?", (today(), uid))
        r = get_user(uid)
    return (max(0, sti("free_spins") - r["free_used"]), max(0, sti("paid_spins") - r["paid_used"]),
            r["bonus_spins"], r["balance"])


def spin_kb(uid):
    f, p, b, _ = spin_info(uid)
    k = []
    if f:
        k.append([C(f"🆓 Bepul spin ({f} ta)", "spin:free")])
    if p:
        k.append([C(f"💰 Pullik spin ({p} ta) - {sti('spin_price')} 💎", "spin:paid")])
    if b:
        k.append([C(f"🎁 Bonus spin ({b} ta)", "spin:bonus")])
    k.append([C("⬅️ Orqaga", "spin:back")])
    return IM(k)


async def spin_menu(update, ctx):
    clrst(ctx)
    f, p, b, bal = spin_info(update.effective_user.id)
    await say(update, f"🎰 <b>Spin Tizimi</b>\n\nXush kelibsiz! Spin orqali almaz yutib olishingiz mumkin!\n\n"
                      f"📊 <b>Bugungi holat:</b>\n🆓 Bepul spin: {f} ta\n💰 Pullik spin: {p} ta (har biri {sti('spin_price')} 💎)\n"
                      f"🎁 Bonus spin: {b} ta\n\n💎 Sizning balansingiz: {bal}\n\n"
                      f"⚠️ Bonus spin olish uchun avval 🎁 <b>Bonus spin olish</b> tugmasini bosing!",
              rk([L_SPIN], [L_MYSPIN], [L_BONUS], [B_BACK]))


async def spin_choose(update, ctx):
    uid = update.effective_user.id
    f, p, b, bal = spin_info(uid)
    await say(update, f"🎰 <b>Spin Tanlang</b>\n\nQaysi turdagi spinni ishlatmoqchisiz?\n\n"
                      f"🆓 <b>Bepul spin:</b> {f} ta qolgan\n   ➜ Bepul! Hech qanday almaz sarflamaysiz.\n\n"
                      f"💰 <b>Pullik spin:</b> {p} ta qolgan\n   ➜ Har bir spin uchun {sti('spin_price')} 💎 sarflanadi.\n\n"
                      f"🎁 <b>Bonus spin:</b> {b} ta\n   ➜ Task bajaring, bonus oling.\n\n💎 Sizning balansingiz: {bal} 💎",
              spin_kb(uid))


async def my_spins(update, ctx):
    f, p, b, bal = spin_info(update.effective_user.id)
    await say(update, f"📊 <b>Qolgan spinlar:</b>\n🆓 Bepul: {f}\n💰 Pullik: {p}\n🎁 Bonus: {b}\n\n💎 Balans: {bal} 💎",
              spin_kb(update.effective_user.id))


async def cb_spin(update, ctx, a):
    q = update.callback_query
    uid, kind = q.from_user.id, a[1]
    if kind == "back":
        await q.answer()
        try:
            await q.message.delete()
        except Exception:
            pass
        return
    f, p, b, bal = spin_info(uid)
    if (kind == "free" and f <= 0) or (kind == "paid" and p <= 0) or (kind == "bonus" and b <= 0):
        return await q.answer("❌ Bu turdagi spin qolmagan", show_alert=True)
    if kind == "paid":
        if bal < sti("spin_price"):
            return await q.answer("❌ Almaz yetarli emas", show_alert=True)
        ex("UPDATE users SET balance=balance-?, paid_used=paid_used+1 WHERE id=?", (sti("spin_price"), uid))
    elif kind == "free":
        ex("UPDATE users SET free_used=free_used+1 WHERE id=?", (uid,))
    else:
        ex("UPDATE users SET bonus_spins=bonus_spins-1 WHERE id=?", (uid,))
    pr = rows("SELECT * FROM prizes")
    if not pr:
        return await q.answer("Sovrinlar sozlanmagan", show_alert=True)
    total = sum(x["weight"] for x in pr)
    x, acc, win = random.uniform(0, total), 0, pr[-1]
    for pz in pr:
        acc += pz["weight"]
        if x <= acc:
            win = pz
            break
    amount, prob = win["amount"], win["weight"] / total * 100
    if amount:
        credit(uid, amount)
    ex("UPDATE users SET spins_total=spins_total+1 WHERE id=?", (uid,))
    await q.answer()
    try:
        await q.message.edit_text("🎰 Spin aylanmoqda... 🍒🍋🔔")
        await asyncio.sleep(1.2)
        if amount:
            res = (f"🎉 <b>TABRIKLAYMIZ!</b>\n\n🎰 Siz yutib oldingiz: <b>{amount}</b> 💎!\n\n"
                   f"🎁 Mukofot: {amount} Almaz 💎\n📊 Ehtimollik: {prob:.1f}%")
        else:
            res = f"😔 <b>Bu safar omad kelmadi...</b>\n\n📊 Ehtimollik: {prob:.1f}%\nYana urinib ko'ring!"
        await q.message.edit_text(res, parse_mode=HTML)
    except Exception:
        pass
    f, p, b, bal = spin_info(uid)
    await q.message.reply_text(f"📊 <b>Qolgan spinlar:</b>\n🆓 Bepul: {f}\n💰 Pullik: {p}\n🎁 Bonus: {b}\n\n"
                               f"💎 Yangi balans: {bal} 💎", parse_mode=HTML, reply_markup=spin_kb(uid))


async def bonus_menu(update, ctx):
    uid = update.effective_user.id
    if get_user(uid)["bonus_date"] == today():
        return await say(update, "✅ Bugungi bonus spinni allaqachon olgansiz. Ertaga yana urinib ko'ring!")
    tk = rows("SELECT * FROM tasks")
    if not tk:
        ex("UPDATE users SET bonus_spins=bonus_spins+1, bonus_date=? WHERE id=?", (today(), uid))
        return await say(update, "🎁 Bonus spin berildi! 🎰 Spin bo'limiga o'ting.")
    kb = [[IB(f"📌 {t['title']}", url=t["url"])] for t in tk if t["url"]] + [[C("✅ Tekshirish", "bonus:check")]]
    await say(update, "🎁 <b>Bonus spin olish</b>\n\nQuyidagi tasklarni bajaring va <b>✅ Tekshirish</b> tugmasini bosing:", IM(kb))


async def cb_bonus(update, ctx, a):
    q = update.callback_query
    uid = q.from_user.id
    if get_user(uid)["bonus_date"] == today():
        return await q.answer("Bugun bonus olgansiz", show_alert=True)
    for t in rows("SELECT * FROM tasks WHERE chat_id!='' AND chat_id IS NOT NULL"):
        try:
            m = await ctx.bot.get_chat_member(t["chat_id"], uid)
            if m.status in ("left", "kicked"):
                return await q.answer(f"❌ Bajarilmadi: {t['title']}", show_alert=True)
        except Exception:
            pass
    ex("UPDATE users SET bonus_spins=bonus_spins+1, bonus_date=? WHERE id=?", (today(), uid))
    await q.answer("🎁 Bonus spin berildi!", show_alert=True)
    await aedit(update, "✅ Tasklar bajarildi! 🎁 Bonus spin hisobingizga qo'shildi. 🎰 Spin bo'limiga o'ting.")


# ───────────────────────── REYTING / PROFIL / DARAJA ─────────────────────────
async def top(update, ctx):
    rs = rows("SELECT id,username,earned FROM users WHERE banned=0 AND verified=1 ORDER BY earned DESC LIMIT 10")
    ln = [f"⭐ {'@' + esc(r['username']) if r['username'] else 'Foydalanuvchi (ID: ' + str(r['id']) + ')'} — 💎 {r['earned']}" for r in rs]
    kb = None
    if st("proofs_url").startswith("http"):
        kb = IM([[IB("📜 Isbotlar", url=st("proofs_url"))]])
    await say(update, "🏆 <b>Top 10 foydalanuvchi (Almaz bo'yicha):</b>\n\n" + ("\n".join(ln) or "Hozircha bo'sh"), kb)


def league_of(pts):
    cur = nxt = None
    for l in rows("SELECT * FROM leagues ORDER BY min_points"):
        if l["min_points"] <= pts:
            cur = l
        elif nxt is None:
            nxt = l
    return cur, nxt


def lg_name(l):
    return f"{l['emoji']} {l['name']} liga" if l else "—"


async def profile(update, ctx):
    clrst(ctx)
    r = get_user(update.effective_user.id)
    cur, _ = league_of(r["points"])
    await say(update, f"👤 <b>Profilingiz</b>\n\n👤 Username: {tag(r)}\n🎮 Free Fire ID: <code>{esc(r['ff_id']) or 'Kiritilmagan'}</code>\n"
                      f"🥇 Liga: <b>{lg_name(cur)}</b>\n📊 Reyting ballari: {r['points']}\n💎 Almaz: <b>{r['balance']}</b>\n"
                      f"🤝 Umumiy tasdiqlangan takliflar: {r['refs']}\n\nAlmazlaringizni istagan paytda yechib olishingiz mumkin 👇",
              IM([[C("💳 Almazni yechish", "prof:wd"), C("🆔 FF ID sozlash", "prof:ff")], [C("📈 Statistika", "prof:stat")]]))


async def cb_prof(update, ctx, a):
    q, uid = update.callback_query, update.callback_query.from_user.id
    r = get_user(uid)
    await q.answer()
    if a[1] == "ff":
        setst(ctx, "ff_id")
        await say(update, "🆔 Free Fire ID raqamingizni yuboring (faqat raqam):", rk([B_BACK]))
    elif a[1] == "wd":
        if not r["ff_id"]:
            setst(ctx, "ff_id")
            return await say(update, "🆔 Avval Free Fire ID raqamingizni yuboring:", rk([B_BACK]))
        mn = sti("min_withdraw")
        if r["balance"] < mn:
            return await say(update, f"❌ Minimal yechish: <b>{mn} 💎</b>\nBalansingiz: {r['balance']} 💎")
        setst(ctx, "wd_amount")
        await say(update, f"💳 Qancha almaz yechmoqchisiz?\n\nMinimal: {mn} 💎 | Balans: {r['balance']} 💎\nFF ID: <code>{esc(r['ff_id'])}</code>",
                  rk([B_BACK]))
    elif a[1] == "stat":
        sp = one("SELECT COUNT(*) FROM promo_uses WHERE user_id=?", (uid,))[0]
        od = one("SELECT COUNT(*) FROM orders WHERE user_id=?", (uid,))[0]
        await say(update, f"📈 <b>Statistika</b>\n\n👥 Takliflar: {r['refs']}\n💎 Jami topilgan: {r['earned']}\n🎰 Spinlar: {r['spins_total']}\n"
                          f"🎁 Promo kodlar: {sp}\n🛒 Buyurtmalar: {od}\n📅 Qo'shilgan: {datetime.fromtimestamp(r['joined'], TZ):%d.%m.%Y}")


async def st_ff_id(update, ctx, s, t):
    if not re.fullmatch(r"\d{5,15}", t.strip()):
        return await say(update, "❌ FF ID faqat raqamlardan iborat bo'lishi kerak (5-15 ta).")
    upd(update.effective_user.id, ff_id=t.strip())
    clrst(ctx)
    await say(update, "✅ FF ID saqlandi!", main_kb(update.effective_user.id))


async def st_wd(update, ctx, s, t):
    uid = update.effective_user.id
    r = get_user(uid)
    if not t.strip().isdigit():
        return await say(update, "❌ Faqat raqam yuboring.")
    n = int(t)
    if n < sti("min_withdraw") or n > r["balance"]:
        return await say(update, f"❌ Miqdor {sti('min_withdraw')} dan {r['balance']} gacha bo'lishi kerak.")
    ex("UPDATE users SET balance=balance-? WHERE id=?", (n, uid))
    wid = ex("INSERT INTO withdrawals(user_id,amount,ff_id,status,ts) VALUES(?,?,?,'pending',?)", (uid, n, r["ff_id"], ts())).lastrowid
    clrst(ctx)
    await say(update, f"✅ So'rov #{wid} yuborildi! Admin tekshirgach almazlar FF ID ga yuboriladi.", main_kb(uid))
    await notify_admins(ctx, wd_text(wid), wd_kb(wid))


def wd_text(wid):
    w = one("SELECT * FROM withdrawals WHERE id=?", (wid,))
    u = get_user(w["user_id"])
    return f"💳 <b>Yechish #{wid}</b>\n👤 {mention(u)} ({tag(u)})\n💎 {w['amount']}\n🆔 FF ID: <code>{esc(w['ff_id'])}</code>\n📌 {w['status']}"


def wd_kb(wid):
    return IM([[C("✅ Bajarildi", f"wd:ok:{wid}"), C("❌ Rad etish", f"wd:no:{wid}")]])


async def cb_wd(update, ctx, a):
    q = update.callback_query
    w = one("SELECT * FROM withdrawals WHERE id=?", (int(a[2]),))
    if not w or w["status"] != "pending":
        return await q.answer("Allaqachon ko'rib chiqilgan", show_alert=True)
    if a[1] == "ok":
        ex("UPDATE withdrawals SET status='done' WHERE id=?", (w["id"],))
        msg, mark = f"✅ Yechish #{w['id']} bajarildi! 💎 {w['amount']} FF ID ga yuborildi.", "✅ BAJARILDI"
        pc = st("proofs_chat").strip()
        if pc:
            u = get_user(w["user_id"])
            try:
                await ctx.bot.send_message(pc if not pc.lstrip("-").isdigit() else int(pc),
                                           f"✅ <b>Yangi to'lov!</b>\n👤 {tag(u)}\n💎 {w['amount']} Almaz yechildi", parse_mode=HTML)
            except Exception as e:
                log.warning("proof: %s", e)
    else:
        ex("UPDATE withdrawals SET status='rejected' WHERE id=?", (w["id"],))
        ex("UPDATE users SET balance=balance+? WHERE id=?", (w["amount"], w["user_id"]))
        msg, mark = f"❌ Yechish #{w['id']} rad etildi. 💎 {w['amount']} balansingizga qaytarildi.", "❌ RAD ETILDI"
    await q.answer("Bajarildi")
    await aedit(update, wd_text(w["id"]) + f"\n\n<b>{mark}</b>")
    try:
        await ctx.bot.send_message(w["user_id"], msg)
    except Exception:
        pass


async def level_menu(update, ctx):
    r = get_user(update.effective_user.id)
    cur, nxt = league_of(r["points"])
    nx = f"🎯 <b>Keyingi liga:</b> {nxt['name']}\nUnga yetish uchun yana <b>{nxt['min_points'] - r['points']}</b> ball kerak." if nxt \
        else "🏆 Siz eng yuqori ligadasiz!"
    await say(update, f"🥇 <b>Mening darajam</b>\n\n⭐ Joriy liga: <b>{lg_name(cur)}</b>\n📊 Reyting ballari: {r['points']}\n\n"
                      f"📌 <b>Shaxsiy statistika:</b>\n• Tasdiqlangan takliflar: {r['refs']}\n• Umumiy takliflar: {r['refs']}\n"
                      f"• Joriy Almaz balansi: {r['balance']}\n\n{nx}\n\nℹ️ <i>Eslatma:</i> Ligalar hozircha faqat obro' sifatida ishlaydi.\n"
                      f"🔜 Yaqin vaqt ichida ligalar uchun alohida bonuslar qo'shiladi.\nFaol bo'ling — birinchilar qatorida bo'lasiz! 🚀",
              IM([[C("Darajani qanday oshiramiz ❓", "lvl:how")]]))


async def cb_lvl(update, ctx, a):
    await update.callback_query.answer()
    await say(update, st("how_text").replace("{pts}", str(sti("ref_points"))))


# ───────────────────────── SHERIK TOPISH ─────────────────────────
PKB = (L_FIND, L_AD, L_PASS, L_GUIDE)
INV = {}


async def partner_menu(update, ctx):
    clrst(ctx)
    await say(update, "⚔️ <b>Sherik topish (Beta)</b>\n\nQuyidagilardan birini tanlang:",
              rk([L_FIND], [L_AD], [L_PASS], [L_GUIDE], [B_BACK]))


async def guide(update, ctx):
    await say(update, st("guide_text").replace("{hours}", str(sti("ad_hours"))))


def card(r, mode):
    return (f"👤 {tag(r)} | 🆙 Lvl: <b>{r['level'] or '?'}</b>\n🎮 {esc(mode)} | 🎙 {esc(r['mic'])}\n"
            f"💬 <i>{esc(r['bio']) or '—'}</i>")


def pick_ad(uid, seen):
    ids = ",".join(str(int(x)) for x in seen) or "0"
    return one(f"SELECT a.mode, u.* FROM partner_ads a JOIN users u ON u.id=a.user_id WHERE a.user_id!=? AND a.ts>? "
               f"AND u.banned=0 AND a.user_id NOT IN ({ids}) ORDER BY RANDOM() LIMIT 1",
               (uid, ts() - sti("ad_hours") * 3600))


def ad_kb(tid):
    return IM([[C("🤝 Taklif yuborish", f"ptr:inv:{tid}"), C("➡️ Keyingisi", "ptr:next")], [C("❌ Chiqish", "ptr:exit")]])


async def partner_find(update, ctx):
    uid = update.effective_user.id
    ctx.user_data["seen"] = []
    r = pick_ad(uid, [])
    if not r:
        return await say(update, "😔 Hozircha e'lonlar yo'q.\n📢 <b>E'lon bering</b> — sherik chiqsa sizga xabar beramiz!")
    ctx.user_data["seen"].append(r["id"])
    await say(update, card(r, r["mode"]), ad_kb(r["id"]))


async def cb_ptr(update, ctx, a):
    q, uid = update.callback_query, update.callback_query.from_user.id
    me = get_user(uid)
    act = a[1]
    if act == "exit":
        await q.answer()
        return await q.message.delete()
    if act == "next":
        seen = ctx.user_data.setdefault("seen", [])
        r = pick_ad(uid, seen)
        await q.answer()
        if not r:
            ctx.user_data["seen"] = []
            return await aedit(update, "😔 Hozircha boshqa e'lon yo'q. Keyinroq qayta urinib ko'ring.")
        seen.append(r["id"])
        return await aedit(update, card(r, r["mode"]), ad_kb(r["id"]))
    if act == "inv":
        tid = int(a[2])
        my = one("SELECT mode FROM partner_ads WHERE user_id=?", (uid,))
        if INV.get((uid, tid), 0) > time.time():
            return await q.answer("⏳ Bu foydalanuvchiga yaqinda taklif yuborgansiz", show_alert=True)
        INV[(uid, tid)] = time.time() + 600
        try:
            await ctx.bot.send_message(tid, f"🤝 <b>Sizga sherik taklifi!</b>\n\n{card(me, my['mode'] if my else '—')}",
                                       parse_mode=HTML,
                                       reply_markup=IM([[C("✅ Qabul qilish", f"ptr:acc:{uid}"), C("❌ Rad etish", f"ptr:rej:{uid}")]]))
        except Exception:
            return await q.answer("❌ Foydalanuvchiga xabar yuborib bo'lmadi", show_alert=True)
        await q.answer()
        return await q.message.reply_text("📨 Taklif yuborildi, javob kutilmoqda...")
    if act in ("acc", "rej"):
        sid = int(a[2])
        s = get_user(sid)
        await q.answer()
        if act == "acc":
            await aedit(update, f"✅ Taklif qabul qilindi!\nSherigingiz: {mention(s)} {tag(s)}")
            try:
                await ctx.bot.send_message(sid, f"🎉 Taklifingiz qabul qilindi!\nSherigingiz: {mention(me)} {tag(me)}", parse_mode=HTML)
            except Exception:
                pass
        else:
            await aedit(update, "❌ Taklif rad etildi.")
            try:
                await ctx.bot.send_message(sid, "😔 Taklifingiz rad etildi. Boshqa sherik qidirib ko'ring.")
            except Exception:
                pass


async def ad_start(update, ctx):
    setst(ctx, "ad_mode")
    await say(update, "🎮 <b>Qaysi rejimda e'lon berasiz?</b>", rk(MODES[:2], [MODES[2]], [B_BACK]))


async def st_ad_mode(update, ctx, s, t):
    uid = update.effective_user.id
    if t not in MODES:
        return await say(update, "Rejimni tugmalardan tanlang 👇")
    ex("INSERT INTO partner_ads(user_id,mode,ts) VALUES(?,?,?) ON CONFLICT(user_id) DO UPDATE SET mode=excluded.mode, ts=excluded.ts",
       (uid, t, ts()))
    clrst(ctx)
    await say(update, "✅ <b>E'loningiz joylandi!</b>\nSherik chiqsa sizga xabar beramiz.", rk([L_FIND], [L_AD], [L_PASS], [L_GUIDE], [B_BACK]))
    asyncio.create_task(notify_ad(ctx, uid, t))


async def notify_ad(ctx, uid, mode):
    me = get_user(uid)
    for r in rows("SELECT a.user_id FROM partner_ads a JOIN users u ON u.id=a.user_id WHERE a.mode=? AND a.user_id!=? "
                  "AND a.ts>? AND u.banned=0 ORDER BY RANDOM() LIMIT 8", (mode, uid, ts() - sti("ad_hours") * 3600)):
        try:
            await ctx.bot.send_message(r["user_id"], f"🔔 <b>Yangi sherik e'loni!</b>\n\n{card(me, mode)}", parse_mode=HTML,
                                       reply_markup=IM([[C("🤝 Taklif yuborish", f"ptr:inv:{uid}")]]))
        except Exception:
            pass
        await asyncio.sleep(0.05)


def pass_kb(r):
    return IM([[C("🆙 Level", "pp:level"), C(f"🎙 Mikrofon: {r['mic']}", "pp:mic")], [C("💬 Izoh", "pp:bio"), C("🎮 FF ID", "pp:ff")]])


def pass_text(r):
    ad = one("SELECT mode FROM partner_ads WHERE user_id=?", (r["id"],))
    return (f"👤 <b>Mening pasportim</b>\n\n🆔 FF ID: <code>{esc(r['ff_id']) or '—'}</code>\n🆙 Level: {r['level'] or '—'}\n"
            f"🎙 Mikrofon: {esc(r['mic'])}\n💬 Izoh: {esc(r['bio']) or '—'}\n📢 E'lon: {ad['mode'] if ad else 'yo‘q'}")


async def passport(update, ctx):
    clrst(ctx)
    r = get_user(update.effective_user.id)
    await say(update, pass_text(r), pass_kb(r))


async def cb_pp(update, ctx, a):
    q, uid = update.callback_query, update.callback_query.from_user.id
    await q.answer()
    if a[1] == "mic":
        r = get_user(uid)
        upd(uid, mic="Yo'q" if r["mic"] == "Bor" else "Bor")
        r = get_user(uid)
        return await aedit(update, pass_text(r), pass_kb(r))
    setst(ctx, {"level": "pp_level", "bio": "pp_bio", "ff": "ff_id"}[a[1]])
    await say(update, {"level": "🆙 Levelingizni yuboring (1-100):", "bio": "💬 Qisqa izoh yozing (max 80 belgi):",
                       "ff": "🆔 FF ID yuboring:"}[a[1]], rk([B_BACK]))


async def st_pp_level(update, ctx, s, t):
    if not t.isdigit() or not 1 <= int(t) <= 100:
        return await say(update, "❌ 1 dan 100 gacha raqam yuboring.")
    upd(update.effective_user.id, level=int(t))
    clrst(ctx)
    await say(update, "✅ Level saqlandi!", rk([L_FIND], [L_AD], [L_PASS], [L_GUIDE], [B_BACK]))


async def st_pp_bio(update, ctx, s, t):
    upd(update.effective_user.id, bio=t.strip()[:80])
    clrst(ctx)
    await say(update, "✅ Izoh saqlandi!", rk([L_FIND], [L_AD], [L_PASS], [L_GUIDE], [B_BACK]))


# ───────────────────────── DO'KON & XIZMATLAR ─────────────────────────
async def shop_menu(update, ctx):
    clrst(ctx)
    await say(update, st("shop_text"), rk([CATS["direct"]], [CATS["pack"], CATS["emote"]], [CATS["skin"]], [L_MYORD], [B_BACK]))


async def yt_menu(update, ctx):
    clrst(ctx)
    await say(update, st("service_text"), rk([L_SVC], [L_MYORD], [B_BACK]))


async def show_cat(update, ctx, cat, edit=False):
    its = rows("SELECT * FROM items WHERE cat=? AND active=1 ORDER BY price", (cat,))
    kb = IM([[C(f"{i['name']} — {i['price']} 💎", f"it:{i['id']}")] for i in its]) if its else None
    text = f"<b>{CATS[cat]}</b>\n\nMahsulotni tanlang 👇" if its else f"<b>{CATS[cat]}</b>\n\nHozircha mahsulot yo'q."
    if edit:
        await aedit(update, text, kb)
    else:
        await say(update, text, kb)


def cat_handler(cat):
    async def h(update, ctx):
        await show_cat(update, ctx, cat)
    return h


async def cb_cat(update, ctx, a):
    await update.callback_query.answer()
    await show_cat(update, ctx, a[1], edit=True)


async def cb_item(update, ctx, a):
    q = update.callback_query
    i = one("SELECT * FROM items WHERE id=? AND active=1", (int(a[1]),))
    if not i:
        return await q.answer("Topilmadi", show_alert=True)
    await q.answer()
    await aedit(update, f"<b>{esc(i['name'])}</b>\n\n{esc(i['descr'])}\n\n💎 Narxi: <b>{i['price']}</b> Almaz",
                IM([[C("🛒 Sotib olish", f"buy:{i['id']}")], [C("⬅️ Orqaga", f"cat:{i['cat']}")]]))


async def cb_buy(update, ctx, a):
    q, uid = update.callback_query, update.callback_query.from_user.id
    i = one("SELECT * FROM items WHERE id=? AND active=1", (int(a[1]),))
    if not i:
        return await q.answer("Topilmadi", show_alert=True)
    r = get_user(uid)
    if r["balance"] < i["price"]:
        return await q.answer(f"❌ Almaz yetarli emas. Kerak: {i['price']} 💎, sizda: {r['balance']} 💎", show_alert=True)
    await q.answer()
    if i["cat"] == "service":
        setst(ctx, "svc_note", id=i["id"])
        return await say(update, "📝 Buyurtma tafsilotlarini yozing (havola, talablar, bog'lanish uchun username):", rk([B_BACK]))
    if not r["ff_id"]:
        setst(ctx, "shop_ffid", id=i["id"])
        return await say(update, "🆔 Free Fire ID raqamingizni yuboring:", rk([B_BACK]))
    await confirm_buy(update, i, r["ff_id"])


async def confirm_buy(update, i, ff):
    await say(update, f"🛒 <b>Tasdiqlaysizmi?</b>\n\n📦 {esc(i['name'])}\n💎 {i['price']} Almaz\n🆔 FF ID: <code>{esc(ff)}</code>",
              IM([[C("✅ Tasdiqlash", f"buyok:{i['id']}"), C("❌ Bekor", "x:0")]]))


async def cb_x(update, ctx, a):
    await update.callback_query.answer()
    try:
        await update.callback_query.message.delete()
    except Exception:
        pass


async def create_order(ctx, uid, i, ff, note):
    r = get_user(uid)
    if r["balance"] < i["price"]:
        return None
    ex("UPDATE users SET balance=balance-? WHERE id=?", (i["price"], uid))
    oid = ex("INSERT INTO orders(user_id,item_id,name,cat,price,ff_id,note,status,ts) VALUES(?,?,?,?,?,?,?,'pending',?)",
             (uid, i["id"], i["name"], i["cat"], i["price"], ff, note, ts())).lastrowid
    await notify_admins(ctx, ord_text(oid), ord_kb(oid))
    return oid


def ord_text(oid):
    o = one("SELECT * FROM orders WHERE id=?", (oid,))
    u = get_user(o["user_id"])
    return (f"🧾 <b>Buyurtma #{oid}</b>\n👤 {mention(u)} ({tag(u)})\n📦 {esc(o['name'])} [{o['cat']}]\n💎 {o['price']}\n"
            f"🆔 FF ID: <code>{esc(o['ff_id']) or '—'}</code>\n📝 {esc(o['note']) or '—'}\n📌 {o['status']}")


def ord_kb(oid):
    return IM([[C("✅ Bajarildi", f"ord:ok:{oid}"), C("❌ Rad etish", f"ord:no:{oid}")]])


async def cb_buyok(update, ctx, a):
    q, uid = update.callback_query, update.callback_query.from_user.id
    i = one("SELECT * FROM items WHERE id=? AND active=1", (int(a[1]),))
    r = get_user(uid)
    if not i or not r["ff_id"]:
        return await q.answer("Xatolik", show_alert=True)
    oid = await create_order(ctx, uid, i, r["ff_id"], "")
    if oid is None:
        return await q.answer("❌ Almaz yetarli emas", show_alert=True)
    await q.answer("✅ Buyurtma qabul qilindi")
    await aedit(update, f"✅ <b>Buyurtma #{oid} qabul qilindi!</b>\nAdmin tez orada bajaradi. Holatni '{L_MYORD}' bo'limidan ko'ring.")


async def st_shop_ffid(update, ctx, s, t):
    if not re.fullmatch(r"\d{5,15}", t.strip()):
        return await say(update, "❌ FF ID faqat raqam (5-15 ta).")
    upd(update.effective_user.id, ff_id=t.strip())
    i = one("SELECT * FROM items WHERE id=?", (s["id"],))
    clrst(ctx)
    if i:
        await confirm_buy(update, i, t.strip())


async def st_svc_note(update, ctx, s, t):
    i = one("SELECT * FROM items WHERE id=? AND active=1", (s["id"],))
    uid = update.effective_user.id
    clrst(ctx)
    if not i:
        return await say(update, "❌ Xizmat topilmadi.", main_kb(uid))
    oid = await create_order(ctx, uid, i, get_user(uid)["ff_id"] or "", t.strip()[:500])
    if oid is None:
        return await say(update, "❌ Almaz yetarli emas.", main_kb(uid))
    await say(update, f"✅ <b>Buyurtma #{oid} qabul qilindi!</b>\nAdmin siz bilan bog'lanadi.", main_kb(uid))


async def my_orders(update, ctx):
    rs = rows("SELECT * FROM orders WHERE user_id=? ORDER BY id DESC LIMIT 15", (update.effective_user.id,))
    if not rs:
        return await say(update, "📰 Sizda hali buyurtmalar yo'q.")
    em = {"pending": "⏳ Kutilmoqda", "done": "✅ Bajarildi", "rejected": "❌ Rad etildi"}
    await say(update, "📰 <b>Mening buyurtmalarim:</b>\n\n" + "\n".join(
        f"#{o['id']} • {esc(o['name'])} • 💎{o['price']} • {em.get(o['status'], o['status'])}" for o in rs))


async def cb_ord(update, ctx, a):
    q = update.callback_query
    o = one("SELECT * FROM orders WHERE id=?", (int(a[2]),))
    if not o or o["status"] != "pending":
        return await q.answer("Allaqachon ko'rib chiqilgan", show_alert=True)
    if a[1] == "ok":
        ex("UPDATE orders SET status='done' WHERE id=?", (o["id"],))
        msg, mark = f"✅ Buyurtma #{o['id']} ({o['name']}) bajarildi!", "✅ BAJARILDI"
    else:
        ex("UPDATE orders SET status='rejected' WHERE id=?", (o["id"],))
        ex("UPDATE users SET balance=balance+? WHERE id=?", (o["price"], o["user_id"]))
        msg, mark = f"❌ Buyurtma #{o['id']} rad etildi. 💎 {o['price']} qaytarildi.", "❌ RAD ETILDI"
    await q.answer("Bajarildi")
    await aedit(update, ord_text(o["id"]) + f"\n\n<b>{mark}</b>")
    try:
        await ctx.bot.send_message(o["user_id"], msg)
    except Exception:
        pass


# ───────────────────────── ADMIN PANEL ─────────────────────────
TASKS = set()


def adm_kb():
    return IM([
        [C("📊 Statistika", "a:stats"), C("📢 Xabar yuborish", "a:bc")],
        [C("👥 Foydalanuvchi", "a:usr"), C("⚙️ Sozlamalar", "as:l:0")],
        [C("📣 Majburiy obuna", "ac:l"), C("🎁 Promo kodlar", "ap:l")],
        [C("🎰 Spin sovrinlari", "ar:l"), C("✅ Bonus tasklar", "at:l")],
        [C("🛒 Do'kon / Xizmatlar", "ae:l"), C("🧾 Buyurtmalar", "a:od")],
        [C("🏅 Ligalar", "al:l"), C("🛡 Adminlar", "aa:l")],
        [C("💾 Backup", "a:bk"), C("♻️ Restore", "a:rs")]])


async def admin_panel(update, ctx):
    if not is_admin(update.effective_user.id):
        return
    clrst(ctx)
    await say(update, "👑 <b>Admin panel</b>\n\nBotni to'liq boshqarish markazi 👇", adm_kb())


async def admin_cmd(update, ctx):
    await admin_panel(update, ctx)


def back(cb="a:home"):
    return [C("⬅️ Orqaga", cb)]


async def crud_list(update, title, lines, dels, addcb, backcb="a:home"):
    kb = [[C(f"🗑 {l}"[:60], d)] for l, d in dels] + [[C("➕ Qo'shish", addcb)], back(backcb)]
    await aedit(update, f"{title}\n\n" + "\n".join(lines or ["<i>Hozircha bo'sh</i>"]), IM(kb))


async def cb_a(update, ctx, a):
    q, s = update.callback_query, a[1]
    if s == "home":
        clrst(ctx)
        await aedit(update, "👑 <b>Admin panel</b>\n\nBotni to'liq boshqarish markazi 👇", adm_kb())
    elif s == "stats":
        n = lambda sql, p=(): one(sql, p)[0]
        day0 = int(now().replace(hour=0, minute=0, second=0).timestamp())
        act = ts() - sti("ad_hours") * 3600
        pend_o = n("SELECT COUNT(*) FROM orders WHERE status='pending'")
        pend_w = n("SELECT COUNT(*) FROM withdrawals WHERE status='pending'")
        txt = ("📊 <b>Statistika</b>\n\n"
               f"👥 Foydalanuvchilar: {n('SELECT COUNT(*) FROM users')} (tasdiqlangan: {n('SELECT COUNT(*) FROM users WHERE verified=1')})\n"
               f"🆕 Bugun qo'shilgan: {n('SELECT COUNT(*) FROM users WHERE joined>?', (day0,))}\n"
               f"🚫 Banlangan: {n('SELECT COUNT(*) FROM users WHERE banned=1')}\n"
               f"💎 Jami balans: {n('SELECT COALESCE(SUM(balance),0) FROM users')} | Jami topilgan: {n('SELECT COALESCE(SUM(earned),0) FROM users')}\n"
               f"🎰 Spinlar: {n('SELECT COALESCE(SUM(spins_total),0) FROM users')}\n"
               f"🎁 Promo ishlatilgan: {n('SELECT COUNT(*) FROM promo_uses')}\n"
               f"🧾 Kutilayotgan buyurtma: {pend_o}\n"
               f"💳 Kutilayotgan yechish: {pend_w}\n"
               f"📢 Faol sherik e'lonlari: {n('SELECT COUNT(*) FROM partner_ads WHERE ts>?', (act,))}")
        await q.answer()
        await aedit(update, txt, IM([back()]))
    elif s == "bc":
        setst(ctx, "adm_bc")
        await q.answer()
        await aedit(update, "📢 <b>Xabar yuborish</b>\n\nHamma foydalanuvchilarga yuboriladigan xabarni yuboring (matn, rasm, video...):", IM([back()]))
    elif s == "bcgo":
        d = ctx.user_data.pop("bc", None)
        await q.answer()
        if not d:
            return
        await aedit(update, "📢 Yuborish boshlandi... Tugagach xabar beraman.", IM([back()]))
        t = asyncio.create_task(do_broadcast(ctx, q.message.chat_id, d[0], d[1]))
        TASKS.add(t)
        t.add_done_callback(TASKS.discard)
    elif s == "usr":
        setst(ctx, "adm_usr")
        await q.answer()
        await aedit(update, "👥 Foydalanuvchi ID yoki @username yuboring:", IM([back()]))
    elif s == "od":
        await q.answer()
        os_ = rows("SELECT id FROM orders WHERE status='pending' ORDER BY id LIMIT 10")
        ws = rows("SELECT id FROM withdrawals WHERE status='pending' ORDER BY id LIMIT 10")
        if not os_ and not ws:
            return await aedit(update, "🧾 Kutilayotgan buyurtma va yechishlar yo'q ✅", IM([back()]))
        await aedit(update, f"🧾 Kutilayotgan: {len(os_)} buyurtma, {len(ws)} yechish (pastda)", IM([back()]))
        for o in os_:
            await q.message.reply_text(ord_text(o["id"]), parse_mode=HTML, reply_markup=ord_kb(o["id"]))
        for w in ws:
            await q.message.reply_text(wd_text(w["id"]), parse_mode=HTML, reply_markup=wd_kb(w["id"]))
    elif s == "bk" and IS_PG:
        await q.answer("✅ Postgres (Neon) ishlatilmoqda — ma'lumotlar doim saqlanadi, backup shart emas.", show_alert=True)
    elif s == "rs" and IS_PG:
        await q.answer("Postgres rejimida restore kerak emas.", show_alert=True)
    elif s == "bk":
        await q.answer("Tayyorlanmoqda...")
        path = tempfile.mktemp(suffix=".db")
        dst = sqlite3.connect(path)
        db.backup(dst)
        dst.close()
        with open(path, "rb") as f:
            await q.message.reply_document(f, filename=f"backup_{today()}.db", caption="💾 Baza nusxasi")
        os.remove(path)
    elif s == "rs":
        setst(ctx, "adm_rs")
        await q.answer()
        await aedit(update, "♻️ <b>Restore</b>\n\nOldin olingan <code>.db</code> faylni yuboring.\n⚠️ Joriy baza almashtiriladi!", IM([back()]))


async def do_broadcast(ctx, admin_chat, src_chat, src_msg):
    ok = bad = 0
    for r in rows("SELECT id FROM users WHERE banned=0 AND verified=1"):
        for _ in range(2):
            try:
                await ctx.bot.copy_message(r["id"], src_chat, src_msg)
                ok += 1
                break
            except RetryAfter as e:
                await asyncio.sleep(e.retry_after + 1)
            except Exception:
                bad += 1
                break
        await asyncio.sleep(0.05)
    await ctx.bot.send_message(admin_chat, f"📢 <b>Yuborish tugadi</b>\n✅ {ok} | ❌ {bad}", parse_mode=HTML)


async def adm_media(update, ctx, s):
    m = update.message
    if s["n"] == "adm_bc":
        ctx.user_data["bc"] = (m.chat_id, m.message_id)
        clrst(ctx)
        await say(update, "👆 Shu xabar hammaga yuborilsinmi?", IM([[C("✅ Yuborish", "a:bcgo"), C("❌ Bekor", "a:home")]]))
    elif s["n"] == "adm_rs":
        if not m.document:
            return await say(update, "❌ .db fayl yuboring.")
        tmp = DB_PATH + ".new"
        f = await m.document.get_file()
        await f.download_to_drive(tmp)
        try:
            t = sqlite3.connect(tmp)
            t.execute("SELECT COUNT(*) FROM users")
            t.close()
        except Exception:
            os.remove(tmp)
            return await say(update, "❌ Noto'g'ri baza fayli.")
        db.close()
        shutil.move(tmp, DB_PATH)
        connect()
        clrst(ctx)
        await say(update, "✅ Baza tiklandi!", main_kb(update.effective_user.id))


# -- foydalanuvchilar
def ucard(r):
    nm = esc(r["name"])
    un = "@" + esc(r["username"]) if r["username"] else "—"
    jd = datetime.fromtimestamp(r["joined"] or 0, TZ).strftime("%d.%m.%Y")
    return (f"👤 <b>{nm}</b> {un}\n🆔 <code>{r['id']}</code>\n📱 {r['phone'] or '—'}\n💎 Balans: <b>{r['balance']}</b> (jami: {r['earned']})\n"
            f"⭐ Ball: {r['points']} | 👥 Takliflar: {r['refs']}\n🎮 FF ID: {esc(r['ff_id']) or '—'}\n"
            f"🚫 Ban: {'ha' if r['banned'] else 'yoq'} | ✔️ Tasdiq: {'ha' if r['verified'] else 'yoq'}\n📅 {jd} | Taklif qilgan: {r['ref_by'] or '—'}")


def ukb(r):
    uid = r["id"]
    return IM([[C("✅ Unban" if r["banned"] else "🚫 Ban", f"au:ban:{uid}"), C("✉️ Xabar", f"au:msg:{uid}")],
               [C("💎 Almaz ±", f"au:bal:{uid}"), C("⭐ Ball ±", f"au:pts:{uid}")], back()])


async def cb_au(update, ctx, a):
    q, act, uid = update.callback_query, a[1], int(a[2])
    r = get_user(uid)
    if not r:
        return await q.answer("Topilmadi", show_alert=True)
    if act == "ban":
        upd(uid, banned=0 if r["banned"] else 1)
        await q.answer("Bajarildi")
        r = get_user(uid)
        return await aedit(update, ucard(r), ukb(r))
    await q.answer()
    setst(ctx, "adm_umsg" if act == "msg" else "adm_uval", uid=uid, f=act)
    await say(update, "✉️ Xabar matnini yuboring:" if act == "msg" else "🔢 Qo'shish/ayirish miqdori (masalan: 50 yoki -20):")


async def st_adm_usr(update, ctx, s, t):
    t = t.strip().lstrip("@")
    r = get_user(int(t)) if t.isdigit() else one("SELECT * FROM users WHERE LOWER(username)=?", (t.lower(),))
    if not r:
        return await say(update, "❌ Foydalanuvchi topilmadi.")
    clrst(ctx)
    await say(update, ucard(r), ukb(r))


async def st_adm_uval(update, ctx, s, t):
    if not re.fullmatch(r"-?\d+", t.strip()):
        return await say(update, "❌ Butun son yuboring.")
    n = int(t)
    if s["f"] == "bal":
        credit(s["uid"], n)
    else:
        ex("UPDATE users SET points=CASE WHEN points+?<0 THEN 0 ELSE points+? END WHERE id=?", (n, n, s["uid"]))
    clrst(ctx)
    r = get_user(s["uid"])
    await say(update, "✅ Bajarildi\n\n" + ucard(r), ukb(r))


async def st_adm_umsg(update, ctx, s, t):
    clrst(ctx)
    try:
        await ctx.bot.send_message(s["uid"], t)
        await say(update, "✅ Yuborildi.")
    except Exception as e:
        await say(update, f"❌ Yuborilmadi: {esc(e)}")


# -- sozlamalar
async def cb_as(update, ctx, a):
    q, s = update.callback_query, a[1]
    keys = list(S.keys())
    if s == "l":
        pg = int(a[2])
        await q.answer()
        part = keys[pg * 8:pg * 8 + 8]
        kb = []
        for k in part:
            d, lab, tp = S[k]
            v = st(k)
            show = ("✅" if v == "1" else "❌") if tp == "bool" else re.sub(r"<[^>]+>|\s+", " ", v)[:18] or "—"
            kb.append([C(f"{lab}: {show}"[:60], f"as:e:{k}")])
        nav = []
        if pg > 0:
            nav.append(C("◀️", f"as:l:{pg - 1}"))
        nav.append(C(f"{pg + 1}/{(len(keys) + 7) // 8}", "as:l:0"))
        if (pg + 1) * 8 < len(keys):
            nav.append(C("▶️", f"as:l:{pg + 1}"))
        kb += [nav, back()]
        await aedit(update, "⚙️ <b>Sozlamalar</b>\nO'zgartirish uchun tanlang:", IM(kb))
    elif s == "e":
        k = a[2]
        d, lab, tp = S[k]
        if tp == "bool":
            set_st(k, "0" if stb(k) else "1")
            await q.answer("O'zgartirildi")
            idx = keys.index(k) // 8
            return await cb_as(update, ctx, ["as", "l", str(idx)])
        await q.answer()
        setst(ctx, "adm_set", k=k)
        cur = st(k)
        await aedit(update, f"✏️ <b>{lab}</b>\n\nHozirgi qiymat:\n<code>{esc(cur[:2500])}</code>\n\n"
                            f"Yangi qiymatni yuboring.\n<i>Tozalash: -  | Asl holiga qaytarish: /default</i>"
                            + ("\n<i>HTML teglar (&lt;b&gt;, &lt;i&gt;) ishlaydi.</i>" if tp == "text" else ""),
                    IM([back(f"as:l:{keys.index(k) // 8}")]))


async def st_adm_set(update, ctx, s, t):
    k = s["k"]
    d, lab, tp = S[k]
    if t.strip() == "/default":
        ex("DELETE FROM settings WHERE k=?", (k,))
    else:
        v = "" if t.strip() == "-" else t
        if tp == "int" and not re.fullmatch(r"-?\d+", v.strip()):
            return await say(update, "❌ Butun son yuboring.")
        set_st(k, v.strip() if tp == "int" else v)
    clrst(ctx)
    await say(update, "✅ Saqlandi!", IM([[C("⚙️ Sozlamalarga", f"as:l:{list(S.keys()).index(k) // 8}")]]))


# -- majburiy obuna kanallari
async def cb_ac(update, ctx, a):
    q, s = update.callback_query, a[1]
    await q.answer()
    if s == "del":
        ex("DELETE FROM channels WHERE id=?", (int(a[2]),))
        SUBC.clear()
    if s == "add":
        setst(ctx, "adm_ch")
        return await aedit(update, "📣 Kanal @username yoki ID yuboring.\n⚠️ Bot kanalda <b>admin</b> bo'lishi shart!", IM([back("ac:l")]))
    ch = rows("SELECT * FROM channels")
    await crud_list(update, "📣 <b>Majburiy obuna kanallari</b>", [f"{c['id']}. {esc(c['title'])} — <code>{esc(c['chat_id'])}</code>" for c in ch],
                    [(c["title"], f"ac:del:{c['id']}") for c in ch], "ac:add")


async def st_adm_ch(update, ctx, s, t):
    t = t.strip()
    cid = int(t) if re.fullmatch(r"-?\d+", t) else (t if t.startswith("@") else "@" + t)
    try:
        chat = await ctx.bot.get_chat(cid)
    except Exception as e:
        return await say(update, f"❌ Kanal topilmadi: {esc(e)}")
    url = f"https://t.me/{chat.username}" if chat.username else chat.invite_link
    if not url:
        try:
            url = await ctx.bot.export_chat_invite_link(chat.id)
        except Exception:
            url = ""
    ex("INSERT INTO channels(chat_id,title,url) VALUES(?,?,?)", (str(chat.id), chat.title or str(chat.id), url or ""))
    SUBC.clear()
    clrst(ctx)
    await say(update, f"✅ Qo'shildi: {esc(chat.title)}", IM([[C("📣 Kanallar", "ac:l")]]))


# -- promo kodlar
async def cb_ap(update, ctx, a):
    q, s = update.callback_query, a[1]
    await q.answer()
    if s == "del":
        ex("DELETE FROM promos WHERE code=?", (a[2],))
    if s == "add":
        setst(ctx, "adm_promo")
        return await aedit(update, "🎁 Format: <code>KOD MIQDOR LIMIT KUN</code>\nMasalan: <code>FF2026 10 100 7</code>\n"
                                   "(LIMIT 0 = cheksiz, KUN 0 = muddatsiz)", IM([back("ap:l")]))
    ps = rows("SELECT * FROM promos ORDER BY code LIMIT 30")
    ln = [f"<code>{esc(p['code'])}</code> — 💎{p['amount']} — {p['used']}/{p['max_uses'] or '∞'}"
          + (f" — {datetime.fromtimestamp(p['expires'], TZ):%d.%m}" if p["expires"] else "") for p in ps]
    await crud_list(update, "🎁 <b>Promo kodlar</b>", ln, [(p["code"], f"ap:del:{p['code']}") for p in ps], "ap:add")


async def st_adm_promo(update, ctx, s, t):
    m = re.fullmatch(r"(\S+)\s+(\d+)\s+(\d+)\s+(\d+)", t.strip())
    if not m:
        return await say(update, "❌ Format: KOD MIQDOR LIMIT KUN")
    code, amt, lim, days = m.group(1).upper(), int(m.group(2)), int(m.group(3)), int(m.group(4))
    ex("INSERT INTO promos(code,amount,max_uses,used,expires) VALUES(?,?,?,0,?) ON CONFLICT(code) DO UPDATE SET "
       "amount=excluded.amount, max_uses=excluded.max_uses, used=0, expires=excluded.expires",
       (code, amt, lim, ts() + days * 86400 if days else 0))
    clrst(ctx)
    await say(update, f"✅ Promo yaratildi: <code>{esc(code)}</code>", IM([[C("🎁 Promolar", "ap:l")]]))


# -- spin sovrinlari
async def cb_ar(update, ctx, a):
    q, s = update.callback_query, a[1]
    await q.answer()
    if s == "del":
        ex("DELETE FROM prizes WHERE id=?", (int(a[2]),))
    if s == "add":
        setst(ctx, "adm_prize")
        return await aedit(update, "🎰 Format: <code>MIQDOR VAZN</code>\nMasalan: <code>5 10</code> (5 almaz, vazn 10)\n"
                                   "0 = omadsiz. Ehtimollik = vazn / jami vazn.", IM([back("ar:l")]))
    ps = rows("SELECT * FROM prizes ORDER BY amount")
    tot = sum(p["weight"] for p in ps) or 1
    await crud_list(update, "🎰 <b>Spin sovrinlari</b>",
                    [f"💎 {p['amount']} — vazn {p['weight']:g} ({p['weight'] / tot * 100:.1f}%)" for p in ps],
                    [(f"{p['amount']} 💎 ({p['weight']:g})", f"ar:del:{p['id']}") for p in ps], "ar:add")


async def st_adm_prize(update, ctx, s, t):
    m = re.fullmatch(r"(\d+)\s+(\d+(?:\.\d+)?)", t.strip())
    if not m:
        return await say(update, "❌ Format: MIQDOR VAZN")
    ex("INSERT INTO prizes(amount,weight) VALUES(?,?)", (int(m.group(1)), float(m.group(2))))
    clrst(ctx)
    await say(update, "✅ Qo'shildi", IM([[C("🎰 Sovrinlar", "ar:l")]]))


# -- bonus tasklar
async def cb_at(update, ctx, a):
    q, s = update.callback_query, a[1]
    await q.answer()
    if s == "del":
        ex("DELETE FROM tasks WHERE id=?", (int(a[2]),))
    if s == "add":
        setst(ctx, "adm_task")
        return await aedit(update, "✅ Format: <code>Sarlavha | URL | @kanal (ixtiyoriy)</code>\n"
                                   "Kanal berilsa a'zolik tekshiriladi.", IM([back("at:l")]))
    ts_ = rows("SELECT * FROM tasks")
    await crud_list(update, "✅ <b>Bonus tasklar</b>", [f"{t['id']}. {esc(t['title'])} — {esc(t['url'])}" for t in ts_],
                    [(t["title"], f"at:del:{t['id']}") for t in ts_], "at:add")


async def st_adm_task(update, ctx, s, t):
    p = [x.strip() for x in t.split("|")]
    if len(p) < 2 or not p[1].startswith("http"):
        return await say(update, "❌ Format: Sarlavha | https://... | @kanal")
    ex("INSERT INTO tasks(title,url,chat_id) VALUES(?,?,?)", (p[0], p[1], p[2] if len(p) > 2 else ""))
    clrst(ctx)
    await say(update, "✅ Task qo'shildi", IM([[C("✅ Tasklar", "at:l")]]))


# -- do'kon / xizmatlar
async def cb_ae(update, ctx, a):
    q, s = update.callback_query, a[1]
    await q.answer()
    if s == "l":
        return await aedit(update, "🛒 <b>Do'kon / Xizmatlar</b>\nKategoriyani tanlang:",
                           IM([[C(v, f"ae:c:{k}")] for k, v in CATS.items()] + [back()]))
    if s == "c":
        cat = a[2]
        its = rows("SELECT * FROM items WHERE cat=? ORDER BY id", (cat,))
        kb = [[C(f"{'✅' if i['active'] else '🚫'} {i['name']} — {i['price']}💎"[:60], f"ae:v:{i['id']}")] for i in its]
        return await aedit(update, f"<b>{CATS[cat]}</b>", IM(kb + [[C("➕ Qo'shish", f"ae:add:{cat}")], back("ae:l")]))
    if s == "add":
        setst(ctx, "adm_item_add", cat=a[2])
        return await aedit(update, "➕ Format: <code>Nomi | Narx | Tavsif</code>\nMasalan: <code>💎 50 Diamond | 50 | Tez yetkaziladi</code>",
                           IM([back(f"ae:c:{a[2]}")]))
    i = one("SELECT * FROM items WHERE id=?", (int(a[2]),)) if len(a) > 2 and a[2].isdigit() else None
    if s == "v" and i:
        return await aedit(update, f"<b>{esc(i['name'])}</b>\n\n{esc(i['descr'])}\n\n💎 {i['price']} | {'faol' if i['active'] else 'o‘chiq'}\nKategoriya: {CATS[i['cat']]}",
                           IM([[C("✏️ Nomi", f"ae:ed:name:{i['id']}"), C("✏️ Narx", f"ae:ed:price:{i['id']}")],
                               [C("✏️ Tavsif", f"ae:ed:descr:{i['id']}"), C("👁 Yoq/O'ch", f"ae:tog:{i['id']}")],
                               [C("🗑 O'chirish", f"ae:del:{i['id']}")], back(f"ae:c:{i['cat']}")]))
    if s == "tog" and i:
        ex("UPDATE items SET active=1-active WHERE id=?", (i["id"],))
        return await cb_ae(update, ctx, ["ae", "v", str(i["id"])])
    if s == "del" and i:
        ex("DELETE FROM items WHERE id=?", (i["id"],))
        return await cb_ae(update, ctx, ["ae", "c", i["cat"]])
    if s == "ed":
        setst(ctx, "adm_item_ed", f=a[2], id=int(a[3]))
        await say(update, "✏️ Yangi qiymatni yuboring:")


async def st_adm_item_add(update, ctx, s, t):
    p = [x.strip() for x in t.split("|")]
    if len(p) < 2 or not p[1].isdigit():
        return await say(update, "❌ Format: Nomi | Narx | Tavsif")
    ex("INSERT INTO items(cat,name,descr,price) VALUES(?,?,?,?)", (s["cat"], p[0], p[2] if len(p) > 2 else "", int(p[1])))
    clrst(ctx)
    await say(update, "✅ Qo'shildi", IM([[C("🛒 Ro'yxat", f"ae:c:{s['cat']}")]]))


async def st_adm_item_ed(update, ctx, s, t):
    f = s["f"]
    if f == "price":
        if not t.strip().isdigit():
            return await say(update, "❌ Raqam yuboring.")
        ex("UPDATE items SET price=? WHERE id=?", (int(t), s["id"]))
    else:
        ex(f"UPDATE items SET {'name' if f == 'name' else 'descr'}=? WHERE id=?", (t.strip(), s["id"]))
    clrst(ctx)
    await say(update, "✅ Saqlandi", IM([[C("📦 Mahsulot", f"ae:v:{s['id']}")]]))


# -- ligalar
async def cb_al(update, ctx, a):
    q, s = update.callback_query, a[1]
    await q.answer()
    if s == "del":
        ex("DELETE FROM leagues WHERE id=?", (int(a[2]),))
    if s == "add":
        setst(ctx, "adm_lg")
        return await aedit(update, "🏅 Format: <code>Nom | emoji | minimal ball</code>\nMasalan: <code>Gold | 🥇 | 150</code>", IM([back("al:l")]))
    ls = rows("SELECT * FROM leagues ORDER BY min_points")
    await crud_list(update, "🏅 <b>Ligalar</b>", [f"{l['emoji']} {esc(l['name'])} — {l['min_points']} ball" for l in ls],
                    [(l["name"], f"al:del:{l['id']}") for l in ls], "al:add")


async def st_adm_lg(update, ctx, s, t):
    p = [x.strip() for x in t.split("|")]
    if len(p) != 3 or not p[2].isdigit():
        return await say(update, "❌ Format: Nom | emoji | ball")
    ex("INSERT INTO leagues(name,emoji,min_points) VALUES(?,?,?)", (p[0], p[1], int(p[2])))
    clrst(ctx)
    await say(update, "✅ Qo'shildi", IM([[C("🏅 Ligalar", "al:l")]]))


# -- adminlar
async def cb_aa(update, ctx, a):
    q, s = update.callback_query, a[1]
    await q.answer()
    if s == "del":
        ex("DELETE FROM admins WHERE id=?", (int(a[2]),))
    if s == "add":
        setst(ctx, "adm_adm")
        return await aedit(update, "🛡 Yangi admin Telegram ID sini yuboring:", IM([back("aa:l")]))
    ad = rows("SELECT id FROM admins")
    ln = [f"👑 <code>{i}</code> (asosiy)" for i in sorted(ENV_ADMINS)] + [f"🛡 <code>{r['id']}</code>" for r in ad]
    await crud_list(update, "🛡 <b>Adminlar</b>", ln, [(str(r["id"]), f"aa:del:{r['id']}") for r in ad], "aa:add")


async def st_adm_adm(update, ctx, s, t):
    if not t.strip().isdigit():
        return await say(update, "❌ Faqat raqamli ID.")
    ex("INSERT INTO admins(id) VALUES(?) ON CONFLICT DO NOTHING", (int(t),))
    clrst(ctx)
    await say(update, "✅ Admin qo'shildi", IM([[C("🛡 Adminlar", "aa:l")]]))


# ───────────────────────── ROUTER ─────────────────────────
MENU = {
    B_AI: ai_menu, B_EARN: earn, B_PROMO: promo_menu, B_SPIN: spin_menu, B_PHONE: None,
    B_PARTNER: partner_menu, B_TOP: top, B_PROF: profile, B_LVL: level_menu, B_SHOP: shop_menu, B_YT: yt_menu,
    B_ADMIN: admin_panel, L_SPIN: spin_choose, L_MYSPIN: my_spins, L_BONUS: bonus_menu, L_PHIST: promo_hist,
    L_FIND: partner_find, L_AD: ad_start, L_PASS: passport, L_GUIDE: guide,
    L_AICHAT: ai_chat_start, L_AICHAR: ai_char_start, L_AINICK: ai_nick_start,
    L_SVC: cat_handler("service"), L_MYORD: my_orders,
}
for _k, _v in CATS.items():
    if _k != "service":
        MENU[_v] = cat_handler(_k)


async def phone_menu(update, ctx):
    setst(ctx, "phone")
    await say(update, st("phone_text"), rk([B_BACK]))


MENU[B_PHONE] = phone_menu

STATES = {
    "promo": st_promo, "phone": st_phone, "ai_chat": st_ai_chat, "ai_charpick": st_ai_charpick, "ai_char": st_ai_char,
    "ai_nick": st_ai_nick, "pp_level": st_pp_level, "pp_bio": st_pp_bio, "ff_id": st_ff_id, "ad_mode": st_ad_mode,
    "wd_amount": st_wd, "svc_note": st_svc_note, "shop_ffid": st_shop_ffid, "adm_usr": st_adm_usr,
    "adm_uval": st_adm_uval, "adm_umsg": st_adm_umsg, "adm_set": st_adm_set, "adm_ch": st_adm_ch,
    "adm_promo": st_adm_promo, "adm_prize": st_adm_prize, "adm_task": st_adm_task, "adm_item_add": st_adm_item_add,
    "adm_item_ed": st_adm_item_ed, "adm_lg": st_adm_lg, "adm_adm": st_adm_adm,
}

CB = {"cap": cb_cap, "sub": cb_sub, "spin": cb_spin, "bonus": cb_bonus, "ptr": cb_ptr, "pp": cb_pp, "prof": cb_prof,
      "lvl": cb_lvl, "it": cb_item, "cat": cb_cat, "buy": cb_buy, "buyok": cb_buyok, "x": cb_x, "ord": cb_ord,
      "wd": cb_wd, "a": cb_a, "as": cb_as, "ac": cb_ac, "ap": cb_ap, "ar": cb_ar, "at": cb_at, "ae": cb_ae,
      "al": cb_al, "aa": cb_aa, "au": cb_au}
ADMIN_CB = {"ord", "wd", "a", "as", "ac", "ap", "ar", "at", "ae", "al", "aa", "au"}


async def on_text(update, ctx):
    t = update.message.text
    if not await gate(update, ctx):
        return
    if t == B_BACK:
        return await home(update, ctx)
    s = getst(ctx)
    if t in MENU:
        if t in (B_ADMIN,) and not is_admin(update.effective_user.id):
            return
        clrst(ctx)
        return await MENU[t](update, ctx)
    if s and s["n"] in STATES:
        if s["n"].startswith("adm_") and not is_admin(update.effective_user.id):
            return clrst(ctx)
        return await STATES[s["n"]](update, ctx, s, t)
    await say(update, "Menyudan tanlang 👇", main_kb(update.effective_user.id))


async def on_msg(update, ctx):
    m = update.effective_message
    if m is None or update.effective_user is None:
        return
    uid = update.effective_user.id
    if m.contact:
        return await on_contact(update, ctx)
    s = getst(ctx)
    if s and s["n"] in ("adm_bc", "adm_rs") and is_admin(uid):
        return await adm_media(update, ctx, s)
    if m.text:
        return await on_text(update, ctx)


async def on_cb(update, ctx):
    q = update.callback_query
    a = (q.data or "").split(":")
    h = CB.get(a[0])
    uid = q.from_user.id
    if not h:
        return await q.answer()
    try:
        if a[0] in ADMIN_CB and not is_admin(uid):
            return await q.answer("⛔ Ruxsat yo'q", show_alert=True)
        if a[0] not in ("cap", "sub") and not is_admin(uid):
            r = get_user(uid)
            if not r or r["banned"] or not r["verified"] or not stb("bot_enabled"):
                return await q.answer("⛔ Avval /start bosing", show_alert=True)
        await h(update, ctx, a)
    except Exception:
        log.exception("callback xatosi: %s", q.data)
    try:
        await q.answer()
    except Exception:
        pass


async def menu_cmd(update, ctx):
    if await gate(update, ctx):
        await home(update, ctx)


async def on_error(update, ctx):
    log.error("Xato: %s", ctx.error, exc_info=ctx.error)


# ───────────────────────── WEB (Render keep-alive) ─────────────────────────
web = Flask(__name__)


@web.route("/")
def index():
    return "Bepul Olov Uz boti ishlayapti ✅"


def run_web():
    web.run(host="0.0.0.0", port=PORT)


def keepalive():
    url = os.getenv("RENDER_EXTERNAL_URL")
    if not url:
        return
    while True:
        time.sleep(600)
        try:
            httpx.get(url, timeout=20)
        except Exception:
            pass


# ───────────────────────── AVTO-BACKUP (Telegram kanal) ─────────────────────────
BACKUP_CHAT = os.getenv("BACKUP_CHAT", "").strip()
_bk = {"ok": False, "changes": -1, "msg": None}


def _bcid():
    return int(BACKUP_CHAT) if BACKUP_CHAT.lstrip("-").isdigit() else BACKUP_CHAT


async def make_backup(bot, force=False):
    if not BACKUP_CHAT or not _bk["ok"]:
        return
    ch = db.total_changes
    if not force and ch == _bk["changes"]:
        return
    path = tempfile.mktemp(suffix=".db")
    try:
        dst = sqlite3.connect(path)
        db.backup(dst)
        dst.close()
        with open(path, "rb") as f:
            m = await bot.send_document(_bcid(), f, filename=f"backup_{now():%Y%m%d_%H%M}.db",
                                        caption="💾 Avto-backup", disable_notification=True)
        try:
            await bot.pin_chat_message(_bcid(), m.message_id, disable_notification=True)
        except Exception as e:
            log.warning("pin xatosi (botga pin huquqi bering): %s", e)
        if _bk["msg"]:
            try:
                await bot.delete_message(_bcid(), _bk["msg"])
            except Exception:
                pass
        _bk.update(changes=ch, msg=m.message_id)
    except Exception as e:
        log.warning("backup xatosi: %s", e)
    finally:
        if os.path.exists(path):
            os.remove(path)


async def try_restore(bot):
    """True = tayyor (tiklandi yoki backup yo'q). False = xato, qayta uriniladi."""
    try:
        chat = await bot.get_chat(_bcid())
        pm = chat.pinned_message
        if not pm or not pm.document:
            return True
        if one("SELECT COUNT(*) FROM users")[0] > 0:
            _bk["msg"] = pm.message_id
            return True
        tmp = DB_PATH + ".new"
        f = await pm.document.get_file()
        await f.download_to_drive(tmp)
        t = sqlite3.connect(tmp)
        t.execute("SELECT COUNT(*) FROM users")
        t.close()
        db.close()
        shutil.move(tmp, DB_PATH)
        connect()
        _bk["msg"] = pm.message_id
        log.info("Baza Telegram backupdan tiklandi")
        return True
    except Exception as e:
        log.warning("restore xatosi: %s", e)
        return False


async def backup_loop(app):
    while True:
        if not _bk["ok"]:
            _bk["ok"] = await try_restore(app.bot)
        await make_backup(app.bot)
        await asyncio.sleep(300)


async def post_init(app):
    if IS_PG:
        log.info("Postgres (Neon) ishlatilmoqda — ma'lumotlar doimiy.")
        return
    if not BACKUP_CHAT:
        log.warning("BACKUP_CHAT berilmagan — redeployda baza o'chadi!")
        return
    _bk["ok"] = await try_restore(app.bot)
    app.bot_data["bk_task"] = asyncio.create_task(backup_loop(app))


async def post_stop(app):
    await make_backup(app.bot, force=True)


def main():
    if not BOT_TOKEN:
        raise SystemExit("BOT_TOKEN topilmadi!")
    connect()
    threading.Thread(target=run_web, daemon=True).start()
    threading.Thread(target=keepalive, daemon=True).start()
    try:
        asyncio.get_event_loop()
    except RuntimeError:
        asyncio.set_event_loop(asyncio.new_event_loop())
    app = (Application.builder().token(BOT_TOKEN)
           .connect_timeout(30).read_timeout(30).write_timeout(30).pool_timeout(30)
           .get_updates_connect_timeout(30).get_updates_read_timeout(30)
           .get_updates_write_timeout(30).get_updates_pool_timeout(30)
           .post_init(post_init).post_stop(post_stop).build())
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("admin", admin_cmd))
    app.add_handler(CommandHandler("menu", menu_cmd))
    app.add_handler(CallbackQueryHandler(on_cb))
    app.add_handler(MessageHandler(filters.ChatType.PRIVATE & ~filters.COMMAND, on_msg))
    app.add_error_handler(on_error)
    log.info("Bot ishga tushdi")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
