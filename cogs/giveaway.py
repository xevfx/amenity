import datetime
import sqlite3
import random
import json
import discord
from discord.ext import commands, tasks
from discord import app_commands
import aiosqlite

# Database setup
connection = sqlite3.connect('data/giveaways.db')
cursor = connection.cursor()
cursor.execute('''CREATE TABLE IF NOT EXISTS Giveaway (
                    guild_id INTEGER,
                    host_id INTEGER,
                    start_time TIMESTAMP,
                    ends_at TIMESTAMP,
                    prize TEXT,
                    winners INTEGER,
                    message_id INTEGER,
                    channel_id INTEGER,
                    participants_json TEXT,
                    PRIMARY KEY (message_id)
                )''')
connection.commit()
connection.close()


class GiveawayJoinView(discord.ui.View):
    """Persistent view for users to click and join a giveaway."""
    def __init__(self, bot: commands.Bot) -> None:
        super().__init__(timeout=None)
        self.bot = bot
        # Track participants dynamically in memory: {message_id: set(user_ids)}
        self.participants: dict[int, set[int]] = {}

    @discord.ui.button(label="0", style=discord.ButtonStyle.primary, custom_id="join_giveaway_btn", emoji="🎉")
    async def join_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        msg_id = interaction.message.id
        user_id = interaction.user.id

        if msg_id not in self.participants:
            self.participants[msg_id] = set()

        if user_id in self.participants[msg_id]:
            self.participants[msg_id].remove(user_id)
            await interaction.response.send_message("You left the giveaway.", ephemeral=True)
        else:
            self.participants[msg_id].add(user_id)
            await interaction.response.send_message("You entered the giveaway! 🎉", ephemeral=True)

        # Update button interface label dynamically
        button.label = str(len(self.participants[msg_id]))
        await interaction.message.edit(view=self)


