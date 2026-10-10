import io
import time
import random
import logging
from typing import Optional

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands, tasks
from PIL import Image, ImageDraw, ImageFont, ImageFilter

from database import (
    get_user_level,
    add_user_xp,
    get_user_rank,
    get_leaderboard,
    get_level_rewards,
    set_level_reward,
    remove_level_reward,
    get_guild_level_settings,
    set_guild_level_settings
)

logger = logging.getLogger("Levels")

# Кэш кулдауна начисления опыта (user_id -> last_xp_timestamp)
_xp_cooldowns: dict[tuple[int, int], float] = {}


def get_font(size: int, bold: bool = False):
    """Безопасная загрузка системных шрифтов с поддержкой Windows и Linux/Render."""
    font_candidates = [
        "arialbd.ttf" if bold else "arial.ttf",
        "DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf",
        "segui_bold.ttf" if bold else "segoeui.ttf",
        "Roboto-Bold.ttf" if bold else "Roboto-Regular.ttf",
    ]
    for font_name in font_candidates:
        try:
            return ImageFont.truetype(font_name, size)
        except Exception:
            continue
    try:
        return ImageFont.load_default()
    except Exception:
        return None


async def render_rank_card(
    member: discord.Member,
    rank: int,
    level: int,
    cur_xp: int,
    needed_xp: int
) -> io.BytesIO:
    """Генерирует стильную неоновую карточку ранга в стиле киберпанк / Discord Dark."""
    width, height = 900, 260
    # Создаем базовое изображение с альфа-каналом
    image = Image.new("RGBA", (width, height), (15, 17, 24, 255))
    draw = ImageDraw.Draw(image)

    # 1. Фоновый градиент и карточка с закругленными углами
    card_bg = (24, 28, 41, 240)
    card_border = (59, 130, 246, 120)  # сияющий синий оттенок
    draw.rounded_rectangle([15, 15, width - 15, height - 15], radius=24, fill=card_bg, outline=card_border, width=2)

    # Декоративная неоновая подсветка в углу
    accent_color = (99, 102, 241)  # Indigo
    draw.ellipse([width - 250, -80, width + 50, 150], fill=(99, 102, 241, 30))

    # 2. Загрузка и отрисовка аватарки пользователя
    avatar_size = 170
    avatar_pos = (50, 45)
    avatar_image = None

    try:
        avatar_url = member.display_avatar.with_format("png").with_size(256).url
        async with aiohttp.ClientSession() as session:
            async with session.get(avatar_url, timeout=aiohttp.ClientTimeout(total=4)) as resp:
                if resp.status == 200:
                    avatar_bytes = await resp.read()
                    avatar_image = Image.open(io.BytesIO(avatar_bytes)).convert("RGBA")
    except Exception as e:
        logger.warning(f"Не удалось загрузить аватарку {member.name}: {e}")

    if not avatar_image:
        # Заглушка, если аватарка не загрузилась
        avatar_image = Image.new("RGBA", (avatar_size, avatar_size), (88, 101, 242, 255))

    avatar_image = avatar_image.resize((avatar_size, avatar_size), Image.Resampling.LANCZOS)

    # Маска круга для аватарки
    mask = Image.new("L", (avatar_size, avatar_size), 0)
    mask_draw = ImageDraw.Draw(mask)
    mask_draw.ellipse((0, 0, avatar_size, avatar_size), fill=255)

    # Ореол вокруг аватарки
    draw.ellipse(
        (avatar_pos[0] - 4, avatar_pos[1] - 4, avatar_pos[0] + avatar_size + 4, avatar_pos[1] + avatar_size + 4),
        fill=(59, 130, 246, 255)
    )
    image.paste(avatar_image, avatar_pos, mask)

    # Индикатор статуса (онлайн / офлайн)
    status_colors = {
        discord.Status.online: (34, 197, 94),
        discord.Status.idle: (234, 179, 8),
        discord.Status.dnd: (239, 68, 68),
        discord.Status.offline: (148, 163, 184)
    }
    status_col = status_colors.get(member.status, (148, 163, 184))
    draw.ellipse((avatar_pos[0] + avatar_size - 36, avatar_pos[1] + avatar_size - 36, avatar_pos[0] + avatar_size, avatar_pos[1] + avatar_size), fill=(24, 28, 41, 255))
    draw.ellipse((avatar_pos[0] + avatar_size - 32, avatar_pos[1] + avatar_size - 32, avatar_pos[0] + avatar_size - 4, avatar_pos[1] + avatar_size - 4), fill=status_col)

    # 3. Текстовая информация
    font_large = get_font(34, bold=True)
    font_medium = get_font(24, bold=True)
    font_small = get_font(18, bold=False)

    text_x = 250
    # Имя пользователя
    username = member.display_name
    if len(username) > 18:
        username = username[:16] + "..."
    draw.text((text_x, 50), username, font=font_large, fill=(255, 255, 255))

    # Бейджи Ранга и Уровня в правом верхнем углу
    rank_text = f"РАНГ #{rank}"
    lvl_text = f"УРОВЕНЬ {level}"

    # Отрисовка бейджей справа
    right_x = width - 60
    draw.text((right_x, 52), lvl_text, font=font_large, fill=(59, 130, 246), anchor="ra")
    draw.text((right_x - 220, 56), rank_text, font=font_medium, fill=(148, 163, 184), anchor="ra")

    # Текст текущего опыта
    xp_text = f"{cur_xp:,} / {needed_xp:,} XP".replace(",", " ")
    draw.text((right_x, 140), xp_text, font=font_small, fill=(203, 213, 225), anchor="ra")

    # 4. Прогресс-бар опыта
    bar_x = text_x
    bar_y = 175
    bar_w = width - bar_x - 60
    bar_h = 24

    # Фон шкалы
    draw.rounded_rectangle([bar_x, bar_y, bar_x + bar_w, bar_y + bar_h], radius=12, fill=(40, 46, 66))

    # Заполнение шкалы прогресса
    ratio = min(max(cur_xp / max(needed_xp, 1), 0.0), 1.0)
    fill_w = max(int(bar_w * ratio), 12 if ratio > 0 else 0)

    if fill_w > 0:
        # Красивое неоновое заполнение
        draw.rounded_rectangle([bar_x, bar_y, bar_x + fill_w, bar_y + bar_h], radius=12, fill=(99, 102, 241))
        # Блик на шкале
        draw.rounded_rectangle([bar_x, bar_y, bar_x + fill_w, bar_y + 8], radius=4, fill=(129, 140, 248, 120))

    # Сохраняем в буфер памяти
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    buffer.seek(0)
    return buffer


