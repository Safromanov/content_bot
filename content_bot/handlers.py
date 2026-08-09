# handlers.py

import asyncio
import logging
import uuid
from telegram import Update
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from content_bot.detector import detect, ContentType
from content_bot.processor import process_task
from content_bot.storage.retry import save_to_dlq, get_dlq_count
from content_bot.storage.stats import format_stats_message
from content_bot.config import cfg
from content_bot.storage.nocodb import update_record_category

logger = logging.getLogger(__name__)

task_queue = asyncio.Queue()

_album_buffer: dict = {}

TYPE_LABELS = {
    ContentType.YOUTUBE:   'YouTube видео',
    ContentType.INSTAGRAM: 'Instagram',
    ContentType.THREADS:   'Threads',
    ContentType.TIKTOK:    'TikTok',
    ContentType.TELEGRAM:  'Telegram контент',
    ContentType.VK:        'VK',
    ContentType.VK_WALL:   'пост ВКонтакте',
    ContentType.LINKEDIN:  'LinkedIn',
    ContentType.OTHER_URL: 'ссылка',
    ContentType.VIDEO:     'видеофайл',
    ContentType.AUDIO:     'аудиофайл',
    ContentType.PHOTO:     'фото',
    ContentType.TEXT:      'текст',
    ContentType.UNKNOWN:   'неизвестный формат',
}

# Типы для которых транскрипция занимает время — показываем статус
LONG_PROCESSING_TYPES = {
    ContentType.YOUTUBE,
    ContentType.INSTAGRAM,
    ContentType.THREADS,
    ContentType.TIKTOK,
    ContentType.VK,
    ContentType.VK_WALL,
    ContentType.VIDEO,
    ContentType.AUDIO,
}


def is_allowed(update: Update) -> bool:
    if cfg.ALLOWED_USER_ID == 0:
        return True
    return update.effective_user.id == cfg.ALLOWED_USER_ID


async def _flush_album(group_id: str, chat_id: int, app):
    import time
    while True:
        entry = _album_buffer.get(group_id)
        if not entry:
            return
        # Ждем 2 секунды с момента последнего добавления фото
        if time.monotonic() - entry['last_update'] >= 2.0:
            break
        await asyncio.sleep(0.5)

    entry = _album_buffer.pop(group_id, None)
    if not entry:
        return
    task = entry['task']
    task['content']['photo_ids'] = entry['photo_ids']
    await task_queue.put(task)
    count = len(entry['photo_ids'])
    await app.bot.send_message(
        chat_id=chat_id,
        text='Принял! Альбом из ' + str(count) + ' фото. Обрабатываю...'
    )


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update):
        logger.info('Чужой user_id=' + str(update.effective_user.id) + ' — игнорируем')
        return

    message  = update.effective_message
    username = update.effective_user.username or 'без имени'
    task_id  = uuid.uuid4().hex[:8]

    logger.info('[' + task_id + '] Сообщение от @' + username)

    content      = detect(message)
    content_type = content['type']

    if content_type == ContentType.UNKNOWN:
        await message.reply_text(
            'Не понял что это. Пришли:\n'
            '• Ссылку на YouTube, Instagram, Threads, TikTok, VK, LinkedIn\n'
            '• Фото или альбом фото\n'
            '• Видео или аудио файл\n'
            '• Текст для сохранения'
        )
        return

    if (content_type == ContentType.THREADS and
            '/share/' in (content.get('url') or '').lower()):
        await message.reply_text(
            'Это короткая share-ссылка Threads. Публичная страница по ней '
            'показывает только экран входа, без самого поста.\n\n'
            'Открой ссылку в браузере и пришли адрес публикации вида:\n'
            'threads.com/@имя/post/код'
        )
        return

    task = {
        'task_id':    task_id,
        'user_id':    update.effective_user.id,
        'chat_id':    message.chat_id,
        'message_id': message.message_id,
        'content':    content,
    }

    # ── Альбомы ────────────────────────────────────────────
    group_id = content.get('media_group_id')
    if content_type == ContentType.PHOTO and group_id:
        import time
        photo_id = content['photo_ids'][0] if content['photo_ids'] else None
        if group_id not in _album_buffer:
            _album_buffer[group_id] = {'task': task, 'photo_ids': [photo_id] if photo_id else [], 'last_update': time.monotonic()}
            asyncio.create_task(_flush_album(group_id, message.chat_id, context.application))
        else:
            if photo_id:
                _album_buffer[group_id]['photo_ids'].append(photo_id)
                _album_buffer[group_id]['last_update'] = time.monotonic()
        return

    # ── Одиночные ──────────────────────────────────────────
    await task_queue.put(task)
    label = TYPE_LABELS.get(content_type, 'контент')

    # Для длинных задач показываем статус
    if content_type in LONG_PROCESSING_TYPES:
        status_hints = {
            ContentType.YOUTUBE:   '🎬 Получаю субтитры...',
            ContentType.VIDEO:     '🎙 Транскрибирую аудио...',
            ContentType.AUDIO:     '🎙 Транскрибирую аудио...',
            ContentType.INSTAGRAM: '📥 Скачиваю и анализирую...',
            ContentType.THREADS:   '📥 Скачиваю изображения...',
            ContentType.TIKTOK:    '📥 Скачиваю и анализирую...',
            ContentType.VK:        '📥 Скачиваю и анализирую...',
            ContentType.VK_WALL:   '📥 Получаю пост ВКонтакте...',
        }
        hint = status_hints.get(content_type, 'Обрабатываю...')
        await message.reply_text(
            'Принял ' + label + ' [' + task_id + ']\n' + hint +
            '\nЭто может занять 30-60 секунд.'
        )
    else:
        await message.reply_text('Принял! Вижу ' + label + '. Обрабатываю...')


