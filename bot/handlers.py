import json
import os
import random
import requests
import re
import unicodedata
import time

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from config import OWNER_ID, LASTFM_API_KEY, AUDD_API_KEY

SONGS_FILE = os.path.join(os.path.dirname(__file__), "..", "songs.json")
LRCLIB_API = "https://lrclib.net/api"

search_cache = {}
lyrics_cache = {}
songs = []

# =========================
# INIT
# =========================

def load_songs():
    global songs
    if os.path.exists(SONGS_FILE) and os.path.getsize(SONGS_FILE) > 0:
        try:
            with open(SONGS_FILE, "r", encoding="utf-8") as f:
                songs = json.load(f)
        except json.JSONDecodeError:
            print("⚠️ فایل songs.json خراب است، فایل جدید ایجاد می‌شود")
            songs = []

load_songs()

# =========================
# HELPERS
# =========================

def save_songs():
    with open(SONGS_FILE, "w", encoding="utf-8") as f:
        json.dump(songs, f, ensure_ascii=False, indent=2)

def normalize_text(text: str) -> str:
    if not text:
        return ""
    text = unicodedata.normalize("NFKC", text)
    text = text.replace("\u200c", " ")
    text = re.sub(r"[^\w\s]", "", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip().lower()

def make_key(artist, title):
    return f"{normalize_text(artist)}-{normalize_text(title)}"

def reorder_ids():
    global songs
    songs.sort(key=lambda x: x["id"])
    for i, song in enumerate(songs, 1):
        song["id"] = i

def is_owner(update):
    return update.effective_user.id == OWNER_ID

def get_next_id():
    return max((s["id"] for s in songs), default=0) + 1

# =========================
# ALBUM ART
# =========================

def get_album_art(artist, title):
    try:
        r = requests.get(
            "https://itunes.apple.com/search",
            params={"term": f"{artist} {title}", "media": "music", "limit": 1},
            timeout=10
        )
        if r.status_code != 200:
            return None
        results = r.json().get("results", [])
        if not results:
            return None
        artwork_url = results[0].get("artworkUrl100", "")
        if artwork_url:
            return artwork_url.replace("100x100bb", "600x600bb")
        return None
    except Exception as e:
        print("iTunes cover error:", e)
        return None

# =========================
# LYRICS
# =========================

def search_lrclib(artist, title):
    try:
        query = f"{artist} {title}"
        if query in search_cache:
            return search_cache[query]
        r = requests.get(f"{LRCLIB_API}/search", params={"q": query}, timeout=10)
        if r.status_code != 200:
            return None
        data = r.json()
        if not data:
            return None
        a = normalize_text(artist)
        t = normalize_text(title)
        best, best_score = None, -1
        for item in data:
            ia = normalize_text(item.get("artist_name", ""))
            it = normalize_text(item.get("track_name", ""))
            score = 0
            if a and a in ia: score += 3
            if t and t in it: score += 4
            if ia and ia in a: score += 2
            if it and it in t: score += 2
            if score > best_score:
                best_score = score
                best = item
        if best:
            search_cache[query] = best["id"]
            return best["id"]
        return None
    except Exception as e:
        print("LRCLIB search error:", e)
        return None

def get_lyrics_lrclib(artist, title):
    try:
        song_id = search_lrclib(artist, title)
        if not song_id:
            return None
        r = requests.get(f"{LRCLIB_API}/get/{song_id}", timeout=10)
        if r.status_code != 200:
            return None
        data = r.json()
        return data.get("plainLyrics") or data.get("syncedLyrics") or data.get("lyrics")
    except Exception as e:
        print("LRCLIB error:", e)
        return None

def get_lyrics(artist, title):
    key = make_key(artist, title)
    if key in lyrics_cache:
        return lyrics_cache[key]
    lyrics = get_lyrics_lrclib(artist, title)
    if lyrics:
        lyrics_cache[key] = lyrics
    return lyrics

def safe_get_lyrics(artist, title, retries=2):
    for _ in range(retries):
        try:
            result = get_lyrics(artist, title)
            if result:
                return result
        except Exception as e:
            print("Retry error:", e)
            time.sleep(1)
    return None

# =========================
# DEEZER
# =========================

def get_deezer_preview(artist, title):
    try:
        r = requests.get(
            "https://api.deezer.com/search",
            params={"q": f"{artist} {title}"},
            timeout=10
        )
        if r.status_code != 200:
            return None
        for item in r.json().get("data", []):
            preview = item.get("preview")
            if preview:
                return preview
        return None
    except Exception as e:
        print("Deezer error:", e)
        return None

# =========================
# AUDD - SONG RECOGNITION
# =========================

def recognize_song(file_bytes: bytes) -> dict | None:
    try:
        r = requests.post(
            "https://api.audd.io/",
            data={"api_token": AUDD_API_KEY, "return": "apple_music,spotify"},
            files={"file": ("audio", file_bytes, "audio/mpeg")},
            timeout=15
        )
        if r.status_code != 200:
            return None
        result = r.json()
        if result.get("status") != "success" or not result.get("result"):
            return None
        return result["result"]
    except Exception as e:
        print("Audd error:", e)
        return None

# =========================
# LAST.FM
# =========================

def get_track_info(artist, title):
    try:
        r = requests.get(
            "https://ws.audioscrobbler.com/2.0/",
            params={
                "method": "track.getInfo",
                "artist": artist,
                "track": title,
                "api_key": LASTFM_API_KEY,
                "format": "json"
            },
            headers={"User-Agent": "MusicBot/1.0 (Telegram Bot)"},
            proxies={"https": os.environ.get("HTTPS_PROXY", ""), "http": os.environ.get("HTTP_PROXY", "")},
            timeout=10
        )
        if r.status_code != 200:
            return None
        track = r.json().get("track")
        if not track:
            return None
        duration_ms = int(track.get("duration", 0))
        duration_sec = duration_ms // 1000
        minutes = duration_sec // 60
        seconds = duration_sec % 60
        published = track.get("wiki", {}).get("published", "").strip()
        if published:
            published = published.split(",")[0]
        return {
            "duration": f"{minutes}:{seconds:02d}" if duration_sec > 0 else None,
            "published": published or None,
        }
    except Exception as e:
        print("Last.fm track.getInfo error:", e)
        return None

def get_lastfm_top_tracks(artist_name, limit=20):
    try:
        r = requests.get(
            "https://ws.audioscrobbler.com/2.0/",
            params={
                "method": "artist.gettoptracks",
                "artist": artist_name,
                "api_key": LASTFM_API_KEY,
                "format": "json",
                "limit": limit
            },
            headers={"User-Agent": "MusicBot/1.0 (Telegram Bot)"},
            proxies={"https": os.environ.get("HTTPS_PROXY", ""), "http": os.environ.get("HTTP_PROXY", "")},
            timeout=10
        )
        if r.status_code != 200:
            return []
        tracks = r.json().get("toptracks", {}).get("track", [])
        return [{"title": t["name"], "playcount": int(t.get("playcount", 0))} for t in tracks]
    except Exception as e:
        print("Last.fm error:", e)
        return []

# =========================
# RECOMMENDATIONS
# =========================

def recommend_songs(current_song, limit=10):
    artist = current_song["performer"]
    listened_titles = {
        normalize_text(s["title"])
        for s in songs
        if s["performer"] == artist and s.get("plays", 0) > 0
    }
    unlistened_titles = {
        normalize_text(s["title"])
        for s in songs
        if s["performer"] == artist and s.get("plays", 0) == 0
    }
    result = []
    for track in get_lastfm_top_tracks(artist, limit=30):
        normalized = normalize_text(track["title"])
        if normalized in listened_titles:
            continue
        result.append({
            "title": track["title"],
            "performer": artist,
            "playcount": track["playcount"],
            "in_archive": normalized in unlistened_titles
        })
        if len(result) >= limit:
            break
    return result

# =========================
# COMMAND HANDLERS
# =========================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update):
        return
    await update.message.reply_text(
        "سلام! 🎵 به MusicBot خوش آمدی.\n"
        "می‌تونی آهنگ‌هاتو برام فوروارد کنی تا ذخیره کنم و بعد پلی کنم."
    )

async def handle_audio(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update):
        return
    audio = update.message.audio
    if not audio:
        return
    songs.append({
        "id": get_next_id(),
        "title": audio.title or "Unknown",
        "performer": audio.performer or "Unknown",
        "file_id": audio.file_id,
        "duration": audio.duration or 0,
        "plays": 0,
        "listened": False,
        "last_played": 0
    })
    reorder_ids()
    save_songs()
    await update.message.reply_text(
        f"✅ آهنگ دریافت شد: {songs[-1]['title']} (ID: {songs[-1]['id']})"
    )

async def play(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update):
        return
    if not songs:
        await update.message.reply_text("❌ هیچ آهنگی موجود نیست")
        return
    artists = sorted({s['performer'] for s in songs}, key=str.lower)
    keyboard = [[InlineKeyboardButton(a, callback_data=f"artist:{a}")] for a in artists]
    await update.message.reply_text("🎤 یک خواننده انتخاب کنید:", reply_markup=InlineKeyboardMarkup(keyboard))

async def random_song(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update):
        return
    if not songs:
        await update.message.reply_text("❌ هیچ آهنگی موجود نیست")
        return
    song = random.choice(songs)
    await update.message.reply_audio(audio=song["file_id"], caption=f"🎵 {song['title']} - {song['performer']}")

async def search(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update):
        return
    if not context.args:
        await update.message.reply_text("❌ لطفا اسم آهنگ رو بعد /search بنویس")
        return
    query = " ".join(context.args).lower()
    results = [s for s in songs if query in s['title'].lower() or query in s['performer'].lower()]
    if not results:
        await update.message.reply_text("❌ چیزی پیدا نشد")
        return
    text = "\n".join(f"{s['id']}. {s['title']} - {s['performer']}" for s in results)
    await update.message.reply_text(f"🎵 نتایج جستجو:\n{text}")

async def list_songs(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update):
        return
    if not songs:
        await update.message.reply_text("❌ هیچ آهنگی موجود نیست")
        return
    artists = sorted({s['performer'] for s in songs}, key=str.lower)
    keyboard = [[InlineKeyboardButton(a, callback_data=f"artist:{a}")] for a in artists]
    await update.message.reply_text("🎤 یک خواننده انتخاب کنید:", reply_markup=InlineKeyboardMarkup(keyboard))

async def top_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update):
        return
    if not songs:
        await update.message.reply_text("❌ هیچ آهنگی موجود نیست")
        return
    top_5 = sorted(songs, key=lambda x: x.get("plays", 0), reverse=True)[:5]
    text = "🔥 تاپ آهنگ‌ها:\n\n"
    for i, song in enumerate(top_5, 1):
        text += f"{i}. {song['title']} - {song['performer']}\n🎧 پخش: {song.get('plays', 0)}\n\n"
    await update.message.reply_text(text)

