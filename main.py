import asyncio
import logging
import os

from colorama import Fore
from dotenv import load_dotenv

load_dotenv()

from core import Amenity

bot = Amenity()
logging.basicConfig(
    level=logging.INFO,
    format=(
        Fore.CYAN
        + "[%(name)s] [%(module)s.%(funcName)s:%(lineno)d] → %(message)s\n"
    ),
)
logger = logging.getLogger(__name__)


async def main() -> None:
    async with bot:
        os.system("clear")
        await bot.start(os.getenv("TOKEN"))

if __name__ == "__main__":
    asyncio.run(main())
