from __future__ import annotations

import asyncio
from datetime import datetime
from typing import TYPE_CHECKING
from urllib.parse import quote_plus

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands

from api.log import log_exception

if TYPE_CHECKING:
    from core.amenity import Amenity


class Github(commands.Cog):
    display_name = "GitHub"
    group_name = "Utilities"

    def __init__(self, bot: Amenity) -> None:
        self.bot = bot
        self.aiohttp = aiohttp.ClientSession()

    def cog_unload(self) -> None:
        if not self.aiohttp.closed:
            self.bot.loop.create_task(self.aiohttp.close())

    async def _fetch_json(self, url: str) -> tuple[dict | None, int | None]:
        try:
            async with self.aiohttp.get(url, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                status = resp.status
                if status != 200:
                    return None, status
                return await resp.json(), status
        except (TimeoutError, aiohttp.ClientError):
            return None, None
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log_exception(exc)
            return None, None

    @commands.hybrid_group(
        name="github",
        description="Github related commands.",
        aliases=["gh"],
        invoke_without_command=True,
    )
    @app_commands.allowed_installs(guilds=True, users=True)
    @app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    @commands.cooldown(1, 10, commands.BucketType.user)
    @commands.max_concurrency(20, commands.BucketType.default, wait=True)
    async def github(self, ctx: commands.Context) -> None:
        prefix = ctx.prefix or "/"
        cmds = "\n".join(
            [
                f"{prefix}github {cmd.name} - {cmd.description}"
                for cmd in self.github.walk_commands()
                if cmd.description
            ]
        )
        if not cmds:
            cmds = "No commands available."
        embed = discord.Embed(
            description="GitHub available commands:",
            color=discord.Color.light_grey(),
            timestamp=datetime.now(),
        )
        embed.add_field(name="Commands", value=f"```{cmds}```", inline=False)
        await ctx.reply(embed=embed, mention_author=False)

    @github.command(
        name="user",
        description="Gets the profile of a GitHub user.",
        aliases=["profile", "pf"],
    )
    async def github_user(self, ctx: commands.Context, user: str) -> None:
        query = user.strip()
        if not query:
            await ctx.send("Please provide a GitHub username.")
            return

        data, status = await self._fetch_json(f"https://api.github.com/users/{quote_plus(query)}")
        if not data or status != 200:
            await ctx.send("User not found!")
            return

        embed = discord.Embed(
            title=f"GitHub: {data.get('login', query)}",
            description=f"**Bio:** {data.get('bio') or 'N/A'}",
            color=discord.Color.dark_grey(),
        )
        if data.get("avatar_url"):
            embed.set_thumbnail(url=data["avatar_url"])
        embed.add_field(
            name="Username 📛:",
            value=f"[{data.get('name') or data.get('login')}]({data.get('html_url')})",
            inline=True,
        )
        embed.add_field(name="Repos 📁:", value=str(data.get("public_repos", 0)), inline=True)
        embed.add_field(name="Location 📍:", value=data.get("location") or "N/A", inline=True)
        embed.add_field(name="Company 🏢:", value=data.get("company") or "N/A", inline=True)
        embed.add_field(name="Followers 👥:", value=str(data.get("followers", 0)), inline=True)
        embed.add_field(name="Website 🖥️:", value=data.get("blog") or "N/A", inline=True)
        await ctx.reply(embed=embed, mention_author=False)

    @github.command(
        name="repo",
        description="Search GitHub repositories.",
        aliases=["repository", "searchrepo", "reposearch", "search"],
    )
    async def reposearch(self, ctx: commands.Context, *, query: str) -> None:
        try:
            api_url = (
                "https://api.github.com/search/repositories"
                f"?q={quote_plus(query)}&sort=stars&order=desc&per_page=10"
            )
            data, status = await self._fetch_json(api_url)
            if not data or status != 200:
                await ctx.send("Failed to fetch repository info from GitHub.")
                return

            items = data.get("items", [])
            if not items:
                await ctx.send(f"No repositories found for `{query}`.")
                return

            def make_embed(repo: dict) -> discord.Embed:
                embed = discord.Embed(
                    title=repo["full_name"],
                    url=repo["html_url"],
                    description=repo.get("description") or "No description provided.",
                    color=discord.Color.dark_grey(),
                )
                owner = repo.get("owner") or {}
                if owner.get("avatar_url"):
                    embed.set_thumbnail(url=owner["avatar_url"])
                embed.add_field(
                    name="Stars ⭐",
                    value=str(repo.get("stargazers_count", 0)),
                    inline=True,
                )
                embed.add_field(
                    name="Forks 🍴",
                    value=str(repo.get("forks_count", 0)),
                    inline=True,
                )
                embed.add_field(
                    name="Language",
                    value=repo.get("language") or "N/A",
                    inline=True,
                )
                embed.add_field(
                    name="Open Issues",
                    value=str(repo.get("open_issues_count", 0)),
                    inline=True,
                )
                license_info = repo.get("license")
                embed.add_field(
                    name="License",
                    value=license_info.get("name") if license_info else "No license",
                    inline=True,
                )
                embed.add_field(
                    name="Default Branch",
                    value=repo.get("default_branch") or "main",
                    inline=True,
                )
                if owner.get("login"):
                    embed.set_footer(text=f"Owner: {owner['login']}")
                return embed

            class RepoPaginator(discord.ui.View):
                def __init__(self, repos: list[dict]) -> None:
                    super().__init__(timeout=60)
                    self.repos = repos
                    self.index = 0
                    self.message: discord.Message | None = None
                    self.previous_button = discord.ui.Button(
                        label="Previous", style=discord.ButtonStyle.secondary
                    )
                    self.next_button = discord.ui.Button(
                        label="Next", style=discord.ButtonStyle.secondary
                    )
                    self.previous_button.callback = self.previous_callback
                    self.next_button.callback = self.next_callback
                    self.add_item(self.previous_button)
                    self.add_item(self.next_button)

                async def previous_callback(self, interaction: discord.Interaction) -> None:
                    if self.index > 0:
                        self.index -= 1
                        embed = make_embed(self.repos[self.index])
                        await interaction.response.edit_message(embed=embed, view=self)
                    else:
                        await interaction.response.defer()

                async def next_callback(self, interaction: discord.Interaction) -> None:
                    if self.index < len(self.repos) - 1:
                        self.index += 1
                        embed = make_embed(self.repos[self.index])
                        await interaction.response.edit_message(embed=embed, view=self)
                    else:
                        await interaction.response.defer()

                async def on_timeout(self) -> None:
                    self.previous_button.disabled = True
                    self.next_button.disabled = True
                    if self.message:
                        await self.message.edit(view=self)

            view = RepoPaginator(items)
            embed = make_embed(items[0])
            message = await ctx.reply(embed=embed, view=view, mention_author=False)
            view.message = message

        except Exception as exc:
            await ctx.send("An error occurred while searching for repositories")
            log_exception(exc)


async def setup(bot: Amenity) -> None:
    await bot.add_cog(Github(bot))
