import discord
import aiohttp
from discord import Webhook
import os
from datetime import datetime
from discord.ext import commands

async def UsageWebhook(embed: discord.Embed, hook= os.getenv("USAGE_HOOK")) -> bool:
    """
    Sends a discord.Embed object to a Discord channel via a webhook.

    Args:
        embed (discord.Embed): The embed object to send.

    Returns:
        bool: True if the log was sent successfully, False otherwise.
    """
    if not isinstance(embed, discord.Embed):
        print(f"Error: Provided 'embed' is not a discord.Embed object. Type: {type(embed)}")
        return False

    try:
        async with aiohttp.ClientSession() as session:
            webhook = Webhook.from_url(hook, session=session)
            await webhook.send(embed=embed)
        return True

    except Exception as e:
        print(f"An unexpected error occurred: {e}")
        return False
    

async def log_command_usage(ctx: commands.Context) -> None:
    """
    Log command usage for on_command_completion event.
    
    Args:
        ctx: Discord context object
    """
    try:
        channel = ctx.channel if ctx.guild else await ctx.author.create_dm()
        
        embed = discord.Embed(
            color=discord.Color.magenta(),
            timestamp=datetime.now()
        )
        if ctx.author.avatar:
            embed.set_author(
            name=f"{ctx.author} | {ctx.author.id}",
            icon_url=ctx.author.avatar.url)
        else:
            embed.set_author(
            name=f"{ctx.author} | {ctx.author.id}")

        embed.set_thumbnail(
            url=ctx.guild.icon.url if ctx.guild and ctx.guild.icon else None
        )

        embed.add_field(name="Command", value=str(ctx.command), inline=False)
        embed.add_field(
            name="Server",
            value=f"{ctx.guild.name} [`{ctx.guild.id}`]" if ctx.guild else "DM",
            inline=False
        )
        embed.add_field(
            name="Author",
            value=f"{ctx.author} [`{ctx.author.id}`]",
            inline=False
        )
        embed.add_field(
            name="Subcommand Called",
            value=str(ctx.invoked_subcommand) if ctx.invoked_subcommand else "None",
            inline=True
        )
        
        await UsageWebhook(embed)
        
    except Exception as e:
        print(f"Error logging command usage: {e}")