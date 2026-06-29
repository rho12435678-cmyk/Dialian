import discord
from discord.ext import tasks
from datetime import timezone, timedelta, time
from config import *

KST = timezone(timedelta(hours=9))


class DailyNotice:

    def __init__(self, bot):
        self.bot = bot
        self.daily_notice.start()

    @tasks.loop(seconds=10)
async def daily_notice(self):
    print("공지 실행")

    channel = self.bot.get_channel(SALE_NOTICE_CHANNEL_ID)
    print(channel)

    if channel is None:
        print("채널 없음")
        return

    await channel.send("테스트 공지")

    @daily_notice.before_loop
    async def before(self):
        print("DailyNotice 시작")
        await self.bot.wait_until_ready()
