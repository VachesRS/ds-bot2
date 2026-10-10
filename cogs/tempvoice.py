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
                        await channel.delete(reason="ShieldGuard: Очистка пустой временной комнаты при запуске")
                    except Exception:
                        pass
                    await remove_temp_channel(ch_id)
        except Exception as e:
            logger.warning(f"Ошибка при очистке временных комнат при запуске: {e}")

    @commands.Cog.listener()
    async def on_voice_state_update(self, member: discord.Member, before: discord.VoiceState, after: discord.VoiceState):
        """Отслеживание входа в мастер-канал и выхода из временных комнат."""
        guild = member.guild

        # 1. Проверяем вход в мастер-канал создания комнаты
        if after.channel is not None and (before.channel is None or before.channel.id != after.channel.id):
            cfg = await get_temp_voice_settings(guild.id)
            if cfg.get("enabled", 0) and cfg.get("master_channel_id", 0) == after.channel.id:
                # Пользователь зашел в канал "Создать комнату"
                await self._create_temp_voice(member, after.channel, cfg)

        # 2. Проверяем выход из временной комнаты
        if before.channel is not None and (after.channel is None or after.channel.id != before.channel.id):
            temp_info = await get_temp_channel(before.channel.id)
            if temp_info:
                # Если в комнате больше никого не осталось — удаляем
                if len(before.channel.members) == 0:
                    try:
                        await before.channel.delete(reason="ShieldGuard: Все участники покинули временную комнату")
                    except Exception as e:
                        logger.warning(f"Не удалось удалить временный канал {before.channel.name}: {e}")
                    await remove_temp_channel(before.channel.id)

    async def _create_temp_voice(self, member: discord.Member, master_channel: discord.VoiceChannel, cfg: dict):
        """Создает персональный голосовой канал и перемещает туда участника."""
        guild = member.guild

        # Проверяем права бота
        if not guild.me.guild_permissions.manage_channels or not guild.me.guild_permissions.move_members:
            logger.warning(f"У бота недостаточно прав для временных войсов на сервере {guild.name}")
            return

        category = master_channel.category
        if cfg.get("category_id"):
            custom_cat = guild.get_channel(cfg["category_id"])
            if isinstance(custom_cat, discord.CategoryChannel):
                category = custom_cat

        overwrites = {
            guild.default_role: discord.PermissionOverwrite(connect=True, speak=True),
            guild.me: discord.PermissionOverwrite(manage_channels=True, move_members=True),
            member: discord.PermissionOverwrite(
                manage_channels=True,
                move_members=True,
                mute_members=True,
                deafen_members=True
            )
        }

        channel_name = f"🔊 Комната {member.display_name}"
        try:
            new_voice = await guild.create_voice_channel(
                name=channel_name,
                category=category,
                overwrites=overwrites,
                reason=f"ShieldGuard: Создание временной комнаты для {member.name}"
            )
            # Перемещаем пользователя в созданную комнату
            await member.move_to(new_voice, reason="ShieldGuard: Перемещение в персональную комнату")
            # Записываем в базу данных
            await add_temp_channel(new_voice.id, guild.id, member.id)

        except Exception as e:
            logger.error(f"Ошибка при создании временной комнаты: {e}")

    # Команды настройки для Администратора
    tempvoice_admin = app_commands.Group(name="tempvoice", description="Настройка системы временных голосовых каналов")

    @tempvoice_admin.command(name="setup", description="Автоматически настроить мастер-канал «➕ Создать комнату».")
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

        if not guild.me.guild_permissions.manage_channels:
            await interaction.followup.send("❌ У бота нет права `Управление каналами` (Manage Channels).", ephemeral=True)
            return

        # Создаем мастер-канал
        target_cat = category
        if not target_cat:
            target_cat = await guild.create_category("🔊 ГОЛОСОВЫЕ КОМНАТЫ")

        master_ch = await guild.create_voice_channel(
            name="➕ Создать комнату",
            category=target_cat,
            reason="ShieldGuard: Мастер-канал временных войсов"
        )

        await set_temp_voice_settings(
            guild_id=guild.id,
            enabled=True,
            category_id=target_cat.id,
            master_channel_id=master_ch.id
        )

        await interaction.followup.send(
            f"✅ Система временных войсов успешно настроена!\n"
            f"• Категория: **{target_cat.name}**\n"
            f"• Мастер-канал: {master_ch.mention}\n\n"
            f"Теперь при заходе в канал {master_ch.mention} для пользователя будет автоматически создаваться его личная комната!",
            ephemeral=True
        )

    # Команды управления комнатой для участников
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


async def setup(bot: commands.Bot):
    await bot.add_cog(TempVoiceCog(bot))
