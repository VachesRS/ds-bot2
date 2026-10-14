import json
import logging
import aiosqlite
import discord
from discord.ext import commands
from datetime import datetime

from database import (
    get_guild_settings, DB_PATH,
    get_welcome_settings, set_welcome_settings,
    get_guild_level_settings, set_guild_level_settings,
    get_temp_voice_settings, set_temp_voice_settings,
    get_level_rewards, set_level_reward
)

logger = logging.getLogger("CloudSync")

DATA_CHANNEL_NAMES = ("🔒-kobi-data", "kobi-data", "kobi-backup", "🔒-shieldguard-data", "shieldguard-data", "shieldguard-backup")
SETTINGS_HEADER = "🐾 **[KOBI_SETTINGS_STORAGE]**"
LEGACY_HEADER = "**[SHIELDGUARD_SETTINGS_STORAGE]**"


async def get_or_create_data_channel(guild: discord.Guild) -> discord.TextChannel | None:
    """Находит или создает скрытый служебный текстовый канал для хранения настроек."""
    # 1. Поиск существующего канала
    for channel in guild.text_channels:
        if channel.name in DATA_CHANNEL_NAMES:
            return channel

    # 2. Проверяем права бота на создание каналов
    bot_member = guild.me
    if not bot_member:
        try:
            bot_member = await guild.fetch_member(guild._state.user.id)
        except Exception:
            pass

    if not bot_member or not bot_member.guild_permissions.manage_channels:
        logger.warning(f"У бота нет прав Manage Channels для создания канала данных на сервере {guild.name}")
        return None


    try:
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(read_messages=False),
            bot_member: discord.PermissionOverwrite(
                read_messages=True,
                send_messages=True,
                embed_links=True,
                manage_messages=True
            )
        }
        channel = await guild.create_text_channel(
            name="🔒-kobi-data",
            overwrites=overwrites,
            topic="Служебное хранилище конфигурации Kobi. Не удаляйте этот канал: здесь бот автоматически сохраняет все настройки сервера при обновлениях и перезапусках на хостинге.",
            reason="Kobi: Автоматическое создание облачного хранилища настроек"
        )
        logger.info(f"Создан служебный канал данных '{channel.name}' на сервере {guild.name}")
        return channel
    except Exception as e:
        logger.error(f"Не удалось создать служебный канал данных в {guild.name}: {e}")
        return None