async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update):
        return
    if not songs:
        await update.message.reply_text("❌ هیچ آهنگی موجود نیست")
        return
    total_duration = sum(s.get("duration", 0) for s in songs)
    listened_duration = sum(s.get("listened_duration", 0) for s in songs)
    most_played = max(songs, key=lambda x: x.get("plays", 0))
    await update.message.reply_text(
        f"📊 آمار موزیک‌ها:\n\n"
        f"🎵 تعداد آهنگ‌ها: {len(songs)}\n"
        f"📦 مجموع زمان آرشیو: {total_duration // 3600} ساعت و {(total_duration % 3600) // 60} دقیقه\n"
        f"🎧 زمان گوش‌داده‌شده: {listened_duration // 3600} ساعت و {(listened_duration % 3600) // 60} دقیقه\n"
        f"🔥 بیشترین آهنگ پخش‌شده:\n"
        f"{most_played['title']} - {most_played['performer']}\n"
        f"🎧 تعداد پخش: {most_played.get('plays', 0)}"
    )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🎵 راهنمای استفاده از MusicBot\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "▶️ دستورات اصلی:\n"
        "• /start    ➜ شروع کار با ربات\n"
        "• /play     ➜ انتخاب خواننده و پخش آهنگ‌ها\n"
        "• /random   ➜ پخش یک آهنگ تصادفی\n"
        "• /search   ➜ جستجو بین آهنگ‌ها\n"
        "• /list     ➜ نمایش لیست خواننده‌ها\n"
        "• /identify ➜ تشخیص آهنگ از کلیپ صوتی یا ویدیو\n\n"
        "🗑 مدیریت:\n"
        "• /delete ➜ حذف آهنگ‌ها\n\n"
        "📊 آمار:\n"
        "• /top   ➜ آهنگ‌های پرپخش\n"
        "• /stats ➜ آمار کامل آرشیو\n\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "📌 برای شروع /play را اجرا کنید"
    )

