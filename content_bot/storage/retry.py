# retry.py
# Retry с exponential backoff + Dead Letter Queue для упавших задач.
#
# DLQ: задачи упавшие все 3 попытки сохраняются в dead_letter.json
# для ручного разбора. При следующем старте бота DLQ не переигрывается
# автоматически — это намеренно (ручной контроль).

import json
import logging
import os
from datetime import datetime
from content_bot.config import cfg

logger = logging.getLogger(__name__)

def save_to_dlq(task: dict, error: str) -> None:
    """Сохраняет задачу в dead_letter.json для ручного разбора."""
    entry = {
        'timestamp': datetime.now().isoformat(),
        'error':     error,
        'task': {
            'user_id':    task.get('user_id'),
            'chat_id':    task.get('chat_id'),
            'task_id':    task.get('task_id', ''),
            'content_type': task.get('content', {}).get('type', {}).value
                if hasattr(task.get('content', {}).get('type', ''), 'value') else '',
            'url':        task.get('content', {}).get('url', ''),
            'raw_text':   (task.get('content', {}).get('raw_text', '') or '')[:200],
        }
    }

    dlq_path = cfg.DLQ_FILE
    existing = []
    if os.path.exists(dlq_path):
        try:
            with open(dlq_path, 'r', encoding='utf-8') as f:
                existing = json.load(f)
        except Exception:
            existing = []

    existing.append(entry)

    with open(dlq_path, 'w', encoding='utf-8') as f:
        json.dump(existing, f, ensure_ascii=False, indent=2)

    logger.error('Задача сохранена в DLQ: ' + dlq_path +
                 ' (всего: ' + str(len(existing)) + ')')


def get_dlq_count() -> int:
    if not os.path.exists(cfg.DLQ_FILE):
        return 0
    try:
        with open(cfg.DLQ_FILE, 'r', encoding='utf-8') as f:
            return len(json.load(f))
    except Exception:
        return 0
