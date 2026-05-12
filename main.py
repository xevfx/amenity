import os
import asyncio
from dotenv import load_dotenv
from core import Amenity

Amenity = Amenity()
load_dotenv()
    
async def main():
    async with Amenity:
        os.system("clear")
        await Amenity.start(os.getenv("TOKEN"))        
        
if __name__ == "__main__":
    asyncio.run(main())