async def about_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🎵 MusicBot\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "🎧 ربات مدیریت و پخش موسیقی برای تلگرام\n\n"
        "✨ امکانات:\n"
        "• ذخیره و پخش آهنگ‌ها\n"
        "• دسته‌بندی بر اساس خواننده\n"
        "• نمایش لیریک آنلاین\n"
        "• نمایش کاور آهنگ\n"
        "• جزییات آهنگ\n"
        "• دمو ۳۰ ثانیه‌ای\n"
        "• پیشنهاد آهنگ بر اساس سلیقه\n"
        "• تشخیص آهنگ از کلیپ صوتی و ویدیو\n"
        "• آمارگیری از آهنگ‌ها\n\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "📌 برای شروع از /play استفاده کنید"
    )

async def identify_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update):
        return
    context.user_data["identifying"] = True
    await update.message.reply_text(
        "🎵 یه کلیپ صوتی یا ویدیو بفرست تا آهنگش رو تشخیص بدم."
    )

async def handle_identify_media(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update):
        return
    if not context.user_data.get("identifying"):
        return

    msg = update.message
    file = None

    if msg.audio:
        file = await msg.audio.get_file()
    elif msg.voice:
        file = await msg.voice.get_file()
    elif msg.video:
        file = await msg.video.get_file()
    elif msg.video_note:
        file = await msg.video_note.get_file()
    else:
        return

    context.user_data["identifying"] = False
    await msg.reply_text("🔍 در حال تشخیص آهنگ...")

    file_bytes = await file.download_as_bytearray()
    result = recognize_song(bytes(file_bytes))

    if not result:
        await msg.reply_text("❌ آهنگی تشخیص داده نشد")
        return

    title = result.get("title", "نامشخص")
    artist = result.get("artist", "نامشخص")
    album = result.get("album", "")
    release_date = result.get("release_date", "")

    text = f"🎵 آهنگ تشخیص داده شد:\n\n"
    text += f"🗣 {artist}\n"
    text += f"🎵 {title}\n"
    if album:
        text += f"💿 {album}\n"
    if release_date:
        text += f"📅 {release_date}\n"

    keyboard = [
        [
            InlineKeyboardButton("✅ بله", callback_data=f"identify_yes:{artist}:{title}"),
            InlineKeyboardButton("❌ خیر", callback_data="identify_no")
        ]
    ]
    await msg.reply_text(text + "\nمنظورت همینه؟", reply_markup=InlineKeyboardMarkup(keyboard))

