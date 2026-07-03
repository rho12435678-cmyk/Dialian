import discord
from discord.ext import tasks
from datetime import timezone, timedelta, time
from config import *

KST = timezone(timedelta(hours=9))

class DailyNotice:

    def __init__(self, bot):
        self.bot = bot
        self.daily_notice.start()

    @tasks.loop(time=time(hour=18, minute=30, tzinfo=KST))
    async def daily_notice(self):
        print("공지 실행 시작")

        try:
            channel = self.bot.get_channel(SALE_NOTICE_CHANNEL_ID)
            print(f"채널: {channel}")

            if channel is None:
                print("채널 없음")
                return

            embed = discord.Embed(
                description=(
                    f"<@&{CUSTOMER_ROLE_ID}>\n\n"
                    "🎨 **Roblox GFX / 로고 / 복장 커미션 받습니다!**\n\n"
                    "✨ **제작 가능**\n"
                    "• 🎨 Roblox GFX\n"
                    "• 🖌️ 로고 디자인\n"
                    "• 👕 Roblox 복장 제작\n\n"
                    f"📸 예시작은 <#{EXAMPLE_CHANNEL_ID}> 에서 확인해주세요.\n"
                    f"💳 구매 및 문의는 <#{PURCHASE_CHANNEL_ID}> 를 이용해주세요.\n\n"
                ),
                color=0xF4A300
            )

            await channel.send(embed=embed)
            print("공지 전송 완료")

        except Exception as e:
            print(f"공지 오류: {e}")

    @daily_notice.before_loop
    async def before(self):
        print("DailyNotice 시작")
        await self.bot.wait_until_ready()
