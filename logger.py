from loguru import logger
import sys
from datetime import datetime
from pathlib import Path

def setup_logging():
    """Настройка логирования с rotation и retention"""
    logs_dir = Path("logs")
    logs_dir.mkdir(exist_ok=True)
    
    log_file = logs_dir / f"bot_{datetime.now().strftime('%Y%m%d')}.log"
    
    logger.remove()  # Удаляем все предыдущие обработчики
    
    # Конфигурация логирования в файл
    logger.add(
        log_file,
        rotation="1 day",  # Ротация каждый день
        retention="7 days",  # Хранение логов 7 дней
        compression="zip",  # Сжатие старых логов
        level="INFO",
        format="{time:YYYY-MM-DD HH:mm:ss} | {level} | {message}"
    )
    
    # Конфигурация логирования в консоль
    logger.add(
        sys.stdout,
        level="INFO",
        format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level}</level> | <cyan>{message}</cyan>"
    )
    
    return logger
