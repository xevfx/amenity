import os
import asyncio
from discord.ext import commands
import discord
import logging

logger = logging.getLogger(__name__)

os.environ["JISHAKU_HIDE"] = "True"
os.environ["JISHAKU_NO_UNDERSCORE"] = "True"
os.environ["JISHAKU_FORCE_PAGINATOR"] = "True"

class Amenity(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True
        intents.members = True
        intents.guilds = True
        intents.messages = True
        intents.dm_messages = True
        super().__init__(
            command_prefix="",
            intents=intents,
            case_insensitive=True,
            help_command=None,
            strip_after_prefix=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )


    async def on_connect(self):
        """Called when bot connects to Discord gateway."""
        await self.change_presence(
            status=discord.Status.idle,
            activity=discord.Activity(
                type=discord.ActivityType.listening,
                name="/help"
            )
        )

    async def setup_hook(self):
        guild_id: int = os.getenv("GUILD_ID")
        if guild_id:
            guild = discord.Object(id=int(guild_id))
            self.tree.copy_global_to(guild=guild)
            await self.tree.sync(guild=guild)
        else:
            await self.tree.sync()

    async def on_ready(self):
        # if not self.user:
        #     return
        # app_info = await self.application_info()
        # install_scope = "users" if app_info.install_params and app_info.install_params.scopes else "unknown"
        # print(f"Logged in as {self.user} (ID: {self.user.id}) | install scope: {install_scope}")
        logger.info(f"[+] | LOGGED IN AS {self.user}")
        logger.info(f"[+] | WATCHING {self.users}")


    async def on_command_error(self, context, exception):
        if isinstance(exception, commands.CommandNotFound):
            return
        raise exception


    async def invoke_help_command(self, ctx: commands.Context) -> None:
        """Send help for the current command."""
        await ctx.send_help(ctx.command)