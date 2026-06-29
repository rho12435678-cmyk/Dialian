import discord
from discord.ext import tasks
from datetime import time
from config import *

KST = datetime.timezone(datetime.timedelta(hours=9))

class DailyNotice:

    def __init__(self, bot):
        self.bot = bot
        self.daily_notice.start()

    @tasks.loop(time=time(hour=18, minute=0, tzinfo=KST))
    async def daily_notice(self):

        channel = self.bot.get_channel(SALE_NOTICE_CHANNEL_ID)

        if channel is None:
            return

        embed = discord.Embed(
            description=(
                "@CUSTOMER/손님\n\n"
                "🎨 **Roblox GFX 커미션 받습니다!**\n\n"
                "📸 예시작은 <#예시채널ID> 에서 확인해주세요.\n"
                "💳 구매는 <#구매채널ID> 를 이용해주세요.\n\n"
                "감사합니다 🙏"
            ),
            color=0xF4A300
        )

        await channel.send(embed=embed)

    @daily_notice.before_loop
    async def before(self):
        await self.bot.wait_until_ready()