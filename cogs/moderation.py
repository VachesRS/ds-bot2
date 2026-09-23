from datetime import datetime, timedelta
import discord
from discord import app_commands
from discord.ext import commands

from database import (
    get_guild_settings,
    set_log_channel,
    set_feature_toggle
)


# ------------------- ИНТЕРАКТИВНОЕ МЕНЮ СПРАВКИ (HELP) -------------------

class HelpSelect(discord.ui.Select):
    def __init__(self, bot: commands.Bot, author_id: int):
        options = [
            discord.SelectOption(
                label="Главная страница",
                description="Общая сводка и возможности бота",
                emoji="🏠",
                value="home",
                default=True
            ),
            discord.SelectOption(
                label="Команды модерации",
                description="/timeout, /untimeout, /kick, /ban, /clear",
                emoji="🛡️",
                value="moderation"
            ),
            discord.SelectOption(
                label="Настройки и аудит",
                description="/setlogs, /automod_toggle, /automod_status",
                emoji="⚙️",
                value="settings"
            ),
            discord.SelectOption(
                label="Система автозащиты (ИИ)",
                description="Нейросеть NSFW, анти-спам, инвайты, капс",
                emoji="🤖",
                value="automod"
            ),
        ]
        super().__init__(
            placeholder="🔍 Выберите категорию для подробностей...",
            min_values=1,
            max_values=1,
            options=options
        )
        self.bot = bot
        self.author_id = author_id

    def get_embed(self, category: str, guild: discord.Guild | None) -> discord.Embed:
        avatar_url = self.bot.user.display_avatar.url if self.bot.user else None

        if category == "home":
            embed = discord.Embed(
                title="🛡️ Панель помощи — ShieldGuard",
                description=(
                    "**ShieldGuard** — автономный бот безопасности нового поколения. "
                    "Оснащен локальной нейросетью для выявления NSFW/18+ медиа (включая GIF Tenor/Klipy), "
                    "защитой от рейдов, флуда и полным арсеналом инструментов модератора.\n\n"
                    "👉 **Выберите интересующий раздел в выпадающем меню ниже**, чтобы изучить команды."
                ),
                color=discord.Color.blurple(),
                timestamp=datetime.utcnow()
            )
            embed.add_field(
                name="📌 Краткая сводка",
                value=(
                    f"• **Пинг бота:** `{round(self.bot.latency * 1000)} мс`\n"
                    f"• **Серверов под защитой:** `{len(self.bot.guilds)}`\n"
                    f"• **Формат команд:** Слэш-команды (`/`)"
                ),
                inline=False
            )
            embed.add_field(
                name="📂 Доступные категории:",
                value=(
                    "• 🛡️ **Команды модерации** — ручные наказания (тайм-аут, бан, кик, очистка)\n"
                    "• ⚙️ **Настройки и аудит** — привязка канала логов и переключатели модулей\n"
                    "• 🤖 **Система автозащиты** — как работает локальная нейросеть и фильтры"
                ),
                inline=False
            )
            if avatar_url:
                embed.set_thumbnail(url=avatar_url)
            embed.set_footer(text="ShieldGuard AutoMod • Защита сервера 24/7", icon_url=avatar_url)
            return embed

        elif category == "moderation":
            embed = discord.Embed(
                title="🛡️ Команды ручной модерации",
                description="Команды для оперативного контроля порядка на сервере (требуют прав модератора):",
                color=discord.Color.red(),
                timestamp=datetime.utcnow()
            )
            embed.add_field(
                name="🔇 `/timeout <участник> <минуты> [причина]`",
                value="Выдать тайм-аут (мут) участнику на время от 1 до 40320 минут (28 дней).",
                inline=False
            )
            embed.add_field(
                name="🔊 `/untimeout <участник> [причина]`",
                value="Досрочно снять активный тайм-аут с участника.",
                inline=False
            )
            embed.add_field(
                name="👢 `/kick <участник> [причина]`",
                value="Выгнать участника с сервера (он сможет вернуться по приглашению).",
                inline=False
            )
            embed.add_field(
                name="🔨 `/ban <участник> [причина] [удалить_сообщения]`",
                value="Заблокировать навсегда с возможностью стереть его сообщения за последние 0–7 дней.",
                inline=False
            )
            embed.add_field(
                name="🧹 `/clear <количество>`",
                value="Быстро удалить от 1 до 100 последних сообщений в текущем канале.",
                inline=False
            )
            embed.set_footer(text="Требуются права: Moderate Members / Kick Members / Ban Members / Manage Messages")
            return embed

        elif category == "settings":
            embed = discord.Embed(
                title="⚙️ Настройки и панель управления",
                description="Команды администратора для конфигурации защиты и канала аудита:",
                color=discord.Color.gold(),
                timestamp=datetime.utcnow()
            )
            embed.add_field(
                name="📝 `/setlogs <канал>`",
                value="Установить текстовый канал, куда бот будет присылать отчёты о нарушениях (удаление NSFW, спам и др.).",
                inline=False
            )
            embed.add_field(
                name="🎛️ `/automod_toggle <модуль> <включить/выключить>`",
                value="Включить (`True`) или выключить (`False`) конкретный модуль:\n`anti_toxicity`, `anti_nsfw`, `anti_spam`, `anti_invite`, `anti_caps`, `anti_mass_mention`, `ignore_admins`.",
                inline=False
            )
            embed.add_field(
                name="📊 `/automod_status`",
                value="Показать статус работы всех систем автомодерации и привязанный канал логов.",
                inline=False
            )
            embed.set_footer(text="Требуются права: Administrator / Manage Server")
            return embed

        elif category == "automod":
            embed = discord.Embed(
                title="🤖 Автономная система защиты (ИИ и фильтры)",
                description="Модули безопасности работают непрерывно 24/7 без участия модераторов:",
                color=discord.Color.green(),
                timestamp=datetime.utcnow()
            )
            embed.add_field(
                name="🤬 Нейросеть оскорблений (RuBERT-Tiny Toxicity)",
                value=(
                    "Локальная языковая модель анализирует смысл сообщений на русском языке. "
                    "Распознает прямые и завуалированные оскорбления в контексте без ложных срабатываний. "
                    "При нарушении — удаление сообщения и тайм-аут на 5 минут."
                ),
                inline=False
            )
            embed.add_field(
                name="🔞 Нейросеть NSFW (OpenNSFW ResNet-50)",
                value=(
                    "Локальная нейросеть анализирует картинки и GIF-анимации (включая ссылки Tenor, Klipy, Giphy) "
                    "покадрово без сторонних API. При обнаружении запрещенного контента — "
                    "сообщение удаляется, а автор получает тайм-аут на 30 минут."
                ),
                inline=False
            )
            embed.add_field(
                name="⚡ Защита от флуда и спама",
                value="Блокирует отправку более 5 сообщений за 4 секунды (автомут на 5 минут).",
                inline=False
            )
            embed.add_field(
                name="📢 Защита от масс-меншнов",
                value="Удаляет сообщения с более чем 4 упоминаниями пользователей/ролей (автомут на 15 минут).",
                inline=False
            )
            embed.add_field(
                name="🔗 Анти-инвайты Discord",
                value="Мгновенно пресекает рекламу чужих серверов и удаляет ссылки-приглашения.",
                inline=False
            )
            embed.add_field(
                name="🔠 Анти-капс",
                value="Удаляет сообщения, содержащие более 70% заглавных букв (при длине от 8 символов).",
                inline=False
            )
            embed.set_footer(text="Все инциденты фиксируются в настроенном канале логов")
            return embed

        return discord.Embed(title="Информация", description="Категория не найдена.")

    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.author_id:
            await interaction.response.send_message("❌ Это меню открыто другим пользователем. Введите `/help`, чтобы открыть своё.", ephemeral=True)
            return

        chosen = self.values[0]
        for opt in self.options:
            opt.default = (opt.value == chosen)

        embed = self.get_embed(chosen, interaction.guild)
        await interaction.response.edit_message(embed=embed, view=self.view)


