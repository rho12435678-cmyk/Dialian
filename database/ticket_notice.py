import discord


def build_ticket_notice_embed():
    embed = discord.Embed(
        title="커미션 안내 사항",
        color=discord.Color.orange(),
    )
    embed.add_field(
        name="기본 안내",
        value=(
            "1. 가격 협상(네고) 안됨\n"
            "2. 작업 중 과도한 수정요청 삼가\n"
            "3. 모든 커미션은 선 결제 후 작업을 원칙으로 함\n"
            "4. 커미션 중 철회 시 수수료 부담"
        ),
        inline=False,
    )
    embed.add_field(
        name="철회 수수료",
        value=(
            "작업 전 철회 : 전액 환불\n\n"
            "작업 후 철회 :\n"
            "상급 : 3,000원\n"
            "중급 : 2,000원\n"
            "초급 : 1,500원\n\n"
            "복장 커미션 : 1,500원"
        ),
        inline=False,
    )
    return embed
