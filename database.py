import aiosqlite
from datetime import datetime

DB_PATH = "bot.db"


async def init_db(db_path: str = DB_PATH) -> None:
    """Инициализация базы данных и создание таблиц."""
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS warnings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                moderator_id INTEGER NOT NULL,
                reason TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS guild_settings (
                guild_id INTEGER PRIMARY KEY,
                log_channel_id INTEGER DEFAULT 0,
                anti_spam INTEGER DEFAULT 1,
                anti_invite INTEGER DEFAULT 1,
                anti_caps INTEGER DEFAULT 1,
                anti_badwords INTEGER DEFAULT 1,
                anti_mass_mention INTEGER DEFAULT 1,
                ignore_admins INTEGER DEFAULT 1,
                anti_nsfw INTEGER DEFAULT 1,
                anti_toxicity INTEGER DEFAULT 1
            )
            """
        )
        # Добавляем колонки, если таблица уже создана ранее
        try:
            await db.execute("ALTER TABLE guild_settings ADD COLUMN ignore_admins INTEGER DEFAULT 1")
        except Exception:
            pass
        try:
            await db.execute("ALTER TABLE guild_settings ADD COLUMN anti_nsfw INTEGER DEFAULT 1")
        except Exception:
            pass
        try:
            await db.execute("ALTER TABLE guild_settings ADD COLUMN anti_toxicity INTEGER DEFAULT 1")
        except Exception:
            pass
        await db.commit()


async def add_warning(guild_id: int, user_id: int, moderator_id: int, reason: str, db_path: str = DB_PATH) -> int:
    """Добавляет предупреждение пользователю и возвращает общее количество его предупреждений."""
    now_str = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            """
            INSERT INTO warnings (guild_id, user_id, moderator_id, reason, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (guild_id, user_id, moderator_id, reason, now_str)
        )
        await db.commit()

        cursor = await db.execute(
            "SELECT COUNT(*) FROM warnings WHERE guild_id = ? AND user_id = ?",
            (guild_id, user_id)
        )
        count = (await cursor.fetchone())[0]
        return count


async def get_warnings(guild_id: int, user_id: int, db_path: str = DB_PATH) -> list[dict]:
    """Возвращает список всех предупреждений пользователя на сервере."""
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            """
            SELECT id, guild_id, user_id, moderator_id, reason, created_at
            FROM warnings
            WHERE guild_id = ? AND user_id = ?
            ORDER BY id ASC
            """,
            (guild_id, user_id)
        )
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]


async def clear_warnings(guild_id: int, user_id: int, db_path: str = DB_PATH) -> int:
    """Удаляет все предупреждения пользователя на сервере."""
    async with aiosqlite.connect(db_path) as db:
        cursor = await db.execute(
            "SELECT COUNT(*) FROM warnings WHERE guild_id = ? AND user_id = ?",
            (guild_id, user_id)
        )
        count = (await cursor.fetchone())[0]
        if count > 0:
            await db.execute(
                "DELETE FROM warnings WHERE guild_id = ? AND user_id = ?",
                (guild_id, user_id)
            )
            await db.commit()
        return count


async def remove_warning(warning_id: int, guild_id: int, db_path: str = DB_PATH) -> bool:
    """Удаляет конкретное предупреждение по его ID."""
    async with aiosqlite.connect(db_path) as db:
        cursor = await db.execute(
            "DELETE FROM warnings WHERE id = ? AND guild_id = ?",
            (warning_id, guild_id)
        )
        await db.commit()
        return cursor.rowcount > 0


async def get_guild_settings(guild_id: int, db_path: str = DB_PATH) -> dict:
    """Возвращает настройки гильдии (или значения по умолчанию)."""
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT * FROM guild_settings WHERE guild_id = ?",
            (guild_id,)
        )
        row = await cursor.fetchone()
        if row:
            return dict(row)

        # Значения по умолчанию
        defaults = {
            "guild_id": guild_id,
            "log_channel_id": 0,
            "anti_spam": 1,
            "anti_invite": 1,
            "anti_caps": 1,
            "anti_badwords": 1,
            "anti_mass_mention": 1,
            "ignore_admins": 1,
            "anti_nsfw": 1,
            "anti_toxicity": 1
        }
        await db.execute(
            """
            INSERT OR IGNORE INTO guild_settings 
            (guild_id, log_channel_id, anti_spam, anti_invite, anti_caps, anti_badwords, anti_mass_mention, ignore_admins, anti_nsfw, anti_toxicity)
            VALUES (?, 0, 1, 1, 1, 1, 1, 1, 1, 1)
            """,
            (guild_id,)
        )
        await db.commit()
        return defaults


async def set_log_channel(guild_id: int, channel_id: int, db_path: str = DB_PATH) -> None:
    """Устанавливает канал для логов."""
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            """
            INSERT INTO guild_settings (guild_id, log_channel_id)
            VALUES (?, ?)
            ON CONFLICT(guild_id) DO UPDATE SET log_channel_id = excluded.log_channel_id
            """,
            (guild_id, channel_id)
        )
        await db.commit()


async def set_feature_toggle(guild_id: int, feature: str, enabled: bool, db_path: str = DB_PATH) -> None:
    """Включает или выключает модуль автомодерации (anti_spam, anti_invite, etc.)."""
    allowed_features = {"anti_spam", "anti_invite", "anti_caps", "anti_badwords", "anti_mass_mention", "ignore_admins", "anti_nsfw", "anti_toxicity"}
    if feature not in allowed_features:
        raise ValueError(f"Unknown feature: {feature}")

    val = 1 if enabled else 0
    async with aiosqlite.connect(db_path) as db:
        # Убедимся, что строка существует
        await get_guild_settings(guild_id, db_path)
        await db.execute(
            f"UPDATE guild_settings SET {feature} = ? WHERE guild_id = ?",
            (val, guild_id)
        )
        await db.commit()
