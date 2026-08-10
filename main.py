import asyncio, time, random, urllib.parse, aiohttp, sys, subprocess
from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.tl.functions.messages import RequestWebViewRequest
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, CallbackQueryHandler, MessageHandler, ContextTypes, ConversationHandler, filters

BOT_TOKEN = "8976290159:AAH10zmWMqZ2QbSx5bBxf9ckoUAwuU0Rhic"

BOT_U = "monsterland_bot"
APP_URL = "https://lets.playmonsterland.com"
API_USER = f"{APP_URL}/api/user?include=monsters"
API_ADS = f"{APP_URL}/api/ads/create-task"
API_RES = f"{APP_URL}/api/ads/task-result"
API_DONE = f"{APP_URL}/api/ads/complete"
API_VITALS_DIRECT = f"{APP_URL}/api/vitals"

VITAL_ITEMS = {"food": "magic_apple", "hygiene": "magic_towel", "energy": "wizard_coffee"}
ITEM_NAMES = {"food": "🍎 Magic Food", "hygiene": "🧻 Wash", "energy": "☕️ Energy"}

CREDS, THRESH = range(2)
db = {}
bot_app = None
account_locks = {}

def get_lock(sess):
    return account_locks.setdefault(sess, asyncio.Lock())

def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)

def allowed(uid): return True
def udb(uid): return db.setdefault(uid, {"idx": 0, "accs": []})
def acc(uid):
    d = udb(uid)
    if not d["accs"]: return None
    d["idx"] = min(d["idx"], len(d["accs"]) - 1)
    return d["accs"][d["idx"]]

def find_account_by_key(uid, key):
    """يبحث عن حساب باستخدام key فريد (نستخدمه بأزرار الإشعار)."""
    d = udb(uid)
    for a in d["accs"]:
        if a.get("key") == key:
            return a
    return None

def headers(tok):
    return {"authority": "lets.playmonsterland.com", "accept": "*/*", "authorization": tok,
            "content-type": "application/json", "origin": APP_URL, "referer": APP_URL + "/",
            "user-agent": "Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Mobile Safari/537.36"}

def parse_creds(text):
    raw = [l.strip() for l in text.strip().splitlines() if l.strip()]
    cleaned = []
    for l in raw:
        if "=" in l:
            cleaned.append(l.split("=", 1)[1].strip().strip('"\''))
        else:
            cleaned.append(l.strip().strip('"\''))
    if len(cleaned) == 1 and " " in cleaned[0]:
        cleaned = [c.strip('"\',:=') for c in cleaned[0].split()]
    aid = hsh = sess = None
    for t in cleaned:
        if t.isdigit() and 5 <= len(t) <= 15 and not aid: aid = t
        elif len(t) == 32 and all(c in '0123456789abcdefABCDEF' for c in t) and not hsh: hsh = t
        elif len(t) > 50 and not sess: sess = t
    return aid, hsh, sess


async def notify(uid, text, kb=None):
    if not bot_app:
        return
    try:
        await bot_app.bot.send_message(chat_id=uid, text=text, parse_mode="Markdown", reply_markup=kb)
    except Exception as e:
        log(f"⚠️ فشل إرسال إشعار لـ {uid}: {e}")


async def notify_turnstile_needed(uid, a):
    """يرسل تنبيه Turnstile مرة واحدة فقط لكل حساب، مع زر تفعيل."""
    if a.get("turnstile_notified"):
        return  # لا نزعج المستخدم بتكرار نفس التنبيه
    a["turnstile_notified"] = True
    a["paused"] = True  # نوقف محاولات الشراء لهذا الحساب لحين التفعيل
    kb = InlineKeyboardMarkup([[InlineKeyboardButton(f"✅ دخلت لحساب {a['name']} الآن", callback_data=f"activate_{a['key']}")]])
    await notify(
        uid,
        f"🔒 **حساب {a['name']} يحتاج تفعيل يدوي!**\n\n"
        f"اللعبة تطلب تحقق أمني (Turnstile) لا يمكن حله آليًا.\n\n"
        f"📱 افتح تطبيق Monsterland من داخل تليجرام يدويًا لهذا الحساب، "
        f"ثم اضغط الزر بالأسفل بعد الدخول لتفعيل التنفيذ التلقائي مجددًا.",
        kb
    )