class UserAppGiveaway(commands.Cog):
    display_name = "Giveaway"
    group_name = "Utilities"
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.join_view = GiveawayJoinView(bot)

    async def cog_load(self) -> None:
        self.connection = await aiosqlite.connect('data/giveaways.db')
        self.cursor = await self.connection.cursor()
        self.bot.add_view(self.join_view)  # Makes the button view persistent across bot reboots
        self.GiveawayEnd.start()

    async def cog_unload(self) -> None:
        await self.connection.close()
        self.GiveawayEnd.cancel()

    def convert_time(self, time_str: str) -> int:
        pos = ["s", "m", "h", "d"]
        time_dict = {"s": 1, "m": 60, "h": 3600, "d": 86400}
        unit = time_str[-1]
        if unit not in pos:
            return -1
        try:
            val = int(time_str[:-1])
        except ValueError:
            return -2
        return val * time_dict[unit]

    @commands.hybrid_command(name="gstart", description="Starts a new button-based giveaway.")
    @app_commands.describe(time="Duration (e.g., 10m, 5h, 1d)", winners="Number of winners", prize="Item up for grabs")
    @app_commands.allowed_installs(guilds=True, users=True)
    @app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    @commands.cooldown(1, 5, commands.BucketType.user)
    async def gstart(self, ctx: commands.Context, time: str, winners: int, *, prize: str) -> None:
        
        if winners > 50 or winners <= 0:
            await ctx.send("⚠️ Winners must be between 1 and 50.", ephemeral=True)
            return

        converted = self.convert_time(time)
        if converted == -1 or converted == -2:
            await ctx.send("⚠️ Invalid time format. Use variations like: `10m`, `5h`, `1d`.", ephemeral=True)
            return
        if converted > 2678400:  # 31 days max
            await ctx.send("⚠️ Time cannot exceed 31 days!", ephemeral=True)
            return

        guild_id = ctx.guild.id if ctx.guild else None
        
        # Limit running giveaways per scope
        await self.cursor.execute("SELECT message_id FROM Giveaway WHERE guild_id IS ?", (guild_id,))
        running = await self.cursor.fetchall()
        if len(running) >= 10:
            await ctx.send("⚠️ The maximum limit of active giveaways has been reached.", ephemeral=True)
            return

        ends_timestamp = datetime.datetime.now().timestamp() + converted
        ends_utc = datetime.datetime.fromtimestamp(ends_timestamp, tz=datetime.timezone.utc)

        embed = discord.Embed(
            description=f"Winner(s): **{winners}**\nClick the 🎉 button below to participate!\nEnds <t:{round(ends_timestamp)}:R> (<t:{round(ends_timestamp)}:f>)\n\nHosted by {ctx.author.mention}", 
            color=0x2f3136
        )
        embed.timestamp = ends_utc
        embed.set_author(name=prize)
        embed.set_footer(text="Ends at")

        self.join_view.children[0].label = "0"

        message = await ctx.send("🎁 **GIVEAWAY** 🎁", embed=embed, view=self.join_view)
        
        self.join_view.participants[message.id] = set()

        await self.cursor.execute(
            "INSERT INTO Giveaway(guild_id, host_id, start_time, ends_at, prize, winners, message_id, channel_id, participants_json) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)", 
            (guild_id, ctx.author.id, datetime.datetime.now(), ends_timestamp, prize, winners, message.id, ctx.channel.id, "[]")
        )
        await self.connection.commit()

    @tasks.loop(seconds=5)
    async def GiveawayEnd(self) -> None:
        await self.cursor.execute("SELECT ends_at, guild_id, message_id, host_id, winners, prize, channel_id FROM Giveaway")
        all_giveaways = await self.cursor.fetchall()
        current_time = datetime.datetime.now().timestamp()

        for giveaway in all_giveaways:
            ends_at, guild_id, message_id, host_id, winners_count, prize, channel_id = giveaway
            
            if int(current_time) >= round(float(ends_at)):
                channel = self.bot.get_channel(int(channel_id))
                if not channel:
                    try:
                        channel = await self.bot.fetch_channel(int(channel_id))
                    except discord.HTTPException:
                        await self.cursor.execute("DELETE FROM Giveaway WHERE message_id = ?", (int(message_id),))
                        continue
                
                try:
                    message = await channel.fetch_message(int(message_id))
                except discord.NotFound:
                    await self.cursor.execute("DELETE FROM Giveaway WHERE message_id = ?", (int(message_id),))
                    continue

                pool = list(self.join_view.participants.get(message.id, set()))
                participants_json = json.dumps(pool)

                if len(pool) < 1:
                    await message.reply(f"No one won the **{prize}** giveaway, due to a lack of participants. 😕")
                    await self.cursor.execute("DELETE FROM Giveaway WHERE message_id = ?", (message.id,))
                    continue

                actual_winners = min(len(pool), int(winners_count))
                selected = random.sample(pool, k=actual_winners)
                winner_mentions = ', '.join(f'<@!{uid}>' for uid in selected)

                embed = discord.Embed(
                    description=f"Ended <t:{int(current_time)}:R>\nHosted by <@{int(host_id)}>\nWinner(s): {winner_mentions}",
                    color=0x2f3136
                )
                embed.timestamp = discord.utils.utcnow()
                embed.set_author(name=prize)
                embed.set_footer(text="Ended at")

                try:
                    await message.edit(content=f"🎁 **GIVEAWAY ENDED** 🎁\n> {winner_mentions} 🎉 Congratulations! You won **{prize}**!", embed=embed, view=None)
                except discord.HTTPException:
                    pass

                # Store the user pool snapshot into DB before closing out, so we can run rerolls later
                await self.cursor.execute("UPDATE Giveaway SET participants_json = ?, ends_at = 0 WHERE message_id = ?", (participants_json, message.id))
                self.join_view.participants.pop(message.id, None)
        
        await self.connection.commit()

    @commands.hybrid_command(name="gend", description="Ends an active giveaway early.")
    @app_commands.allowed_installs(guilds=True, users=True)
    @app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    async def gend(self, ctx: commands.Context, message_id: str) -> None:
        try:
            msg_id = int(message_id)
        except ValueError:
            await ctx.send("⚠️ Invalid message ID format.", ephemeral=True)
            return

        await self.cursor.execute("SELECT prize, host_id, winners, channel_id FROM Giveaway WHERE message_id = ?", (msg_id,))
        re = await self.cursor.fetchone()
        if not re:
            await ctx.send("⚠️ Active giveaway matching this ID was not found.", ephemeral=True)
            return

        prize, host_id, winners_count, channel_id = re
        
        # Perms Check: Original host OR User has Manage Channels permission in server
        is_host = ctx.author.id == int(host_id)
        is_admin = ctx.guild and ctx.author.guild_permissions.manage_channels
        
        if not (is_host or is_admin):
            await ctx.send("❌ Only the giveaway host or an administrator can end this giveaway.", ephemeral=True)
            return

        channel = self.bot.get_channel(int(channel_id)) or await self.bot.fetch_channel(int(channel_id))
        message = await channel.fetch_message(msg_id)

        pool = list(self.join_view.participants.get(msg_id, set()))
        if len(pool) < 1:
            await message.reply(f"No one won the **{prize}** giveaway, due to a lack of participants. 😕")
            await self.cursor.execute("DELETE FROM Giveaway WHERE message_id = ?", (msg_id,))
            await self.connection.commit()
            await ctx.send("✅ Ended giveaway.", ephemeral=True)
            return

        actual_winners = min(len(pool), int(winners_count))
        selected = random.sample(pool, k=actual_winners)
        winner_mentions = ', '.join(f'<@!{uid}>' for uid in selected)

        embed = discord.Embed(
            description=f"Ended Early\nHosted by <@{int(host_id)}>\nWinner(s): {winner_mentions}",
            color=0x2f3136
        )
        embed.timestamp = discord.utils.utcnow()
        embed.set_author(name=prize)

        await message.edit(content="🎁 **GIVEAWAY ENDED** 🎁", embed=embed, view=None)
        await message.reply(f"{winner_mentions} 🎉 Congratulations! You won **{prize}**!")
        
        participants_json = json.dumps(pool)
        await self.cursor.execute("UPDATE Giveaway SET participants_json = ?, ends_at = 0 WHERE message_id = ?", (participants_json, msg_id))
        self.join_view.participants.pop(msg_id, None)
        await self.connection.commit()
        await ctx.send("✅ Ended giveaway.", ephemeral=True)

    @commands.hybrid_command(name="greroll", description="Rerolls a finished giveaway to select new winners.")
    @app_commands.allowed_installs(guilds=True, users=True)
    @app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    async def greroll(self, ctx: commands.Context, message_id: str) -> None:
        try:
            msg_id = int(message_id)
        except ValueError:
            await ctx.send("⚠️ Invalid message ID format.", ephemeral=True)
            return

        await self.cursor.execute("SELECT prize, host_id, winners, channel_id, participants_json, ends_at FROM Giveaway WHERE message_id = ?", (msg_id,))
        re = await self.cursor.fetchone()
        if not re:
            await ctx.send("⚠️ Giveaway record matching this ID was not found.", ephemeral=True)
            return

        prize, host_id, winners_count, channel_id, participants_json, ends_at = re
        
        if float(ends_at) > 0:
            await ctx.send("⚠️ This giveaway is still running! Use `/gend` if you want to end it early instead.", ephemeral=True)
            return

        # Perms Check: Original host OR User has Manage Channels permission in server
        is_host = ctx.author.id == int(host_id)
        is_admin = ctx.guild and ctx.author.guild_permissions.manage_channels
        
        if not (is_host or is_admin):
            await ctx.send("❌ Only the giveaway host or an administrator can reroll this giveaway.", ephemeral=True)
            return

        try:
            pool = json.loads(participants_json)
        except Exception:
            pool = []

        if not pool or len(pool) < 1:
            await ctx.send("⚠️ There are no eligible participants to reroll from.", ephemeral=True)
            return

        channel = self.bot.get_channel(int(channel_id)) or await self.bot.fetch_channel(int(channel_id))
        try:
            message = await channel.fetch_message(msg_id)
        except discord.NotFound:
            await ctx.send("⚠️ The original giveaway message could not be found.", ephemeral=True)
            return

        actual_winners = min(len(pool), int(winners_count))
        selected = random.sample(pool, k=actual_winners)
        winner_mentions = ', '.join(f'<@!{uid}>' for uid in selected)

        await message.reply(f"🔄 **Reroll Results:**\n{winner_mentions} 🎉 Congratulations! You have won the reroll for **{prize}**!")
        await ctx.send("✅ Successfully rerolled the giveaway.", ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(UserAppGiveaway(bot))