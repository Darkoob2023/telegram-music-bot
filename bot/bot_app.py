from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    filters
)

from bot.handlers import (
    handle_audio,
    start, play, random_song, search,
    top_command, delete_start, list_songs,
    stats_command, help_command, about_command,
    artist_callback, song_callback,
    delete_artist_callback, delete_song_callback,
    lyrics_callback, button_handler, recommendation_callback,
    cover_callback, details_callback, demo_callback,
    identify_command, handle_identify_media,
    identify_yes_callback, identify_no_callback
)
from config import BOT_TOKEN


def run_bot():
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    # 🎵 audio
    app.add_handler(CommandHandler("identify", identify_command))
    app.add_handler(MessageHandler(filters.AUDIO, handle_audio))
    app.add_handler(MessageHandler(
        filters.AUDIO | filters.VOICE | filters.VIDEO | filters.VIDEO_NOTE,
        handle_identify_media
    ))

    # 📌 commands
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("play", play))
    app.add_handler(CommandHandler("random", random_song))
    app.add_handler(CommandHandler("search", search))
    app.add_handler(CommandHandler("top", top_command))
    app.add_handler(CommandHandler("delete", delete_start))
    app.add_handler(CommandHandler("list", list_songs))
    app.add_handler(CommandHandler("stats", stats_command))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("about", about_command))
    app.add_handler(CommandHandler("identify", identify_command))
    
    # 🎛 callbacks
    app.add_handler(CallbackQueryHandler(identify_yes_callback,        pattern=r"^identify_yes:"))
    app.add_handler(CallbackQueryHandler(identify_no_callback,         pattern=r"^identify_no$"))
    app.add_handler(CallbackQueryHandler(artist_callback,              pattern=r"^artist:"))
    app.add_handler(CallbackQueryHandler(song_callback,             pattern=r"^song:"))
    app.add_handler(CallbackQueryHandler(lyrics_callback,           pattern=r"^lyrics:"))
    app.add_handler(CallbackQueryHandler(cover_callback,            pattern=r"^cover:"))
    app.add_handler(CallbackQueryHandler(details_callback,          pattern=r"^details:"))
    app.add_handler(CallbackQueryHandler(demo_callback,             pattern=r"^demo:"))
    app.add_handler(CallbackQueryHandler(recommendation_callback,   pattern=r"^rec:"))
    app.add_handler(CallbackQueryHandler(delete_artist_callback,    pattern=r"^del_artist:"))
    app.add_handler(CallbackQueryHandler(delete_song_callback,      pattern=r"^del_song:"))
    app.add_handler(CallbackQueryHandler(button_handler,            pattern=r"^(back_to_artists|random)$"))
    
    print("🚀 MusicBot running...")
    app.run_polling()