async def identify_yes_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    parts = query.data.split(":", 2)
    if len(parts) < 3:
        return
    artist, title = parts[1], parts[2]

    existing = next((s for s in songs if normalize_text(s["title"]) == normalize_text(title)
                     and normalize_text(s["performer"]) == normalize_text(artist)), None)

    if existing:
        await query.edit_message_text(
            f"✅ این آهنگ توی آرشیوته!\n🎵 {existing['title']} - {existing['performer']}\n🎧 پخش: {existing.get('plays', 0)}"
        )
    else:
        await query.edit_message_text(
            f"🎵 {title} - {artist}\n\n❌ این آهنگ توی آرشیوت نیست."
        )

async def identify_no_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data["identifying"] = True
    await query.edit_message_text("باشه، دوباره یه کلیپ بفرست تا دوباره امتحان کنم.")

# =========================
# CALLBACK HANDLERS
# =========================

async def artist_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    performer = query.data.split("artist:", 1)[1]
    artist_songs = sorted(
        [s for s in songs if s['performer'] == performer],
        key=lambda x: x['title'].lower()
    )
    if not artist_songs:
        await query.edit_message_text(f"❌ آهنگی از {performer} موجود نیست")
        return
    keyboard = [[InlineKeyboardButton(s["title"], callback_data=f"song:{s['id']}")] for s in artist_songs]
    keyboard.append([InlineKeyboardButton("🔙 بازگشت", callback_data="back_to_artists")])
    await query.edit_message_text(f"🎵 آهنگ‌های {performer}:", reply_markup=InlineKeyboardMarkup(keyboard))

