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
                anti_toxicity INTEGER DEFAULT 1,
                autorole_id INTEGER DEFAULT 0
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
        try:
            await db.execute("ALTER TABLE guild_settings ADD COLUMN autorole_id INTEGER DEFAULT 0")
        except Exception:
            pass

        # 1. Уровни и опыт
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS user_levels (
                guild_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                xp INTEGER DEFAULT 0,
                level INTEGER DEFAULT 0,
                last_xp_time REAL DEFAULT 0,
                PRIMARY KEY (guild_id, user_id)
            )
            """
        )
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS level_rewards (
                guild_id INTEGER NOT NULL,
                level INTEGER NOT NULL,
                role_id INTEGER NOT NULL,
                PRIMARY KEY (guild_id, level)
            )
            """
        )
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS guild_level_settings (
                guild_id INTEGER PRIMARY KEY,
                enabled INTEGER DEFAULT 1,
                announce_channel_id INTEGER DEFAULT 0,
                xp_rate REAL DEFAULT 1.0
            )
            """
        )

        # 2. Роли по кнопкам
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS button_roles (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id INTEGER NOT NULL,
                channel_id INTEGER NOT NULL,
                message_id INTEGER NOT NULL,
                role_id INTEGER NOT NULL,
                label TEXT DEFAULT '',
                emoji TEXT DEFAULT '',
                style TEXT DEFAULT 'primary'
            )
            """
        )

        # 3. Приветствия новичков
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS welcome_settings (
                guild_id INTEGER PRIMARY KEY,
                enabled INTEGER DEFAULT 0,
                channel_id INTEGER DEFAULT 0,
                message TEXT DEFAULT '',
                dm_message TEXT DEFAULT '',
                send_card INTEGER DEFAULT 1
            )
            """
        )

        # 4. Временные голосовые каналы
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS temp_voice_settings (
                guild_id INTEGER PRIMARY KEY,
                enabled INTEGER DEFAULT 0,
                category_id INTEGER DEFAULT 0,
                master_channel_id INTEGER DEFAULT 0
            )
            """
        )
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS active_temp_channels (
                channel_id INTEGER PRIMARY KEY,
                guild_id INTEGER NOT NULL,
                owner_id INTEGER NOT NULL,
                created_at REAL NOT NULL
            )
            """
        )

        # 5. Розыгрыши призов
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS giveaways (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                message_id INTEGER UNIQUE NOT NULL,
                channel_id INTEGER NOT NULL,
                guild_id INTEGER NOT NULL,
                prize TEXT NOT NULL,
                winners_count INTEGER DEFAULT 1,
                end_time REAL NOT NULL,
                status TEXT DEFAULT 'active',
                host_id INTEGER NOT NULL
            )
            """
        )
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS giveaway_entries (
                giveaway_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                PRIMARY KEY (giveaway_id, user_id)
            )
            """
        )

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
            "anti_toxicity": 1,
            "autorole_id": 0
        }
        await db.execute(
            """
            INSERT OR IGNORE INTO guild_settings 
            (guild_id, log_channel_id, anti_spam, anti_invite, anti_caps, anti_badwords, anti_mass_mention, ignore_admins, anti_nsfw, anti_toxicity, autorole_id)
            VALUES (?, 0, 1, 1, 1, 1, 1, 1, 1, 1, 0)
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


async def set_autorole(guild_id: int, role_id: int, db_path: str = DB_PATH) -> None:
    """Устанавливает или отключает автороль для новых участников (0 - выключено)."""
    async with aiosqlite.connect(db_path) as db:
        await get_guild_settings(guild_id, db_path)
        await db.execute(
            "UPDATE guild_settings SET autorole_id = ? WHERE guild_id = ?",
            (role_id, guild_id)
        )
        await db.commit()


# ---------------------------------------------------------------------------
# Модуль: Уровни и опыт (Levels & XP)
# ---------------------------------------------------------------------------

def calculate_level_from_xp(total_xp: int) -> tuple[int, int, int]:
    """
    Рассчитывает уровень на основе общего количества XP.
    Возвращает (уровень, опыт_в_текущем_уровне, опыт_до_следующего_уровня).
    Формула: для перехода с уровня L на L+1 нужно 5 * (L^2) + 50 * L + 100 XP.
    """
    level = 0
    needed = 100
    while total_xp >= needed:
        total_xp -= needed
        level += 1
        needed = 5 * (level ** 2) + 50 * level + 100
    return level, total_xp, needed


