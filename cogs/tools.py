from __future__ import annotations

import io
from pathlib import Path
from typing import TYPE_CHECKING

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands
from PIL import Image, ImageDraw, ImageOps

from api.log import log_exception

if TYPE_CHECKING:
    from collections.abc import Callable

    from core.amenity import Amenity

STROKE_SIZE = 5


def add_stroke_to_avatar(
    image_bytes: bytes,
    output_path: str,
    *,
    stroke_size: int = STROKE_SIZE,
) -> bool:
    """Crop avatar to a circle with a white border on a transparent background."""
    try:
        SCALE = 4
        img = Image.open(io.BytesIO(image_bytes)).convert("RGBA")

        size = max(img.width, img.height)
        square = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        square.paste(img, ((size - img.width) // 2, (size - img.height) // 2))

        canvas_size = size + stroke_size * 2
        ss = canvas_size * SCALE
        ss_size = size * SCALE
        ss_stroke = stroke_size * SCALE

        big = Image.new("RGBA", (ss, ss), (0, 0, 0, 0))
        draw = ImageDraw.Draw(big)
        draw.ellipse((0, 0, ss, ss), fill="white")

        mask = Image.new("L", (ss_size, ss_size), 0)
        draw_mask = ImageDraw.Draw(mask)
        draw_mask.ellipse((0, 0, ss_size, ss_size), fill=255)

        square_big = square.resize((ss_size, ss_size), Image.LANCZOS)
        big.paste(square_big, (ss_stroke, ss_stroke), mask)

        result = big.resize((canvas_size, canvas_size), Image.LANCZOS)
        result.save(output_path, "PNG")
        return True
    except Exception as exc:
        log_exception(exc)
        return False


def rotate_avatar(
    image_bytes: bytes,
    output_path: str,
    angle: int,
) -> bool:
    """Rotate an avatar image by the given angle."""
    try:
        img = Image.open(io.BytesIO(image_bytes)).convert("RGBA")
        rotated = img.rotate(angle, expand=True, resample=Image.LANCZOS)
        rotated.save(output_path, "PNG")
        return True
    except Exception as exc:
        log_exception(exc)
        return False


def invert_image(
    image_bytes: bytes,
    output_path: str,
) -> bool:
    """Invert the colours of an image."""
    try:
        img = Image.open(io.BytesIO(image_bytes)).convert("RGBA")
        r, g, b, a = img.split()
        rgb = Image.merge("RGB", (r, g, b))
        inverted = ImageOps.invert(rgb)
        inverted.putalpha(a)
        inverted.save(output_path, "PNG")
        return True
    except Exception as exc:
        log_exception(exc)
        return False


class Tools(commands.Cog):
    display_name = "Tools"
    group_name = "Utilities"

    def __init__(self, bot: Amenity) -> None:
        self.bot = bot
        self.aiohttp = aiohttp.ClientSession()

    def cog_unload(self) -> None:
        if not self.aiohttp.closed:
            self.bot.loop.create_task(self.aiohttp.close())

    @commands.hybrid_group(
        name="image",
        invoke_without_command=True,
        with_app_command=True,
        description="Various image manipulation tools.",
    )
    @commands.cooldown(1, 10, commands.BucketType.user)
    @commands.max_concurrency(5, commands.BucketType.default, wait=True)
    @app_commands.allowed_installs(guilds=True, users=True)
    @app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    async def image_group(self, ctx: commands.Context) -> None:
        await ctx.reply(
            "Use /image add-stroke | rotate-right | rotate-left | rotate-up | rotate-down | invert",
            delete_after=5,
            ephemeral=True,
            mention_author=False,
        )

    @image_group.command(
        name="add-stroke",
        description="Add a white outline on the profile picture of selected users.",
    )
    @app_commands.describe(
        user="The user whose profile picture to add the stroke to.",
    )
    @commands.cooldown(1, 10, commands.BucketType.user)
    async def image_add_stroke(
        self,
        ctx: commands.Context,
        user: discord.User | None = None,
    ) -> None:
        target = user or ctx.author
        avatar_url = target.display_avatar.with_format("png").with_size(512)
        output = Path(f"stroke_{target.id}.png")

        await ctx.defer()

        try:
            async with self.aiohttp.get(str(avatar_url)) as resp:
                if resp.status != 200:
                    await ctx.send("Failed to fetch the avatar.")
                    return
                avatar_bytes = await resp.read()

            if not add_stroke_to_avatar(avatar_bytes, str(output)):
                await ctx.send("Failed to process the image.")
                return

            embed = discord.Embed(
                title=f"Stroked Avatar: {target}",
                color=discord.Color.onyx_embed()
            )
            embed.set_image(url=f"attachment://{output.name}")
            embed.set_footer(
                text=f"Requested by {ctx.author}",
                icon_url=ctx.author.display_avatar.url,
            )
            await ctx.send(embed=embed, file=discord.File(str(output)))
        except Exception as exc:
            log_exception(exc)
            await ctx.send("An error occurred while processing the image.")
        finally:
            output.unlink(missing_ok=True)

    async def _process_avatar(
        self,
        ctx: commands.Context,
        user: discord.User | None,
        process_fn: Callable[[bytes, str], bool],
        filename_prefix: str,
        embed_title: str,
    ) -> None:
        target = user or ctx.author
        avatar_url = target.display_avatar.with_format("png").with_size(512)
        output = Path(f"{filename_prefix}_{target.id}.png")

        await ctx.defer()

        try:
            async with self.aiohttp.get(str(avatar_url)) as resp:
                if resp.status != 200:
                    await ctx.send("Failed to fetch the avatar.")
                    return
                avatar_bytes = await resp.read()

            if not process_fn(avatar_bytes, str(output)):
                await ctx.send("Failed to process the image.")
                return

            embed = discord.Embed(
                title=f"{embed_title}: {target}",
                color=discord.Color.onyx_embed(),
            )
            embed.set_image(url=f"attachment://{output.name}")
            embed.set_footer(
                text=f"Requested by {ctx.author}",
                icon_url=ctx.author.display_avatar.url,
            )
            await ctx.send(embed=embed, file=discord.File(str(output)))
        except Exception as exc:
            log_exception(exc)
            await ctx.send("An error occurred while processing the image.")
        finally:
            output.unlink(missing_ok=True)

    @image_group.command(
        name="rotate-right",
        description="Rotate the profile picture 90 degrees clockwise.",
    )
    @app_commands.describe(
        user="The user whose profile picture to rotate.",
    )
    @commands.cooldown(1, 10, commands.BucketType.user)
    async def image_rotate_right(
        self,
        ctx: commands.Context,
        user: discord.User | None = None,
    ) -> None:
        await self._process_avatar(
            ctx, user,
            lambda b, p: rotate_avatar(b, p, -90),
            "rotate-right",
            "Rotated Right",
        )

    @image_group.command(
        name="rotate-left",
        description="Rotate the profile picture 90 degrees counter-clockwise.",
    )
    @app_commands.describe(
        user="The user whose profile picture to rotate.",
    )
    @commands.cooldown(1, 10, commands.BucketType.user)
    async def image_rotate_left(
        self,
        ctx: commands.Context,
        user: discord.User | None = None,
    ) -> None:
        await self._process_avatar(
            ctx, user,
            lambda b, p: rotate_avatar(b, p, 90),
            "rotate-left",
            "Rotated Left",
        )

    @image_group.command(
        name="rotate-up",
        description="Rotate the profile picture upside down.",
    )
    @app_commands.describe(
        user="The user whose profile picture to rotate.",
    )
    @commands.cooldown(1, 10, commands.BucketType.user)
    async def image_rotate_up(
        self,
        ctx: commands.Context,
        user: discord.User | None = None,
    ) -> None:
        await self._process_avatar(
            ctx, user,
            lambda b, p: rotate_avatar(b, p, 180),
            "rotate-up",
            "Rotated Up",
        )

    @image_group.command(
        name="rotate-down",
        description="Rotate the profile picture upside down.",
    )
    @app_commands.describe(
        user="The user whose profile picture to rotate.",
    )
    @commands.cooldown(1, 10, commands.BucketType.user)
    async def image_rotate_down(
        self,
        ctx: commands.Context,
        user: discord.User | None = None,
    ) -> None:
        await self._process_avatar(
            ctx, user,
            lambda b, p: rotate_avatar(b, p, 180),
            "rotate-down",
            "Rotated Down",
        )


    @image_group.command(
        name="invert",
        description="Invert the colours of an image.",
    )
    @app_commands.describe(
        image="The image to invert.",
    )
    @commands.cooldown(1, 10, commands.BucketType.user)
    async def image_invert(
        self,
        ctx: commands.Context,
        image: discord.Attachment,
    ) -> None:
        output = Path(f"invert_{ctx.author.id}.png")

        await ctx.defer()

        try:
            image_bytes = await image.read()

            if not invert_image(image_bytes, str(output)):
                await ctx.send("Failed to process the image.")
                return

            embed = discord.Embed(
                title="Inverted Image",
                color=discord.Color.onyx_embed(),
            )
            embed.set_image(url=f"attachment://{output.name}")
            embed.set_footer(
                text=f"Requested by {ctx.author}",
                icon_url=ctx.author.display_avatar.url,
            )
            await ctx.send(embed=embed, file=discord.File(str(output)))
        except Exception as exc:
            log_exception(exc)
            await ctx.send("An error occurred while processing the image.")
        finally:
            output.unlink(missing_ok=True)


async def setup(bot: Amenity) -> None:
    await bot.add_cog(Tools(bot))