async def song_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    try:
        song_id = int(query.data.split("song:", 1)[1])
    except (ValueError, IndexError):
        return
    song = next((s for s in songs if s["id"] == song_id), None)
    if not song:
        await query.edit_message_text("❌ پیدا نشد")
        return
    song["plays"] = song.get("plays", 0) + 1
    song["listened"] = True
    song["last_played"] = time.time()
    song["listened_duration"] = song.get("listened_duration", 0) + song.get("duration", 0)
    save_songs()
    keyboard = [
        [InlineKeyboardButton("📝 لیریک", callback_data=f"lyrics:{song_id}"),
         InlineKeyboardButton("🖼 کاور", callback_data=f"cover:{song_id}")],
        [InlineKeyboardButton("ℹ️ جزییات", callback_data=f"details:{song_id}"),
         InlineKeyboardButton("🎯 پیشنهاد", callback_data=f"rec:{song_id}")],
        [InlineKeyboardButton("▶️ دمو ۳۰ ثانیه", callback_data=f"demo:{song_id}")]
    ]
    await query.message.reply_audio(
        audio=song["file_id"],
        caption=f"🎵 {song['title']} - {song['performer']}",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def cover_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    try:
        song_id = int(query.data.split("cover:", 1)[1])
    except (ValueError, IndexError):
        return
    song = next((s for s in songs if s["id"] == song_id), None)
    if not song:
        await query.message.reply_text("❌ آهنگ پیدا نشد")
        return
    cover_url = get_album_art(song["performer"], song["title"])
    if cover_url:
        await query.message.reply_photo(photo=cover_url, caption=f"🖼 {song['title']} - {song['performer']}")
    else:
        await query.message.reply_text("❌ کاور پیدا نشد")

async def lyrics_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    try:
        song_id = int(query.data.split("lyrics:", 1)[1])
    except (ValueError, IndexError):
        return
    song = next((s for s in songs if s["id"] == song_id), None)
    if not song:
        await query.message.reply_text("❌ آهنگ پیدا نشد")
        return
    lyrics = safe_get_lyrics(song["performer"], song["title"])
    if not lyrics:
        await query.message.reply_text("❌ لیریک پیدا نشد")
        return
    if len(lyrics) > 3500:
        lyrics = lyrics[:3500] + "\n\n..."
    await query.message.reply_text(f"📝 {song['title']} - {song['performer']}\n\n{lyrics}")

async def details_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    try:
        song_id = int(query.data.split("details:", 1)[1])
    except (ValueError, IndexError):
        return
    song = next((s for s in songs if s["id"] == song_id), None)
    if not song:
        await query.message.reply_text("❌ آهنگ پیدا نشد")
        return
    info = get_track_info(song["performer"], song["title"])
    text = "ℹ️ جزییات آهنگ\n─────────────────\n\n"
    text += f"🗣 {song['performer']}\n"
    text += f"🎵 {song['title']}\n"
    if info and info.get("published"):
        text += f"📅 {info['published']}\n"
    if info and info.get("duration"):
        text += f"⏱ {info['duration']}\n"
    await query.message.reply_text(text)

async def demo_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    try:
        song_id = int(query.data.split("demo:", 1)[1])
    except (ValueError, IndexError):
        return
    song = next((s for s in songs if s["id"] == song_id), None)
    if not song:
        await query.message.reply_text("❌ آهنگ پیدا نشد")
        return
    preview_url = get_deezer_preview(song["performer"], song["title"])
    if not preview_url:
        await query.message.reply_text("❌ دمو موجود نیست")
        return
    await query.message.reply_voice(
        voice=preview_url,
        caption=f"▶️ دمو ۳۰ ثانیه\n🎵 {song['title']} - {song['performer']}"
    )

async def recommendation_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    try:
        song_id = int(query.data.split("rec:", 1)[1])
    except (ValueError, IndexError):
        return
    song = next((s for s in songs if s["id"] == song_id), None)
    if not song:
        await query.message.reply_text("❌ آهنگ پیدا نشد")
        return
    await query.message.reply_text("🔍 در حال جستجو...")
    recs = recommend_songs(song)
    if not recs:
        await query.message.reply_text("😎 پیشنهادی پیدا نشد")
        return

    def fmt_playcount(n):
        if n >= 1_000_000:
            return f"{n / 1_000_000:.1f}M"
        if n >= 1_000:
            return f"{n / 1_000:.0f}K"
        return str(n)

    text = f"🎯 پیشنهاد از {song['performer']}:\n─────────────────\n\n"
    for i, s in enumerate(recs, 1):
        text += f"{i:02d}. {s['title']}  •  ▶️ {fmt_playcount(s['playcount'])}\n"
    await query.message.reply_text(text)

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data
    if data == "back_to_artists":
        await query.answer()
        artists = sorted({s['performer'] for s in songs}, key=str.lower)
        keyboard = [[InlineKeyboardButton(a, callback_data=f"artist:{a}")] for a in artists]
        await query.edit_message_text("🎤 یک خواننده انتخاب کنید:", reply_markup=InlineKeyboardMarkup(keyboard))
    elif data == "random":
        await query.answer()
        if not songs:
            await query.message.reply_text("❌ هیچ آهنگی موجود نیست")
            return
        song = random.choice(songs)
        song["plays"] = song.get("plays", 0) + 1
        save_songs()
        await query.message.reply_audio(audio=song["file_id"], caption=f"🎲 {song['title']} - {song['performer']}")

# =========================
# DELETE HANDLERS
# =========================

async def delete_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update):
        return
    if not songs:
        await update.message.reply_text("❌ هیچ آهنگی موجود نیست")
        return
    artists = sorted({s['performer'] for s in songs}, key=str.lower)
    keyboard = [[InlineKeyboardButton(a, callback_data=f"del_artist:{a}")] for a in artists]
    await update.message.reply_text("🗑 یک خواننده انتخاب کنید:", reply_markup=InlineKeyboardMarkup(keyboard))

async def delete_artist_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    performer = query.data.split("del_artist:", 1)[1]
    artist_songs = sorted(
        [s for s in songs if s['performer'] == performer],
        key=lambda x: x['title'].lower()
    )
    if not artist_songs:
        await query.edit_message_text(f"❌ آهنگی از {performer} موجود نیست")
        return
    keyboard = [[InlineKeyboardButton(s["title"], callback_data=f"del_song:{s['id']}")] for s in artist_songs]
    keyboard.append([InlineKeyboardButton("🔙 بازگشت", callback_data="back_to_artists")])
    await query.edit_message_text(f"🗑 آهنگ‌های {performer}:", reply_markup=InlineKeyboardMarkup(keyboard))

async def delete_song_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    try:
        song_id = int(query.data.split("del_song:", 1)[1])
    except (ValueError, IndexError):
        return
    song = next((s for s in songs if s["id"] == song_id), None)
    if not song:
        await query.edit_message_text("❌ آهنگ پیدا نشد")
        return
    songs.remove(song)
    reorder_ids()
    save_songs()
    await query.edit_message_text(f"🗑 آهنگ حذف شد: {song['title']} ({song['performer']})")