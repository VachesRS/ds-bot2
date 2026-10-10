import logging
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

from database import (
    add_button_role,
    get_button_roles_for_message,
    get_all_button_role_messages,
    delete_button_roles
)

logger = logging.getLogger("ButtonRoles")

STYLE_MAP = {
    "primary": discord.ButtonStyle.primary,      # Синий (Blurple)
    "secondary": discord.ButtonStyle.secondary,  # Серый
    "success": discord.ButtonStyle.success,      # Зелёный
    "danger": discord.ButtonStyle.danger         # Красный
}


class RoleButton(discord.ui.Button):
    """Интерактивная кнопка для переключения роли (toggle)."""

    def __init__(self, role_id: int, label: str, emoji: Optional[str] = None, style: discord.ButtonStyle = discord.ButtonStyle.primary):
        custom_id = f"sg_btnrole:{role_id}"
        super().__init__(
            style=style,
            label=label,
            emoji=emoji if emoji and emoji.strip() else None,
            custom_id=custom_id
        )
        self.role_id = role_id

    async def callback(self, interaction: discord.Interaction):
        guild = interaction.guild
        member = interaction.user

        if not isinstance(member, discord.Member):
            await interaction.response.send_message("❌ Ошибка определения участника сервера.", ephemeral=True)
            return

        role = guild.get_role(self.role_id)
        if not role:
            await interaction.response.send_message("❌ Роль больше не существует на сервере.", ephemeral=True)
            return

        if not guild.me.guild_permissions.manage_roles:
            await interaction.response.send_message("❌ У бота нет права `Управление ролями` (Manage Roles).", ephemeral=True)
            return

        if role >= guild.me.top_role:
            await interaction.response.send_message("❌ Роль бота должна быть ВЫШЕ этой роли в настройках ролей сервера.", ephemeral=True)
            return

        # Переключение роли: если есть — снимаем, если нет — выдаем
        try:
            if role in member.roles:
                await member.remove_roles(role, reason="ShieldGuard: Снятие роли по кнопке")
                await interaction.response.send_message(f"➖ Роль {role.mention} была успешно снята!", ephemeral=True)
            else:
                await member.add_roles(role, reason="ShieldGuard: Выдача роли по кнопке")
                await interaction.response.send_message(f"➕ Вам успешно выдана роль {role.mention}!", ephemeral=True)
        except discord.Forbidden:
            await interaction.response.send_message("❌ Недостаточно прав для управления этой ролью.", ephemeral=True)
        except Exception as e:
            logger.error(f"Ошибка при выдаче роли по кнопке: {e}")
            await interaction.response.send_message(f"❌ Произошла ошибка: {e}", ephemeral=True)


class PersistentButtonRoleView(discord.ui.View):
    """Персистентный контейнер кнопок (timeout=None)."""

    def __init__(self):
        super().__init__(timeout=None)


