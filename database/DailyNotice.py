import discord
from discord.ext import commands, tasks
from datetime import datetime, timezone, timedelta, time
from config import *

KST = timezone(timedelta(hours=9))

class DailyNotice(commands.Cog):

    def __init__(self, bot):
        self.bot = bot
        self.daily_notice.start()

    def cog_unload(self):
        self.daily_notice.cancel()

    @tasks.loop(time=time(hour=18, minute=0, tzinfo=KST))
    async def daily_notice(self):
        # toordinal()을 사용해 월말/월초(31일->1일) 연속 스킵 버그 방지
        today = datetime.now(KST).date()
        if today.toordinal() % 2 != 0:
            return

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
                    "🎨 **Roblox GFX / 복장 커미션 받습니다!**\n\n"
                    "✨ **제작 가능**\n"
                    "• 🎨 Roblox GFX\n"
                    "• 👕 Roblox 복장 제작\n\n"
                    f"📸 **예시작** : <#{EXAMPLE_CHANNEL_ID}> 에서 확인해주세요.\n"
                    f"📊 **디자이너 통계** : <#{DESIGNER_STATS_CHANNEL_ID}> 에서 확인해주세요.\n"
                    f"⭐ **구매 후기** : <#{REVIEWS_CHANNEL_ID}> 에서 확인해주세요.\n"
                    f"💳 **구매 및 문의** : <#{PURCHASE_CHANNEL_ID}> 를 이용해주세요.\n\n"
                ),
                color=0xF4A300
            )

            embed.set_footer(text="DDS System | 이틀에 1회, 오후 6시 정각 정기 발송")

            await channel.send(embed=embed)
            print("공지 전송 완료")

        except Exception as e:
            print(f"공지 오류: {e}")

    @daily_notice.before_loop
    async def before(self):
        print("DailyNotice 시작")
        await self.bot.wait_until_ready()

async def setup(bot):
    await bot.add_cog(DailyNotice(bot))
