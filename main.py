import asyncio
import os
import sys
import logging
from dotenv import load_dotenv

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
        self.web_runner = None

    async def setup_hook(self):
        """Асинхронная инициализация перед запуском бота."""
        # 1. Инициализация базы данных SQLite
        logger.info("Инициализация базы данных SQLite...")
        await init_db()

        # 2. Запуск веб-панели управления (Dashboard) и health check
        try:
            from web.server import start_web_server
            port = int(os.getenv("PORT", 10000))
            self.web_runner = await start_web_server(self, port=port)
            logger.info(f"Веб-интерфейс Dashboard успешно запущен на порту {port}")
        except Exception as e:
            logger.error(f"Не удалось запустить веб-интерфейс Dashboard: {e}")

        # 3. Загрузка когов (модулей)
        cogs = ["cogs.automod", "cogs.moderation"]
        for cog in cogs:
            try:
                await self.load_extension(cog)
                logger.info(f"Успешно загружен модуль: {cog}")
            except Exception as e:
                logger.error(f"Не удалось загрузить модуль {cog}: {e}")

        # 4. Синхронизация слэш-команд с Discord
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

        # Мгновенная синхронизация слэш-команд для каждого сервера (устраняет 1-часовую задержку кэша Discord)
        for guild in self.guilds:
            try:
                self.tree.copy_global_to(guild=guild)
                await self.tree.sync(guild=guild)
                logger.info(f"Слэш-команды успешно синхронизированы для сервера: {guild.name} ({guild.id})")
            except Exception as e:
                logger.warning(f"Не удалось синхронизировать команды для {guild.name}: {e}")

        # Автоматическое восстановление настроек всех серверов из облака Discord
        try:
            from cloud_sync import restore_all_guilds
            asyncio.create_task(restore_all_guilds(self))
        except Exception as e:
            logger.error(f"Не удалось запустить восстановление настроек: {e}")

    async def on_guild_join(self, guild: discord.Guild):
        """При добавлении на новый сервер — синхронизация команд и создание облачного хранилища."""
        try:
            self.tree.copy_global_to(guild=guild)
            await self.tree.sync(guild=guild)
        except Exception as e:
            logger.warning(f"Не удалось синхронизировать команды для нового сервера {guild.id}: {e}")

        try:
            from cloud_sync import sync_guild_settings_to_discord
            await sync_guild_settings_to_discord(self, guild)
        except Exception as e:
            logger.error(f"Ошибка при сохранении настроек для нового сервера {guild.id}: {e}")

    async def close(self):
        """Очистка ресурсов при завершении работы бота."""
        if self.web_runner:
            try:
                await self.web_runner.cleanup()
                logger.info("Веб-сервер Dashboard корректно остановлен.")
            except Exception as e:
                logger.warning(f"Ошибка при остановке веб-сервера: {e}")
        await super().close()


def main():
    if not TOKEN or TOKEN.strip() == "" or "your_bot_token" in TOKEN:
        print("\n" + "!" * 60)
        print("ОШИБКА: Токен бота не настроен!")
        print("1. Откройте файл '.env' в папке проекта.")
        print("2. Вставьте ваш токен в поле: DISCORD_TOKEN=ваш_токен_здесь")
        print("3. Убедитесь, что в Discord Developer Portal включен 'Message Content Intent'.")
        print("!" * 60 + "\n")
        return

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
