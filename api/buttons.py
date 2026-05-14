import discord
from discord.ui import View, Button



class Links:
    """Simple utility for creating Discord link buttons."""
    
    @staticmethod
    def button(label: str, url: str, emoji: str = None) -> Button:
        """Create a link button."""
        return Button(label=label, url=url, emoji=emoji, style=discord.ButtonStyle.link)
    
    @staticmethod
    def view(label: str, url: str, emoji: str = None) -> View:
        """Create a persistent view with one link button."""
        view = View(timeout=None)  # Link views don't need timeout
        view.add_item(Links.button(label, url, emoji))
        return view
    
    @staticmethod
    def multi_view(links: list) -> View:
        """Create a persistent view with multiple link buttons.
        
        Args:
            links: List of tuples like [("Label", "URL"), ("Label", "URL", "emoji")]
        """
        view = View(timeout=None)  # Link views don't need timeout
        for link in links:
            if len(link) == 2:
                label, url = link
                emoji = None
            else:
                label, url, emoji = link
            view.add_item(Links.button(label, url, emoji))
        return view


class BotLinks:
    """Quick preset links for common bot needs."""
    
    def __init__(self, support_url: str = None, invite_url: str = None):
        self.support_url = support_url or "https://discord.gg/x4kaVDcubT"
        self.invite_url = invite_url or "https://discord.com/oauth2/authorize?client_id=1455170105666306113"
    
    def support(self, label: str = "Support Server", emoji: str = "🎗️") -> View:
        """Get support server link view."""
        return Links.view(label, self.support_url, emoji)
    
    def invite(self, label: str = "Invite Bot", emoji: str = "🤖") -> View:
        """Get bot invite link view."""
        return Links.view(label, self.invite_url, emoji)
    
    def both(self) -> View:
        """Get view with both support and invite buttons."""
        return Links.multi_view([
            ("Support Server", self.support_url, "🎗️"),
            ("Invite Bot", self.invite_url, "🤖")
        ])

# bot_links = BotLinks(
#     support_url="https://discord.gg/your-server",
#     invite_url="https://discord.com/oauth2/authorize?client_id=123456789&scope=bot"
# )
# SupportServerLink = bot_links.support()



# Usage:
"""
# Setup once
bot_links = BotLinks(
    support_url="https://discord.gg/your-server",
    invite_url="https://discord.com/oauth2/authorize?client_id=123456789&scope=bot"
)

# Use in commands
@bot.command()
async def support(ctx):
    await ctx.send("Join our support server!", view=bot_links.support())

@bot.command()
async def invite(ctx):
    await ctx.send("Invite me!", view=bot_links.invite())

@bot.command()
async def links(ctx):
    await ctx.send("Useful links:", view=bot_links.both())

# Custom links
custom_view = Links.multi_view([
    ("Website", "https://mysite.com", "🌐"),
    ("GitHub", "https://github.com/myrepo", "🐙")
])
"""