# ============== واجهة المستخدم ==============

def main_kb(uid):
    a = acc(uid)
    if not a: return InlineKeyboardMarkup([[InlineKeyboardButton("➕ إضافة حساب", callback_data="add")]])
    ads = "الخدمة ADS قيد تشغيل 🟢" if a["ads"] else "الخدمة ADS متوقفة 🔴"
    noads = "تنفيد بدون ADS مشغل 🟢" if a["noads"] else "تنفيد بدون ADS متوقف 🔴"
    kb = [
        [InlineKeyboardButton(f"👤 {a['name']} 🔄", callback_data="accs")],
        [InlineKeyboardButton(ads, callback_data="t_ads")],
        [InlineKeyboardButton(noads, callback_data="t_noads")],
        [InlineKeyboardButton("Setting ⚙️", callback_data="settings")],
        [InlineKeyboardButton("تنفيد مباشرة بدون ads 🎯", callback_data="direct")],
        [InlineKeyboardButton("🔄 تحديث بيانات الوحش", callback_data="refresh")],
    ]
    if a.get("paused"):
        kb.insert(0, [InlineKeyboardButton(f"⏸️ متوقف مؤقتًا (بانتظار تفعيل)", callback_data=f"activate_{a['key']}")])
    return InlineKeyboardMarkup(kb)

def direct_kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🍎 Magic Food", callback_data="d_food")],
        [InlineKeyboardButton("🧻 Wash", callback_data="d_hygiene")],
        [InlineKeyboardButton("☕️ Energy", callback_data="d_energy")],
        [InlineKeyboardButton("🔙 رجوع", callback_data="back")],
    ])

def accs_kb(uid):
    d = udb(uid)
    kb = [[InlineKeyboardButton(("✅" if i == d["idx"] else "🔘") + " " + a["name"], callback_data=f"sw_{i}")]
          for i, a in enumerate(d["accs"])]
    kb += [[InlineKeyboardButton("➕ إضافة حساب جديد", callback_data="add")]]
    if d["accs"]: kb += [[InlineKeyboardButton("🗑️ حذف حساب", callback_data="deltmenu")]]
    kb += [[InlineKeyboardButton("🔙 رجوع", callback_data="back")]]
    return InlineKeyboardMarkup(kb)

def del_kb(uid):
    kb = [[InlineKeyboardButton(f"❌ حذف {a['name']}", callback_data=f"del_{i}")] for i, a in enumerate(udb(uid)["accs"])]
    kb += [[InlineKeyboardButton("🔙 إلغاء ورجوع", callback_data="accs")]]
    return InlineKeyboardMarkup(kb)

def settings_kb(a):
    notif = "🔔 إشعارات مفعلة 🟢" if a.get("notify", True) else "🔕 إشعارات مغلقة 🔴"
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📊 تعديل النسبة", callback_data="set_th")],
        [InlineKeyboardButton(notif, callback_data="t_notify")],
        [InlineKeyboardButton("🔙 رجوع", callback_data="back")],
    ])

def info_text(a, vitals, lumis):
    t = (f"👾 **{a['name']}**\n\n🍎 Food: `{vitals.get('food',0):.2f}%`\n"
         f"🧻 Hygiene: `{vitals.get('hygiene',0):.2f}%`\n☕️ Energy: `{vitals.get('energy',0):.2f}%`\n💰 Lumis: `{lumis}`")
    if a.get("paused"):
        t += "\n\n⏸️ **متوقف مؤقتًا — بحاجة تفعيل يدوي (زر أعلى القائمة)**"
    elif a.get("sched", 0) > 0:
        remaining = int(a["sched"] - time.time())
        if remaining > 0:
            item_label = ITEM_NAMES.get(a.get("sv"), "عنصر")
            t += f"\n\n⏳ **الشراء القادم ({item_label}) بعد:** `{remaining}` **ثانية**"
        else:
            t += f"\n\n⏳ **جاري تنفيذ الشراء الآن...**"
    return t


