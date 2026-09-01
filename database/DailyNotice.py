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

        # 1. 판매 공지 (매일 오후 6시 정각 Embed 전송)
        sales_channel = self.bot.get_channel(config.SALES_NOTICE_CHANNEL_ID)
        if sales_channel:
            try:
                sales_embed = discord.Embed(
                    title="📢 Sales Notice / 판매 공지",
                    description=config.SALES_NOTICE_MESSAGE,
                    color=discord.Color.gold()
                )
                await sales_channel.send(embed=sales_embed)
                print("[DailyNotice] 판매 공지 전송 완료 (매일)")
            except Exception as e:
                print(f"[DailyNotice] 판매 공지 오류: {e}")
        else:
            print("[DailyNotice] 판매 공지 채널을 찾을 수 없음")

        # 2. 한국어 / 영어 가이드 공지 (교대 전송: 짝수일 KR, 홀수일 EN)
        if today.toordinal() % 2 == 0:
            # 짝수일: 한국어 가이드
            kr_channel = self.bot.get_channel(config.KR_CHAT_CHANNEL_ID)
            if kr_channel:
                try:
                    kr_embed = discord.Embed(
                        title="📘 공식 가이드 안내",
                        description=config.GUIDE_MESSAGE_KR,
                        color=discord.Color.blue()
                    )
                    await kr_channel.send(embed=kr_embed)
                    print("[DailyNotice] 한국어 가이드 공지 전송 완료 (짝수일)")
                except Exception as e:
                    print(f"[DailyNotice] 한국어 공지 오류: {e}")
        else:
            # 홀수일: 영어 가이드
            en_channel = self.bot.get_channel(config.EN_CHAT_CHANNEL_ID)
            if en_channel:
                try:
                    en_embed = discord.Embed(
                        title="📘 Official Guide",
                        description=config.GUIDE_MESSAGE_EN,
                        color=discord.Color.blue()
                    )
                    await en_channel.send(embed=en_embed)
                    print("[DailyNotice] 영어 가이드 공지 전송 완료 (홀수일)")
                except Exception as e:
                    print(f"[DailyNotice] 영어 공지 오류: {e}")

    @daily_notice.before_loop
    async def before(self):
        print("[DailyNotice] 루프 대기 시작")
        await self.bot.wait_until_ready()

async def setup(bot):
    await bot.add_cog(DailyNotice(bot))
