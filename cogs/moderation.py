from datetime import datetime, timedelta
import discord
from discord import app_commands
from discord.ext import commands

from database import (
    get_guild_settings,
    set_log_channel,
    set_feature_toggle
)


class Moderation(commands.Cog):
    """Слэш-команды модерации и управления настройками сервера."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    # ------------------- ТАЙМ-АУТ (МУТ) -------------------

    @app_commands.command(name="timeout", description="Замутить (выдать тайм-аут) участнику")
    @app_commands.describe(member="Участник сервера", minutes="Длительность в минутах (макс. 40320 - 28 дней)", reason="Причина")
    @app_commands.checks.has_permissions(moderate_members=True)
    async def timeout(self, interaction: discord.Interaction, member: discord.Member, minutes: int, reason: str = "Нарушение правил"):
        if minutes <= 0 or minutes > 40320:
            await interaction.response.send_message("❌ Время тайм-аута должно быть от 1 до 40320 минут (28 дней).", ephemeral=True)
            return

        if member.top_role >= interaction.user.top_role and interaction.guild.owner_id != interaction.user.id:
            await interaction.response.send_message("❌ Вы не можете наказать участника с ролью равной или выше вашей.", ephemeral=True)
            return

        duration = timedelta(minutes=minutes)
        try:
            await member.timeout(duration, reason=f"Модератор: {interaction.user} | {reason}")
            embed = discord.Embed(
                title="🔇 Выдан тайм-аут",
                color=discord.Color.red(),
                timestamp=datetime.utcnow()
            )
            embed.add_field(name="Участник", value=f"{member.mention} (`{member.id}`)", inline=True)
            embed.add_field(name="Длительность", value=f"{minutes} минут", inline=True)
            embed.add_field(name="Модератор", value=interaction.user.mention, inline=True)
            embed.add_field(name="Причина", value=reason, inline=False)
            await interaction.response.send_message(embed=embed)
        except discord.Forbidden:
            await interaction.response.send_message("❌ У бота недостаточно прав для выдачи тайм-аута этому пользователю (роль бота должна быть выше роли участника).", ephemeral=True)

    @app_commands.command(name="untimeout", description="Снять тайм-аут (размутить) участника")
    @app_commands.describe(member="Участник сервера", reason="Причина снятия тайм-аута")
    @app_commands.checks.has_permissions(moderate_members=True)
    async def untimeout(self, interaction: discord.Interaction, member: discord.Member, reason: str = "Снятие наказания"):
        try:
            await member.timeout(None, reason=f"Модератор: {interaction.user} | {reason}")
            await interaction.response.send_message(f"🔊 Тайм-аут с {member.mention} успешно снят. Причина: *{reason}*")
        except discord.Forbidden:
            await interaction.response.send_message("❌ У бота недостаточно прав для снятия тайм-аута с этого участника.", ephemeral=True)

    # ------------------- КИК И БАН -------------------

    @app_commands.command(name="kick", description="Выгнать (кикнуть) участника с сервера")
    @app_commands.describe(member="Участник сервера", reason="Причина")
    @app_commands.checks.has_permissions(kick_members=True)
    async def kick(self, interaction: discord.Interaction, member: discord.Member, reason: str = "Нарушение правил"):
        if member.top_role >= interaction.user.top_role and interaction.guild.owner_id != interaction.user.id:
            await interaction.response.send_message("❌ Вы не можете кикнуть участника с ролью равной или выше вашей.", ephemeral=True)
            return

        try:
            await member.kick(reason=f"Модератор: {interaction.user} | {reason}")
            await interaction.response.send_message(f"👢 {member.mention} был исключен с сервера. Причина: *{reason}*")
        except discord.Forbidden:
            await interaction.response.send_message("❌ У бота недостаточно прав для кика этого участника.", ephemeral=True)

    @app_commands.command(name="ban", description="Заблокировать (забанить) участника на сервере")
    @app_commands.describe(member="Участник сервера", reason="Причина", delete_message_days="Удалить сообщения за N дней (0-7)")
    @app_commands.checks.has_permissions(ban_members=True)
    async def ban(self, interaction: discord.Interaction, member: discord.Member, reason: str = "Нарушение правил", delete_message_days: int = 0):
        if member.top_role >= interaction.user.top_role and interaction.guild.owner_id != interaction.user.id:
            await interaction.response.send_message("❌ Вы не можете забанить участника с ролью равной или выше вашей.", ephemeral=True)
            return

        delete_days = max(0, min(7, delete_message_days))
        try:
            await member.ban(reason=f"Модератор: {interaction.user} | {reason}", delete_message_days=delete_days)
            await interaction.response.send_message(f"🔨 {member.mention} был заблокирован на сервере. Причина: *{reason}*")
        except discord.Forbidden:
            await interaction.response.send_message("❌ У бота недостаточно прав для бана этого участника.", ephemeral=True)

    # ------------------- ОЧИСТКА ЧАТА -------------------

    @app_commands.command(name="clear", description="Удалить последние сообщения в текущем канале")
    @app_commands.describe(amount="Количество сообщений для удаления (1-100)")
    @app_commands.checks.has_permissions(manage_messages=True)
    async def clear(self, interaction: discord.Interaction, amount: int):
        if amount < 1 or amount > 100:
            await interaction.response.send_message("❌ Количество сообщений должно быть от 1 до 100.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)
        deleted = await interaction.channel.purge(limit=amount)
        await interaction.followup.send(f"🧹 Удалено сообщений: **{len(deleted)}**", ephemeral=True)

    # ------------------- НАСТРОЙКИ АВТОМОДЕРАЦИИ -------------------

    @app_commands.command(name="setlogs", description="Установить канал для логов автомодерации")
    @app_commands.describe(channel="Текстовый канал для логов")
    @app_commands.checks.has_permissions(administrator=True)
    async def setlogs(self, interaction: discord.Interaction, channel: discord.TextChannel):
        await set_log_channel(interaction.guild.id, channel.id)
        await interaction.response.send_message(f"✅ Канал логов автомодерации установлен на {channel.mention}.")

    @app_commands.command(name="automod_toggle", description="Включить или выключить модуль автомодерации")
    @app_commands.describe(
        feature="Модуль для переключения",
        enabled="Включить (True) или выключить (False)"
    )
    @app_commands.choices(feature=[
        app_commands.Choice(name="Анти-спам (anti_spam)", value="anti_spam"),
        app_commands.Choice(name="Анти-инвайт (anti_invite)", value="anti_invite"),
        app_commands.Choice(name="Анти-капс (anti_caps)", value="anti_caps"),
        app_commands.Choice(name="Анти-массменшн (anti_mass_mention)", value="anti_mass_mention"),
        app_commands.Choice(name="Иммунитет админов (ignore_admins)", value="ignore_admins"),
        app_commands.Choice(name="Фильтр NSFW 18+ (anti_nsfw)", value="anti_nsfw"),
    ])
    @app_commands.checks.has_permissions(administrator=True)
    async def automod_toggle(self, interaction: discord.Interaction, feature: app_commands.Choice[str], enabled: bool):
        await set_feature_toggle(interaction.guild.id, feature.value, enabled)
        status_text = "включен" if enabled else "выключен"
        await interaction.response.send_message(f"⚙️ Модуль **{feature.name}** теперь **{status_text}**.", ephemeral=True)

    @app_commands.command(name="automod_status", description="Показать статус модулей автомодерации")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def automod_status(self, interaction: discord.Interaction):
        settings = await get_guild_settings(interaction.guild.id)
        log_channel = interaction.guild.get_channel(settings.get("log_channel_id", 0))
        log_text = log_channel.mention if log_channel else "Не настроен"

        def icon(val):
            return "🟢 Включено" if val else "🔴 Выключено"

        embed = discord.Embed(
            title="🛡️ Статус автомодерации",
            color=discord.Color.blue(),
            timestamp=datetime.utcnow()
        )
        embed.add_field(name="Канал логов", value=log_text, inline=False)
        embed.add_field(name="Анти-спам / Флуд", value=icon(settings.get("anti_spam", 1)), inline=True)
        embed.add_field(name="Анти-инвайты Discord", value=icon(settings.get("anti_invite", 1)), inline=True)
        embed.add_field(name="Анти-капс", value=icon(settings.get("anti_caps", 1)), inline=True)
        embed.add_field(name="Анти-массменшн", value=icon(settings.get("anti_mass_mention", 1)), inline=True)
        embed.add_field(name="Фильтр NSFW (18+)", value=icon(settings.get("anti_nsfw", 1)), inline=True)
        embed.add_field(name="Иммунитет админов", value=icon(settings.get("ignore_admins", 1)), inline=True)

        await interaction.response.send_message(embed=embed, ephemeral=True)

    # Обработка ошибок прав доступа
    async def cog_app_command_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        if isinstance(error, app_commands.MissingPermissions):
            perms = ", ".join(error.missing_permissions)
            if not interaction.response.is_done():
                await interaction.response.send_message(f"❌ У вас недостаточно прав для выполнения этой команды (требуется: `{perms}`).", ephemeral=True)
            else:
                await interaction.followup.send(f"❌ У вас недостаточно прав для выполнения этой команды (требуется: `{perms}`).", ephemeral=True)
        else:
            if not interaction.response.is_done():
                await interaction.response.send_message(f"⚠️ Произошла ошибка: {error}", ephemeral=True)
            else:
                await interaction.followup.send(f"⚠️ Произошла ошибка: {error}", ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(Moderation(bot))
