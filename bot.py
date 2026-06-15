@bot.command(name="가격표")
async def price(ctx):
    channel = bot.get_channel(PRICE_CHANNEL_ID)

    file = discord.File("price.png", filename="price.png")

    embed = discord.Embed(
        title="🎨 Dial GFX Hub 가격표",
        color=0x5865F2
    )

    embed.set_image(url="https://cdn.discordapp.com/attachments/1396537248018731020/1516085715740393612/file_0000000047c47206840f4e48fc0c0f9d_.png?ex=6a315c5a&is=6a300ada&hm=b8ccdadedfac2b65efa9a2d80ad781483b892c9a04d1e03bf407570bca173689&")

    await channel.send(file=file, embed=embed)
