import io
import logging
from typing import Optional

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands
from PIL import Image, ImageDraw, ImageFont

from database import get_welcome_settings, set_welcome_settings

logger = logging.getLogger("Welcome")


def get_font(size: int, bold: bool = False):
    """Безопасная загрузка системных шрифтов."""
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


async def render_welcome_card(member: discord.Member) -> io.BytesIO:
    """Генерирует приветственную графическую открытку с аватаром новичка."""
    width, height = 900, 300
    image = Image.new("RGBA", (width, height), (15, 17, 24, 255))
    draw = ImageDraw.Draw(image)

    # Фон и неоновые элементы
    draw.rounded_rectangle([15, 15, width - 15, height - 15], radius=24, fill=(24, 28, 41, 245), outline=(99, 102, 241, 140), width=2)
    draw.ellipse([-50, -50, 200, 200], fill=(59, 130, 246, 35))
    draw.ellipse([width - 180, height - 180, width + 60, height + 60], fill=(168, 85, 247, 35))

    # Загрузка аватара
    avatar_size = 180
    avatar_pos = (55, 60)
    avatar_image = None

    try:
        avatar_url = member.display_avatar.with_format("png").with_size(256).url
        async with aiohttp.ClientSession() as session:
            async with session.get(avatar_url, timeout=aiohttp.ClientTimeout(total=4)) as resp:
                if resp.status == 200:
                    avatar_bytes = await resp.read()
                    avatar_image = Image.open(io.BytesIO(avatar_bytes)).convert("RGBA")
    except Exception as e:
        logger.warning(f"Не удалось загрузить аватар {member.name} для открытки: {e}")

    if not avatar_image:
        avatar_image = Image.new("RGBA", (avatar_size, avatar_size), (88, 101, 242, 255))

    avatar_image = avatar_image.resize((avatar_size, avatar_size), Image.Resampling.LANCZOS)

    # Круглая маска
    mask = Image.new("L", (avatar_size, avatar_size), 0)
    mask_draw = ImageDraw.Draw(mask)
    mask_draw.ellipse((0, 0, avatar_size, avatar_size), fill=255)

    # Ореол
    draw.ellipse(
        (avatar_pos[0] - 5, avatar_pos[1] - 5, avatar_pos[0] + avatar_size + 5, avatar_pos[1] + avatar_size + 5),
        fill=(99, 102, 241, 255)
    )
    image.paste(avatar_image, avatar_pos, mask)

    # Шрифты
    font_title = get_font(38, bold=True)
    font_name = get_font(28, bold=True)
    font_sub = get_font(20, bold=False)

    text_x = 275
    # Заголовок
    draw.text((text_x, 65), "ДОБРО ПОЖАЛОВАТЬ!", font=font_title, fill=(255, 255, 255))

    # Никнейм
    uname = member.display_name
    if len(uname) > 22:
        uname = uname[:20] + "..."
    draw.text((text_x, 125), uname, font=font_name, fill=(129, 140, 248))

    # Подзаголовок
    guild_name = member.guild.name
    if len(guild_name) > 24:
        guild_name = guild_name[:22] + "..."
    member_count = member.guild.member_count
    sub_text = f"на сервер {guild_name} • Участник #{member_count}"
    draw.text((text_x, 175), sub_text, font=font_sub, fill=(203, 213, 225))

    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    buffer.seek(0)
    return buffer


def format_text(template: str, member: discord.Member) -> str:
    """Форматирует переменные в тексте приветствия."""
    return template.replace("{user}", member.display_name)\
                   .replace("{mention}", member.mention)\
                   .replace("{server}", member.guild.name)\
                   .replace("{count}", str(member.guild.member_count))


