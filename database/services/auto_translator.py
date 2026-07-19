import asyncio
import os
import re

import discord
from discord.ext import commands
from openai import AsyncOpenAI


# DDS 채널 ID
KOREAN_CHANNEL_ID = 1505074223356317771
ENGLISH_CHANNEL_ID = 1527725232864100362

# 전체 번역과 요약 번역을 나누는 기준
MAX_FULL_TRANSLATION_LENGTH = 500
MAX_FULL_TRANSLATION_SENTENCES = 4

# 동시에 처리할 최대 번역 요청 수
MAX_CONCURRENT_TRANSLATIONS = 5

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4.1-mini")


def count_sentences(text: str) -> int:
    """
    한국어와 영어의 문장부호를 기준으로 대략적인 문장 수를 계산합니다.
    """
    sentences = re.split(r"[.!?。！？]+(?:\s+|$)", text.strip())
    return len([sentence for sentence in sentences if sentence.strip()])


def should_summarize(text: str) -> bool:
    """
    500자를 넘거나 문장이 4개를 넘으면 요약 번역합니다.
    """
    return (
        len(text) > MAX_FULL_TRANSLATION_LENGTH
        or count_sentences(text) > MAX_FULL_TRANSLATION_SENTENCES
    )


class AutoTranslator(commands.Cog):

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.semaphore = asyncio.Semaphore(MAX_CONCURRENT_TRANSLATIONS)

        if not OPENAI_API_KEY:
            raise RuntimeError(
                "OPENAI_API_KEY 환경변수가 설정되지 않았습니다."
            )

        self.client = AsyncOpenAI(
            api_key=OPENAI_API_KEY,
            timeout=15.0
        )

    async def translate_message(
        self,
        text: str,
        source_language: str,
        target_language: str,
        summarize: bool
    ) -> str:
        if summarize:
            task_instruction = (
                f"다음 {source_language} 메시지를 읽고 핵심 내용을 빠뜨리지 않도록 "
                f"{target_language}로 짧고 자연스럽게 요약 번역하세요. "
                "불필요한 설명, 제목, 따옴표, '번역:' 같은 표시는 넣지 마세요. "
                "원문의 말투와 의도를 최대한 유지하세요."
            )
        else:
            task_instruction = (
                f"다음 {source_language} 메시지를 {target_language}로 자연스럽게 번역하세요. "
                "단어를 기계적으로 직역하지 말고 말투, 감정, 뉘앙스와 문맥을 유지하세요. "
                "내용을 추가하거나 빼지 마세요. "
                "불필요한 설명, 따옴표, '번역:' 같은 표시는 넣지 마세요."
            )

        response = await self.client.responses.create(
            model=OPENAI_MODEL,
            instructions=task_instruction,
            input=text,
            max_output_tokens=500
        )

        result = response.output_text.strip()

        if not result:
            raise RuntimeError("번역 결과가 비어 있습니다.")

        return result

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        # 봇이 보낸 번역문을 다시 번역하는 무한 반복 방지
        if message.author.bot:
            return

        # DM 제외
        if message.guild is None:
            return

        content = message.content.strip()

        # 빈 메시지와 파일만 있는 메시지 제외
        if not content:
            return

        # 봇 명령어 제외
        command_prefixes = ("!", "?", ".", "/")

        if content.startswith(command_prefixes):
            return

        if message.channel.id == KOREAN_CHANNEL_ID:
            source_language = "한국어"
            target_language = "자연스러운 영어"
            flag = "🇺🇸"

        elif message.channel.id == ENGLISH_CHANNEL_ID:
            source_language = "영어"
            target_language = "자연스러운 한국어"
            flag = "🇰🇷"

        else:
            return

        summarize = should_summarize(content)

        try:
            async with self.semaphore:
                translated = await asyncio.wait_for(
                    self.translate_message(
                        text=content,
                        source_language=source_language,
                        target_language=target_language,
                        summarize=summarize
                    ),
                    timeout=18
                )

            if summarize:
                output = f"{flag} **요약 번역:** {translated}"
            else:
                output = f"{flag} {translated}"

            # Discord 메시지 최대 길이 보호
            if len(output) > 2000:
                output = output[:1997] + "..."

            # 같은 채널에서 원문에 답글로 번역 표시
            await message.reply(
                output,
                mention_author=False,
                silent=True,
                allowed_mentions=discord.AllowedMentions.none()
            )

        except asyncio.TimeoutError:
            print(
                f"[자동 번역 시간 초과] "
                f"메시지 ID: {message.id}"
            )

        except discord.Forbidden:
            print(
                f"[자동 번역 권한 오류] "
                f"채널에서 메시지를 보낼 권한이 없습니다: {message.channel.id}"
            )

        except discord.HTTPException as error:
            print(f"[Discord 전송 오류] {error}")

        except Exception as error:
            print(
                f"[자동 번역 오류] "
                f"{type(error).__name__}: {error}"
            )


async def setup(bot: commands.Bot):
    await bot.add_cog(AutoTranslator(bot))
