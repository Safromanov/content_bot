# bot.py

import logging
import asyncio
from telegram import Update
from telegram.ext import ApplicationBuilder, MessageHandler, CommandHandler, CallbackQueryHandler, filters
from config import cfg, validate_config
from handlers import (
    handle_message, handle_start, handle_status,
    handle_stats, handle_dlq, handle_recategory, queue_worker,
)

logging.basicConfig(
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
    level=logging.INFO
)
# Подавляем httpx/httpcore — они логируют URL с токеном
logging.getLogger('httpx').setLevel(logging.WARNING)
logging.getLogger('httpcore').setLevel(logging.WARNING)
logging.getLogger('telegram.vendor.ptb_urllib3').setLevel(logging.WARNING)

logger = logging.getLogger(__name__)


async def post_init(app):
    asyncio.create_task(queue_worker(app))
    logger.info('Бот запущен')


def main():
    validate_config()   # завершает процесс если .env неполный

    app = (
        ApplicationBuilder()
        .token(cfg.TELEGRAM_TOKEN)
        .post_init(post_init)
        .build()
    )

    app.add_handler(CommandHandler('start',  handle_start))
    app.add_handler(CommandHandler('status', handle_status))
    app.add_handler(CommandHandler('stats',  handle_stats))
    app.add_handler(CommandHandler('dlq',    handle_dlq))
    app.add_handler(CallbackQueryHandler(handle_recategory, pattern=r'^r(?:e|c):'))

    app.add_handler(MessageHandler(
        filters.TEXT | filters.PHOTO | filters.VIDEO |
        filters.AUDIO | filters.VOICE | filters.Document.ALL,
        handle_message
    ))

    logger.info('Polling...')
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == '__main__':
    main()