# ============== منطق اللعبة ==============

def pick_real_monster(monsters_list):
    for mm in monsters_list:
        if not mm.get("is_egg", False):
            return mm
    return monsters_list[0] if monsters_list else None


async def get_monster_cached(aid, ahash, sess, tok):
    if not tok:
        return False, None, None, None, "لا يوجد توكن محفوظ"
    async with aiohttp.ClientSession() as s:
        async with s.get(API_USER, headers=headers(tok), timeout=10) as r:
            if r.status == 200:
                d = await r.json()
                ms = d.get("monsters", [])
                picked = pick_real_monster(ms)
                if picked:
                    return True, picked, d.get("profile", {}), tok, None
            return False, None, None, None, f"status={r.status}"


async def get_monster_fresh(aid, ahash, sess):
    log(f"  🔐 محاولة فتح اتصال Telethon جديد (aid={aid})...")
    async with get_lock(sess):
        try:
            async with TelegramClient(StringSession(sess), int(aid), ahash) as c:
                bot = await c.get_input_entity(BOT_U)
                wv = await c(RequestWebViewRequest(peer=bot, bot=bot, platform="android", from_bot_menu=False, url=APP_URL))
                init = wv.url.split("tgWebAppData=")[1].split("&tgWebAppVersion")[0]
                ntok = f"tma {urllib.parse.unquote(init)}"
                log(f"  ✅ تم توليد توكن جديد بنجاح")
                async with aiohttp.ClientSession() as s:
                    async with s.get(API_USER, headers=headers(ntok), timeout=10) as r:
                        if r.status != 200:
                            return False, None, None, None, f"خطأ سيرفر ({r.status})"
                        d = await r.json()
                        ms = d.get("monsters", [])
                        picked = pick_real_monster(ms)
                        if not picked:
                            return False, None, None, None, "لا يوجد وحش."
                        return True, picked, d.get("profile", {}), ntok, None
        except Exception as e:
            log(f"  ❌ فشل فتح اتصال Telethon: {e}")
            return False, None, None, None, f"فشل الاتصال: {e}"


async def get_monster(aid, ahash, sess, tok=None):
    ok, m, p, ntok, err = await get_monster_cached(aid, ahash, sess, tok)
    if ok:
        return ok, m, p, ntok, err
    return await get_monster_fresh(aid, ahash, sess)


async def buy_direct(tok, mid, item):
    async with aiohttp.ClientSession() as s:
        async with s.post(API_VITALS_DIRECT, headers=headers(tok), json={"monsterId": mid, "itemId": item, "action": "purchase"}, timeout=15) as r:
            body = None
            if r.status != 200:
                body = await r.text()
                log(f"  🛒 buy_direct({item}) -> status={r.status} | body={body[:200]}")
            else:
                log(f"  🛒 buy_direct({item}) -> status={r.status}")
            return r.status, body

async def buy_with_ad(session, tok, mid, item):
    async with session.post(API_ADS, headers=headers(tok), json={"action": "vitals", "metadata": {"monsterId": mid, "itemId": item}}, timeout=15) as r:
        if r.status != 200:
            body = await r.text()
            log(f"  ❌ buy_with_ad create -> status={r.status} | body={body[:200]}")
            return r.status, body
        tx = (await r.json()).get("adTxId")
    if not tx: return None, None
    await asyncio.sleep(random.randint(8, 12))
    async with session.get(f"{API_RES}?txId={tx}", headers=headers(tok), timeout=15): pass
    async with session.post(API_DONE, headers=headers(tok), json={"adTxId": tx, "provider": "gigapub"}, timeout=15) as r:
        body = None if r.status == 200 else await r.text()
        log(f"  🚀 buy_with_ad complete -> status={r.status}")
        return r.status, body


def is_turnstile_error(status, body):
    return status == 403 and body and "TURNSTILE" in body


