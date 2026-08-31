import discord
from discord.ext import commands, tasks
from datetime import datetime, timezone, timedelta, time
import config

KST = timezone(timedelta(hours=9))

class DailyNotice(commands.Cog):

    def __init__(self, bot):
        self.bot = bot
        self.last_run_date = None  # 하루 중복 실행 방지용 상태 변수
        self.daily_notice.start()

    def cog_unload(self):
        self.daily_notice.cancel()

    @tasks.loop(time=time(hour=18, minute=0, tzinfo=KST))
    async def daily_notice(self):
        today = datetime.now(KST).date()

        # 당일 이미 실행되었다면 루프 중복 호출 스킵 (중복 발송 방지)
        if self.last_run_date == today:
            return
        self.last_run_date = today

        print("[DailyNotice] 오후 6시 정기 공지 스케줄 시작")

        # 1. 판매 공지 (매일 오후 6시 정각 전송)
        sales_channel = self.bot.get_channel(config.SALES_NOTICE_CHANNEL_ID)
        if sales_channel:
            try:
                await sales_channel.send(config.SALES_NOTICE_MESSAGE)
                print("[DailyNotice] 판매 공지 전송 완료 (매일)")
            except Exception as e:
                print(f"[DailyNotice] 판매 공지 오류: {e}")
        else:
            print("[DailyNotice] 판매 공지 채널을 찾을 수 없음")

        # 2. 한국어 / 영어 가이드 공지 (2일 주기 전송)
        if today.toordinal() % 2 == 0:
            # 한국어 가이드
            kr_channel = self.bot.get_channel(config.KR_CHAT_CHANNEL_ID)
            if kr_channel:
                try:
                    await kr_channel.send(config.GUIDE_MESSAGE_KR)
                    print("[DailyNotice] 한국어 가이드 공지 전송 완료 (2일 주기)")
                except Exception as e:
                    print(f"[DailyNotice] 한국어 공지 오류: {e}")

            # 영어 가이드
            en_channel = self.bot.get_channel(config.EN_CHAT_CHANNEL_ID)
            if en_channel:
                try:
                    await en_channel.send(config.GUIDE_MESSAGE_EN)
                    print("[DailyNotice] 영어 가이드 공지 전송 완료 (2일 주기)")
                except Exception as e:
                    print(f"[DailyNotice] 영어 공지 오류: {e}")

    @daily_notice.before_loop
    async def before(self):
        print("[DailyNotice] 루프 대기 시작")
        await self.bot.wait_until_ready()

async def setup(bot):
    await bot.add_cog(DailyNotice(bot))