async def get_user_level(guild_id: int, user_id: int, db_path: str = DB_PATH) -> dict:
    """Возвращает информацию об уровне и опыте пользователя."""
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT * FROM user_levels WHERE guild_id = ? AND user_id = ?",
            (guild_id, user_id)
        )
        row = await cursor.fetchone()
        if row:
            d = dict(row)
            lvl, cur_xp, needed_xp = calculate_level_from_xp(d["xp"])
            d["level"] = lvl
            d["cur_xp"] = cur_xp
            d["needed_xp"] = needed_xp
            return d

        return {
            "guild_id": guild_id,
            "user_id": user_id,
            "xp": 0,
            "level": 0,
            "cur_xp": 0,
            "needed_xp": 100,
            "last_xp_time": 0.0
        }


async def add_user_xp(guild_id: int, user_id: int, xp_amount: int, db_path: str = DB_PATH) -> tuple[int, int, bool]:
    """
    Добавляет XP пользователю.
    Возвращает (новый_уровень, суммарный_XP, повысился_ли_уровень).
    """
    import time
    now = time.time()
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT xp, level FROM user_levels WHERE guild_id = ? AND user_id = ?",
            (guild_id, user_id)
        )
        row = await cursor.fetchone()
        if row:
            old_xp = row["xp"]
            old_level = row["level"]
        else:
            old_xp = 0
            old_level = 0

        new_total_xp = old_xp + xp_amount
        new_level, _, _ = calculate_level_from_xp(new_total_xp)
        did_level_up = new_level > old_level

        await db.execute(
            """
            INSERT INTO user_levels (guild_id, user_id, xp, level, last_xp_time)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(guild_id, user_id) DO UPDATE SET
                xp = excluded.xp,
                level = excluded.level,
                last_xp_time = excluded.last_xp_time
            """,
            (guild_id, user_id, new_total_xp, new_level, now)
        )
        await db.commit()
        return new_level, new_total_xp, did_level_up


async def get_user_rank(guild_id: int, user_id: int, db_path: str = DB_PATH) -> int:
    """Возвращает позицию пользователя в таблице лидеров сервера (1, 2, 3...)."""
    async with aiosqlite.connect(db_path) as db:
        cursor = await db.execute(
            """
            SELECT COUNT(*) + 1 FROM user_levels
            WHERE guild_id = ? AND xp > (
                SELECT COALESCE(xp, 0) FROM user_levels WHERE guild_id = ? AND user_id = ?
            )
            """,
            (guild_id, guild_id, user_id)
        )
        row = await cursor.fetchone()
        return row[0] if row else 1


async def get_leaderboard(guild_id: int, limit: int = 10, db_path: str = DB_PATH) -> list[dict]:
    """Возвращает список лидеров сервера по опыту."""
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            """
            SELECT user_id, xp, level
            FROM user_levels
            WHERE guild_id = ? AND xp > 0
            ORDER BY xp DESC
            LIMIT ?
            """,
            (guild_id, limit)
        )
        rows = await cursor.fetchall()
        result = []
        for rank, r in enumerate(rows, start=1):
            d = dict(r)
            d["rank"] = rank
            lvl, cur, needed = calculate_level_from_xp(d["xp"])
            d["level"] = lvl
            d["cur_xp"] = cur
            d["needed_xp"] = needed
            result.append(d)
        return result


async def get_level_rewards(guild_id: int, db_path: str = DB_PATH) -> list[dict]:
    """Возвращает список наградных ролей за уровни."""
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT level, role_id FROM level_rewards WHERE guild_id = ? ORDER BY level ASC",
            (guild_id,)
        )
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]


async def set_level_reward(guild_id: int, level: int, role_id: int, db_path: str = DB_PATH) -> None:
    """Устанавливает роль-награду за достижение уровня."""
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            """
            INSERT INTO level_rewards (guild_id, level, role_id)
            VALUES (?, ?, ?)
            ON CONFLICT(guild_id, level) DO UPDATE SET role_id = excluded.role_id
            """,
            (guild_id, level, role_id)
        )
        await db.commit()


async def remove_level_reward(guild_id: int, level: int, db_path: str = DB_PATH) -> bool:
    """Удаляет роль-награду за уровень."""
    async with aiosqlite.connect(db_path) as db:
        cursor = await db.execute(
            "DELETE FROM level_rewards WHERE guild_id = ? AND level = ?",
            (guild_id, level)
        )
        await db.commit()
        return cursor.rowcount > 0


async def get_guild_level_settings(guild_id: int, db_path: str = DB_PATH) -> dict:
    """Возвращает настройки модуля уровней сервера."""
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT * FROM guild_level_settings WHERE guild_id = ?",
            (guild_id,)
        )
        row = await cursor.fetchone()
        if row:
            return dict(row)
        return {"guild_id": guild_id, "enabled": 1, "announce_channel_id": 0, "xp_rate": 1.0}