async def buy_with_retry(uid, a, mid, it, use_ads):
    """يحاول الشراء. لو Turnstile: يوقف الحساب ويرسل تنبيه (مرة وحدة). غير كذا: يجدد التوكن ويعيد المحاولة عادي."""
    if use_ads:
        async with aiohttp.ClientSession() as s:
            status, body = await buy_with_ad(s, a["tok"], mid, it)
    else:
        status, body = await buy_direct(a["tok"], mid, it)

    if is_turnstile_error(status, body):
        await notify_turnstile_needed(uid, a)
        return status

    if status in (401, 403):
        ok, m, p, ntok, err = await get_monster_fresh(a["aid"], a["ahash"], a["sess"])
        if ok:
            a["tok"] = ntok
            if use_ads:
                async with aiohttp.ClientSession() as s:
                    status, body = await buy_with_ad(s, ntok, mid, it)
            else:
                status, body = await buy_direct(ntok, mid, it)
            if is_turnstile_error(status, body):
                await notify_turnstile_needed(uid, a)

    return status


# ============== الخلفية ==============

async def bg_worker():
    log("🔄 [Worker] بدء تشغيل حلقة الخلفية...")
    while True:
        try:
            for uid, d in list(db.items()):
                for a in d["accs"]:
                    if a.get("paused"):
                        continue  # لا نحاول أبدًا لحساب متوقف بانتظار تفعيل يدوي
                    if not a["ads"] and not a["noads"]:
                        a["sched"] = 0
                        continue

                    ok, m, p, tok, err = await get_monster(a["aid"], a["ahash"], a["sess"], a.get("tok"))
                    if not ok:
                        continue
                    a["tok"] = tok
                    a["name"] = m.get("name", a["name"])
                    mid, v, th, now = m.get("_id"), m.get("vitals", {}), a["th"], time.time()

                    target = next(((vt, it) for vt, it in VITAL_ITEMS.items() if v.get(vt, 100) < th), None)
                    if target:
                        vt, it = target
                        if a.get("sched", 0) == 0:
                            a["sched"] = now + random.randint(8, 16)
                            a["sv"] = vt
                        elif now >= a["sched"]:
                            status = await buy_with_retry(uid, a, mid, it, use_ads=a["ads"])

                            if a.get("notify", True) and not a.get("paused"):
                                label = ITEM_NAMES.get(vt, vt)
                                if status == 200:
                                    await notify(uid, f"✅ تم شراء **{label}** بنجاح لحساب **{a['name']}**.")

                            a["sched"] = 0
                    else:
                        a["sched"] = 0
            await asyncio.sleep(10)
        except Exception as e:
            log(f"💥 [Worker] خطأ عام: {e}")
            await asyncio.sleep(10)


# ============== أوامر البوت ==============

async def cmd_start(u: Update, c: ContextTypes.DEFAULT_TYPE):
    uid = u.effective_user.id
    a = acc(uid)
    if not a:
        await u.message.reply_text("أرسل بيانات الحساب (كل قيمة بسطر أو بسطر واحد):\nAPI_ID\nAPI_HASH\nSESSION")
        return CREDS
    ok, m, p, tok, _ = await get_monster(a["aid"], a["ahash"], a["sess"], a.get("tok"))
    if ok:
        a["tok"] = tok
        a["name"] = m.get("name", a["name"])
        txt = info_text(a, m.get("vitals", {}), p.get("lumis", 0))
    else:
        txt = "🏠 القائمة الرئيسية:"
    await u.message.reply_text(txt, reply_markup=main_kb(uid), parse_mode="Markdown")
    return ConversationHandler.END

async def on_creds(u: Update, c: ContextTypes.DEFAULT_TYPE):
    uid = u.effective_user.id
    aid, ah, sess = parse_creds(u.message.text.strip())
    if not (aid and ah and sess):
        await u.message.reply_text("⚠️ البيانات غير مكتملة. أعد الإرسال:")
        return CREDS
    msg = await u.message.reply_text("⏳ جاري التحقق...")
    ok, m, p, tok, err = await get_monster(aid, ah, sess)
    if not ok:
        await msg.edit_text(f"❌ {err}\n\nأعد الإرسال:")
        return CREDS
    d = udb(uid)
    a = {"aid": aid, "ahash": ah, "sess": sess, "tok": tok, "name": m.get("name", "وحش"),
         "mid": m.get("_id"), "ads": False, "noads": False, "th": 55, "sched": 0, "sv": None,
         "notify": True, "key": f"{uid}_{len(d['accs'])}_{int(time.time())}",
         "paused": False, "turnstile_notified": False}
    d["accs"].append(a)
    d["idx"] = len(d["accs"]) - 1
    await msg.edit_text(f"✅ **تم إضافة الحساب!**\n\n{info_text(a, m.get('vitals', {}), p.get('lumis', 0))}", parse_mode="Markdown")
    await u.message.reply_text("🏠 القائمة الرئيسية:", reply_markup=main_kb(uid))
    return ConversationHandler.END

