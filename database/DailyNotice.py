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
        # toordinal()을 활용한 이틀(48시간) 주기 발송 체크
        today = datetime.now(KST).date()
        if today.toordinal() % 2 != 0:
            return

        print("가이드 공지 실행 시작")

        try:
            channel = self.bot.get_channel(SALE_NOTICE_CHANNEL_ID)
            print(f"채널: {channel}")

            if channel is None:
                print("채널 없음")
                return

            embed = discord.Embed(
                title="✨ DDS (Design & Developer Service) 공식 가이드",
                description=(
                    "안녕하세요! **DDS 공식 커뮤니티**에 오신 것을 환영합니다! 🎉\n\n"
                    "서버를 효율적으로 이용하실 수 있도록 주요 채널 안내를 드립니다."
                ),
                color=0x5865F2
            )

            # 주요 이용 안내 채널 (보안실 -> 문의 채널로 변경)
            embed.add_field(
                name="📌 주요 이용 안내 채널",
                value=(
                    f"• <#{PURCHASE_CHANNEL_ID}> : 커미션 주문 및 문의/지원 신청\n"
                    f"• <#{EXAMPLE_CHANNEL_ID}> : 디자이너 샘플 및 예시작 감상\n"
                    f"• <#{DESIGNER_RANKING_CHANNEL_ID}> : 디자이너 등급 및 분야 현황\n"
                    f"• <#{DESIGNER_STATS_CHANNEL_ID}> : 디자이너 작업 완료 통계\n"
                    f"• <#{REVIEWS_CHANNEL_ID}> : 실제 이용 고객님들의 솔직한 후기"
                ),
                inline=False
            )

            # 포인트 & 혜택 시스템
            embed.add_field(
                name="🏛️ 포인트 & 혜택 시스템",
                value=(
                    f"• <#{POINTS_RANKING_CHANNEL_ID}> : 포인트 실시간 랭킹 확인\n"
                    f"• <#{GETTING_POINTS_CHANNEL_ID}> : 포인트 적립 방법 및 단골(15% 할인) 혜택 안내"
                ),
                inline=False
            )

            # 푸터 문구 수정
            embed.set_footer(text="자동 가이드 공지 | 이틀에 1회, 오후 6시 정각 업데이트")

            await channel.send(embed=embed)
            print("가이드 공지 전송 완료")

        except Exception as e:
            print(f"공지 오류: {e}")

    @daily_notice.before_loop
    async def before(self):
        print("DailyNotice 시작")
        await self.bot.wait_until_ready()

async def setup(bot):
    await bot.add_cog(DailyNotice(bot))
