import asyncio
from urllib.parse import quote

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands

from api.log import log_exception
from core.amenity import Amenity


class Fun(commands.Cog):
    display_name = "Fun"
    group_name = "Fun"

    def __init__(self, bot: Amenity) -> None:
        self.bot = bot
        self.aiohttp = aiohttp.ClientSession()

    def cog_unload(self) -> None:
        if not self.aiohttp.closed:
            self.bot.loop.create_task(self.aiohttp.close())

    async def _fetch_json(self, url: str) -> tuple[dict | list | None, int | None]:
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

    def _format_definitions(
        self,
        meanings: list[dict],
        *,
        max_defs: int = 3,
    ) -> list[tuple[str, str]]:
        fields: list[tuple[str, str]] = []
        for meaning in meanings:
            part = meaning.get("partOfSpeech") or "Definition"
            definitions = meaning.get("definitions") or []
            lines: list[str] = []
            for index, definition in enumerate(definitions[:max_defs], start=1):
                text = definition.get("definition")
                if not text:
                    continue
                example = definition.get("example")
                if example:
                    lines.append(f"{index}. {text}\n> {example}")
                else:
                    lines.append(f"{index}. {text}")
            if lines:
                value = "\n".join(lines)
                fields.append((part.title(), value[:1000]))
        return fields

    @commands.hybrid_command(name="dictionary", description="Look up a word in the dictionary.")
    @app_commands.allowed_installs(guilds=True, users=True)
    @app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    @app_commands.describe(word="The word to define.")
    @commands.cooldown(1, 5, commands.BucketType.user)
    async def dictionary(self, ctx: commands.Context, *, word: str) -> None:
        query = word.strip()
        if not query:
            await ctx.send("Please provide a word to look up.")
            return

        url = f"https://api.dictionaryapi.dev/api/v2/entries/en/{quote(query)}"
        data, status = await self._fetch_json(url)
        if not data or status != 200 or not isinstance(data, list):
            await ctx.send(f"No definitions found for `{query}`.")
            return

        entry = data[0]
        title = entry.get("word", query).title()
        phonetic = entry.get("phonetic")
        meanings = entry.get("meanings") or []

        embed = discord.Embed(
            title=f"Dictionary: {title}",
            description=f"/{phonetic}/" if phonetic else None,
            color=discord.Color.blurple(),
        )
        for name, value in self._format_definitions(meanings):
            embed.add_field(name=name, value=value, inline=False)

        if not embed.fields:
            embed.description = embed.description or "No definitions available."

        embed.set_footer(text="Source: DictionaryAPI.dev")
        await ctx.send(embed=embed)

    @commands.hybrid_command(name="joke", description="Get a random joke.")
    @app_commands.allowed_installs(guilds=True, users=True)
    @app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    @commands.cooldown(1, 5, commands.BucketType.user)
    async def joke(self, ctx: commands.Context) -> None:
        url = "https://official-joke-api.appspot.com/jokes/random"
        data, status = await self._fetch_json(url)
        if not data or status != 200 or not isinstance(data, dict):
            await ctx.send("I couldn't fetch a joke right now. Try again later.")
            return

        setup = data.get("setup")
        punchline = data.get("punchline")
        if not setup or not punchline:
            await ctx.send("I couldn't fetch a joke right now. Try again later.")
            return

        embed = discord.Embed(
            title="Random Joke",
            description=f"{setup}\n\n**{punchline}**",
            color=discord.Color.green(),
        )
        await ctx.send(embed=embed)

    @commands.hybrid_command(name="catfact", description="Get a random cat fact.")
    @app_commands.allowed_installs(guilds=True, users=True)
    @app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    @commands.cooldown(1, 5, commands.BucketType.user)
    async def catfact(self, ctx: commands.Context) -> None:
        url = "https://catfact.ninja/fact"
        data, status = await self._fetch_json(url)
        if not data or status != 200 or not isinstance(data, dict):
            await ctx.send("I couldn't fetch a cat fact right now. Try again later.")
            return

        fact = data.get("fact")
        if not fact:
            await ctx.send("I couldn't fetch a cat fact right now. Try again later.")
            return

        embed = discord.Embed(
            title="Random Cat Fact",
            description=fact,
            color=discord.Color.orange(),
        )
        await ctx.send(embed=embed)

    @commands.hybrid_command(name="dogfact", description="Get a random dog fact.")
    @app_commands.allowed_installs(guilds=True, users=True)
    @app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    @commands.cooldown(1, 5, commands.BucketType.user)
    async def dogfact(self, ctx: commands.Context) -> None:
        url = "https://dogapi.dog/api/v2/facts"
        data, status = await self._fetch_json(url)
        if not data or status != 200 or not isinstance(data, dict):
            await ctx.send("I couldn't fetch a dog fact right now. Try again later.")
            return

        facts = data.get("data")
        fact = None
        if isinstance(facts, list) and facts:
            attributes = facts[0].get("attributes") if isinstance(facts[0], dict) else None
            if isinstance(attributes, dict):
                fact = attributes.get("body")

        if not fact:
            await ctx.send("I couldn't fetch a dog fact right now. Try again later.")
            return

        embed = discord.Embed(
            title="Random Dog Fact",
            description=fact,
            color=discord.Color.blue(),
        )
        await ctx.send(embed=embed)


async def setup(bot: Amenity) -> None:
    await bot.add_cog(Fun(bot))