async def on_button(u: Update, c: ContextTypes.DEFAULT_TYPE):
    q = u.callback_query
    await q.answer()
    uid = u.effective_user.id

    data, d, a = q.data, udb(uid), acc(uid)

    async def safe_edit(text=None, kb=None, **kw):
        try:
            if text is not None:
                await q.edit_message_text(text, reply_markup=kb, **kw)
            else:
                await q.edit_message_reply_markup(reply_markup=kb)
        except Exception:
            pass

    # -------------------- تفعيل حساب بعد Turnstile --------------------
    if data.startswith("activate_"):
        key = data[len("activate_"):]
        target = find_account_by_key(uid, key)
        if target:
            target["paused"] = False
            target["turnstile_notified"] = False
            target["sched"] = 0
            try:
                await q.edit_message_text(f"✅ تم تفعيل حساب **{target['name']}** — سيُستأنف التنفيذ التلقائي الآن.", parse_mode="Markdown")
            except Exception:
                pass
        else:
            try:
                await q.edit_message_text("⚠️ لم يتم العثور على الحساب (ربما تم حذفه).")
            except Exception:
                pass
        return

    if data == "t_ads":
        if a:
            a["ads"] = not a["ads"]
            if a["ads"]: a["noads"] = False
            await safe_edit(kb=main_kb(uid))
        return

    if data == "t_noads":
        if a:
            a["noads"] = not a["noads"]
            if a["noads"]: a["ads"] = False
            await safe_edit(kb=main_kb(uid))
        return

    if data == "refresh":
        if not a:
            await safe_edit("❌ لا يوجد حساب.", main_kb(uid))
            return
        ok, m, p, tok, err = await get_monster(a["aid"], a["ahash"], a["sess"], a.get("tok"))
        if ok:
            a["tok"] = tok
            a["name"] = m.get("name", a["name"])
            await safe_edit(info_text(a, m.get("vitals", {}), p.get("lumis", 0)), main_kb(uid), parse_mode="Markdown")
        else:
            await safe_edit(f"❌ فشل التحديث: {err}", main_kb(uid))
        return

    if data == "settings":
        if not a: return
        await safe_edit(
            f"⚙️ **إعدادات ({a['name']})**\n\n🔹 النسبة الحالية: `{a['th']}%`",
            settings_kb(a), parse_mode="Markdown"
        )
        return

    if data == "t_notify":
        if a:
            a["notify"] = not a.get("notify", True)
            await safe_edit(
                f"⚙️ **إعدادات ({a['name']})**\n\n🔹 النسبة الحالية: `{a['th']}%`",
                settings_kb(a), parse_mode="Markdown"
            )
        return

    if data == "set_th":
        await safe_edit(
            "📊 أدخل نسبة جديدة (لا تتجاوز 70):",
            InlineKeyboardMarkup([[InlineKeyboardButton("إلغاء ❌", callback_data="cancel")]])
        )
        return THRESH

    if data == "cancel":
        await safe_edit("تم الإلغاء.", main_kb(uid))
        return ConversationHandler.END

    if data == "direct":
        if not a:
            await safe_edit("❌ لا يوجد حساب.", main_kb(uid))
            return
        await safe_edit("🎯 **اختر العنصر للتنفيذ المباشر:**", direct_kb(), parse_mode="Markdown")
        return

    if data.startswith("d_"):
        if not a: return
        vt = data[2:]
        item = VITAL_ITEMS[vt]
        await safe_edit(f"⚡ جاري تنفيذ {ITEM_NAMES[vt]}...")
        st = await buy_with_retry(uid, a, a["mid"], item, use_ads=False)
        if is_turnstile_error(st, None) or a.get("paused"):
            txt = f"🔒 يحتاج تفعيل يدوي — راجع الإشعار المرسل."
        else:
            txt = f"✅ تم شراء {ITEM_NAMES[vt]} بنجاح!" if st == 200 else f"⚠️ فشل. كود: {st}"
        await safe_edit(txt, main_kb(uid))
        return

    if data == "accs":
        await safe_edit("🔄 **إدارة الحسابات**", accs_kb(uid), parse_mode="Markdown")
        return

    if data.startswith("sw_"):
        i = int(data[3:])
        if 0 <= i < len(d["accs"]):
            d["idx"] = i
        await safe_edit("🔄 **إدارة الحسابات**", accs_kb(uid), parse_mode="Markdown")
        return

    if data == "add":
        await safe_edit("📥 أرسل بيانات الحساب الجديد:\nAPI_ID\nAPI_HASH\nSESSION")
        return CREDS

    if data == "deltmenu":
        await safe_edit("🗑️ **اختر للحذف:**", del_kb(uid), parse_mode="Markdown")
        return

    if data.startswith("del_"):
        i = int(data[4:])
        if 0 <= i < len(d["accs"]):
            removed = d["accs"].pop(i)
            d["idx"] = 0
            await safe_edit(f"🗑️ تم حذف حساب **{removed['name']}**.\n\n🏠 القائمة الرئيسية:", main_kb(uid), parse_mode="Markdown")
        else:
            await safe_edit("🏠 القائمة الرئيسية:", main_kb(uid))
        return

    if data == "back":
        if a:
            ok, m, p, tok, _ = await get_monster(a["aid"], a["ahash"], a["sess"], a.get("tok"))
            if ok:
                a["tok"] = tok
                a["name"] = m.get("name", a["name"])
                await safe_edit(info_text(a, m.get("vitals", {}), p.get("lumis", 0)), main_kb(uid), parse_mode="Markdown")
                return
        await safe_edit("🏠 القائمة الرئيسية:", main_kb(uid))

