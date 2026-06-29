import discord
from discord.ext import tasks
from datetime import timezone, timedelta, time
from database.daily_notice import DailyNotice
from config import *

KST = timezone(timedelta(hours=9))


class DailyNotice:

    def __init__(self, bot):
        self.bot = bot
        self.daily_notice.start()

    @tasks.loop(time=time(hour=20, minute=48, tzinfo=KST))
    async def daily_notice(self):

        print("공지 실행")
        
        channel = self.bot.get_channel(SALE_NOTICE_CHANNEL_ID)

        if channel is None:
            return

        embed = discord.Embed(
            description=(
                "@CUSTOMER/손님\n\n"

                "🎨 **Roblox GFX / 로고 / 복장 커미션 받습니다!**\n\n"

                "✨ **제작 가능**\n"
                "• 🎨 Roblox GFX\n"
                "• 🖌️ 로고 디자인\n"
                "• 👕 Roblox 복장 제작\n\n"

                "✅ 디자이너 직접 상담\n"
                "✅ 실시간 진행률 확인\n"
                "✅ 안전한 티켓 시스템\n"
                "✅ 빠른 작업 및 고퀄리티 제작\n\n"

                "📸 예시작은 <#예시채널ID> 에서 확인해주세요.\n"
                "💳 구매 및 문의는 <#구매채널ID> 를 이용해주세요.\n\n"

                "감사합니다 🙏"
            ),
            color=0xF4A300
        )

        await channel.send(embed=embed)

    @daily_notice.before_loop
    async def before(self):
        print("DailyNotice 시작")
        await self.bot.wait_until_ready()
