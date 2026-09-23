import asyncio
import os
import re
import time
import urllib.parse
from collections import defaultdict
from datetime import datetime, timedelta

import aiohttp
import discord
from discord.ext import commands

from database import get_guild_settings
from nsfw_detector import detector
from toxicity_detector import ToxicityDetector

# Регулярное выражение для поиска ссылок-приглашений Discord
INVITE_REGEX = re.compile(
    r"(?:https?://)?(?:www\.)?(?:discord\.(?:gg|io|me|li)|discordapp\.com/invite|discord\.com/invite)/[a-zA-Z0-9\-]+",
    re.IGNORECASE
)

# Список запрещенных тегов/ключевых слов для GIF и ссылок
NSFW_SLUG_KEYWORDS = [
    "porn", "sex", "hentai", "nude", "nudity", "erotic", "erotica", "boobs", "tits",
    "pussy", "dick", "cock", "vagina", "ass", "butt", "blowjob", "creampie", "cum",
    "milf", "dildo", "anal", "masturbat", "horny", "bdsm", "ecchi", "r34", "rule34",
    "yiff", "lewd", "naked", "boob", "nsfw", "xxx", "bra", "panties", "hardcore", "softcore",
    "порно", "секс", "хентай", "сиськи", "член", "вагина", "сиська", "писька", "эротика", "дилдо"
]

GIF_SERVICES_REGEX = re.compile(
    r"(?:https?://)?(?:www\.)?(?:tenor\.com/view/|media\.tenor\.com/|giphy\.com/gifs/|gph\.is/|klipy\.com/gifs/|klipy\.com/clips/|cdn\.klipy\.com/|media\.klipy\.com/|klipy\.co/)([^\s\?\#]+)",
    re.IGNORECASE
)

IMAGE_URL_REGEX = re.compile(
    r"https?://\S+\.(?:png|jpg|jpeg|webp|gif)(?:\?\S*)?",
    re.IGNORECASE
)


