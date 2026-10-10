import time
import random
import re
import logging
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands, tasks

from database import (
    create_giveaway,
    get_giveaway,
    get_giveaway_by_message,
    get_active_giveaways,
    add_giveaway_entry,
    remove_giveaway_entry,
    get_giveaway_entries,
    get_giveaway_entries_count,
    end_giveaway
)

logger = logging.getLogger("Giveaways")


def parse_duration(duration_str: str) -> Optional[int]:
    """
    Парсит строку длительности (например: 10m, 2h, 1d, 30s) в секунды.
    """
    match = re.match(r"^(\d+)([smhd])$", duration_str.strip().lower())
    if not match:
        if duration_str.isdigit():
            return int(duration_str) * 60  # По умолчанию минуты
        return None

    val = int(match.group(1))
    unit = match.group(2)
    multipliers = {"s": 1, "m": 60, "h": 3600, "d": 86400}
    return val * multipliers.get(unit, 60)


class GiveawayEntryButton(discord.ui.Button):
    """Кнопка участия в розыгрыше."""

    def __init__(self, giveaway_id: int):
        custom_id = f"sg_giveaway_entry:{giveaway_id}"
        super().__init__(
            style=discord.ButtonStyle.success,
            label="Участвовать 🎉",
            custom_id=custom_id
        )
        self.giveaway_id = giveaway_id

    async def callback(self, interaction: discord.Interaction):
        user_id = interaction.user.id
        gw = await get_giveaway(self.giveaway_id)
        if not gw or gw["status"] != "active":
            await interaction.response.send_message("❌ Этот розыгрыш уже завершен.", ephemeral=True)
            return

        # Попытка добавить пользователя
        added = await add_giveaway_entry(self.giveaway_id, user_id)
        count = await get_giveaway_entries_count(self.giveaway_id)

        if added:
            await interaction.response.send_message(
                f"🎉 Вы успешно зарегистрированы в розыгрыше **{gw['prize']}**!\n"
                f"👥 Всего участников: **{count}**",
                ephemeral=True
            )
        else:
            # Если уже участвует — спрашиваем или отменяем участие
            await remove_giveaway_entry(self.giveaway_id, user_id)
            new_count = await get_giveaway_entries_count(self.giveaway_id)
            await interaction.response.send_message(
                f"➖ Вы отменили своё участие в розыгрыше **{gw['prize']}**.\n"
                f"👥 Осталось участников: **{new_count}**",
                ephemeral=True
            )


class PersistentGiveawayView(discord.ui.View):
    """Персистентный контейнер кнопки розыгрыша."""

    def __init__(self, giveaway_id: int):
        super().__init__(timeout=None)
        self.add_item(GiveawayEntryButton(giveaway_id))


