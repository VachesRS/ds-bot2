import asyncio
import os
import sys
import logging
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from dotenv import load_dotenv

class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write(b"Bot is alive and running!")

    def log_message(self, format, *args):
        # Отключаем логирование HTTP-запросов, чтобы не засорять логи бота
        return

def start_health_server():
    try:
        port = int(os.getenv("PORT", 10000))
        server = HTTPServer(("0.0.0.0", port), HealthCheckHandler)
        server.serve_forever()
    except Exception as e:
        logger.warning(f"Веб-сервер проверки здоровья не запустился: {e}")

import discord
from discord.ext import commands

from database import init_db

# Настройка логирования (в консоль и в файл bot.log)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("bot.log", encoding="utf-8")
    ]
)
logger = logging.getLogger("DiscordBot")

# Загрузка переменных окружения из .env
load_dotenv()

TOKEN = os.getenv("DISCORD_TOKEN")


class AutoModBot(commands.Bot):
    def __init__(self):
        # Настройка намерений (Intents)
        # Message Content Intent обязателен для работы автомодерации!
        intents = discord.Intents.default()
        intents.message_content = True
        intents.members = True
        intents.guilds = True

        super().__init__(
            command_prefix="!",  # Префикс для классических команд (основное управление через слэш-команды /)
            intents=intents,
            help_command=None
        )

    async def setup_hook(self):
        """Асинхронная инициализация перед запуском бота."""
        # 1. Инициализация базы данных SQLite
        logger.info("Инициализация базы данных SQLite...")
        await init_db()

        # 2. Загрузка когов (модулей)
        cogs = ["cogs.automod", "cogs.moderation"]
        for cog in cogs:
            try:
                await self.load_extension(cog)
                logger.info(f"Успешно загружен модуль: {cog}")
            except Exception as e:
                logger.error(f"Не удалось загрузить модуль {cog}: {e}")

        # 3. Синхронизация слэш-команд с Discord
        try:
            synced = await self.tree.sync()
            logger.info(f"Синхронизировано {len(synced)} слэш-команд(ы) глобально.")
        except Exception as e:
            logger.error(f"Ошибка при синхронизации слэш-команд: {e}")

    async def on_ready(self):
        """Событие, когда бот успешно подключился к Discord."""
        activity = discord.Activity(
            type=discord.ActivityType.watching,
            name="за сервером | /automod_status"
        )
        await self.change_presence(status=discord.Status.online, activity=activity)

        logger.info("=" * 45)
        logger.info(f"Бот успешно запущен: {self.user} (ID: {self.user.id})")
        logger.info(f"Подключен к {len(self.guilds)} серверу(ам).")
        logger.info(f"Discord.py версия: {discord.__version__}")
        logger.info("Автомодерация активна и готова к работе.")
        logger.info("=" * 45)


def main():
    if not TOKEN or TOKEN.strip() == "" or "your_bot_token" in TOKEN:
        print("\n" + "!" * 60)
        print("ОШИБКА: Токен бота не настроен!")
        print("1. Откройте файл '.env' в папке проекта.")
        print("2. Вставьте ваш токен в поле: DISCORD_TOKEN=ваш_токен_здесь")
        print("3. Убедитесь, что в Discord Developer Portal включен 'Message Content Intent'.")
        print("!" * 60 + "\n")
        return

    # Запуск фонового веб-сервера для совместимости с облачными хостингами (Render/Koyeb)
    threading.Thread(target=start_health_server, daemon=True).start()
    logger.info("Фоновый веб-сервер проверки работоспособности запущен.")

    while True:
        try:
            bot = AutoModBot()
            bot.run(TOKEN)
        except discord.errors.LoginFailure:
            logger.error("Неверный токен бота (LoginFailure)! Проверьте файл .env.")
            break
        except KeyboardInterrupt:
            logger.info("Бот остановлен пользователем (Ctrl+C).")
            break
        except Exception as e:
            logger.error(f"Бот завершил работу с ошибкой: {e}. Перезапуск через 5 секунд...", exc_info=True)
            import time
            time.sleep(5)


if __name__ == "__main__":
    main()