class LevelsCog(commands.Cog, name="Уровни и ранги"):
    """Система уровней, опыта (XP), наградных ролей и графических карточек."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.voice_xp_task.start()

    def cog_unload(self):
        self.voice_xp_task.cancel()

    @tasks.loop(minutes=1)
    async def voice_xp_task(self):
        """Фоновый цикл начисления опыта за нахождение в голосовых каналах (Voice XP)."""
        for guild in self.bot.guilds:
            try:
                # 1. Проверяем включена ли система уровней и Voice XP для сервера
                level_settings = await get_guild_level_settings(guild.id)
                if not level_settings.get("enabled", 1) or not level_settings.get("voice_xp_enabled", 1):
                    continue

                rate = float(level_settings.get("xp_rate", 1.0))

                # 2. Перебираем голосовые и трибунные каналы
                channels = list(guild.voice_channels) + list(guild.stage_channels)
                for vc in channels:
                    # Пропускаем AFK-канал сервера
                    if guild.afk_channel and vc.id == guild.afk_channel.id:
                        continue

                    # Реальные участники без ботов
                    human_members = [m for m in vc.members if not m.bot]

                    # Анти-накрутка: если в войсе меньше 2 реальных участников, опыт не дается
                    if len(human_members) < 2:
                        continue

                    for member in human_members:
                        voice_state = member.voice
                        if not voice_state:
                            continue

                        # Анти-AFK: если участник полностью заглушил звук (self_deaf или deaf), опыт не дается
                        if voice_state.self_deaf or voice_state.deaf:
                            continue

                        # Если микрофон выключен (прослушивание) — 4-7 XP/мин, если говорит — 8-14 XP/мин
                        is_muted = voice_state.self_mute or voice_state.mute
                        base_xp = random.randint(4, 7) if is_muted else random.randint(8, 14)
                        earned_xp = int(base_xp * max(rate, 0.1))

                        if earned_xp <= 0:
                            continue

                        new_level, total_xp, did_level_up = await add_user_xp(guild.id, member.id, earned_xp)

                        if did_level_up:
                            # 1. Проверяем роль-награду
                            rewards = await get_level_rewards(guild.id)
                            for rew in rewards:
                                if rew["level"] == new_level:
                                    role_id = rew["role_id"]
                                    reward_role = guild.get_role(role_id)
                                    if reward_role and guild.me.guild_permissions.manage_roles:
                                        try:
                                            await member.add_roles(reward_role, reason=f"Kobi: Награда за {new_level} уровень в войсе!")
                                        except Exception as e:
                                            logger.warning(f"Не удалось выдать роль {reward_role.name}: {e}")

                            # 2. Оповещение о повышении уровня
                            announce_ch_id = level_settings.get("announce_channel_id", 0)
                            announce_channel = guild.get_channel(announce_ch_id) if announce_ch_id else None

                            if announce_channel and announce_channel.permissions_for(guild.me).send_messages:
                                embed = discord.Embed(
                                    title="🎉 Повышение уровня за общение в войсе!",
                                    description=f"{member.mention}, отличная беседа в **{vc.name}**! Ты достиг **{new_level} уровня**!",
                                    color=discord.Color.gold()
                                )
                                embed.set_thumbnail(url=member.display_avatar.url)
                                try:
                                    await announce_channel.send(embed=embed)
                                except Exception:
                                    pass

            except Exception as e:
                logger.error(f"Ошибка в цикле voice_xp_task для сервера {guild.id}: {e}")

    @voice_xp_task.before_loop
    async def before_voice_xp(self):
        await self.bot.wait_until_ready()

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        """Начисление XP за сообщения в текстовых каналах."""
        if message.author.bot or not message.guild:
            return

        guild = message.guild
        author = message.author

        # Проверка включен ли модуль уровней для сервера
        level_settings = await get_guild_level_settings(guild.id)
        if not level_settings.get("enabled", 1):
            return

        # Защита от спама: не чаще 1 раза в 60 секунд
        cooldown_key = (guild.id, author.id)
        now = time.time()
        last_time = _xp_cooldowns.get(cooldown_key, 0.0)
        if now - last_time < 60:
            return

        _xp_cooldowns[cooldown_key] = now

        # Расчет случайного количества XP (от 15 до 25 с учетом множителя сервера)
        rate = float(level_settings.get("xp_rate", 1.0))
        base_xp = random.randint(15, 25)
        earned_xp = int(base_xp * max(rate, 0.1))

        # Добавление в базу данных
        new_level, total_xp, did_level_up = await add_user_xp(guild.id, author.id, earned_xp)

        if did_level_up:
            # 1. Проверяем наличие роли-награды за новый уровень
            rewards = await get_level_rewards(guild.id)
            for rew in rewards:
                if rew["level"] == new_level:
                    role_id = rew["role_id"]
                    reward_role = guild.get_role(role_id)
                    if reward_role and guild.me.guild_permissions.manage_roles:
                        try:
                            await author.add_roles(reward_role, reason=f"Kobi: Награда за {new_level} уровень!")
                        except Exception as e:
                            logger.warning(f"Не удалось выдать роль {reward_role.name} пользователю {author.name}: {e}")

            # 2. Отправка поздравления о новом уровне
            announce_ch_id = level_settings.get("announce_channel_id", 0)
            target_channel = guild.get_channel(announce_ch_id) if announce_ch_id else message.channel

            if target_channel and target_channel.permissions_for(guild.me).send_messages:
                embed = discord.Embed(
                    title="🎉 Повышение уровня!",
                    description=f"{author.mention}, поздравляем! Ты достиг **{new_level} уровня**!",
                    color=discord.Color.gold()
                )
                embed.set_thumbnail(url=author.display_avatar.url)
                try:
                    await target_channel.send(embed=embed)
                except Exception:
                    pass

    @app_commands.command(name="rank", description="Показать вашу карточку ранга, уровень и опыт на сервере.")
    @app_commands.describe(user="Пользователь, чей ранг вы хотите посмотреть (по умолчанию — вы)")
    async def slash_rank(self, interaction: discord.Interaction, user: Optional[discord.Member] = None):
        """Слэш-команда просмотра ранга."""
        target = user or interaction.user
        if target.bot:
            await interaction.response.send_message("❌ У ботов нет уровней и опыта.", ephemeral=True)
            return

        await interaction.response.defer()

        # Получаем данные из базы
        user_data = await get_user_level(interaction.guild_id, target.id)
        rank = await get_user_rank(interaction.guild_id, target.id)

        # Рендерим графическую карточку
        card_buffer = await render_rank_card(
            member=target,
            rank=rank,
            level=user_data["level"],
            cur_xp=user_data["cur_xp"],
            needed_xp=user_data["needed_xp"]
        )

        file = discord.File(fp=card_buffer, filename=f"rank_{target.id}.png")
        await interaction.followup.send(file=file)

    @commands.command(name="rank", aliases=["ранг", "lvl", "level"])
    async def cmd_rank(self, ctx: commands.Context, user: Optional[discord.Member] = None):
        """Префиксная команда просмотра ранга (!rank)."""
        target = user or ctx.author
        if target.bot:
            await ctx.reply("❌ У ботов нет уровней и опыта.", mention_author=False)
            return

        async with ctx.typing():
            user_data = await get_user_level(ctx.guild.id, target.id)
            rank = await get_user_rank(ctx.guild.id, target.id)

            card_buffer = await render_rank_card(
                member=target,
                rank=rank,
                level=user_data["level"],
                cur_xp=user_data["cur_xp"],
                needed_xp=user_data["needed_xp"]
            )

            file = discord.File(fp=card_buffer, filename=f"rank_{target.id}.png")
            await ctx.reply(file=file, mention_author=False)

    @app_commands.command(name="leaderboard", description="Показать топ самых активных участников сервера по опыту.")
    async def slash_leaderboard(self, interaction: discord.Interaction):
        """Слэш-команда таблицы лидеров."""
        await interaction.response.defer()
        leaders = await get_leaderboard(interaction.guild_id, limit=10)

        if not leaders:
            await interaction.followup.send("📊 На этом сервере пока никто не набрал опыт. Начните общаться!")
            return

        medals = ["🥇", "🥈", "🥉", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣", "🔟"]
        lines = []
        for i, entry in enumerate(leaders):
            medal = medals[i] if i < len(medals) else f"`#{i+1}`"
            member = interaction.guild.get_member(entry["user_id"])
            name = member.mention if member else f"<@{entry['user_id']}>"
            lines.append(f"{medal} **{name}** — Уровень **{entry['level']}** (`{entry['xp']:,}` XP)".replace(",", " "))

        embed = discord.Embed(
            title=f"🏆 Таблица лидеров — {interaction.guild.name}",
            description="\n".join(lines),
            color=discord.Color.from_rgb(99, 102, 241)
        )
        if interaction.guild.icon:
            embed.set_thumbnail(url=interaction.guild.icon.url)
        embed.set_footer(text="Опыт начисляется за активное общение в текстовых чатах!")

        await interaction.followup.send(embed=embed)

    @commands.command(name="leaderboard", aliases=["топ", "top", "lb"])
    async def cmd_leaderboard(self, ctx: commands.Context):
        """Префиксная команда таблицы лидеров (!leaderboard)."""
        leaders = await get_leaderboard(ctx.guild.id, limit=10)
        if not leaders:
            await ctx.reply("📊 На сервере пока нет лидеров. Начните общаться!", mention_author=False)
            return

        medals = ["🥇", "🥈", "🥉", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣", "🔟"]
        lines = []
        for i, entry in enumerate(leaders):
            medal = medals[i] if i < len(medals) else f"`#{i+1}`"
            member = ctx.guild.get_member(entry["user_id"])
            name = member.mention if member else f"<@{entry['user_id']}>"
            lines.append(f"{medal} **{name}** — Уровень **{entry['level']}** (`{entry['xp']:,}` XP)".replace(",", " "))

        embed = discord.Embed(
            title=f"🏆 Таблица лидеров — {ctx.guild.name}",
            description="\n".join(lines),
            color=discord.Color.from_rgb(99, 102, 241)
        )
        if ctx.guild.icon:
            embed.set_thumbnail(url=ctx.guild.icon.url)
        embed.set_footer(text="Опыт начисляется за активное общение в текстовых чатах!")
        await ctx.reply(embed=embed, mention_author=False)

    @app_commands.command(name="level_reward", description="Управление наградами (ролями) за достижение уровней.")
    @app_commands.describe(
        action="Действие: добавить, удалить или показать список",
        level="Уровень, за который выдается награда (для добавления/удаления)",
        role="Роль, которая выдается при достижении уровня (для добавления)"
    )
    @app_commands.choices(action=[
        app_commands.Choice(name="Добавить роль за уровень", value="add"),
        app_commands.Choice(name="Удалить роль за уровень", value="remove"),
        app_commands.Choice(name="Показать текущие награды", value="list")
    ])
    @commands.has_permissions(administrator=True)
    async def slash_level_reward(
        self,
        interaction: discord.Interaction,
        action: app_commands.Choice[str],
        level: Optional[int] = None,
        role: Optional[discord.Role] = None
    ):
        """Настройка наградных ролей за уровни."""
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("❌ У вас нет прав Администратора.", ephemeral=True)
            return

        if action.value == "add":
            if not level or not role:
                await interaction.response.send_message("❌ Укажите и уровень, и роль для добавления награды.", ephemeral=True)
                return
            await set_level_reward(interaction.guild_id, level, role.id)
            await interaction.response.send_message(f"✅ Награда успешно сохранена: за **{level} уровень** выдаётся роль {role.mention}.", ephemeral=True)

        elif action.value == "remove":
            if not level:
                await interaction.response.send_message("❌ Укажите уровень, награду за который нужно удалить.", ephemeral=True)
                return
            ok = await remove_level_reward(interaction.guild_id, level)
            if ok:
                await interaction.response.send_message(f"✅ Награда за **{level} уровень** удалена.", ephemeral=True)
            else:
                await interaction.response.send_message(f"⚠️ Награда за {level} уровень не была найдена.", ephemeral=True)

        elif action.value == "list":
            rewards = await get_level_rewards(interaction.guild_id)
            if not rewards:
                await interaction.response.send_message("ℹ️ На этом сервере пока не настроены награды за уровни.", ephemeral=True)
                return

            lines = []
            for rew in rewards:
                r_obj = interaction.guild.get_role(rew["role_id"])
                r_text = r_obj.mention if r_obj else f"`Удаленная роль (ID: {rew['role_id']})`"
                lines.append(f"⭐ **Уровень {rew['level']}** ➔ {r_text}")

            embed = discord.Embed(
                title=f"🎁 Награды за уровни — {interaction.guild.name}",
                description="\n".join(lines),
                color=discord.Color.green()
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="level_settings", description="Настройка системы уровней, множителя XP и опыта в войсе (Voice XP).")
    @app_commands.describe(
        enabled="Включить или выключить всю систему уровней",
        voice_xp="Включить или выключить начисление опыта в голосовых каналах",
        xp_rate="Множитель опыта (например, 1.0 = обычный, 1.5 = +50%, 2.0 = удвоенный)",
        announce_channel="Канал для поздравлений о новом уровне"
    )
    @commands.has_permissions(administrator=True)
    async def slash_level_settings(
        self,
        interaction: discord.Interaction,
        enabled: Optional[bool] = None,
        voice_xp: Optional[bool] = None,
        xp_rate: Optional[float] = None,
        announce_channel: Optional[discord.TextChannel] = None
    ):
        """Настройка параметров уровней и Voice XP сервера."""
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("❌ У вас нет прав Администратора.", ephemeral=True)
            return

        current = await get_guild_level_settings(interaction.guild_id)
        new_enabled = enabled if enabled is not None else bool(current.get("enabled", 1))
        new_voice = voice_xp if voice_xp is not None else bool(current.get("voice_xp_enabled", 1))
        new_rate = xp_rate if xp_rate is not None else float(current.get("xp_rate", 1.0))
        new_ch = announce_channel.id if announce_channel is not None else current.get("announce_channel_id", 0)

        await set_guild_level_settings(interaction.guild_id, new_enabled, new_ch, new_rate, new_voice)

        ch_text = f"<#{new_ch}>" if new_ch else "💬 В текущем канале общения"
        embed = discord.Embed(
            title="⚙️ Настройки системы уровней Kobi",
            color=discord.Color.from_rgb(99, 102, 241)
        )
        embed.add_field(name="Система уровней", value="✅ Включена" if new_enabled else "❌ Выключена", inline=True)
        embed.add_field(name="Голосовой опыт (Voice XP)", value="✅ Включен" if new_voice else "❌ Выключен", inline=True)
        embed.add_field(name="Множитель опыта", value=f"`{new_rate:.1f}x`", inline=True)
        embed.add_field(name="Канал поздравлений", value=ch_text, inline=False)
        embed.set_footer(text="Голосовой опыт начисляется автоматически каждую минуту при общении от 2-х человек.")
        await interaction.response.send_message(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(LevelsCog(bot))