async def set_guild_level_settings(guild_id: int, enabled: bool, announce_channel_id: int = 0, xp_rate: float = 1.0, db_path: str = DB_PATH) -> None:
    """Сохраняет настройки уровней сервера."""
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            """
            INSERT INTO guild_level_settings (guild_id, enabled, announce_channel_id, xp_rate)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(guild_id) DO UPDATE SET
                enabled = excluded.enabled,
                announce_channel_id = excluded.announce_channel_id,
                xp_rate = excluded.xp_rate
            """,
            (guild_id, 1 if enabled else 0, announce_channel_id, xp_rate)
        )
        await db.commit()


# ---------------------------------------------------------------------------
# Модуль: Роли по кнопкам (Button Roles)
# ---------------------------------------------------------------------------

async def add_button_role(guild_id: int, channel_id: int, message_id: int, role_id: int, label: str, emoji: str = "", style: str = "primary", db_path: str = DB_PATH) -> int:
    """Добавляет кнопку выдачи роли для указанного сообщения."""
    async with aiosqlite.connect(db_path) as db:
        cursor = await db.execute(
            """
            INSERT INTO button_roles (guild_id, channel_id, message_id, role_id, label, emoji, style)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (guild_id, channel_id, message_id, role_id, label, emoji, style)
        )
        await db.commit()
        return cursor.lastrowid


async def get_button_roles_for_message(message_id: int, db_path: str = DB_PATH) -> list[dict]:
    """Возвращает список кнопок-ролей для конкретного сообщения."""
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT * FROM button_roles WHERE message_id = ?",
            (message_id,)
        )
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]


async def get_all_button_role_messages(guild_id: int = 0, db_path: str = DB_PATH) -> list[dict]:
    """Возвращает уникальные сообщения с кнопками-ролями."""
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        if guild_id:
            cursor = await db.execute(
                "SELECT DISTINCT message_id, channel_id, guild_id FROM button_roles WHERE guild_id = ?",
                (guild_id,)
            )
        else:
            cursor = await db.execute(
                "SELECT DISTINCT message_id, channel_id, guild_id FROM button_roles"
            )
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]


async def delete_button_roles(message_id: int, db_path: str = DB_PATH) -> None:
    """Удаляет все кнопки-роли для сообщения."""
    async with aiosqlite.connect(db_path) as db:
        await db.execute("DELETE FROM button_roles WHERE message_id = ?", (message_id,))
        await db.commit()


# ---------------------------------------------------------------------------
# Модуль: Приветствия новичков (Welcome)
# ---------------------------------------------------------------------------

async def get_welcome_settings(guild_id: int, db_path: str = DB_PATH) -> dict:
    """Возвращает настройки приветствий новичков."""
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("SELECT * FROM welcome_settings WHERE guild_id = ?", (guild_id,))
        row = await cursor.fetchone()
        if row:
            return dict(row)
        return {
            "guild_id": guild_id,
            "enabled": 0,
            "channel_id": 0,
            "message": "Добро пожаловать на наш сервер, {user}! 🎉",
            "dm_message": "",
            "send_card": 1
        }


async def set_welcome_settings(guild_id: int, enabled: bool, channel_id: int, message: str, dm_message: str = "", send_card: bool = True, db_path: str = DB_PATH) -> None:
    """Сохраняет настройки приветствий новичков."""
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            """
            INSERT INTO welcome_settings (guild_id, enabled, channel_id, message, dm_message, send_card)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(guild_id) DO UPDATE SET
                enabled = excluded.enabled,
                channel_id = excluded.channel_id,
                message = excluded.message,
                dm_message = excluded.dm_message,
                send_card = excluded.send_card
            """,
            (guild_id, 1 if enabled else 0, channel_id, message, dm_message, 1 if send_card else 0)
        )
        await db.commit()


# ---------------------------------------------------------------------------
# Модуль: Временные приватные войсы (Temp Voice)
# ---------------------------------------------------------------------------

async def get_temp_voice_settings(guild_id: int, db_path: str = DB_PATH) -> dict:
    """Возвращает конфигурацию временных войс-комнат."""
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("SELECT * FROM temp_voice_settings WHERE guild_id = ?", (guild_id,))
        row = await cursor.fetchone()
        if row:
            return dict(row)
        return {"guild_id": guild_id, "enabled": 0, "category_id": 0, "master_channel_id": 0}


async def set_temp_voice_settings(guild_id: int, enabled: bool, category_id: int, master_channel_id: int, db_path: str = DB_PATH) -> None:
    """Сохраняет конфигурацию временных войс-комнат."""
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            """
            INSERT INTO temp_voice_settings (guild_id, enabled, category_id, master_channel_id)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(guild_id) DO UPDATE SET
                enabled = excluded.enabled,
                category_id = excluded.category_id,
                master_channel_id = excluded.master_channel_id
            """,
            (guild_id, 1 if enabled else 0, category_id, master_channel_id)
        )
        await db.commit()


async def add_temp_channel(channel_id: int, guild_id: int, owner_id: int, db_path: str = DB_PATH) -> None:
    """Регистрирует созданную временную комнату."""
    import time
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            "INSERT INTO active_temp_channels (channel_id, guild_id, owner_id, created_at) VALUES (?, ?, ?, ?)",
            (channel_id, guild_id, owner_id, time.time())
        )
        await db.commit()


async def remove_temp_channel(channel_id: int, db_path: str = DB_PATH) -> None:
    """Удаляет запись о временной комнате."""
    async with aiosqlite.connect(db_path) as db:
        await db.execute("DELETE FROM active_temp_channels WHERE channel_id = ?", (channel_id,))
        await db.commit()


async def get_temp_channel(channel_id: int, db_path: str = DB_PATH) -> dict | None:
    """Возвращает информацию о временном канале."""
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("SELECT * FROM active_temp_channels WHERE channel_id = ?", (channel_id,))
        row = await cursor.fetchone()
        return dict(row) if row else None


async def get_all_temp_channels(db_path: str = DB_PATH) -> list[dict]:
    """Возвращает все активные временные каналы во всех гильдиях."""
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("SELECT * FROM active_temp_channels")
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Модуль: Розыгрыши призов (Giveaways)
# ---------------------------------------------------------------------------

async def create_giveaway(message_id: int, channel_id: int, guild_id: int, prize: str, winners_count: int, end_time: float, host_id: int, db_path: str = DB_PATH) -> int:
    """Создает новый розыгрыш в базе."""
    async with aiosqlite.connect(db_path) as db:
        cursor = await db.execute(
            """
            INSERT INTO giveaways (message_id, channel_id, guild_id, prize, winners_count, end_time, status, host_id)
            VALUES (?, ?, ?, ?, ?, ?, 'active', ?)
            """,
            (message_id, channel_id, guild_id, prize, winners_count, end_time, host_id)
        )
        await db.commit()
        return cursor.lastrowid


async def get_giveaway(giveaway_id: int, db_path: str = DB_PATH) -> dict | None:
    """Возвращает розыгрыш по ID."""
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("SELECT * FROM giveaways WHERE id = ?", (giveaway_id,))
        row = await cursor.fetchone()
        return dict(row) if row else None


async def get_giveaway_by_message(message_id: int, db_path: str = DB_PATH) -> dict | None:
    """Возвращает розыгрыш по message_id."""
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("SELECT * FROM giveaways WHERE message_id = ?", (message_id,))
        row = await cursor.fetchone()
        return dict(row) if row else None


async def get_active_giveaways(db_path: str = DB_PATH) -> list[dict]:
    """Возвращает список активных розыгрышей."""
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("SELECT * FROM giveaways WHERE status = 'active' ORDER BY end_time ASC")
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]


async def add_giveaway_entry(giveaway_id: int, user_id: int, db_path: str = DB_PATH) -> bool:
    """Добавляет участника в розыгрыш. Возвращает True, если добавлен, False если уже участвует."""
    async with aiosqlite.connect(db_path) as db:
        try:
            await db.execute(
                "INSERT INTO giveaway_entries (giveaway_id, user_id) VALUES (?, ?)",
                (giveaway_id, user_id)
            )
            await db.commit()
            return True
        except Exception:
            return False


async def remove_giveaway_entry(giveaway_id: int, user_id: int, db_path: str = DB_PATH) -> bool:
    """Удаляет участника из розыгрыша."""
    async with aiosqlite.connect(db_path) as db:
        cursor = await db.execute(
            "DELETE FROM giveaway_entries WHERE giveaway_id = ? AND user_id = ?",
            (giveaway_id, user_id)
        )
        await db.commit()
        return cursor.rowcount > 0


async def get_giveaway_entries(giveaway_id: int, db_path: str = DB_PATH) -> list[int]:
    """Возвращает список ID участников розыгрыша."""
    async with aiosqlite.connect(db_path) as db:
        cursor = await db.execute("SELECT user_id FROM giveaway_entries WHERE giveaway_id = ?", (giveaway_id,))
        rows = await cursor.fetchall()
        return [r[0] for r in rows]


async def get_giveaway_entries_count(giveaway_id: int, db_path: str = DB_PATH) -> int:
    """Возвращает количество участников розыгрыша."""
    async with aiosqlite.connect(db_path) as db:
        cursor = await db.execute("SELECT COUNT(*) FROM giveaway_entries WHERE giveaway_id = ?", (giveaway_id,))
        row = await cursor.fetchone()
        return row[0] if row else 0


async def end_giveaway(giveaway_id: int, status: str = "ended", db_path: str = DB_PATH) -> None:
    """Завершает розыгрыш."""
    async with aiosqlite.connect(db_path) as db:
        await db.execute("UPDATE giveaways SET status = ? WHERE id = ?", (status, giveaway_id))
        await db.commit()


