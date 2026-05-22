import asyncio
import os
import re
import sqlite3
import time
from contextlib import suppress

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands, tasks

from api.log import log_exception
from core.amenity import Amenity
from core.cache import cache

_ADDRESS_RE = re.compile(
    r"^(ltc1[ac-hj-np-z02-9]{39,59}|[LM][a-km-zA-HJ-NP-Z1-9]{26,33})$"
)
_PRICE_CACHE_KEY = "ltc:usd_price"
_ETH_PRICE_CACHE_KEY = "eth:usd_price"
_SOL_PRICE_CACHE_KEY = "sol:usd_price"
_COIN_LIST_CACHE_KEY = "coingecko:coin_list"
_PRICE_DETAILS_CACHE_PREFIX = "price:multi:"


class Crypto(commands.Cog):
    display_name = "Crypto"
    group_name = "Utilities"

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.aiohttp = aiohttp.ClientSession()
        self.db_path = os.path.abspath(
            os.path.join(os.path.dirname(__file__), "..", "data", "ltc_notifier.db")
        )
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        self._init_db()
        self.check_addresses.start()

    def cog_unload(self) -> None:
        self.check_addresses.cancel()
        if not self.aiohttp.closed:
            self.bot.loop.create_task(self.aiohttp.close())

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS ltc_notifiers (
                    user_id INTEGER PRIMARY KEY,
                    address TEXT NOT NULL,
                    last_tx TEXT,
                    last_notified_tx TEXT,
                    last_balance INTEGER,
                    created_at INTEGER NOT NULL,
                    updated_at INTEGER NOT NULL
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS ltc_notifiers_user_id ON ltc_notifiers (user_id)"
            )
            existing_columns = {
                row["name"] for row in conn.execute("PRAGMA table_info(ltc_notifiers)")
            }
            if "last_tx" not in existing_columns:
                conn.execute("ALTER TABLE ltc_notifiers ADD COLUMN last_tx TEXT")
            if "last_notified_tx" not in existing_columns:
                conn.execute("ALTER TABLE ltc_notifiers ADD COLUMN last_notified_tx TEXT")
            if "last_balance" not in existing_columns:
                conn.execute("ALTER TABLE ltc_notifiers ADD COLUMN last_balance INTEGER")
            if "created_at" not in existing_columns:
                conn.execute("ALTER TABLE ltc_notifiers ADD COLUMN created_at INTEGER")
            if "updated_at" not in existing_columns:
                conn.execute("ALTER TABLE ltc_notifiers ADD COLUMN updated_at INTEGER")

            now = int(time.time())
            conn.execute(
                "UPDATE ltc_notifiers SET created_at = ? WHERE created_at IS NULL",
                (now,),
            )
            conn.execute(
                "UPDATE ltc_notifiers SET updated_at = ? WHERE updated_at IS NULL",
                (now,),
            )

            existing_columns = {
                row["name"] for row in conn.execute("PRAGMA table_info(ltc_notifiers)")
            }
            if "last_total_received" in existing_columns:
                conn.execute("ALTER TABLE ltc_notifiers RENAME TO ltc_notifiers_old")
                conn.execute(
                    """
                    CREATE TABLE ltc_notifiers (
                        user_id INTEGER PRIMARY KEY,
                        address TEXT NOT NULL,
                        last_tx TEXT,
                        last_notified_tx TEXT,
                        last_balance INTEGER,
                        created_at INTEGER NOT NULL,
                        updated_at INTEGER NOT NULL
                    )
                    """
                )
                conn.execute(
                    """
                    INSERT INTO ltc_notifiers (
                        user_id, address, last_tx, last_notified_tx,
                        last_balance, created_at, updated_at
                    )
                    SELECT user_id, address, last_tx, last_notified_tx,
                           last_balance, created_at, updated_at
                    FROM ltc_notifiers_old
                    """
                )
                conn.execute("DROP TABLE ltc_notifiers_old")
                conn.execute(
                    "CREATE INDEX IF NOT EXISTS ltc_notifiers_user_id ON ltc_notifiers (user_id)"
                )

    def _get_user(self, user_id: int) -> dict | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT user_id, address, last_tx, last_notified_tx, last_balance "
                "FROM ltc_notifiers WHERE user_id = ?",
                (user_id,),
            ).fetchone()
        return dict(row) if row else None

    def _set_user(
        self,
        user_id: int,
        address: str,
        last_tx: str | None,
        last_notified_tx: str | None,
        last_balance: int | None,
    ) -> None:
        now = int(time.time())
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO ltc_notifiers (
                    user_id, address, last_tx, last_notified_tx,
                    last_balance, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    address = excluded.address,
                    last_tx = excluded.last_tx,
                    last_notified_tx = excluded.last_notified_tx,
                    last_balance = excluded.last_balance,
                    updated_at = excluded.updated_at
                """,
                (user_id, address, last_tx, last_notified_tx, last_balance, now, now),
            )

    def _delete_user(self, user_id: int) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM ltc_notifiers WHERE user_id = ?", (user_id,))

    def _list_all(self) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT user_id, address, last_tx, last_notified_tx, last_balance "
                "FROM ltc_notifiers"
            ).fetchall()
        return [dict(row) for row in rows]

    def _valid_address(self, address: str) -> bool:
        return bool(_ADDRESS_RE.match(address))

    async def _fetch_json(self, url: str) -> dict | None:
        try:
            async with self.aiohttp.get(url, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                if resp.status != 200:
                    return None
                return await resp.json()
        except Exception as exc:
            log_exception(exc)
            return None

    async def _get_coin_list(self) -> list[dict]:
        async def fetch() -> list[dict]:
            url = "https://api.coingecko.com/api/v3/coins/list"
            data = await self._fetch_json(url)
            if isinstance(data, list):
                return data
            return []

        return await cache.get_or_set_async(_COIN_LIST_CACHE_KEY, fetch, ttl=21_600)

    async def _get_coin_id(self, coin: str) -> str | None:
        query = coin.lower().strip()
        if not query:
            return None

        aliases = {
            "btc": "bitcoin",
            "eth": "ethereum",
            "ltc": "litecoin",
            "sol": "solana",
            "bnb": "binancecoin",
            "doge": "dogecoin",
            "xrp": "ripple",
            "ada": "cardano",
            "dot": "polkadot",
        }
        if query in aliases:
            return aliases[query]

        coins = await self._get_coin_list()
        if not coins:
            return None

        for entry in coins:
            if entry.get("id") == query:
                return entry.get("id")

        for entry in coins:
            name = str(entry.get("name") or "").lower()
            if name == query:
                return entry.get("id")

        for entry in coins:
            symbol = str(entry.get("symbol") or "").lower()
            if symbol == query:
                return entry.get("id")

        return None

    async def _get_price_details(self, coin_id: str) -> dict | None:
        cache_key = f"{_PRICE_DETAILS_CACHE_PREFIX}{coin_id}"
        cached = cache.get(cache_key)
        if isinstance(cached, dict):
            return cached

        url = (
            "https://api.coingecko.com/api/v3/simple/price"
            f"?ids={coin_id}&vs_currencies=usd,eur,inr,gbp&include_24hr_change=true"
        )
        data = await self._fetch_json(url)
        if not data or not isinstance(data.get(coin_id), dict):
            return None

        price_data = data[coin_id]
        cache.set(cache_key, price_data, ttl=60)
        return price_data

    def _to_litoshis(self, value: str | float | int | None) -> int | None:
        if value is None:
            return None
        with suppress(ValueError, TypeError):
            return int(round(float(value) * 100_000_000))
        return None

    async def _fetch_blockcypher_state(self, address: str, limit: int = 5) -> dict | None:
        url = f"https://api.blockcypher.com/v1/ltc/main/addrs/{address}?limit={limit}"
        payload = await self._fetch_json(url)
        if payload is None or "error" in payload:
            return None

        latest_tx = self._find_latest_tx(payload)
        latest_hash = latest_tx.get("tx_hash") if latest_tx else None
        latest_value = latest_tx.get("value") if latest_tx else None
        explorer_url = (
            f"https://live.blockcypher.com/ltc/tx/{latest_hash}/" if latest_hash else None
        )
        return {
            "tx_hash": latest_hash,
            "value": latest_value,
            "balance": payload.get("final_balance"),
            "explorer_url": explorer_url,
            "source": "blockcypher",
        }

    async def _fetch_sochain_state(self, address: str) -> dict | None:
        url = f"https://sochain.com/api/v2/address/LTC/{address}"
        payload = await self._fetch_json(url)
        if not payload or payload.get("status") != "success":
            return None

        data = payload.get("data") or {}
        confirmed = self._to_litoshis(data.get("confirmed_balance"))
        unconfirmed = self._to_litoshis(data.get("unconfirmed_balance"))
        balance = None
        if confirmed is not None or unconfirmed is not None:
            balance = (confirmed or 0) + (unconfirmed or 0)
        txs = data.get("txs") or []
        latest_tx = None
        if txs:
            latest_tx = max(txs, key=lambda tx: tx.get("time", 0) or 0)

        latest_hash = latest_tx.get("txid") if latest_tx else None
        latest_value = None
        if latest_tx:
            latest_value = self._to_litoshis(abs(float(latest_tx.get("value") or 0)))
        explorer_url = (
            f"https://sochain.com/tx/LTC/{latest_hash}" if latest_hash else None
        )
        return {
            "tx_hash": latest_hash,
            "value": latest_value,
            "balance": balance,
            "explorer_url": explorer_url,
            "source": "sochain",
        }

    async def _fetch_address_state(self, address: str, limit: int = 5) -> dict | None:
        state = await self._fetch_blockcypher_state(address, limit=limit)
        if state:
            return state
        return await self._fetch_sochain_state(address)

    async def _validate_address(self, address: str) -> tuple[bool, dict | None]:
        state = await self._fetch_address_state(address, limit=1)
        return (state is not None), state

    async def _get_ltc_price_usd(self) -> float | None:
        cached = cache.get(_PRICE_CACHE_KEY)
        if isinstance(cached, (int, float)):
            return float(cached)

        coingecko_url = (
            "https://api.coingecko.com/api/v3/simple/price"
            "?ids=litecoin&vs_currencies=usd"
        )
        data = await self._fetch_json(coingecko_url)
        price = None
        if data and isinstance(data.get("litecoin"), dict):
            price = data["litecoin"].get("usd")

        if price is None:
            binance_url = "https://api.binance.com/api/v3/ticker/price?symbol=LTCUSDT"
            data = await self._fetch_json(binance_url)
            if data and "price" in data:
                with suppress(ValueError, TypeError):
                    price = float(data["price"])

        if price is None:
            return None

        cache.set(_PRICE_CACHE_KEY, float(price), ttl=60)
        return float(price)

    async def _get_price_usd(self, coin_id: str, cache_key: str) -> float | None:
        cached = cache.get(cache_key)
        if isinstance(cached, (int, float)):
            return float(cached)

        coingecko_url = (
            "https://api.coingecko.com/api/v3/simple/price"
            f"?ids={coin_id}&vs_currencies=usd"
        )
        data = await self._fetch_json(coingecko_url)
        price = None
        if data and isinstance(data.get(coin_id), dict):
            price = data[coin_id].get("usd")

        if price is None:
            return None

        cache.set(cache_key, float(price), ttl=60)
        return float(price)

    def _find_latest_tx(self, payload: dict) -> dict | None:
        txrefs = payload.get("txrefs") or []
        unconfirmed = payload.get("unconfirmed_txrefs") or []
        candidates = [*txrefs, *unconfirmed]
        if not candidates:
            return None

        def sort_key(tx: dict) -> tuple[int, str]:
            confirmed = tx.get("confirmed") or tx.get("received") or ""
            return (1 if tx.get("confirmed") else 0, confirmed)

        candidates.sort(key=sort_key, reverse=True)
        return candidates[0]

    async def _notify_user(
        self,
        user_id: int,
        address: str,
        tx_hash: str,
        value_litoshis: int,
        price_usd: float | None,
        explorer_url: str | None,
    ) -> None:
        user = self.bot.get_user(int(user_id))
        if user is None:
            try:
                user = await self.bot.fetch_user(int(user_id))
            except discord.HTTPException:
                return

        amount_ltc = value_litoshis / 100_000_000
        usd_value = None
        if price_usd is not None:
            usd_value = amount_ltc * price_usd

        if explorer_url is None:
            explorer_url = f"https://live.blockcypher.com/ltc/tx/{tx_hash}/"
        description = (
            f"Address: `{address}`\n"
            f"Amount: `{amount_ltc:.8f} LTC`"
        )
        if usd_value is not None:
            description += f" (`${usd_value:,.2f}`)"

        embed = discord.Embed(
            title="LTC Activity",
            description=description,
            color=discord.Color.gold(),
        )
        embed.add_field(name="Tx", value=f"[View on explorer]({explorer_url})", inline=False)
        with suppress(discord.HTTPException):
            await user.send(embed=embed)

    async def _check_one(self, row: dict, price_usd: float | None) -> None:
        address = row["address"]
        state = await self._fetch_address_state(address, limit=5)
        if not state:
            return

        latest_hash = state.get("tx_hash")
        latest_value = state.get("value")
        balance = state.get("balance")
        explorer_url = state.get("explorer_url")
        last_tx = row.get("last_tx")
        last_notified = row.get("last_notified_tx") or last_tx

        should_notify = (
            latest_hash
            and latest_hash != last_notified
            and latest_value is not None
        )

        if latest_hash or balance is not None or should_notify:
            self._set_user(
                row["user_id"],
                address,
                latest_hash,
                latest_hash if should_notify else last_notified,
                balance,
            )

        if should_notify:
            await self._notify_user(
                row["user_id"],
                address,
                latest_hash,
                int(latest_value),
                price_usd,
                explorer_url,
            )

    async def _check_all(self) -> None:
        rows = self._list_all()
        if not rows:
            return

        price_usd = await self._get_ltc_price_usd()
        semaphore = asyncio.Semaphore(5)

        async def run_row(row: dict) -> None:
            async with semaphore:
                await self._check_one(row, price_usd)

        await asyncio.gather(*(run_row(row) for row in rows))

    @tasks.loop(seconds=75)
    async def check_addresses(self) -> None:
        try:
            await self._check_all()
        except Exception as exc:
            log_exception(exc)

    @check_addresses.before_loop
    async def check_addresses_before_loop(self) -> None:
        await self.bot.wait_until_ready()

    async def _send_embed(
        self,
        ctx: commands.Context,
        description: str,
        *,
        title: str | None = None,
        color: discord.Color = discord.Color.blurple(),
        ephemeral: bool = True,
    ) -> None:
        embed = discord.Embed(description=description, color=color)
        if title:
            embed.title = title
        if ctx.interaction:
            if ctx.interaction.response.is_done():
                await ctx.interaction.followup.send(embed=embed, ephemeral=ephemeral)
            else:
                await ctx.interaction.response.send_message(embed=embed, ephemeral=ephemeral)
            return
        await ctx.send(embed=embed)

    @commands.hybrid_group(
        name="ltc-notifier",
        description="Manage your LTC notifier",
        invoke_without_command=True
    )
    @app_commands.allowed_installs(guilds=True, users=True)
    @app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    async def ltc_notifier(self, ctx: commands.Context) -> None:
        if ctx.invoked_subcommand is None:
            await ctx.send_help(ctx.command)

    @ltc_notifier.command(name="set", description="Set your Litecoin address")
    @app_commands.describe(addy="The Litecoin address to watch")
    @commands.cooldown(1, 5, commands.BucketType.user)
    async def ltc_set(self, ctx: commands.Context, *, addy: str) -> None:
        address = addy.strip()
        if not address:
            await self._send_embed(ctx, "Usage: /ltc-notifier set <address>")
            return
        if not self._valid_address(address):
            await self._send_embed(ctx, "That doesn't look like a valid LTC address.")
            return

        existing = self._get_user(ctx.author.id)
        if existing:
            await self._send_embed(
                ctx,
                "You already have a saved address. Use `/ltc-notifier change <new address>`.",
            )
            return

        valid, payload = await self._validate_address(address)
        if not valid:
            await self._send_embed(ctx, "Unable to validate that address right now.")
            return

        last_tx = payload.get("tx_hash") if payload else None
        balance = payload.get("balance") if payload else None
        self._set_user(ctx.author.id, address, last_tx, last_tx, balance)
        await self._send_embed(ctx, f"Saved address: `{address}`", title="LTC Notifier")

    @ltc_notifier.command(name="change", description="Change your saved Litecoin address")
    @app_commands.describe(addy="The new Litecoin address to watch")
    @commands.cooldown(1, 5, commands.BucketType.user)
    async def ltc_change(self, ctx: commands.Context, *, addy: str) -> None:
        address = addy.strip()
        if not address:
            await self._send_embed(ctx, "Usage: /ltc-notifier change <new address>")
            return
        if not self._valid_address(address):
            await self._send_embed(ctx, "That doesn't look like a valid LTC address.")
            return

        valid, payload = await self._validate_address(address)
        if not valid:
            await self._send_embed(ctx, "Unable to validate that address right now.")
            return

        last_tx = payload.get("tx_hash") if payload else None
        balance = payload.get("balance") if payload else None
        self._set_user(ctx.author.id, address, last_tx, last_tx, balance)
        await self._send_embed(ctx, f"Updated address: `{address}`", title="LTC Notifier")

    @ltc_notifier.command(
        name="addy",
        description="Show your saved Litecoin address"
    )
    @commands.cooldown(1, 5, commands.BucketType.user)
    async def ltc_addy(self, ctx: commands.Context) -> None:
        entry = self._get_user(ctx.author.id)
        if not entry:
            await self._send_embed(ctx, "No address saved. Use `/ltc-notifier set <address>`. ")
            return
        await self._send_embed(
            ctx,
            f"Saved address: `{entry['address']}`",
            title="LTC Notifier",
        )

    @ltc_notifier.command(name="delete", description="Delete your saved Litecoin address")
    @commands.cooldown(1, 5, commands.BucketType.user)
    async def ltc_delete(self, ctx: commands.Context) -> None:
        entry = self._get_user(ctx.author.id)
        if not entry:
            await self._send_embed(ctx, "No address saved.")
            return
        self._delete_user(ctx.author.id)
        await self._send_embed(ctx, "Your address has been deleted.", title="LTC Notifier")

    @commands.hybrid_group(
        name="balance",
        description="Get crypto address balances",
        invoke_without_command=True,
    )
    @app_commands.allowed_installs(guilds=True, users=True)
    @app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    async def balance(self, ctx: commands.Context) -> None:
        if ctx.invoked_subcommand is None:
            await ctx.send_help(ctx.command)

    @balance.command(name="ltc", description="Get the balance of a ltc address.")
    @app_commands.describe(address="The Litecoin address to check")
    @commands.cooldown(1, 5, commands.BucketType.user)
    async def ltc_balance(self, ctx: commands.Context, address: str) -> None:
        address = address.strip()
        if not address:
            await self._send_embed(ctx, "Usage: /balance ltc <address>", ephemeral=False)
            return

        url = f"https://api.blockcypher.com/v1/ltc/main/addrs/{address}/balance"
        try:
            async with self.aiohttp.get(url, timeout=aiohttp.ClientTimeout(total=15)) as response:
                if response.status == 200:
                    data = await response.json()
                elif response.status == 429:
                    await ctx.send("⚠️ Rate limited by BlockCypher. Please try again later.")
                    return
                else:
                    await ctx.send(
                        f"Error fetching balance from BlockCypher (Status: {response.status})."
                    )
                    return
        except Exception as exc:
            log_exception(exc)
            await ctx.send("An error occurred while fetching LTC balance.")
            return

        balance = data.get("balance", 0)
        unconfirmed_balance = data.get("unconfirmed_balance", 0)
        total_received = data.get("total_received", 0)
        n_tx = data.get("n_tx", 0)

        balance_in_coin = balance / 10**8
        unconfirmed_balance_in_coin = unconfirmed_balance / 10**8
        total_received_in_coin = total_received / 10**8

        price_usd = await self._get_ltc_price_usd()

        embed = discord.Embed(
            description=f"[{address}](https://live.blockcypher.com/ltc/address/{address})",
            color=0xB0C4DE,
        )

        balance_str = f"{balance_in_coin:.8f} LTC"
        if price_usd:
            balance_usd = balance_in_coin * price_usd
            balance_str += f" (${balance_usd:,.2f} USD)"
        embed.add_field(name="Balance", value=balance_str, inline=False)

        unconfirmed_balance_str = f"{unconfirmed_balance_in_coin:.8f} LTC"
        if price_usd:
            unconfirmed_balance_usd = unconfirmed_balance_in_coin * price_usd
            unconfirmed_balance_str += f" (${unconfirmed_balance_usd:,.2f} USD)"
        embed.add_field(
            name="Unconfirmed Balance",
            value=unconfirmed_balance_str,
            inline=False,
        )

        total_received_str = f"{total_received_in_coin:.8f} LTC"
        if price_usd:
            total_received_usd = total_received_in_coin * price_usd
            total_received_str += f" (${total_received_usd:,.2f} USD)"
        embed.add_field(name="Total Received", value=total_received_str, inline=False)

        embed.set_footer(text=f"Total Transactions: {n_tx}")
        await ctx.send(embed=embed)

    @balance.command(name="eth", description="Get the balance of an eth address.")
    @app_commands.describe(address="The Ethereum address to check")
    @commands.cooldown(1, 5, commands.BucketType.user)
    async def eth_balance(self, ctx: commands.Context, address: str) -> None:
        address = address.strip()
        if not address:
            await self._send_embed(ctx, "Usage: /balance eth <address>", ephemeral=False)
            return

        url = f"https://api.blockcypher.com/v1/eth/main/addrs/{address}/balance"
        try:
            async with self.aiohttp.get(url, timeout=aiohttp.ClientTimeout(total=15)) as response:
                if response.status == 200:
                    data = await response.json()
                elif response.status == 429:
                    await ctx.send("⚠️ Rate limited by BlockCypher. Please try again later.")
                    return
                else:
                    await ctx.send(
                        f"Error fetching balance from BlockCypher (Status: {response.status})."
                    )
                    return
        except Exception as exc:
            log_exception(exc)
            await ctx.send("An error occurred while fetching ETH balance.")
            return

        balance = data.get("balance", 0)
        unconfirmed_balance = data.get("unconfirmed_balance", 0)
        total_received = data.get("total_received", 0)
        n_tx = data.get("n_tx", 0)

        balance_in_coin = balance / 10**18
        unconfirmed_balance_in_coin = unconfirmed_balance / 10**18
        total_received_in_coin = total_received / 10**18

        price_usd = await self._get_price_usd("ethereum", _ETH_PRICE_CACHE_KEY)

        embed = discord.Embed(
            description=f"[{address}](https://live.blockcypher.com/eth/address/{address})",
            color=0x627EEA,
        )

        balance_str = f"{balance_in_coin:.8f} ETH"
        if price_usd:
            balance_usd = balance_in_coin * price_usd
            balance_str += f" (${balance_usd:,.2f} USD)"
        embed.add_field(name="Balance", value=balance_str, inline=False)

        unconfirmed_balance_str = f"{unconfirmed_balance_in_coin:.8f} ETH"
        if price_usd:
            unconfirmed_balance_usd = unconfirmed_balance_in_coin * price_usd
            unconfirmed_balance_str += f" (${unconfirmed_balance_usd:,.2f} USD)"
        embed.add_field(
            name="Unconfirmed Balance",
            value=unconfirmed_balance_str,
            inline=False,
        )

        total_received_str = f"{total_received_in_coin:.8f} ETH"
        if price_usd:
            total_received_usd = total_received_in_coin * price_usd
            total_received_str += f" (${total_received_usd:,.2f} USD)"
        embed.add_field(name="Total Received", value=total_received_str, inline=False)

        embed.set_footer(text=f"Total Transactions: {n_tx}")
        await ctx.send(embed=embed)

    @balance.command(name="sol", description="Get the balance of a sol address.")
    @app_commands.describe(address="The Solana address to check")
    @commands.cooldown(1, 5, commands.BucketType.user)
    async def sol_balance(self, ctx: commands.Context, address: str) -> None:
        address = address.strip()
        if not address:
            await self._send_embed(ctx, "Usage: /balance sol <address>", ephemeral=False)
            return

        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "getBalance",
            "params": [address],
        }
        try:
            async with self.aiohttp.post(
                "https://api.mainnet-beta.solana.com",
                json=payload,
                timeout=aiohttp.ClientTimeout(total=15),
            ) as response:
                if response.status == 200:
                    data = await response.json()
                else:
                    await ctx.send(
                        f"Error fetching balance from Solana RPC (Status: {response.status})."
                    )
                    return
        except Exception as exc:
            log_exception(exc)
            await ctx.send("An error occurred while fetching SOL balance.")
            return

        result = data.get("result") or {}
        value = result.get("value")
        if value is None:
            await ctx.send("Unable to read balance from Solana RPC response.")
            return

        balance_in_coin = value / 10**9
        price_usd = await self._get_price_usd("solana", _SOL_PRICE_CACHE_KEY)

        embed = discord.Embed(
            description=f"[{address}](https://solscan.io/account/{address})",
            color=0x00FFA3,
        )

        balance_str = f"{balance_in_coin:.9f} SOL"
        if price_usd:
            balance_usd = balance_in_coin * price_usd
            balance_str += f" (${balance_usd:,.2f} USD)"
        embed.add_field(name="Balance", value=balance_str, inline=False)
        await ctx.send(embed=embed)


    @commands.hybrid_command(name="price", description="Get the price of a cryptocurrency.")
    @app_commands.allowed_installs(guilds=True, users=True)
    @app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    @commands.cooldown(1, 5, commands.BucketType.user)
    async def crypto_price(self, ctx: commands.Context, *, coin: str) -> None:
        """
        Get the price of a cryptocurrency.
        Examples: ,price btc | ,price bitcoin | ,price eth
        """
        coin = coin.lower().strip()
        coin_id = await self._get_coin_id(coin)
        if not coin_id:
            await self._send_embed(
                ctx,
                f"Could not find a coin with the name: `{coin}`",
                color=discord.Color.red(),
                ephemeral=False,
            )
            return

        coin_data = await self._get_price_details(coin_id)
        if not coin_data:
            await self._send_embed(
                ctx,
                f"Could not fetch price data for `{coin}`",
                color=discord.Color.red(),
                ephemeral=False,
            )
            return

        def format_line(symbol: str, value: float | int | None) -> str | None:
            if value is None:
                return None
            value = float(value)
            if value >= 0.01:
                return f"{symbol}{value:,.2f}"
            return f"{symbol}{value:.8f}"

        lines = [
            format_line("$", coin_data.get("usd")),
            format_line("€", coin_data.get("eur")),
            format_line("£", coin_data.get("gbp")),
            format_line("₹", coin_data.get("inr")),
        ]
        lines = [line for line in lines if line]
        if not lines:
            await self._send_embed(
                ctx,
                f"Could not fetch price data for `{coin}`",
                color=discord.Color.red(),
                ephemeral=False,
            )
            return

        change_24h = coin_data.get("usd_24h_change")
        change_str = ""
        if isinstance(change_24h, (int, float)):
            emoji = "📈" if change_24h >= 0 else "📉"
            change_str = f"\n\n{emoji} **24h Change:** {change_24h:+.2f}%"

        embed = discord.Embed(
            title=f"💰 Price of {coin.upper()}",
            description=" | ".join(lines) + change_str,
            color=discord.Color.green()
            if (isinstance(change_24h, (int, float)) and change_24h >= 0)
            else discord.Color.gold(),
        )
        embed.set_footer(text="Data from CoinGecko")
        await ctx.send(embed=embed)


async def setup(bot: Amenity) -> None:
    await bot.add_cog(Crypto(bot))
