import discord
from discord.ext import commands
import os
import asyncio

TOKEN = os.getenv("TOKEN")

# 채널 감시 키워드 (구매로그, log 등이 포함되면 인식)
LOG_CHANNEL_KEYWORDS = ["구매로그"] 
REVIEW_CHANNEL_NAME = "😄후기"

intents = discord.Intents.all()
bot = commands.Bot(command_prefix="!", intents=intents)


class ReviewView(discord.ui.View):
    def __init__(self, guild):
        super().__init__(timeout=None)
        self.guild = guild

    @discord.ui.button(label="리뷰 작성하러 가기", style=discord.ButtonStyle.green)
    async def review_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            review_channel = discord.utils.get(self.guild.text_channels, name=REVIEW_CHANNEL_NAME)
            if not review_channel:
                review_channel = next((ch for ch in self.guild.text_channels if "후기" in ch.name), None)

            if review_channel:
                await review_channel.send(
                    f"{interaction.user.mention} 님, 최근 커미션 후기를 아래에 작성 부탁드립니다 ⭐"
                )
                await interaction.response.send_message(
                    "리뷰 채널로 안내되었습니다. 감사합니다!",
                    ephemeral=True
                )
            else:
                await interaction.response.send_message(
                    "리뷰 채널을 찾을 수 없습니다. 서버 관리자에게 문의해 주세요.",
                    ephemeral=True
                )
        except Exception as e:
            print(f"[버튼 에러 발생] {e} - 하지만 봇은 죽지 않습니다.")


@bot.event
async def on_message(message: discord.Message):
    try:
        # 봇 자신이 쓴 메시지는 무시
        if message.author == bot.user:
            return

        await bot.process_commands(message)

        # 1. 채널 이름 검사
        if not any(keyword in message.channel.name for keyword in LOG_CHANNEL_KEYWORDS):
            return

        # 2. 멘션(태그)된 유저가 있는지 확인
        if message.mentions:
            owner = next((user for user in message.mentions if not user.bot), None)
            
            if owner:
                try:
                    embed = discord.Embed(
                        title="⭐ 커미션을 이용해주셔서 감사합니다!",
                        description=(
                            f"안녕하세요, {owner.mention}님! 이용하신 티켓이 종료되었습니다.\n\n"
                            "작업에 만족하셨다면, 간단한 후기를 남겨주시면 큰 도움이 됩니다 🙏\n"
                            "아래 버튼을 눌러 리뷰를 작성해주세요!"
                        ),
                        color=discord.Color.gold()
                    )

                    # 🌟 DM 전송 시도
                    await owner.send(embed=embed, view=ReviewView(message.guild))
                    print(f"[성공] {owner.name}님에게 자동 DM 전송 완료.")

                # ⚠️ 유저가 DM을 차단했을 때 발생하는 핵심 에러 원천 차단
                except discord.Forbidden:
                    print(f"[DM 차단됨] {owner.name}님이 서버 DM을 차단하여 발송에 실패했습니다. (봇 유지)")
                except discord.HTTPException as he:
                    print(f"[디스코드 통신 에러] {he} (봇 유지)")
                except Exception as e:
                    print(f"[DM 발송 중 의외의 에러] {e} (봇 유지)")
                    
    except Exception as general_error:
        print(f"[메시지 감시 중 치명적 에러 발생] {general_error} - 튕김을 방지하고 시스템을 유지합니다.")


@bot.event
async def on_ready():
    print(f"리뷰 감시 봇 실행됨: {bot.user}")


async def main():
    async with bot:
        await bot.start(TOKEN)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("봇이 수동으로 종료되었습니다.")
    except Exception as e:
        print(f"메인 루프 에러: {e}")