async def on_thresh(u: Update, c: ContextTypes.DEFAULT_TYPE):
    uid = u.effective_user.id
    t = u.message.text.strip()
    if t in ("إلغاء", "/cancel"):
        await u.message.reply_text("تم الإلغاء.", reply_markup=main_kb(uid))
        return ConversationHandler.END
    if not t.isdigit() or int(t) > 70:
        await u.message.reply_text("⚠️ رقم صحيح فقط (حتى 70).")
        return THRESH
    a = acc(uid)
    if a: a["th"] = int(t)
    await u.message.reply_text(f"✅ تم: {t}%", reply_markup=main_kb(uid))
    return ConversationHandler.END

async def on_startup(app):
    global bot_app
    bot_app = app
    asyncio.create_task(bg_worker())

def main():
    app = ApplicationBuilder().token(BOT_TOKEN).post_init(on_startup).concurrent_updates(True).build()
    conv = ConversationHandler(
        entry_points=[CommandHandler("start", cmd_start), CallbackQueryHandler(on_button)],
        states={
            CREDS: [MessageHandler(filters.TEXT & ~filters.COMMAND, on_creds)],
            THRESH: [CallbackQueryHandler(on_button), MessageHandler(filters.TEXT & ~filters.COMMAND, on_thresh)],
        },
        fallbacks=[CommandHandler("start", cmd_start), CallbackQueryHandler(on_button)],
        allow_reentry=True,
    )
    app.add_handler(conv)
    log("🚀 البوت يعمل...")
    app.run_polling()


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--child":
        main()
    else:
        while True:
            log("🚀 تشغيل عملية جديدة للبوت...")
            result = subprocess.run([sys.executable, __file__, "--child"])
            log(f"⚠️ توقفت العملية (كود الخروج: {result.returncode}) — إعادة تشغيل خلال 10 ثوانٍ...")
            time.sleep(10)
