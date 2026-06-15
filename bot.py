async def t_create_panel(ctx):

    file = discord.File("price.png", filename="price.png")

    embed = discord.Embed(
        title="💼 커미션 및 문의 상담 공간",
        description=(
            "상담, 구매 진행, 문의사항이 있으시다면\n"
            "아래 📩 버튼을 눌러주세요!\n\n"
            "📌 아래 가격표를 먼저 확인해주세요."
        ),
        color=0x5865F2
    )

    embed.set_image(url="attachment://price.png")

    await ctx.send(
        file=file,
        embed=embed,
        view=TicketOpenView()
    )
