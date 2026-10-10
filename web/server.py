import os
import time
import json
import base64
import hmac
import hashlib
import logging
from typing import Optional

import aiohttp
from aiohttp import web
import jinja2
import discord
from discord.ext import commands

from database import get_guild_settings, set_log_channel, set_autorole, set_feature_toggle
from cloud_sync import sync_guild_settings_to_discord

logger = logging.getLogger("Dashboard")

TEMPLATES_DIR = os.path.join(os.path.dirname(__file__), "templates")
jinja_env = jinja2.Environment(
    loader=jinja2.FileSystemLoader(TEMPLATES_DIR),
    autoescape=jinja2.select_autoescape(["html", "xml"])
)

# Секретный ключ для подписи сессионных кук (HMAC-SHA256)
COOKIE_SECRET = os.getenv("DASHBOARD_SECRET_KEY", "shieldguard-secure-secret-key-2026").encode()
COOKIE_NAME = "sg_session"


def get_client_id(bot: commands.Bot) -> str:
    """Извлекает Client ID бота из переменных или декодирует из токена."""
    env_id = os.getenv("DISCORD_CLIENT_ID")
    if env_id and env_id.strip():
        return env_id.strip()

    if bot.user:
        return str(bot.user.id)

    token = os.getenv("DISCORD_TOKEN", "")
    if token and "." in token:
        try:
            first_part = token.split(".")[0]
            decoded = base64.b64decode(first_part + "==").decode("utf-8")
            if decoded.isdigit():
                return decoded
        except Exception:
            pass
    return "1551930795210575933"


def get_redirect_uri(request: web.Request) -> str:
    """Определяет корректный Redirect URI с учетом HTTPS-прокси на Render."""
    env_uri = os.getenv("DISCORD_REDIRECT_URI")
    if env_uri and env_uri.strip():
        return env_uri.strip()

    proto = request.headers.get("X-Forwarded-Proto", request.scheme)
    host = request.headers.get("X-Forwarded-Host", request.host)
    return f"{proto}://{host}/callback"


def sign_session(data: dict) -> str:
    """Создает подписанную сессионную куку: base64(json) . hmac_hex."""
    payload_bytes = json.dumps(data).encode("utf-8")
    b64 = base64.urlsafe_b64encode(payload_bytes).decode("ascii")
    sig = hmac.new(COOKIE_SECRET, b64.encode("ascii"), hashlib.sha256).hexdigest()
    return f"{b64}.{sig}"


