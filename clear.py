from discord.ext import commands

class Clear(commands.Cog):

    def __init__(self, bot):
        self.bot = bot

    @commands.command(name="청소")
    @commands.has_permissions(manage_messages=True)
    async def clear(self, ctx, amount: int):

        await ctx.channel.purge(limit=amount + 1)

        msg = await ctx.send(f"✅ {amount}개의 메시지를 삭제했습니다.")

        await msg.delete(delay=3)


async def setup(bot):
    await bot.add_cog(Clear(bot))