class HelpView(discord.ui.View):
    def __init__(self, bot: commands.Bot, author_id: int):
        super().__init__(timeout=180)
        self.bot = bot
        self.author_id = author_id
        self.message: discord.Message | None = None
        self.help_select = HelpSelect(bot, author_id)
        self.add_item(self.help_select)

    @discord.ui.button(label="Статус защиты", style=discord.ButtonStyle.secondary, emoji="📊", row=1)
    async def status_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        settings = await get_guild_settings(interaction.guild.id)
        log_channel = interaction.guild.get_channel(settings.get("log_channel_id", 0))
        log_text = log_channel.mention if log_channel else "Не настроен"

        def icon(val):
            return "🟢 Вкл" if val else "🔴 Выкл"

        embed = discord.Embed(
            title="🛡️ Экспресс-статус защиты на сервере",
            color=discord.Color.blue(),
            timestamp=datetime.utcnow()
        )
        embed.add_field(name="Канал логов", value=log_text, inline=False)
        embed.add_field(name="Анти-спам", value=icon(settings.get("anti_spam", 1)), inline=True)
        embed.add_field(name="Анти-инвайты", value=icon(settings.get("anti_invite", 1)), inline=True)
        embed.add_field(name="Анти-капс", value=icon(settings.get("anti_caps", 1)), inline=True)
        embed.add_field(name="Анти-массменшн", value=icon(settings.get("anti_mass_mention", 1)), inline=True)
        embed.add_field(name="Нейросеть NSFW", value=icon(settings.get("anti_nsfw", 1)), inline=True)
        embed.add_field(name="ИИ оскорбления", value=icon(settings.get("anti_toxicity", 1)), inline=True)
        embed.add_field(name="Иммунитет админов", value=icon(settings.get("ignore_admins", 1)), inline=True)

        await interaction.response.send_message(embed=embed, ephemeral=True)

    @discord.ui.button(label="Закрыть", style=discord.ButtonStyle.danger, emoji="🗑️", row=1)
    async def close_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.author_id:
            await interaction.response.send_message("❌ Только автор команды может закрыть это меню.", ephemeral=True)
            return
        if interaction.message:
            await interaction.message.delete()

    async def on_timeout(self):
        for item in self.children:
            item.disabled = True
        try:
            if self.message:
                await self.message.edit(view=self)
        except Exception:
            pass


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
        app_commands.Choice(name="ИИ-фильтр оскорблений (anti_toxicity)", value="anti_toxicity"),
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
        embed.add_field(name="ИИ-фильтр оскорблений", value=icon(settings.get("anti_toxicity", 1)), inline=True)
        embed.add_field(name="Иммунитет админов", value=icon(settings.get("ignore_admins", 1)), inline=True)

        await interaction.response.send_message(embed=embed, ephemeral=True)

    # ------------------- КОМАНДА ПОМОЩИ (HELP) -------------------

    @app_commands.command(name="help", description="Интерактивное меню со всеми командами и возможностями бота")
    async def help_slash(self, interaction: discord.Interaction):
        view = HelpView(self.bot, interaction.user.id)
        embed = view.help_select.get_embed("home", interaction.guild)
        await interaction.response.send_message(embed=embed, view=view)
        try:
            view.message = await interaction.original_response()
        except Exception:
            pass

    @commands.command(name="help")
    async def help_prefix(self, ctx: commands.Context):
        view = HelpView(self.bot, ctx.author.id)
        embed = view.help_select.get_embed("home", ctx.guild)
        msg = await ctx.send(embed=embed, view=view)
        view.message = msg

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