async def sync_guild_settings_to_discord(bot: commands.Bot, guild: discord.Guild) -> bool:
    """Сохраняет текущие настройки из SQLite в скрытый канал Discord."""
    try:
        channel = await get_or_create_data_channel(guild)
        if not channel:
            return False

        settings = await get_guild_settings(guild.id)
        welcome_cfg = await get_welcome_settings(guild.id)
        level_cfg = await get_guild_level_settings(guild.id)
        rewards_list = await get_level_rewards(guild.id)
        temp_voice_cfg = await get_temp_voice_settings(guild.id)

        data = {
            "guild_id": guild.id,
            "log_channel_id": settings.get("log_channel_id", 0),
            "autorole_id": settings.get("autorole_id", 0),
            "anti_spam": settings.get("anti_spam", 1),
            "anti_invite": settings.get("anti_invite", 1),
            "anti_caps": settings.get("anti_caps", 1),
            "anti_badwords": settings.get("anti_badwords", 1),
            "anti_mass_mention": settings.get("anti_mass_mention", 1),
            "ignore_admins": settings.get("ignore_admins", 1),
            "anti_nsfw": settings.get("anti_nsfw", 1),
            "anti_toxicity": settings.get("anti_toxicity", 1),
            "welcome": welcome_cfg,
            "levels": level_cfg,
            "level_rewards": rewards_list,
            "temp_voice": temp_voice_cfg,
            "updated_at": datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")
        }

        json_payload = json.dumps(data, indent=2, ensure_ascii=False)
        is_large = len(json_payload) > 1700
        if not is_large:
            message_content = f"{SETTINGS_HEADER}\n```json\n{json_payload}\n```"
        else:
            message_content = f"{SETTINGS_HEADER}\n📁 Конфигурация сохранена во вложенном файле `kobi_settings.json`"

        embed = discord.Embed(
            title="🐾 Kobi — Облачное хранилище настроек",
            description=(
                "Этот канал и закреплённое сообщение используются ботом для сохранения параметров конфигурации "
                "между деплоями, обновлениями кода и перезапусками на Render.\n\n"
                f"**Последнее обновление:** `{data['updated_at']}`"
            ),
            color=discord.Color.blue(),
            timestamp=datetime.utcnow()
        )
        log_ch = guild.get_channel(data["log_channel_id"]) if data["log_channel_id"] else None
        role_obj = guild.get_role(data["autorole_id"]) if data["autorole_id"] else None
        embed.add_field(name="Канал логов", value=log_ch.mention if log_ch else "Не настроен", inline=True)
        embed.add_field(name="Автороль", value=role_obj.mention if role_obj else "Не настроена", inline=True)
        embed.add_field(name="Анти-спам", value="Вкл" if data["anti_spam"] else "Выкл", inline=True)
        embed.add_field(name="Анти-инвайт", value="Вкл" if data["anti_invite"] else "Выкл", inline=True)
        embed.add_field(name="ИИ NSFW", value="Вкл" if data["anti_nsfw"] else "Выкл", inline=True)
        embed.add_field(name="ИИ Оскорбления", value="Вкл" if data["anti_toxicity"] else "Выкл", inline=True)

        target_message = None
        pinned = await channel.pins()
        for p in pinned:
            if p.author.id == bot.user.id and (SETTINGS_HEADER in p.content or LEGACY_HEADER in p.content):
                target_message = p
                break

        if not target_message:
            async for msg in channel.history(limit=50):
                if msg.author.id == bot.user.id and (SETTINGS_HEADER in msg.content or LEGACY_HEADER in msg.content):
                    target_message = msg
                    break

        import io
        if is_large:
            file = discord.File(io.BytesIO(json_payload.encode("utf-8")), filename="kobi_settings.json")
            if target_message:
                await target_message.edit(content=message_content, embed=embed, attachments=[file])
            else:
                new_msg = await channel.send(content=message_content, embed=embed, file=file)
                try:
                    await new_msg.pin(reason="Kobi: Закрепление файла настроек")
                except Exception:
                    pass
        else:
            if target_message:
                await target_message.edit(content=message_content, embed=embed)
            else:
                new_msg = await channel.send(content=message_content, embed=embed)
                try:
                    await new_msg.pin(reason="Kobi: Закрепление файла настроек")
                except Exception:
                    pass

        logger.info(f"Настройки сервера '{guild.name}' успешно синхронизированы в Discord!")
        return True
    except Exception as e:
        logger.error(f"Ошибка при синхронизации настроек в Discord для {guild.name}: {e}")
        return False