class AutoMod(commands.Cog):
    """Модуль автоматической модерации сервера."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        # История сообщений для защиты от флуда: (guild_id, user_id) -> list[timestamp]
        self.message_timestamps = defaultdict(list)
        # Кулдаун предупреждений в чат, чтобы не спамить пользователю: (guild_id, user_id) -> last_warn_time
        self.warn_cooldowns = {}
        self.session: aiohttp.ClientSession | None = None
        try:
            self.toxicity_detector = ToxicityDetector()
        except Exception as e:
            import logging
            logging.getLogger("AutoMod").error(f"Не удалось загрузить ToxicityDetector: {e}")
            self.toxicity_detector = None

    async def check_image_nsfw_api(self, image_url: str, api_key: str) -> tuple[bool, str]:
        """Проверяет изображение через ModerateContent API на наличие контента 18+."""
        if not api_key:
            return False, "No API key"

        endpoint = "https://api.moderatecontent.com/moderate/"
        params = {"url": image_url, "key": api_key}
        try:
            if not self.session or self.session.closed:
                self.session = aiohttp.ClientSession()

            async with self.session.get(endpoint, params=params, timeout=aiohttp.ClientTimeout(total=5)) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    rating = data.get("rating_letter", "").lower()
                    label = data.get("rating_label", "")
                    predictions = data.get("predictions", {})
                    adult_prob = float(predictions.get("adult", 0))

                    if rating == "a" or adult_prob > 60.0:
                        return True, f"AI-детектор: обнаружен взрослый контент (категория '{label}', {adult_prob:.1f}%)"
        except Exception as e:
            print(f"⚠️ [AutoMod NSFW API] Ошибка проверки изображения: {e}")
        return False, ""

    def is_exempt(self, member: discord.Member, settings: dict) -> bool:
        """Проверяет, освобожден ли пользователь от проверок автомодерации."""
        if member.bot:
            return True
        # Если включен иммунитет администраторов (по умолчанию включен)
        if settings.get("ignore_admins", 1):
            if member.guild_permissions.administrator:
                return True
            if member.guild_permissions.manage_guild or member.guild_permissions.manage_messages:
                return True
        return False

    async def get_log_channel(self, guild: discord.Guild) -> discord.TextChannel | None:
        """Находит канал для логов модерации."""
        settings = await get_guild_settings(guild.id)
        log_channel_id = settings.get("log_channel_id", 0)

        # Если не задано в БД, проверяем переменную окружения
        if not log_channel_id or log_channel_id == 0:
            env_id = os.getenv("LOG_CHANNEL_ID")
            if env_id and env_id.isdigit():
                log_channel_id = int(env_id)

        if log_channel_id:
            channel = guild.get_channel(log_channel_id)
            if channel and isinstance(channel, discord.TextChannel):
                return channel
        return None

    async def send_log_embed(
        self,
        guild: discord.Guild,
        member: discord.Member,
        action: str,
        reason: str,
        channel: discord.abc.GuildChannel | None = None,
        content: str | None = None,
        color: discord.Color = discord.Color.orange()
    ):
        """Отправляет структурированный лог в специальный канал."""
        log_channel = await self.get_log_channel(guild)
        if not log_channel:
            return

        embed = discord.Embed(
            title=f"🛡️ Автомодерация: {action}",
            color=color,
            timestamp=datetime.utcnow()
        )
        embed.add_field(name="Участник", value=f"{member.mention} (`{member.id}`)", inline=True)
        if channel:
            embed.add_field(name="Канал", value=channel.mention, inline=True)
        embed.add_field(name="Причина", value=reason, inline=False)

        if content:
            # Обрезаем контент сообщения для компактности
            display_content = content[:800] + ("..." if len(content) > 800 else "")
            embed.add_field(name="Содержимое", value=f"```{display_content}```", inline=False)

        embed.set_footer(text=f"ID сервера: {guild.id}")
        if member.display_avatar:
            embed.set_thumbnail(url=member.display_avatar.url)

        try:
            await log_channel.send(embed=embed)
        except discord.DiscordException:
            pass

    async def log_violation(
        self,
        member: discord.Member,
        action: str,
        reason: str,
        channel: discord.abc.GuildChannel,
        content: str | None = None,
        color: discord.Color = discord.Color.orange()
    ):
        """Отправляет лог нарушения в специальный канал (без начисления варнов)."""
        await self.send_log_embed(
            member.guild, member, action, reason, channel=channel, content=content, color=color
        )

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        try:
            await self.process_message(message)
        except Exception as e:
            print(f"⚠️ [AutoMod Error] Ошибка в обработчике on_message: {e}")

    async def process_message(self, message: discord.Message):
        # Игнорируем сообщения от ботов и сообщения вне серверов (в ЛС)
        if not message.guild or not isinstance(message.author, discord.Member):
            return

        member = message.author
        if member.bot:
            return

        settings = await get_guild_settings(message.guild.id)

        # Диагностика в консоль для проверки работы
        if not message.content:
            print(f"⚠️ [AutoMod] Получено пустое сообщение от {member.name}! Проверьте, включен ли 'MESSAGE CONTENT INTENT' в Discord Developer Portal.")
        else:
            print(f"[AutoMod] Получено сообщение от {member.name}: '{message.content}'")

        if self.is_exempt(member, settings):
            print(f"[AutoMod] Участник {member.name} имеет права администратора/модератора — сообщение пропущено (иммунитет).")
            return

        now = time.time()
        user_key = (message.guild.id, member.id)

        # 1. Защита от спама и флуда
        if settings.get("anti_spam", 1):
            history = self.message_timestamps[user_key]
            # Оставляем только сообщения за последние 4 секунды
            history = [t for t in history if now - t < 4.0]
            history.append(now)
            self.message_timestamps[user_key] = history

            # Если пользователь отправил более 5 сообщений за 4 секунды
            if len(history) >= 5:
                self.message_timestamps[user_key] = []
                try:
                    await message.delete()
                except discord.DiscordException:
                    pass

                try:
                    # Таймаут на 5 минут
                    await member.timeout(timedelta(minutes=5), reason="Автомодерация: Флуд / Спам")
                except discord.DiscordException:
                    pass

                await self.log_violation(
                    member, "Тайм-аут (Флуд)", "Флуд / спам сообщениями (выдан тайм-аут на 5 минут)",
                    message.channel, color=discord.Color.red()
                )

                try:
                    warn_msg = await message.channel.send(
                        f"⚠️ {member.mention}, пожалуйста, не флудите! Вам выдан тайм-аут на 5 минут.",
                        delete_after=7
                    )
                except discord.DiscordException:
                    pass
                return

        # 2. Защита от массовых упоминаний (@everyone, @here, пинг многих участников)
        if settings.get("anti_mass_mention", 1):
            mention_count = len(message.mentions)
            has_mass_ping = message.mention_everyone and not member.guild_permissions.mention_everyone

            if mention_count >= 5 or has_mass_ping:
                try:
                    await message.delete()
                except discord.DiscordException:
                    pass

                try:
                    await member.timeout(timedelta(minutes=15), reason="Автомодерация: Массовые упоминания")
                except discord.DiscordException:
                    pass

                await self.log_violation(
                    member, "Тайм-аут (Массменшн)", f"Массовое упоминание ({mention_count} участников, тайм-аут на 15 минут)",
                    message.channel, color=discord.Color.red()
                )
                return

        # 3. Анти-инвайт (блокировка приглашений на сторонние серверы)
        if settings.get("anti_invite", 1):
            if INVITE_REGEX.search(message.content):
                try:
                    await message.delete()
                except discord.DiscordException:
                    pass

                await self.log_violation(
                    member, "Удаление ссылки-приглашения", "Публикация сторонних ссылок-приглашений Discord",
                    message.channel, content=message.content, color=discord.Color.orange()
                )
                try:
                    await message.channel.send(
                        f"⛔ {member.mention}, ссылки-приглашения на другие серверы запрещены!",
                        delete_after=5
                    )
                except discord.DiscordException:
                    pass
                return

        # 5. Анти-капс (более 70% заглавных букв при длине от 10 символов)
        if settings.get("anti_caps", 1):
            letters = [c for c in message.content if c.isalpha()]
            if len(letters) >= 10:
                upper_count = sum(1 for c in letters if c.isupper())
                ratio = upper_count / len(letters)
                if ratio > 0.70:
                    try:
                        await message.delete()
                    except discord.DiscordException:
                        pass

                    await self.send_log_embed(
                        message.guild,
                        member,
                        "Удаление сообщения (Капс)",
                        f"Превышение допустимого капса ({int(ratio * 100)}% заглавных букв)",
                        channel=message.channel,
                        content=message.content,
                        color=discord.Color.light_grey()
                    )
                    try:
                        await message.channel.send(
                            f"🔇 {member.mention}, пожалуйста, отключите Caps Lock!",
                            delete_after=5
                        )
                    except discord.DiscordException:
                        pass
                    return

        # 5.1 Нейросетевая фильтрация оскорблений (RuBERT Toxicity)
        if settings.get("anti_toxicity", 1) and self.toxicity_detector and message.content:
            text_clean = message.content.strip()
            if len(text_clean) >= 3:
                try:
                    is_insult, score = await asyncio.to_thread(
                        self.toxicity_detector.is_insult, text_clean, 0.85
                    )
                    if is_insult:
                        try:
                            await message.delete()
                        except discord.DiscordException:
                            pass

                        # Выдача тайм-аута нарушителю на 5 минут
                        try:
                            await member.timeout(timedelta(minutes=5), reason="Автомодерация: Оскорбления (ИИ)")
                        except discord.DiscordException:
                            pass

                        await self.send_log_embed(
                            message.guild,
                            member,
                            "🤬 Обнаружено оскорбление (ИИ RuBERT)",
                            f"Нейросеть классифицировала текст как оскорбление (уверенность: **{int(score * 100)}%**).\nНаказание: тайм-аут на 5 минут.",
                            channel=message.channel,
                            content=message.content,
                            color=discord.Color.red()
                        )

                        try:
                            await message.channel.send(
                                f"⚠️ {member.mention}, оскорбления запрещены правилами сервера! (Выдан тайм-аут на 5 минут)",
                                delete_after=7
                            )
                        except discord.DiscordException:
                            pass
                        return
                except Exception as e:
                    import logging
                    logging.getLogger("AutoMod").error(f"Ошибка при анализе оскорблений: {e}")

        # 6. Защита от NSFW (18+ картинок, файлов и GIF)
        is_channel_nsfw = getattr(message.channel, "is_nsfw", lambda: False)()
        if settings.get("anti_nsfw", 1) and not is_channel_nsfw:
            is_nsfw = False
            nsfw_reason = ""

            # 6.1 Проверка ссылок на Klipy / Tenor / Giphy по NSFW-тегам в URL и описании
            gif_matches = GIF_SERVICES_REGEX.findall(message.content)

            # Проверяем также данные в эмбедах (Discord часто генерирует embed для Klipy/Tenor)
            for emb in message.embeds:
                if emb.url:
                    gif_matches.extend(GIF_SERVICES_REGEX.findall(emb.url))

                # Проверяем заголовок и описание встроенного GIF/клипа
                embed_texts = [t for t in [emb.title, emb.description] if t]
                for text in embed_texts:
                    clean_text = text.lower().replace("-", " ").replace("_", " ")
                    for kw in NSFW_SLUG_KEYWORDS:
                        if re.search(r"\b" + re.escape(kw) + r"\b", clean_text, re.UNICODE):
                            is_nsfw = True
                            nsfw_reason = f"Обнаружен NSFW GIF/Клип Klipy/Tenor (тег: '{kw}')"
                            break
                    if is_nsfw:
                        break
                if is_nsfw:
                    break

            # Сканируем слоги найденных ссылок
            if not is_nsfw:
                for slug in gif_matches:
                    decoded_slug = urllib.parse.unquote(slug)
                    slug_clean = decoded_slug.lower().replace("-", " ").replace("_", " ")
                    for kw in NSFW_SLUG_KEYWORDS:
                        if re.search(r"\b" + re.escape(kw) + r"\b", slug_clean, re.UNICODE):
                            is_nsfw = True
                            nsfw_reason = f"Обнаружен NSFW GIF (тег: '{kw}')"
                            break
                    if is_nsfw:
                        break

            # 6.2 Прямой покадровый анализ прикрепленных файлов (GIF, картинки) локальной нейросетью
            if not is_nsfw and message.attachments:
                for att in message.attachments:
                    is_image_file = (att.content_type and att.content_type.startswith("image/")) or \
                                    any(att.filename.lower().endswith(ext) for ext in [".png", ".jpg", ".jpeg", ".webp", ".gif"])
                    if is_image_file:
                        try:
                            file_data = await att.read()
                            is_gif = att.filename.lower().endswith(".gif") or (att.content_type and "gif" in att.content_type)
                            flagged, score, reason = await asyncio.to_thread(detector.analyze_bytes, file_data, is_gif, 5)
                            if flagged:
                                is_nsfw = True
                                nsfw_reason = reason
                                break
                        except Exception as e:
                            print(f"⚠️ [AutoMod] Ошибка при чтении файла {att.filename}: {e}")

            # 6.3 Анализ внешних ссылок и встроенных GIF/картинок (Klipy, Tenor, Giphy, direct URLs) нейросетью
            if not is_nsfw:
                target_urls = []
                for url_match in IMAGE_URL_REGEX.findall(message.content):
                    target_urls.append(url_match)

                # Собираем медиа из эмбедов (Discord создает их для Klipy / Tenor)
                for emb in message.embeds:
                    if emb.image and emb.image.url:
                        target_urls.append(emb.image.url)
                    elif emb.thumbnail and emb.thumbnail.url:
                        target_urls.append(emb.thumbnail.url)
                    elif emb.video and emb.video.url and emb.video.url.endswith((".gif", ".png", ".jpg", ".webp")):
                        target_urls.append(emb.video.url)

                if target_urls:
                    if not self.session or self.session.closed:
                        self.session = aiohttp.ClientSession()

                    for img_url in target_urls[:3]:  # проверяем до 3 медиа
                        try:
                            async with self.session.get(img_url, timeout=aiohttp.ClientTimeout(total=4)) as resp:
                                if resp.status == 200:
                                    data = await resp.read()
                                    is_gif = ".gif" in img_url.lower() or "tenor" in img_url.lower() or "klipy" in img_url.lower() or "giphy" in img_url.lower()
                                    flagged, score, reason = await asyncio.to_thread(detector.analyze_bytes, data, is_gif, 5)
                                    if flagged:
                                        is_nsfw = True
                                        nsfw_reason = reason
                                        break
                        except Exception:
                            pass

            if is_nsfw:
                try:
                    await message.delete()
                except discord.DiscordException:
                    pass

                await self.log_violation(
                    member, "Удаление NSFW (18+)", f"NSFW контент: {nsfw_reason}",
                    message.channel, color=discord.Color.dark_red()
                )
                try:
                    await message.channel.send(
                        f"⛔ {member.mention}, публикация контента 18+ (NSFW) в этом канале запрещена!",
                        delete_after=6
                    )
                except discord.DiscordException:
                    pass
                return


async def setup(bot: commands.Bot):
    await bot.add_cog(AutoMod(bot))
