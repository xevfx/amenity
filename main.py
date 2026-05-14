import os
import asyncio
from dotenv import load_dotenv
from core import Amenity
import logging
from colorama import Fore

Amenity = Amenity()
load_dotenv()
logging.basicConfig(
    level=logging.INFO,
    format=Fore.CYAN + '[%(name)s] [%(module)s.%(funcName)s:%(lineno)d] → %(message)s\n'
)
logger = logging.getLogger(__name__)    


async def main():
    async with Amenity:
        os.system("clear")
        await Amenity.start(os.getenv("TOKEN"))        
        
if __name__ == "__main__":
    asyncio.run(main())