class GiveawaysCog(commands.Cog, name="Розыгрыши"):
    """Проведение розыгрышей призов с кнопками участия и таймером."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.giveaway_check_loop.start()

    def cog_unload(self):
        self.giveaway_check_loop.cancel()

    async def cog_load(self):
        """Восстановление активных кнопок розыгрышей при старте."""
        try:
            active_gws = await get_active_giveaways()
            for gw in active_gws:
                self.bot.add_view(PersistentGiveawayView(gw["id"]), message_id=gw["message_id"])
            logger.info(f"Успешно восстановлено {len(active_gws)} активных розыгрышей.")
        except Exception as e:
            logger.error(f"Ошибка при восстановлении кнопок розыгрышей: {e}")

    @tasks.loop(seconds=15.0)
    async def giveaway_check_loop(self):
        """Периодическая проверка истечения времени розыгрышей."""
        try:
            active_gws = await get_active_giveaways()
            now = time.time()
            for gw in active_gws:
                if now >= gw["end_time"]:
                    await self._finish_giveaway(gw)
        except Exception as e:
            logger.error(f"Ошибка в цикле проверки розыгрышей: {e}")

    @giveaway_check_loop.before_loop
    async def before_giveaway_check(self):
        await self.bot.wait_until_ready()

    async def _finish_giveaway(self, gw: dict):
        """Завершение розыгрыша, выбор победителей и публикация результатов."""
        channel = self.bot.get_channel(gw["channel_id"])
        if not channel or not isinstance(channel, discord.TextChannel):
            await end_giveaway(gw["id"], status="ended_no_channel")
            return

        try:
            message = await channel.fetch_message(gw["message_id"])
        except Exception:
            message = None

        entries = await get_giveaway_entries(gw["id"])
        winners_count = max(gw["winners_count"], 1)

        if not entries:
            # Никто не участвовал
            embed = discord.Embed(
                title="🎉 РОЗЫГРЫШ ЗАВЕРШЁН 🎉",
                description=f"**Приз:** {gw['prize']}\n\n❌ **Победители:** В розыгрыше никто не принял участие.",
                color=discord.Color.red()
            )
            embed.set_footer(text="Kobi Giveaways")
            if message:
                await message.edit(embed=embed, view=None)
            await end_giveaway(gw["id"], status="ended_no_entries")
            return

        # Выбираем случайных победителей
        actual_winners_count = min(len(entries), winners_count)
        winner_ids = random.sample(entries, actual_winners_count)
        winner_mentions = [f"<@{wid}>" for wid in winner_ids]

        embed = discord.Embed(
            title="🎉 РОЗЫГРЫШ ЗАВЕРШЁН 🎉",
            description=(
                f"**Приз:** {gw['prize']}\n"
                f"🏆 **Победители:** {', '.join(winner_mentions)}\n"
                f"👥 Всего участников: **{len(entries)}**"
            ),
            color=discord.Color.gold()
        )
        embed.set_footer(text="Поздравляем с победой!")

        if message:
            await message.edit(embed=embed, view=None)

        # Отправляем поздравление в канал
        congrats_text = f"🎊 Поздравляем {', '.join(winner_mentions)}! Вы выиграли приз: **{gw['prize']}**!"
        await channel.send(content=congrats_text)

        await end_giveaway(gw["id"], status="ended")
        logger.info(f"Розыгрыш {gw['id']} успешно завершен. Победители: {winner_ids}")

    giveaway_group = app_commands.Group(name="giveaway", description="Управление розыгрышами призов")

    @giveaway_group.command(name="start", description="Запустить новый розыгрыш с кнопкой участия.")
    @app_commands.describe(
        channel="Канал для публикации розыгрыша",
        duration="Длительность (например: 10m, 2h, 1d, 30m)",
        prize="Какой приз разыгрывается",
        winners="Количество победителей (по умолчанию: 1)"
    )
    @commands.has_permissions(administrator=True)
    async def slash_giveaway_start(
        self,
        interaction: discord.Interaction,
        channel: discord.TextChannel,
        duration: str,
        prize: str,
        winners: Optional[int] = 1
    ):
        """Запуск розыгрыша."""
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("❌ У вас нет прав Администратора.", ephemeral=True)
            return

        secs = parse_duration(duration)
        if not secs or secs < 10:
            await interaction.response.send_message(
                "❌ Неверный формат времени. Примеры: `10m` (10 минут), `2h` (2 часа), `1d` (1 день), `30s` (30 секунд).",
                ephemeral=True
            )
            return

        if not channel.permissions_for(interaction.guild.me).send_messages:
            await interaction.response.send_message(f"❌ У бота нет прав отправлять сообщения в {channel.mention}.", ephemeral=True)
            return

        winners_count = max(winners or 1, 1)
        end_timestamp = time.time() + secs
        discord_time = f"<t:{int(end_timestamp)}:R>"

        embed = discord.Embed(
            title="🎉 РОЗЫГРЫШ ПРИЗА! 🎉",
            description=(
                f"🎁 **Приз:** {prize}\n"
                f"🏆 **Количество победителей:** `{winners_count}`\n"
                f"⏳ **Окончание:** {discord_time}\n"
                f"👑 **Организатор:** {interaction.user.mention}\n\n"
                f"Нажмите кнопку **«Участвовать 🎉»** ниже, чтобы испытать удачу!"
            ),
            color=discord.Color.from_rgb(99, 102, 241)
        )
        embed.set_footer(text="Kobi • Удачи всем участникам!")

        # Временная заглушка View для отправки сообщения
        temp_view = discord.ui.View()
        temp_btn = discord.ui.Button(style=discord.ButtonStyle.success, label="Участвовать 🎉", disabled=True)
        temp_view.add_item(temp_btn)

        sent_msg = await channel.send(embed=embed, view=temp_view)

        # Сохраняем в базу данных
        gw_id = await create_giveaway(
            message_id=sent_msg.id,
            channel_id=channel.id,
            guild_id=interaction.guild_id,
            prize=prize,
            winners_count=winners_count,
            end_time=end_timestamp,
            host_id=interaction.user.id
        )

        # Подключаем рабочий персистентный View
        real_view = PersistentGiveawayView(gw_id)
        await sent_msg.edit(view=real_view)
        self.bot.add_view(real_view, message_id=sent_msg.id)

        await interaction.response.send_message(
            f"✅ Розыгрыш успешно запущен в канале {channel.mention}!\n"
            f"Ссылка: {sent_msg.jump_url}",
            ephemeral=True
        )

    @giveaway_group.command(name="end", description="Досрочно завершить активный розыгрыш.")
    @app_commands.describe(message_id="ID сообщения розыгрыша")
    @commands.has_permissions(administrator=True)
    async def slash_giveaway_end(self, interaction: discord.Interaction, message_id: str):
        """Досрочное завершение розыгрыша."""
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("❌ У вас нет прав Администратора.", ephemeral=True)
            return

        if not message_id.isdigit():
            await interaction.response.send_message("❌ Неверный ID сообщения.", ephemeral=True)
            return

        gw = await get_giveaway_by_message(int(message_id))
        if not gw or gw["status"] != "active":
            await interaction.response.send_message("❌ Активный розыгрыш с таким ID сообщения не найден.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)
        await self._finish_giveaway(gw)
        await interaction.followup.send("✅ Розыгрыш успешно завершен досрочно!", ephemeral=True)

    @giveaway_group.command(name="reroll", description="Выбрать новых победителей для завершенного розыгрыша.")
    @app_commands.describe(message_id="ID сообщения розыгрыша")
    @commands.has_permissions(administrator=True)
    async def slash_giveaway_reroll(self, interaction: discord.Interaction, message_id: str):
        """Переигровка розыгрыша."""
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("❌ У вас нет прав Администратора.", ephemeral=True)
            return

        if not message_id.isdigit():
            await interaction.response.send_message("❌ Неверный ID сообщения.", ephemeral=True)
            return

        gw = await get_giveaway_by_message(int(message_id))
        if not gw:
            await interaction.response.send_message("❌ Розыгрыш не найден в базе данных.", ephemeral=True)
            return

        entries = await get_giveaway_entries(gw["id"])
        if not entries:
            await interaction.response.send_message("❌ В этом розыгрыше не было участников для переигровки.", ephemeral=True)
            return

        winner_id = random.choice(entries)
        channel = self.bot.get_channel(gw["channel_id"])
        if channel and isinstance(channel, discord.TextChannel):
            await channel.send(f"🎲 **Переигровка розыгрыша:** Новый победитель приза **{gw['prize']}** ➔ <@{winner_id}>! Поздравляем!")
            await interaction.response.send_message(f"✅ Новый победитель выбран: <@{winner_id}>.", ephemeral=True)
        else:
            await interaction.response.send_message("❌ Не удалось найти канал розыгрыша.", ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(GiveawaysCog(bot))