async def restore_guild_settings_from_discord(bot: commands.Bot, guild: discord.Guild) -> bool:
    """Считывает настройки из служебного канала Discord и записывает их в SQLite базу данных."""
    try:
        channel = None
        for ch in guild.text_channels:
            if ch.name in DATA_CHANNEL_NAMES:
                channel = ch
                break

        if not channel:
            # Канала ещё нет — синхронизируем текущие локальные настройки в Discord
            await sync_guild_settings_to_discord(bot, guild)
            return False

        found_data = None
        pinned = await channel.pins()
        target_messages = [p for p in pinned if SETTINGS_HEADER in p.content or LEGACY_HEADER in p.content]
        if not target_messages:
            async for msg in channel.history(limit=50):
                if SETTINGS_HEADER in msg.content or LEGACY_HEADER in msg.content:
                    target_messages.append(msg)
                    break

        for msg in target_messages:
            # 1. Если данные прикреплены как файл
            if msg.attachments:
                for att in msg.attachments:
                    if att.filename.endswith(".json"):
                        try:
                            file_bytes = await att.read()
                            found_data = json.loads(file_bytes.decode("utf-8"))
                            break
                        except Exception:
                            pass
                if found_data:
                    break

            # 2. Если данные внутри текста
            content = msg.content
            if "```json" in content:
                try:
                    after_fence = content.split("```json", 1)[1]
                    json_str = after_fence.rsplit("```", 1)[0].strip()
                    found_data = json.loads(json_str)
                    break
                except Exception:
                    pass

        if not found_data:
            # Если в SQLite есть реальные локальные данные, синхронизируем их в облако
            local_set = await get_guild_settings(guild.id)
            if local_set.get("log_channel_id", 0) > 0 or local_set.get("autorole_id", 0) > 0:
                await sync_guild_settings_to_discord(bot, guild)
            return False

        # Записываем восстановленные данные в SQLite базу данных
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                """
                INSERT INTO guild_settings (
                    guild_id, log_channel_id, autorole_id, anti_spam, anti_invite,
                    anti_caps, anti_badwords, anti_mass_mention, ignore_admins, anti_nsfw, anti_toxicity
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(guild_id) DO UPDATE SET
                    log_channel_id = excluded.log_channel_id,
                    autorole_id = excluded.autorole_id,
                    anti_spam = excluded.anti_spam,
                    anti_invite = excluded.anti_invite,
                    anti_caps = excluded.anti_caps,
                    anti_badwords = excluded.anti_badwords,
                    anti_mass_mention = excluded.anti_mass_mention,
                    ignore_admins = excluded.ignore_admins,
                    anti_nsfw = excluded.anti_nsfw,
                    anti_toxicity = excluded.anti_toxicity
                """,
                (
                    guild.id,
                    found_data.get("log_channel_id", 0),
                    found_data.get("autorole_id", 0),
                    found_data.get("anti_spam", 1),
                    found_data.get("anti_invite", 1),
                    found_data.get("anti_caps", 1),
                    found_data.get("anti_badwords", 1),
                    found_data.get("anti_mass_mention", 1),
                    found_data.get("ignore_admins", 1),
                    found_data.get("anti_nsfw", 1),
                    found_data.get("anti_toxicity", 1)
                )
            )
            await db.commit()

        # Восстановление конфигурации новых модулей
        if "welcome" in found_data and isinstance(found_data["welcome"], dict):
            w = found_data["welcome"]
            await set_welcome_settings(
                guild_id=guild.id,
                enabled=bool(w.get("enabled", 0)),
                channel_id=int(w.get("channel_id", 0)),
                message=str(w.get("message", "Добро пожаловать, {mention}! 🎉")),
                dm_message=str(w.get("dm_message", "")),
                send_card=bool(w.get("send_card", 1))
            )

        if "levels" in found_data and isinstance(found_data["levels"], dict):
            lvl = found_data["levels"]
            await set_guild_level_settings(
                guild_id=guild.id,
                enabled=bool(lvl.get("enabled", 1)),
                announce_channel_id=int(lvl.get("announce_channel_id", 0)),
                xp_rate=float(lvl.get("xp_rate", 1.0)),
                voice_xp_enabled=bool(lvl.get("voice_xp_enabled", 1))
            )

        if "level_rewards" in found_data and isinstance(found_data["level_rewards"], list):
            for rew in found_data["level_rewards"]:
                if "level" in rew and "role_id" in rew:
                    await set_level_reward(guild.id, int(rew["level"]), int(rew["role_id"]))

        if "temp_voice" in found_data and isinstance(found_data["temp_voice"], dict):
            tv = found_data["temp_voice"]
            cloud_master = int(tv.get("master_channel_id", 0))
            cloud_enabled = bool(tv.get("enabled", 0))
            cloud_cat = int(tv.get("category_id", 0))

            current_local = await get_temp_voice_settings(guild.id)
            local_master = int(current_local.get("master_channel_id", 0))
            local_enabled = bool(current_local.get("enabled", 0))

            # Восстанавливаем из облака, только если в облаке есть данные или локально система не настроена
            if cloud_master > 0 or not local_enabled:
                await set_temp_voice_settings(
                    guild_id=guild.id,
                    enabled=cloud_enabled,
                    category_id=cloud_cat,
                    master_channel_id=cloud_master
                )

        logger.info(f"✅ Настройки сервера '{guild.name}' успешно восстановлены из облака Discord!")
        return True
    except Exception as e:
        logger.error(f"Ошибка при восстановлении настроек для {guild.name}: {e}")
        return False


async def restore_all_guilds(bot: commands.Bot):
    """Восстанавливает настройки для всех серверов, где присутствует бот."""
    logger.info("Запуск восстановления настроек всех серверов из облака Discord...")
    for guild in bot.guilds:
        try:
            await restore_guild_settings_from_discord(bot, guild)
        except Exception as e:
            logger.error(f"Ошибка восстановления для гильдии {guild.id}: {e}")