class ButtonRolesCog(commands.Cog, name="Роли по кнопкам"):
    """Создание и управление интерактивными кнопками для выдачи ролей."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def cog_load(self):
        """Восстановление работы всех кнопок-ролей после перезапуска бота."""
        try:
            messages = await get_all_button_role_messages()
            count = 0
            for msg in messages:
                msg_id = msg["message_id"]
                buttons = await get_button_roles_for_message(msg_id)
                if not buttons:
                    continue

                view = PersistentButtonRoleView()
                for btn_data in buttons:
                    style_enum = STYLE_MAP.get(btn_data.get("style", "primary"), discord.ButtonStyle.primary)
                    view.add_item(
                        RoleButton(
                            role_id=btn_data["role_id"],
                            label=btn_data["label"],
                            emoji=btn_data.get("emoji"),
                            style=style_enum
                        )
                    )
                self.bot.add_view(view, message_id=msg_id)
                count += 1
            logger.info(f"Успешно восстановлено {count} интерактивных меню с кнопками-ролями.")
        except Exception as e:
            logger.error(f"Ошибка при загрузке персистентных кнопок ролей: {e}")

    @app_commands.command(
        name="buttonrole_quick",
        description="Быстро создать сообщение с кнопкой получения роли в указанном канале."
    )
    @app_commands.describe(
        channel="Канал, куда отправить сообщение",
        title="Заголовок объявления",
        description="Текст описания в объявлении",
        role="Роль, которая выдается по нажатию кнопки",
        button_label="Текст на кнопке (например: Получить роль)",
        emoji="Эмодзи на кнопке (например: 🎮, 🔔, ⭐)",
        style="Цвет кнопки: primary (синий), success (зеленый), danger (красный), secondary (серый)"
    )
    @app_commands.choices(style=[
        app_commands.Choice(name="Синий (Primary)", value="primary"),
        app_commands.Choice(name="Зелёный (Success)", value="success"),
        app_commands.Choice(name="Серый (Secondary)", value="secondary"),
        app_commands.Choice(name="Красный (Danger)", value="danger")
    ])
    @commands.has_permissions(administrator=True)
    async def slash_buttonrole_quick(
        self,
        interaction: discord.Interaction,
        channel: discord.TextChannel,
        title: str,
        description: str,
        role: discord.Role,
        button_label: str,
        emoji: Optional[str] = None,
        style: Optional[app_commands.Choice[str]] = None
    ):
        """Быстрое создание сообщения с кнопкой роли."""
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("❌ У вас нет прав Администратора.", ephemeral=True)
            return

        if not channel.permissions_for(interaction.guild.me).send_messages:
            await interaction.response.send_message(f"❌ У бота нет прав на отправку сообщений в {channel.mention}.", ephemeral=True)
            return

        style_str = style.value if style else "primary"
        style_enum = STYLE_MAP.get(style_str, discord.ButtonStyle.primary)

        # Создаем Embed
        embed = discord.Embed(
            title=title,
            description=description,
            color=role.color if role.color.value != 0 else discord.Color.blue()
        )
        embed.set_footer(text="Нажмите кнопку ниже, чтобы получить или снять роль")

        # Создаем View с кнопкой
        view = PersistentButtonRoleView()
        btn = RoleButton(
            role_id=role.id,
            label=button_label,
            emoji=emoji,
            style=style_enum
        )
        view.add_item(btn)

        # Отправляем сообщение в целевой канал
        sent_message = await channel.send(embed=embed, view=view)

        # Сохраняем в базу данных для авто-восстановления при перезапуске
        await add_button_role(
            guild_id=interaction.guild_id,
            channel_id=channel.id,
            message_id=sent_message.id,
            role_id=role.id,
            label=button_label,
            emoji=emoji or "",
            style=style_str
        )

        # Регистрируем View в боте
        self.bot.add_view(view, message_id=sent_message.id)

        await interaction.response.send_message(
            f"✅ Сообщение с кнопкой роли успешно создано в канале {channel.mention}!\n"
            f"Ссылка на сообщение: {sent_message.jump_url}",
            ephemeral=True
        )

    @app_commands.command(
        name="buttonrole_add",
        description="Добавить еще одну кнопку роли к уже существующему сообщению от бота."
    )
    @app_commands.describe(
        message_id="ID сообщения от бота с кнопками",
        role="Роль, которая будет выдаваться",
        button_label="Текст на новой кнопке",
        emoji="Эмодзи на кнопке",
        style="Цвет кнопки"
    )
    @app_commands.choices(style=[
        app_commands.Choice(name="Синий (Primary)", value="primary"),
        app_commands.Choice(name="Зелёный (Success)", value="success"),
        app_commands.Choice(name="Серый (Secondary)", value="secondary"),
        app_commands.Choice(name="Красный (Danger)", value="danger")
    ])
    @commands.has_permissions(administrator=True)
    async def slash_buttonrole_add(
        self,
        interaction: discord.Interaction,
        message_id: str,
        role: discord.Role,
        button_label: str,
        emoji: Optional[str] = None,
        style: Optional[app_commands.Choice[str]] = None
    ):
        """Добавление дополнительной кнопки к существующему сообщению."""
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("❌ У вас нет прав Администратора.", ephemeral=True)
            return

        if not message_id.isdigit():
            await interaction.response.send_message("❌ Неверный формат ID сообщения. ID должен состоять только из цифр.", ephemeral=True)
            return

        mid = int(message_id)
        # Ищем канал сообщения
        target_message = None
        for ch in interaction.guild.text_channels:
            try:
                target_message = await ch.fetch_message(mid)
                if target_message:
                    break
            except Exception:
                continue

        if not target_message:
            await interaction.response.send_message("❌ Сообщение с указанным ID не найдено ни в одном канале сервера.", ephemeral=True)
            return

        if target_message.author.id != self.bot.user.id:
            await interaction.response.send_message("❌ Кнопки можно добавлять только к сообщениям, отправленным самим ботом!", ephemeral=True)
            return

        style_str = style.value if style else "primary"

        # Сохраняем в базу данных
        await add_button_role(
            guild_id=interaction.guild_id,
            channel_id=target_message.channel.id,
            message_id=mid,
            role_id=role.id,
            label=button_label,
            emoji=emoji or "",
            style=style_str
        )

        # Собираем обновленный View со всеми кнопками
        all_buttons = await get_button_roles_for_message(mid)
        new_view = PersistentButtonRoleView()
        for b in all_buttons:
            b_style = STYLE_MAP.get(b.get("style", "primary"), discord.ButtonStyle.primary)
            new_view.add_item(
                RoleButton(
                    role_id=b["role_id"],
                    label=b["label"],
                    emoji=b.get("emoji"),
                    style=b_style
                )
            )

        # Обновляем сообщение в канале
        await target_message.edit(view=new_view)
        self.bot.add_view(new_view, message_id=mid)

        await interaction.response.send_message(f"✅ Кнопка для роли {role.mention} успешно добавлена к сообщению!", ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(ButtonRolesCog(bot))