class WelcomeCog(commands.Cog, name="Приветствия"):
    """Приветствия новых участников с открытками и отправкой в ЛС."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        """Обработка входа нового участника."""
        if member.bot:
            return

        guild = member.guild
        cfg = await get_welcome_settings(guild.id)
        if not cfg.get("enabled", 0):
            return

        # 1. Отправка в канал приветствий
        channel_id = cfg.get("channel_id", 0)
        target_channel = guild.get_channel(channel_id) if channel_id else None

        if target_channel and target_channel.permissions_for(guild.me).send_messages:
            msg_text = format_text(cfg.get("message", "Добро пожаловать на наш сервер, {mention}! 🎉"), member)
            
            file = None
            if cfg.get("send_card", 1):
                try:
                    card_buf = await render_welcome_card(member)
                    file = discord.File(fp=card_buf, filename=f"welcome_{member.id}.png")
                except Exception as e:
                    logger.error(f"Ошибка при создании открытки приветствия: {e}")

            try:
                if file:
                    await target_channel.send(content=msg_text, file=file)
                else:
                    await target_channel.send(content=msg_text)
            except Exception as e:
                logger.warning(f"Не удалось отправить приветствие в {target_channel.name}: {e}")

        # 2. Отправка личного сообщения (DM), если настроено
        dm_text = cfg.get("dm_message", "").strip()
        if dm_text:
            formatted_dm = format_text(dm_text, member)
            try:
                await member.send(formatted_dm)
            except discord.Forbidden:
                pass  # У пользователя закрыты ЛС
            except Exception as e:
                logger.debug(f"Не удалось отправить приветственное ЛС {member.name}: {e}")

    welcome_group = app_commands.Group(name="welcome", description="Управление приветствиями новых участников")

    @welcome_group.command(name="setup", description="Быстрая настройка канала и статуса приветствий.")
    @app_commands.describe(
        channel="Канал для приветствий",
        enabled="Включить или выключить систему",
        send_card="Генерировать графическую открытку с аватаром"
    )
    @commands.has_permissions(administrator=True)
    async def slash_welcome_setup(
        self,
        interaction: discord.Interaction,
        channel: discord.TextChannel,
        enabled: bool,
        send_card: bool = True
    ):
        """Настройка канала приветствий."""
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("❌ У вас нет прав Администратора.", ephemeral=True)
            return

        cfg = await get_welcome_settings(interaction.guild_id)
        await set_welcome_settings(
            guild_id=interaction.guild_id,
            enabled=enabled,
            channel_id=channel.id,
            message=cfg.get("message", "Добро пожаловать на наш сервер, {mention}! 🎉"),
            dm_message=cfg.get("dm_message", ""),
            send_card=send_card
        )

        status_text = "включены" if enabled else "выключены"
        await interaction.response.send_message(
            f"✅ Настройки приветствий обновлены!\n"
            f"• Статус: **{status_text}**\n"
            f"• Канал: {channel.mention}\n"
            f"• Графическая открытка: **{'Да' if send_card else 'Нет'}**",
            ephemeral=True
        )

    @welcome_group.command(name="message", description="Установить текст сообщения в канале приветствий.")
    @app_commands.describe(
        text="Текст сообщения. Доступны теги: {mention}, {user}, {server}, {count}"
    )
    @commands.has_permissions(administrator=True)
    async def slash_welcome_message(self, interaction: discord.Interaction, text: str):
        """Настройка текста приветствия."""
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("❌ У вас нет прав Администратора.", ephemeral=True)
            return

        cfg = await get_welcome_settings(interaction.guild_id)
        await set_welcome_settings(
            guild_id=interaction.guild_id,
            enabled=cfg.get("enabled", 0),
            channel_id=cfg.get("channel_id", 0),
            message=text,
            dm_message=cfg.get("dm_message", ""),
            send_card=cfg.get("send_card", 1)
        )
        sample = format_text(text, interaction.user)
        await interaction.response.send_message(
            f"✅ Текст приветствия успешно обновлен!\n**Пример отображения:**\n{sample}",
            ephemeral=True
        )

    @welcome_group.command(name="dm", description="Установить приветственное личное сообщение (DM) для новичка.")
    @app_commands.describe(
        text="Текст сообщения в ЛС (оставьте пустым для отключения). Доступны: {user}, {server}"
    )
    @commands.has_permissions(administrator=True)
    async def slash_welcome_dm(self, interaction: discord.Interaction, text: Optional[str] = ""):
        """Настройка приветственного личного сообщения."""
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("❌ У вас нет прав Администратора.", ephemeral=True)
            return

        cfg = await get_welcome_settings(interaction.guild_id)
        await set_welcome_settings(
            guild_id=interaction.guild_id,
            enabled=cfg.get("enabled", 0),
            channel_id=cfg.get("channel_id", 0),
            message=cfg.get("message", "Добро пожаловать, {mention}!"),
            dm_message=text or "",
            send_card=cfg.get("send_card", 1)
        )
        if text and text.strip():
            sample = format_text(text, interaction.user)
            await interaction.response.send_message(
                f"✅ Приветственное ЛС настроено!\n**Пример:**\n{sample}",
                ephemeral=True
            )
        else:
            await interaction.response.send_message("✅ Приветственное личное сообщение отключено.", ephemeral=True)

    @welcome_group.command(name="test", description="Протестировать и посмотреть, как выглядит приветствие.")
    @commands.has_permissions(administrator=True)
    async def slash_welcome_test(self, interaction: discord.Interaction):
        """Тестовая отправка приветствия."""
        await interaction.response.defer(ephemeral=True)
        cfg = await get_welcome_settings(interaction.guild_id)

        msg_text = format_text(cfg.get("message", "Добро пожаловать на наш сервер, {mention}! 🎉"), interaction.user)
        card_buf = await render_welcome_card(interaction.user)
        file = discord.File(fp=card_buf, filename="test_welcome.png")

        await interaction.followup.send(
            content=f"🔔 **Предпросмотр приветствия:**\n{msg_text}",
            file=file,
            ephemeral=True
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(WelcomeCog(bot))
