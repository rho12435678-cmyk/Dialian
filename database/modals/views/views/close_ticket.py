import discord
import asyncio
import re

from datetime import datetime

from config import *

from views.review_view import StarRatingView

                            try:

                                success_role_embed = discord.Embed(
                                    title="🎉 구매자 역할 지급 완료",
                                    description=(
                                        f"`{guild.name}` 서버에서\n"
                                        f"구매자 역할이 지급되었습니다!"
                                    ),
                                    color=discord.Color.green()
                                )

                                await ticket_owner.send(
                                    embed=success_role_embed
                                )

                            except:
                                pass

                except Exception as role_err:
                    print(f"[구매자 역할 지급 실패] {role_err}")

                # ==============================
                # 유저 DM
                # ==============================

                try:

                    dm_embed = discord.Embed(
                        title="💌 서비스를 이용해 주셔서 감사합니다!",
                        description=(
                            "진행하시던 커미션 완료되어 티켓이 종료되었습니다.\n"
                            "아래 버튼을 통해 만족도 별점을 남겨주세요!"
                        ),
                        color=0x5865F2
                    )

                    await ticket_owner.send(
                        embed=dm_embed,
                        view=StarRatingView()
                    )

                except Exception as dm_e:
                    print(f"[DM 실패] {dm_e}")

                await interaction.followup.send(
                    "⚠️ 로그 정리 완료! 채널은 5초 후 삭제됩니다."
                )

                await asyncio.sleep(5)
                await channel.delete()

            else:

                await interaction.followup.send(
                    "❌ 올바른 티켓 채널이 아닙니다.",
                    ephemeral=True
                )

        except Exception as e:
            print(f"[티켓 닫기 에러] {e}")
