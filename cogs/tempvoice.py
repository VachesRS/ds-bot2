import asyncio
import logging
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

from database import (
    get_temp_voice_settings,
    set_temp_voice_settings,
    add_temp_channel,
    remove_temp_channel,
    get_temp_channel,
    get_all_temp_channels
)
from cloud_sync import sync_guild_settings_to_discord

logger = logging.getLogger("TempVoice")


class TempVoiceCog(commands.Cog, name="Временные войс-комнаты"):
    """Автоматическое создание и удаление персональных голосовых комнат («Создай свою комнату»)."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def cog_load(self):
        """Очистка пустых временных каналов при перезапуске бота."""
        try:
            active_channels = await get_all_temp_channels()
            for ch_data in active_channels:
                ch_id = ch_data["channel_id"]
                channel = self.bot.get_channel(ch_id)
                if not channel:
                    await remove_temp_channel(ch_id)
                elif isinstance(channel, discord.VoiceChannel) and len(channel.members) == 0:
                    try:
                        await channel.delete(reason="Kobi: Очистка пустой временной комнаты при запуске")
                    except Exception:
                        pass
                    await remove_temp_channel(ch_id)
        except Exception as e:
            logger.warning(f"Ошибка при очистке временных комнат при запуске: {e}")

    @commands.Cog.listener()
    async def on_voice_state_update(self, member: discord.Member, before: discord.VoiceState, after: discord.VoiceState):
        """Отслеживание входа в мастер-канал и выхода из временных комнат."""
        if member.bot:
            return

        guild = member.guild

        # 1. Проверяем вход в мастер-канал создания комнаты
        if after.channel is not None and (before.channel is None or before.channel.id != after.channel.id):
            try:
                cfg = await get_temp_voice_settings(guild.id)
                master_id = int(cfg.get("master_channel_id") or 0)
                is_enabled = bool(cfg.get("enabled", 0))

                if is_enabled and master_id == after.channel.id:
                    logger.info(
                        f"[TempVoice] Пользователь {member.display_name} ({member.id}) вошел в мастер-канал "
                        f"'{after.channel.name}' ({after.channel.id}) на сервере '{guild.name}'"
                    )
                    await self._create_temp_voice(member, after.channel, cfg)
                elif not is_enabled and master_id == after.channel.id:
                    logger.warning(
                        f"[TempVoice] Пользователь {member.display_name} вошел в мастер-канал, "
                        f"но система временных комнат выключена (enabled=0) на сервере '{guild.name}'"
                    )
                elif "создать" in after.channel.name.lower() or "войс" in after.channel.name.lower() or "комнат" in after.channel.name.lower():
                    if master_id != after.channel.id:
                        logger.debug(
                            f"[TempVoice] Пользователь зашел в потенциальный мастер-канал '{after.channel.name}' "
                            f"(ID: {after.channel.id}), однако настроен master_id={master_id}"
                        )
            except Exception as e:
                logger.error(f"[TempVoice] Ошибка при обработке входа в канал: {e}", exc_info=True)

        # 2. Проверяем выход из временной комнаты
        if before.channel is not None and (after.channel is None or after.channel.id != before.channel.id):
            try:
                temp_info = await get_temp_channel(before.channel.id)
                if temp_info:
                    # Если в комнате больше никого не осталось — удаляем
                    if len(before.channel.members) == 0:
                        try:
                            await before.channel.delete(reason="Kobi: Все участники покинули временную комнату")
                            logger.info(f"[TempVoice] Пустая временная комната '{before.channel.name}' ({before.channel.id}) удалена")
                        except Exception as e:
                            logger.warning(f"Не удалось удалить временный канал {before.channel.name}: {e}")
                        await remove_temp_channel(before.channel.id)
            except Exception as e:
                logger.error(f"[TempVoice] Ошибка при обработке выхода из канала: {e}", exc_info=True)

    async def _create_temp_voice(self, member: discord.Member, master_channel: discord.VoiceChannel, cfg: dict):
        """Создает персональный голосовой канал и перемещает туда участника."""
        guild = member.guild

        # 1. Получаем объект бота
        bot_member = guild.me or guild.get_member(self.bot.user.id)
        if not bot_member:
            try:
                bot_member = await guild.fetch_member(self.bot.user.id)
            except Exception:
                bot_member = None

        # 2. Проверяем необходимые права бота
        has_manage = False
        has_move = False
        if bot_member:
            if bot_member.guild_permissions.administrator:
                has_manage = True
                has_move = True
            else:
                has_manage = bot_member.guild_permissions.manage_channels or master_channel.permissions_for(bot_member).manage_channels
                has_move = bot_member.guild_permissions.move_members or master_channel.permissions_for(bot_member).move_members

        if not has_manage or not has_move:
            logger.warning(
                f"[TempVoice] У бота недостаточно прав на сервере '{guild.name}' ({guild.id})! "
                f"Manage Channels: {has_manage}, Move Members: {has_move}"
            )
            try:
                await member.send(
                    f"⚠️ **Ошибка временных комнат на сервере {guild.name}**:\n"
                    f"У бота Kobi нет необходимых прав (`Управление каналами` и/или `Перемещение участников`).\n"
                    f"Пожалуйста, сообщите администраторам сервера, чтобы они выдали боту эти права."
                )
            except Exception:
                pass
            return

        # 3. Определяем категорию
        category = master_channel.category
        if cfg.get("category_id"):
            custom_cat = guild.get_channel(int(cfg["category_id"]))
            if isinstance(custom_cat, discord.CategoryChannel):
                category = custom_cat

        # 4. Формируем права для новой комнаты
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(connect=True, speak=True),
            member: discord.PermissionOverwrite(
                manage_channels=True,
                move_members=True,
                mute_members=True,
                deafen_members=True,
                connect=True,
                speak=True,
                view_channel=True
            )
        }
        if bot_member:
            overwrites[bot_member] = discord.PermissionOverwrite(
                manage_channels=True,
                move_members=True,
                connect=True,
                speak=True,
                view_channel=True
            )

        channel_name = f"🔊 Комната {member.display_name}"[:95]

        # 5. Создаем канал и перемещаем пользователя
        try:
            new_voice = await guild.create_voice_channel(
                name=channel_name,
                category=category,
                overwrites=overwrites,
                reason=f"Kobi: Создание временной комнаты для {member.display_name} ({member.id})"
            )
            logger.info(f"[TempVoice] Создан канал '{new_voice.name}' ({new_voice.id}) для {member.display_name}")

            # Регистрируем в БД СРАЗУ
            await add_temp_channel(new_voice.id, guild.id, member.id)

            # Проверяем, находится ли пользователь всё ещё в голосовом канале
            if member.voice and member.voice.channel:
                try:
                    await member.move_to(new_voice, reason="Kobi: Перемещение в персональную комнату")
                except discord.HTTPException as move_err:
                    logger.warning(f"[TempVoice] Не удалось переместить {member.display_name} в {new_voice.name}: {move_err}")
            else:
                logger.info(f"[TempVoice] Пользователь {member.display_name} отключился до перемещения в {new_voice.name}")
                if len(new_voice.members) == 0:
                    try:
                        await new_voice.delete(reason="Kobi: Пользователь отключился до перемещения")
                    except Exception:
                        pass
                    await remove_temp_channel(new_voice.id)

        except discord.Forbidden as f_err:
            logger.error(f"[TempVoice] 403 Forbidden при создании канала на сервере {guild.name}: {f_err}")
        except Exception as e:
            logger.error(f"[TempVoice] Ошибка при создании временной комнаты: {e}", exc_info=True)

    # ---------------------------------------------------------------------------
    # Команды настройки для Администратора (/tempvoice)
    # ---------------------------------------------------------------------------
    tempvoice_admin = app_commands.Group(name="tempvoice", description="Настройка системы временных голосовых каналов")

    @tempvoice_admin.command(name="setup", description="Автоматически создать категорию и мастер-канал «➕ Создать комнату».")
    @app_commands.describe(
        category="Категория, где будут создаваться временные комнаты (необязательно)"
    )
    @commands.has_permissions(administrator=True)
    async def slash_tempvoice_setup(
        self,
        interaction: discord.Interaction,
        category: Optional[discord.CategoryChannel] = None
    ):
        """Создает и настраивает мастер-канал временных комнат."""
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("❌ У вас нет прав Администратора.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)
        guild = interaction.guild

        bot_member = guild.me or guild.get_member(self.bot.user.id)
        if not bot_member or not bot_member.guild_permissions.manage_channels:
            await interaction.followup.send("❌ У бота нет права `Управление каналами` (Manage Channels).", ephemeral=True)
            return

        target_cat = category
        if not target_cat:
            target_cat = await guild.create_category("🔊 ГОЛОСОВЫЕ КОМНАТЫ")

        master_ch = await guild.create_voice_channel(
            name="➕ Создать комнату",
            category=target_cat,
            reason="Kobi: Мастер-канал временных войсов"
        )

        await set_temp_voice_settings(
            guild_id=guild.id,
            enabled=True,
            category_id=target_cat.id,
            master_channel_id=master_ch.id
        )

        # Синхронизируем настройки в облачный канал Discord
        asyncio.create_task(sync_guild_settings_to_discord(self.bot, guild))

        await interaction.followup.send(
            f"✅ **Система временных войсов успешно настроена!**\n\n"
            f"• **Категория:** {target_cat.name}\n"
            f"• **Мастер-канал:** {master_ch.mention} (`ID: {master_ch.id}`)\n\n"
            f"Теперь любой участник при входе в {master_ch.mention} автоматически получит собственную комнату и будет перемещен туда!",
            ephemeral=True
        )

    @tempvoice_admin.command(name="channel", description="Назначить существующий голосовой канал мастер-каналом для создания комнат.")
    @app_commands.describe(
        channel="Голосовой канал, при входе в который создается комната",
        category="Категория для создания комнат (по умолчанию — категория канала)"
    )
    @commands.has_permissions(administrator=True)
    async def slash_tempvoice_channel(
        self,
        interaction: discord.Interaction,
        channel: discord.VoiceChannel,
        category: Optional[discord.CategoryChannel] = None
    ):
        """Привязывает существующий голосовой канал."""
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("❌ У вас нет прав Администратора.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)
        guild = interaction.guild

        cat_id = category.id if category else (channel.category.id if channel.category else 0)

        await set_temp_voice_settings(
            guild_id=guild.id,
            enabled=True,
            category_id=cat_id,
            master_channel_id=channel.id
        )

        # Синхронизируем настройки в облачный канал Discord
        asyncio.create_task(sync_guild_settings_to_discord(self.bot, guild))

        cat_text = category.name if category else (channel.category.name if channel.category else "Без категории")
        await interaction.followup.send(
            f"✅ **Мастер-канал успешно привязан!**\n\n"
            f"• **Канал:** {channel.mention} (`ID: {channel.id}`)\n"
            f"• **Категория для комнат:** {cat_text}\n"
            f"• **Статус:** Включено",
            ephemeral=True
        )

    @tempvoice_admin.command(name="status", description="Показать статус и диагностику системы временных комнат.")
    @commands.has_permissions(administrator=True)
    async def slash_tempvoice_status(self, interaction: discord.Interaction):
        """Диагностика временных комнат."""
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("❌ У вас нет прав Администратора.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)
        guild = interaction.guild

        cfg = await get_temp_voice_settings(guild.id)
        enabled = bool(cfg.get("enabled", 0))
        master_id = int(cfg.get("master_channel_id", 0))
        cat_id = int(cfg.get("category_id", 0))

        master_ch = guild.get_channel(master_id) if master_id else None
        cat_ch = guild.get_channel(cat_id) if cat_id else None

        bot_member = guild.me or guild.get_member(self.bot.user.id)
        if not bot_member:
            try:
                bot_member = await guild.fetch_member(self.bot.user.id)
            except Exception:
                bot_member = None

        is_admin = bot_member.guild_permissions.administrator if bot_member else False
        has_manage = is_admin or (bot_member.guild_permissions.manage_channels if bot_member else False)
        has_move = is_admin or (bot_member.guild_permissions.move_members if bot_member else False)

        active_channels = await get_all_temp_channels()
        guild_active_count = sum(1 for ch in active_channels if ch.get("guild_id") == guild.id)

        is_healthy = enabled and master_ch and has_manage and has_move
        status_emoji = "🟢" if is_healthy else "🔴"
        status_text = "Работает штатно" if is_healthy else "Требует внимания"

        embed = discord.Embed(
            title=f"{status_emoji} Статус временных комнат: {status_text}",
            color=discord.Color.green() if is_healthy else discord.Color.red(),
            timestamp=interaction.created_at
        )

        embed.add_field(
            name="Состояние",
            value="✅ **Включена**" if enabled else "❌ **Выключена** (`/tempvoice toggle enabled:True`)",
            inline=True
        )

        if master_ch:
            master_val = f"🔊 {master_ch.mention} (`{master_ch.id}`)"
        elif master_id:
            master_val = f"⚠️ ID `{master_id}` (канал удален в Discord!)"
        else:
            master_val = "❌ Не настроен (`/tempvoice setup`)"
        embed.add_field(name="Мастер-канал", value=master_val, inline=True)

        cat_val = f"📁 {cat_ch.name}" if cat_ch else "⚙️ В категории мастер-канала"
        embed.add_field(name="Категория комнат", value=cat_val, inline=True)

        perms_val = (
            f"• Администратор: {'✅' if is_admin else '⚠️ Нет'}\n"
            f"• Управление каналами: {'✅' if has_manage else '❌ **НЕТ**'}\n"
            f"• Перемещение участников: {'✅' if has_move else '❌ **НЕТ**'}"
        )
        embed.add_field(name="Права бота", value=perms_val, inline=False)
        embed.add_field(name="Активных комнат сейчас", value=f"👥 **{guild_active_count}**", inline=True)

        tips = []
        if not enabled:
            tips.append("• Включите систему: `/tempvoice toggle enabled:True`")
        if not master_ch:
            tips.append("• Привяжите существующий канал: `/tempvoice channel #канал` или выполните `/tempvoice setup`")
        if not has_manage or not has_move:
            tips.append("• Выдайте боту права **«Управление каналами»** и **«Перемещение участников»** в настройках сервера Discord")

        if tips:
            embed.add_field(name="🛠️ Рекомендации по исправлению", value="\n".join(tips), inline=False)

        await interaction.followup.send(embed=embed, ephemeral=True)

    @tempvoice_admin.command(name="toggle", description="Включить или отключить систему временных комнат.")
    @app_commands.describe(enabled="Включить (True) или выключить (False)")
    @commands.has_permissions(administrator=True)
    async def slash_tempvoice_toggle(self, interaction: discord.Interaction, enabled: bool):
        """Включение/выключение временных комнат."""
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("❌ У вас нет прав Администратора.", ephemeral=True)
            return

        cfg = await get_temp_voice_settings(interaction.guild.id)
        await set_temp_voice_settings(
            guild_id=interaction.guild.id,
            enabled=enabled,
            category_id=cfg.get("category_id", 0),
            master_channel_id=cfg.get("master_channel_id", 0)
        )

        asyncio.create_task(sync_guild_settings_to_discord(self.bot, interaction.guild))

        state_str = "включена ✅" if enabled else "отключена ⏸️"
        await interaction.response.send_message(f"Система временных комнат успешно {state_str}.", ephemeral=True)

    @tempvoice_admin.command(name="category", description="Указать категорию для создания временных комнат.")
    @app_commands.describe(category="Категория, где будут создаваться комнаты")
    @commands.has_permissions(administrator=True)
    async def slash_tempvoice_category(self, interaction: discord.Interaction, category: discord.CategoryChannel):
        """Настройка категории комнат."""
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("❌ У вас нет прав Администратора.", ephemeral=True)
            return

        cfg = await get_temp_voice_settings(interaction.guild.id)
        await set_temp_voice_settings(
            guild_id=interaction.guild.id,
            enabled=cfg.get("enabled", 1),
            category_id=category.id,
            master_channel_id=cfg.get("master_channel_id", 0)
        )

        asyncio.create_task(sync_guild_settings_to_discord(self.bot, interaction.guild))

        await interaction.response.send_message(f"📁 Категория временных комнат установлена: **{category.name}**", ephemeral=True)

    # ---------------------------------------------------------------------------
    # Команды управления комнатой для участников (/voice)
    # ---------------------------------------------------------------------------
    voice_user = app_commands.Group(name="voice", description="Управление вашей текущей временной комнатой")

    @voice_user.command(name="lock", description="Закрыть доступ в вашу комнату для остальных участников.")
    async def slash_voice_lock(self, interaction: discord.Interaction):
        """Закрывает комнату."""
        member = interaction.user
        if not member.voice or not member.voice.channel:
            await interaction.response.send_message("❌ Вы должны находиться в своей голосовой комнате.", ephemeral=True)
            return

        channel = member.voice.channel
        temp_data = await get_temp_channel(channel.id)
        if not temp_data or temp_data["owner_id"] != member.id:
            await interaction.response.send_message("❌ Вы не являетесь владельцем этой временной комнаты.", ephemeral=True)
            return

        await channel.set_permissions(interaction.guild.default_role, connect=False)
        await interaction.response.send_message("🔒 Комната успешно закрыта! Новые участники не смогут зайти.", ephemeral=True)

    @voice_user.command(name="unlock", description="Открыть доступ в вашу комнату для всех.")
    async def slash_voice_unlock(self, interaction: discord.Interaction):
        """Открывает комнату."""
        member = interaction.user
        if not member.voice or not member.voice.channel:
            await interaction.response.send_message("❌ Вы должны находиться в своей голосовой комнате.", ephemeral=True)
            return

        channel = member.voice.channel
        temp_data = await get_temp_channel(channel.id)
        if not temp_data or temp_data["owner_id"] != member.id:
            await interaction.response.send_message("❌ Вы не являетесь владельцем этой временной комнаты.", ephemeral=True)
            return

        await channel.set_permissions(interaction.guild.default_role, connect=True)
        await interaction.response.send_message("🔓 Комната открыта для всех участников сервера.", ephemeral=True)

    @voice_user.command(name="limit", description="Установить лимит участников в комнате (0 - без лимита).")
    @app_commands.describe(slots="Количество мест (от 0 до 99)")
    async def slash_voice_limit(self, interaction: discord.Interaction, slots: int):
        """Устанавливает лимит мест."""
        member = interaction.user
        if not member.voice or not member.voice.channel:
            await interaction.response.send_message("❌ Вы должны находиться в своей голосовой комнате.", ephemeral=True)
            return

        channel = member.voice.channel
        temp_data = await get_temp_channel(channel.id)
        if not temp_data or temp_data["owner_id"] != member.id:
            await interaction.response.send_message("❌ Вы не являетесь владельцем этой временной комнаты.", ephemeral=True)
            return

        if not 0 <= slots <= 99:
            await interaction.response.send_message("❌ Лимит должен быть от 0 до 99.", ephemeral=True)
            return

        await channel.edit(user_limit=slots)
        limit_text = f"**{slots} участников**" if slots > 0 else "**без ограничений**"
        await interaction.response.send_message(f"👥 Лимит участников изменен: {limit_text}.", ephemeral=True)

    @voice_user.command(name="rename", description="Переименовать вашу временную комнату.")
    @app_commands.describe(name="Новое название комнаты")
    async def slash_voice_rename(self, interaction: discord.Interaction, name: str):
        """Переименовывает комнату."""
        member = interaction.user
        if not member.voice or not member.voice.channel:
            await interaction.response.send_message("❌ Вы должны находиться в своей голосовой комнате.", ephemeral=True)
            return

        channel = member.voice.channel
        temp_data = await get_temp_channel(channel.id)
        if not temp_data or temp_data["owner_id"] != member.id:
            await interaction.response.send_message("❌ Вы не являетесь владельцем этой временной комнаты.", ephemeral=True)
            return

        cleaned_name = name.strip()[:90]
        await channel.edit(name=cleaned_name)
        await interaction.response.send_message(f"✏️ Комната переименована в: **{cleaned_name}**", ephemeral=True)

    @voice_user.command(name="kick", description="Выгнать участника из вашей временной комнаты.")
    @app_commands.describe(member="Участник, которого нужно выгнать")
    async def slash_voice_kick(self, interaction: discord.Interaction, member: discord.Member):
        """Выгоняет участника из комнаты."""
        author = interaction.user
        if not author.voice or not author.voice.channel:
            await interaction.response.send_message("❌ Вы должны находиться в своей голосовой комнате.", ephemeral=True)
            return

        channel = author.voice.channel
        temp_data = await get_temp_channel(channel.id)
        if not temp_data or temp_data["owner_id"] != author.id:
            await interaction.response.send_message("❌ Вы не являетесь владельцем этой временной комнаты.", ephemeral=True)
            return

        if member.id == author.id:
            await interaction.response.send_message("❌ Вы не можете выгнать самого себя.", ephemeral=True)
            return

        if member.voice and member.voice.channel and member.voice.channel.id == channel.id:
            try:
                await member.move_to(None, reason="Kobi: Исключен владельцем временной комнаты")
                await channel.set_permissions(member, connect=False)
                await interaction.response.send_message(f"👢 {member.mention} исключен из комнаты и заблокирован.", ephemeral=True)
            except discord.Forbidden:
                await interaction.response.send_message("❌ У бота нет прав для отключения участников.", ephemeral=True)
        else:
            await channel.set_permissions(member, connect=False)
            await interaction.response.send_message(f"🚫 {member.mention} заблокирован для входа в вашу комнату.", ephemeral=True)

    @voice_user.command(name="permit", description="Разрешить участнику доступ в вашу закрытую комнату.")
    @app_commands.describe(member="Участник, которому открыть доступ")
    async def slash_voice_permit(self, interaction: discord.Interaction, member: discord.Member):
        """Дает доступ участнику в закрытую комнату."""
        author = interaction.user
        if not author.voice or not author.voice.channel:
            await interaction.response.send_message("❌ Вы должны находиться в своей голосовой комнате.", ephemeral=True)
            return

        channel = author.voice.channel
        temp_data = await get_temp_channel(channel.id)
        if not temp_data or temp_data["owner_id"] != author.id:
            await interaction.response.send_message("❌ Вы не являетесь владельцем этой временной комнаты.", ephemeral=True)
            return

        await channel.set_permissions(member, connect=True, view_channel=True)
        await interaction.response.send_message(f"✅ Доступ в комнату предоставлен для {member.mention}!", ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(TempVoiceCog(bot))
