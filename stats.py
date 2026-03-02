# stats.py
# Метрики обработки задач — сохраняются в stats.json после каждой задачи.
# Используются командой /stats.

import json
import logging
import os
import time
from datetime import datetime
from config import cfg

logger = logging.getLogger(__name__)

_STATS_FILE = cfg.STATS_FILE

_DEFAULT = {
    'total':        0,
    'success':      0,
    'errors':       0,
    'by_category':  {},
    'by_platform':  {},
    'avg_time_sec': 0.0,
    'last_updated': '',
}


def _load() -> dict:
    if os.path.exists(_STATS_FILE):
        try:
            with open(_STATS_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
                # Добавляем отсутствующие ключи при обновлении формата
                for k, v in _DEFAULT.items():
                    data.setdefault(k, v)
                return data
        except Exception:
            pass
    return dict(_DEFAULT)


def _save(data: dict) -> None:
    try:
        with open(_STATS_FILE, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.warning('stats save: ' + str(e))


def record(category: str, platform: str, elapsed_sec: float, success: bool) -> None:
    """Записывает результат обработки одной задачи."""
    data = _load()

    data['total'] += 1
    if success:
        data['success'] += 1
    else:
        data['errors'] += 1

    # Скользящее среднее времени обработки (по всем задачам)
    n = data['total']  # уже увеличен на 1 выше
    data['avg_time_sec'] = round(
        (data['avg_time_sec'] * (n - 1) + elapsed_sec) / n, 2
    )

    if category:
        data['by_category'][category] = data['by_category'].get(category, 0) + 1
    if platform:
        data['by_platform'][platform] = data['by_platform'].get(platform, 0) + 1

    data['last_updated'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    _save(data)


def format_stats_message() -> str:
    """Форматирует статистику для отправки пользователю через /stats."""
    from retry_utils import get_dlq_count

    data  = _load()
    lines = [
        '📊 *Статистика бота*',
        '',
        '📥 Всего задач: ' + str(data['total']),
        '✅ Успешно: '     + str(data['success']),
        '❌ Ошибок: '      + str(data['errors']),
        '⚠️ В DLQ: '       + str(get_dlq_count()),
        '⏱ Среднее время: ' + str(data['avg_time_sec']) + ' сек',
        '',
        '📂 *По категориям:*',
    ]

    cats = sorted(data['by_category'].items(), key=lambda x: -x[1])
    for cat, count in cats:
        lines.append('  ' + cat + ': ' + str(count))

    lines += ['', '📱 *По платформам:*']
    platforms = sorted(data['by_platform'].items(), key=lambda x: -x[1])
    for p, count in platforms:
        lines.append('  ' + p + ': ' + str(count))

    if data['last_updated']:
        lines += ['', '🕐 Обновлено: ' + data['last_updated']]

    return '\n'.join(lines)