async def handle_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update):
        return
    await update.message.reply_text(
        'Привет! Я сохраняю контент в NocoDB.\n\n'
        'Пришли мне:\n'
        '• Ссылку на YouTube / Instagram / Threads / TikTok / VK / LinkedIn\n'
        '• Фото или альбом\n'
        '• Видео или аудио файл\n'
        '• Текст\n\n'
        'Команды:\n'
        '/stats — статистика\n'
        '/status — очередь\n'
        '/dlq — упавшие задачи'
    )


async def handle_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update):
        return
    size = task_queue.qsize()
    dlq  = get_dlq_count()
    msg  = '✅ Очередь пуста' if size == 0 else '⏳ В очереди: ' + str(size) + ' задач(и)'
    if dlq > 0:
        msg += '\n⚠️ В DLQ (упавшие): ' + str(dlq) + ' — /dlq для деталей'
    await update.message.reply_text(msg)


async def handle_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update):
        return
    await update.message.reply_text(
        format_stats_message(),
        parse_mode='Markdown'
    )


async def handle_dlq(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update):
        return
    import json, os
    dlq_file = cfg.DLQ_FILE
    if not os.path.exists(dlq_file):
        await update.message.reply_text('✅ DLQ пуст — упавших задач нет')
        return
    try:
        with open(dlq_file, 'r', encoding='utf-8') as f:
            entries = json.load(f)
        if not entries:
            await update.message.reply_text('✅ DLQ пуст')
            return
        lines = ['⚠️ Dead Letter Queue (' + str(len(entries)) + ' задач):']
        for e in entries[-10:]:   # последние 10
            lines.append(
                '\n🕐 ' + e.get('timestamp', '')[:16] +
                '\n   Тип: '  + e.get('task', {}).get('content_type', '?') +
                '\n   URL: '  + (e.get('task', {}).get('url', '') or '—')[:50] +
                '\n   Ошибка: ' + e.get('error', '')[:80]
            )
        await update.message.reply_text('\n'.join(lines))
    except Exception as ex:
        await update.message.reply_text('Ошибка чтения DLQ: ' + str(ex))


async def handle_recategory(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показывает категории и применяет выбранную к строке NocoDB."""
    if not is_allowed(update):
        return

    query = update.callback_query
    data = query.data or ''

    if data.startswith('re:'):
        await query.answer()
        row_id = data.split(':', 1)[1]
        buttons = [
            InlineKeyboardButton(category, callback_data='rc:' + row_id + ':' + str(index))
            for index, category in enumerate(cfg.CATEGORIES)
        ]
        keyboard = [buttons[i:i + 2] for i in range(0, len(buttons), 2)]
        await query.edit_message_reply_markup(reply_markup=InlineKeyboardMarkup(keyboard))
        return

    try:
        _, row_id, category_index = data.split(':', 2)
        category = cfg.CATEGORIES[int(category_index)]
        loop = asyncio.get_running_loop()
        updated = await loop.run_in_executor(None, update_record_category, row_id, category)
        if not updated:
            raise ValueError('Некорректная категория или ID записи')
        await query.edit_message_reply_markup(reply_markup=None)
        await query.answer('Категория изменена: ' + category, show_alert=True)
    except Exception as ex:
        logger.error('Ошибка смены категории: ' + str(ex), exc_info=True)
        await query.answer('Не удалось изменить категорию', show_alert=True)


async def queue_worker(app):
    logger.info('Воркер запущен')
    while True:
        task = await task_queue.get()
        task_id = task.get('task_id', '?')
        attempts = 0
        last_error = None

        while attempts < cfg.RETRY_ATTEMPTS:
            try:
                import copy
                await process_task(copy.deepcopy(task), app)
                break
            except Exception as e:
                attempts  += 1
                last_error = e
                if attempts < cfg.RETRY_ATTEMPTS:
                    delay = cfg.RETRY_BASE_DELAY * (2 ** (attempts - 1))
                    logger.warning(
                        '[' + task_id + '] Попытка ' + str(attempts) +
                        '/' + str(cfg.RETRY_ATTEMPTS) +
                        ' failed: ' + str(e)[:100] +
                        ' — retry in ' + str(delay) + 's'
                    )
                    await asyncio.sleep(delay)
                else:
                    # Все попытки исчерпаны — DLQ
                    logger.error('[' + task_id + '] Все попытки исчерпаны: ' + str(e))
                    save_to_dlq(task, str(e))
                    try:
                        await app.bot.send_message(
                            chat_id=task['chat_id'],
                            text='❌ Задача [' + task_id + '] не удалась после ' +
                                 str(cfg.RETRY_ATTEMPTS) + ' попыток.\n' +
                                 'Сохранена в DLQ для ручного разбора.'
                        )
                    except Exception:
                        pass

        task_queue.task_done()
