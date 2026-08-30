import discord
from discord.ext import commands, tasks
from datetime import datetime, timezone, timedelta, time
import config

KST = timezone(timedelta(hours=9))

class DailyNotice(commands.Cog):

    def __init__(self, bot):
        self.bot = bot
        self.daily_notice.start()

    def cog_unload(self):
        self.daily_notice.cancel()

    @tasks.loop(time=time(hour=18, minute=0, tzinfo=KST))
    async def daily_notice(self):
        # 2일(48시간) 주기 발송 체크
        today = datetime.now(KST).date()
        if today.toordinal() % 2 != 0:
            return

        print("[DailyNotice] 가이드 공지 실행 시작")

        # 1. 한국어 채팅 채널 전송
        kr_channel = self.bot.get_channel(config.KR_CHAT_CHANNEL_ID)
        if kr_channel:
            try:
                await kr_channel.send(config.GUIDE_MESSAGE_KR)
                print("[DailyNotice] 한국어 가이드 공지 전송 완료")
            except Exception as e:
                print(f"[DailyNotice] 한국어 공지 오류: {e}")
        else:
            print("[DailyNotice] 한국어 채널을 찾을 수 없음")

        # 2. 영어 채팅 채널 전송
        en_channel = self.bot.get_channel(config.EN_CHAT_CHANNEL_ID)
        if en_channel:
            try:
                await en_channel.send(config.GUIDE_MESSAGE_EN)
                print("[DailyNotice] 영어 가이드 공지 전송 완료")
            except Exception as e:
                print(f"[DailyNotice] 영어 공지 오류: {e}")
        else:
            print("[DailyNotice] 영어 채널을 찾을 수 없음")

    @daily_notice.before_loop
    async def before(self):
        print("[DailyNotice] 루프 대기 시작")
        await self.bot.wait_until_ready()

async def setup(bot):
    await bot.add_cog(DailyNotice(bot))