def verify_session(cookie_str: Optional[str]) -> Optional[dict]:
    """Проверяет подлинность сессионной куки."""
    if not cookie_str or "." not in cookie_str:
        return None
    try:
        b64, sig = cookie_str.split(".", 1)
        expected_sig = hmac.new(COOKIE_SECRET, b64.encode("ascii"), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(sig, expected_sig):
            return None

        payload_bytes = base64.urlsafe_b64decode(b64.encode("ascii"))
        data = json.loads(payload_bytes.decode("utf-8"))

        # Проверка срока жизни сессии (7 дней)
        if time.time() > data.get("exp", 0):
            return None
        return data
    except Exception:
        return None


class DashboardServer:
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.app = web.Application()
        self.session: Optional[aiohttp.ClientSession] = None
        self._setup_routes()

    def _setup_routes(self):
        self.app.router.add_get("/health", self.handle_health)
        self.app.router.add_get("/", self.handle_index)
        self.app.router.add_get("/login", self.handle_login)
        self.app.router.add_get("/callback", self.handle_callback)
        self.app.router.add_get("/logout", self.handle_logout)
        self.app.router.add_get("/dashboard/{guild_id}", self.handle_guild_dashboard)
        self.app.router.add_post("/api/guild/{guild_id}/settings", self.handle_api_save_settings)
        self.app.router.add_post("/api/guild/{guild_id}/sync", self.handle_api_sync)

    def _common_context(self, request: web.Request, user: Optional[dict] = None) -> dict:
        """Общие переменные для всех шаблонов."""
        avatar_url = None
        if self.bot.user and self.bot.user.display_avatar:
            avatar_url = self.bot.user.display_avatar.url

        import math
        latency = 0
        if self.bot.latency and not math.isnan(self.bot.latency):
            latency = round(self.bot.latency * 1000)

        client_secret = os.getenv("DISCORD_CLIENT_SECRET", "")
        return {
            "bot_avatar": avatar_url,
            "bot_latency": latency,
            "user": user,
            "client_id": get_client_id(self.bot),
            "oauth_configured": bool(client_secret and client_secret.strip()),
            "redirect_uri": get_redirect_uri(request)
        }

    async def handle_health(self, request: web.Request) -> web.Response:
        """Health-check эндпоинт для Render и UptimeRobot."""
        return web.Response(text="ShieldGuard Bot & Dashboard are healthy!", content_type="text/plain")

    async def handle_index(self, request: web.Request) -> web.Response:
        """Главная страница: лендинг или список серверов."""
        session_data = verify_session(request.cookies.get(COOKIE_NAME))
        ctx = self._common_context(request, user=session_data)

        admin_guilds = []
        if session_data and "guilds" in session_data:
            user_guilds = session_data.get("guilds", [])
            for g in user_guilds:
                perms = int(g.get("permissions", 0))
                is_owner = g.get("owner", False)
                has_admin = is_owner or ((perms & 0x8) != 0) or ((perms & 0x20) != 0)

                if has_admin:
                    guild_id = int(g["id"])
                    bot_guild = self.bot.get_guild(guild_id)
                    icon_url = f"https://cdn.discordapp.com/icons/{g['id']}/{g['icon']}.png" if g.get("icon") else None
                    admin_guilds.append({
                        "id": str(guild_id),
                        "name": g.get("name", "Сервер"),
                        "icon_url": icon_url,
                        "is_owner": is_owner,
                        "has_bot": bot_guild is not None
                    })

        ctx["admin_guilds"] = admin_guilds
        template = jinja_env.get_template("index.html")
        html = template.render(**ctx)
        return web.Response(text=html, content_type="text/html")

    async def handle_login(self, request: web.Request) -> web.Response:
        """Перенаправление на страницу авторизации Discord OAuth2."""
        client_secret = os.getenv("DISCORD_CLIENT_SECRET", "")
        if not client_secret or not client_secret.strip():
            # Если secret еще не настроен, направляем на главную с инструкцией
            return web.HTTPFound("/?oauth_error=no_secret")

        client_id = get_client_id(self.bot)
        redirect_uri = get_redirect_uri(request)
        scope = "identify guilds"

        oauth_url = (
            f"https://discord.com/oauth2/authorize?client_id={client_id}"
            f"&redirect_uri={redirect_uri}"
            f"&response_type=code&scope={scope.replace(' ', '%20')}"
            f"&prompt=none"
        )
        return web.HTTPFound(oauth_url)

    async def handle_callback(self, request: web.Request) -> web.Response:
        """Обработка ответа от Discord OAuth2."""
        code = request.query.get("code")
        if not code:
            error = request.query.get("error_description", "Авторизация отменена")
            return web.HTTPFound(f"/?error={error}")

        client_id = get_client_id(self.bot)
        client_secret = os.getenv("DISCORD_CLIENT_SECRET", "")
        redirect_uri = get_redirect_uri(request)

        # 1. Обмен кода на access_token
        token_url = "https://discord.com/api/v10/oauth2/token"
        payload = {
            "client_id": client_id,
            "client_secret": client_secret,
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri
        }

        try:
            if not self.session or self.session.closed:
                self.session = aiohttp.ClientSession()

            async with self.session.post(token_url, data=payload) as resp:
                if resp.status != 200:
                    err_body = await resp.text()
                    logger.error(f"OAuth token error: {resp.status} {err_body}")
                    return web.HTTPFound("/?error=token_exchange_failed")
                token_data = await resp.json()

            access_token = token_data.get("access_token")
            headers = {"Authorization": f"Bearer {access_token}"}

            # 2. Получение данных пользователя
            async with self.session.get("https://discord.com/api/v10/users/@me", headers=headers) as resp:
                if resp.status != 200:
                    return web.HTTPFound("/?error=user_fetch_failed")
                user_info = await resp.json()

            # 3. Получение списка серверов пользователя
            async with self.session.get("https://discord.com/api/v10/users/@me/guilds", headers=headers) as resp:
                if resp.status != 200:
                    guilds_info = []
                else:
                    guilds_info = await resp.json()

            # Аватарка пользователя
            user_id = user_info.get("id")
            avatar_hash = user_info.get("avatar")
            if avatar_hash:
                avatar_url = f"https://cdn.discordapp.com/avatars/{user_id}/{avatar_hash}.png"
            else:
                avatar_url = "https://cdn.discordapp.com/embed/avatars/0.png"

            # 4. Формирование сессии (7 дней)
            session_data = {
                "id": user_id,
                "username": user_info.get("global_name") or user_info.get("username"),
                "avatar_url": avatar_url,
                "guilds": guilds_info,
                "exp": time.time() + (7 * 86400)
            }

            cookie_val = sign_session(session_data)
            response = web.HTTPFound("/")
            response.set_cookie(
                COOKIE_NAME,
                cookie_val,
                max_age=7 * 86400,
                httponly=True,
                samesite="Lax"
            )
            return response

        except Exception as e:
            logger.error(f"Ошибка OAuth2 callback: {e}")
            return web.HTTPFound("/?error=oauth_internal_error")

    async def handle_logout(self, request: web.Request) -> web.Response:
        """Выход из учетной записи."""
        response = web.HTTPFound("/")
        response.del_cookie(COOKIE_NAME)
        return response

    async def _check_guild_access(self, request: web.Request, guild_id_str: str) -> tuple[Optional[dict], Optional[discord.Guild]]:
        """Проверяет права авторизованного пользователя на управление гильдией."""
        session_data = verify_session(request.cookies.get(COOKIE_NAME))
        if not session_data:
            return None, None

        if not guild_id_str.isdigit():
            return None, None

        guild_id = int(guild_id_str)
        bot_guild = self.bot.get_guild(guild_id)
        if not bot_guild:
            return None, None

        user_id = int(session_data.get("id", 0))

        # 1. Проверяем кэшированный список серверов из OAuth2
        has_permission = False
        for g in session_data.get("guilds", []):
            if int(g.get("id", 0)) == guild_id:
                perms = int(g.get("permissions", 0))
                if g.get("owner", False) or ((perms & 0x8) != 0) or ((perms & 0x20) != 0):
                    has_permission = True
                break

        # 2. Также проверяем прямое членство в объекте гильдии Discord
        if not has_permission:
            member = bot_guild.get_member(user_id)
            if member:
                if member.guild_permissions.administrator or member.guild_permissions.manage_guild or bot_guild.owner_id == user_id:
                    has_permission = True

        if not has_permission:
            return None, None

        return session_data, bot_guild

    async def handle_guild_dashboard(self, request: web.Request) -> web.Response:
        """Страница настроек конкретного сервера."""
        guild_id_str = request.match_info.get("guild_id", "")
        session_data, bot_guild = await self._check_guild_access(request, guild_id_str)

        if not session_data:
            return web.HTTPFound("/login")

        if not bot_guild:
            return web.HTTPFound("/?error=guild_not_found_or_forbidden")

        settings = await get_guild_settings(bot_guild.id)
        ctx = self._common_context(request, user=session_data)

        # Данные гильдии для шаблона
        icon_url = bot_guild.icon.url if bot_guild.icon else None
        roles = []
        for r in reversed(bot_guild.roles):
            if not r.is_default() and not r.managed:
                roles.append({
                    "id": r.id,
                    "name": r.name,
                    "color": str(r.color) if r.color.value != 0 else "#94a3b8"
                })

        text_channels = []
        for ch in bot_guild.text_channels:
            text_channels.append({
                "id": ch.id,
                "name": ch.name
            })

        ctx["guild"] = {
            "id": str(bot_guild.id),
            "name": bot_guild.name,
            "icon_url": icon_url,
            "member_count": bot_guild.member_count or len(bot_guild.members),
            "roles": roles,
            "text_channels": text_channels
        }
        ctx["settings"] = settings

        template = jinja_env.get_template("guild.html")
        html = template.render(**ctx)
        return web.Response(text=html, content_type="text/html")

    async def handle_api_save_settings(self, request: web.Request) -> web.Response:
        """AJAX API: сохранение настроек сервера."""
        guild_id_str = request.match_info.get("guild_id", "")
        session_data, bot_guild = await self._check_guild_access(request, guild_id_str)

        if not session_data or not bot_guild:
            return web.json_response({"success": False, "error": "Доступ запрещен"}, status=403)

        try:
            data = await request.json()
        except Exception:
            return web.json_response({"success": False, "error": "Неверный формат JSON"}, status=400)

        guild_id = bot_guild.id
        try:
            # 1. Автороль
            if "autorole_id" in data:
                role_id = int(data["autorole_id"])
                await set_autorole(guild_id, role_id)

            # 2. Канал логов
            if "log_channel_id" in data:
                channel_id = int(data["log_channel_id"])
                await set_log_channel(guild_id, channel_id)

            # 3. Тумблеры автомодерации
            toggles = [
                "anti_toxicity", "anti_nsfw", "anti_spam",
                "anti_invite", "anti_caps", "anti_mass_mention", "ignore_admins"
            ]
            for tog in toggles:
                if tog in data:
                    val = bool(int(data[tog]))
                    await set_feature_toggle(guild_id, tog, val)

            # 4. Фоновая синхронизация с облачным каналом Discord
            import asyncio
            asyncio.create_task(sync_guild_settings_to_discord(self.bot, bot_guild))

            logger.info(f"Настройки сервера {bot_guild.name} ({guild_id}) успешно обновлены через Web Dashboard")
            return web.json_response({"success": True})

        except Exception as e:
            logger.error(f"Ошибка сохранения настроек через веб-интерфейс: {e}")
            return web.json_response({"success": False, "error": str(e)}, status=500)

    async def handle_api_sync(self, request: web.Request) -> web.Response:
        """AJAX API: принудительная синхронизация в облачный канал Discord."""
        guild_id_str = request.match_info.get("guild_id", "")
        session_data, bot_guild = await self._check_guild_access(request, guild_id_str)

        if not session_data or not bot_guild:
            return web.json_response({"success": False, "error": "Доступ запрещен"}, status=403)

        ok = await sync_guild_settings_to_discord(self.bot, bot_guild)
        if ok:
            return web.json_response({"success": True})
        return web.json_response({"success": False, "error": "Не удалось создать или обновить служебный канал. Проверьте права бота."}, status=500)


async def start_web_server(bot: commands.Bot, port: int = 10000) -> web.AppRunner:
    """Запуск веб-сервера aiohttp в едином асинхронном цикле с ботом."""
    dashboard = DashboardServer(bot)
    runner = web.AppRunner(dashboard.app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    return runner
