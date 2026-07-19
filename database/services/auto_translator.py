import asyncio
import os
import re
import unicodedata

import discord
from discord.ext import commands
from openai import AsyncOpenAI


# =========================================================
# 채널 설정
# =========================================================

KOREAN_CHANNEL_ID = 1505074223356317771
ENGLISH_CHANNEL_ID = 1527725232864100362


# =========================================================
# 번역 설정
# =========================================================

SUMMARY_SENTENCE_THRESHOLD = 3
MAX_CONCURRENT_TRANSLATIONS = 5

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4.1-mini")

SKIP_RESPONSE = "__SKIP__"


# =========================================================
# 정규식
# =========================================================

DISCORD_EMOJI_PATTERN = re.compile(
    r"<a?:[A-Za-z0-9_]+:\d+>"
)

HANGUL_SYLLABLE_PATTERN = re.compile(
    r"[가-힣]"
)

HANGUL_JAMO_PATTERN = re.compile(
    r"[ㄱ-ㅎㅏ-ㅣ]"
)

LATIN_PATTERN = re.compile(
    r"[A-Za-z]"
)

JAPANESE_PATTERN = re.compile(
    r"[\u3040-\u309F"
    r"\u30A0-\u30FF"
    r"\u31F0-\u31FF]"
)

CJK_PATTERN = re.compile(
    r"[\u3400-\u4DBF\u4E00-\u9FFF]"
)

URL_PATTERN = re.compile(
    r"https?://\S+|www\.\S+",
    re.IGNORECASE
)

MENTION_PATTERN = re.compile(
    r"<@!?\d+>|<@&\d+>|<#\d+>"
)

MULTI_SPACE_PATTERN = re.compile(
    r"[ \t]+"
)

ONLY_JAMO_PATTERN = re.compile(
    r"^[ㄱ-ㅎㅏ-ㅣ\s]+$"
)

ONLY_SYMBOLS_PATTERN = re.compile(
    r"^[\W_]+$",
    re.UNICODE
)


# =========================================================
# 문장 수 / 요약 판정
# =========================================================

def count_sentences(text: str) -> int:
    """
    문장부호와 줄바꿈을 기준으로 문장 수를 계산합니다.
    """
    text = text.strip()

    if not text:
        return 0

    normalized = re.sub(
        r"\n+",
        ". ",
        text
    )

    parts = re.split(
        r"[.!?。！？]+",
        normalized
    )

    sentences = [
        part.strip()
        for part in parts
        if part.strip()
    ]

    return len(sentences)


def should_summarize(text: str) -> bool:
    """
    3문장 이상이면 요약 번역합니다.
    """
    return (
        count_sentences(text)
        >= SUMMARY_SENTENCE_THRESHOLD
    )


# =========================================================
# 이모지 처리
# =========================================================

def is_emoji_character(character: str) -> bool:
    code = ord(character)

    emoji_ranges = (
        0x1F1E6 <= code <= 0x1F1FF,
        0x1F300 <= code <= 0x1F5FF,
        0x1F600 <= code <= 0x1F64F,
        0x1F680 <= code <= 0x1F6FF,
        0x1F700 <= code <= 0x1F77F,
        0x1F780 <= code <= 0x1F7FF,
        0x1F800 <= code <= 0x1F8FF,
        0x1F900 <= code <= 0x1F9FF,
        0x1FA00 <= code <= 0x1FAFF,
        0x2600 <= code <= 0x26FF,
        0x2700 <= code <= 0x27BF,
    )

    if any(emoji_ranges):
        return True

    if code in (
        0xFE0E,
        0xFE0F,
        0x200D,
        0x20E3
    ):
        return True

    if 0x1F3FB <= code <= 0x1F3FF:
        return True

    return False


def remove_emojis(text: str) -> str:
    text = DISCORD_EMOJI_PATTERN.sub(
        "",
        text
    )

    return "".join(
        character
        for character in text
        if not is_emoji_character(character)
    )


# =========================================================
# 텍스트 정리
# =========================================================

def clean_message_text(text: str) -> str:
    """
    이모지와 제어문자를 제거하고 공백을 정리합니다.
    문장 안의 초성 표현은 보존합니다.
    """
    text = unicodedata.normalize(
        "NFKC",
        text
    )

    text = remove_emojis(text)

    text = "".join(
        character
        for character in text
        if (
            character in "\n\t"
            or unicodedata.category(character)[0] != "C"
        )
    )

    text = MULTI_SPACE_PATTERN.sub(
        " ",
        text
    )

    lines = [
        line.strip()
        for line in text.splitlines()
        if line.strip()
    ]

    return "\n".join(lines).strip()


def extract_meaningful_characters(text: str) -> str:
    """
    URL, 멘션, 공백, 문장부호를 제거한 판정용 문자열입니다.
    """
    value = URL_PATTERN.sub(
        "",
        text
    )

    value = MENTION_PATTERN.sub(
        "",
        value
    )

    return "".join(
        character
        for character in value
        if (
            character.isalnum()
            or HANGUL_JAMO_PATTERN.match(character)
        )
    )


# =========================================================
# 메시지 무시 판정
# =========================================================

def is_only_jamo_message(text: str) -> bool:
    """
    초성·중성만 있는 메시지는 전부 무시합니다.

    예:
    ㄷ
    ㅋㅋㅋㅋ
    ㅇㅇ
    ㄹㅇ
    ㅈㅅ
    """
    compact = re.sub(
        r"\s+",
        "",
        text
    )

    if not compact:
        return True

    return bool(
        ONLY_JAMO_PATTERN.fullmatch(compact)
    )


def is_low_information_message(text: str) -> bool:
    compact = extract_meaningful_characters(
        text
    )

    if not compact:
        return True

    if ONLY_SYMBOLS_PATTERN.fullmatch(
        text
    ):
        return True

    if is_only_jamo_message(text):
        return True

    if len(compact) < 2:
        return True

    return False


def is_valid_korean_channel_message(
    text: str
) -> bool:
    """
    한국어 채널은 완성형 한글이 들어간 메시지만 처리합니다.
    문장 안의 영어, 초성, 숫자, 용어는 허용합니다.
    """
    if not HANGUL_SYLLABLE_PATTERN.search(
        text
    ):
        return False

    if (
        JAPANESE_PATTERN.search(text)
        and not HANGUL_SYLLABLE_PATTERN.search(text)
    ):
        return False

    return True


def is_valid_english_channel_message(
    text: str
) -> bool:
    """
    영어 채널은 영어 알파벳이 들어간 메시지만 처리합니다.
    문장 안의 숫자나 게임 용어는 허용합니다.
    """
    if not LATIN_PATTERN.search(
        text
    ):
        return False

    if HANGUL_SYLLABLE_PATTERN.search(
        text
    ):
        return False

    if JAPANESE_PATTERN.search(
        text
    ):
        return False

    return True


# =========================================================
# 출력 검증
# =========================================================

def appears_to_be_meta_response(
    text: str
) -> bool:
    """
    AI가 번역 대신 설명, 질문, 안내를 한 경우 차단합니다.
    """
    lowered = text.lower().strip()

    blocked_phrases = (
        "please provide",
        "please enter",
        "could you please",
        "there is no actual message",
        "does not convey meaning",
        "doesn't form a meaningful",
        "i can translate",
        "translation cannot",
        "번역할 문장을 입력",
        "메시지를 입력",
        "올바른 문장을 입력",
        "번역할 내용이",
        "의미가 없습니다",
        "해석할 수 없습니다",
        "번역할 수 없습니다",
    )

    return any(
        phrase in lowered
        for phrase in blocked_phrases
    )


def remove_wrapping_quotes(
    text: str
) -> str:
    """
    번역 결과를 감싼 따옴표를 제거합니다.
    """
    text = text.strip()

    quote_pairs = (
        ('"', '"'),
        ("'", "'"),
        ("“", "”"),
        ("‘", "’"),
    )

    for start_quote, end_quote in quote_pairs:
        if (
            len(text) >= 2
            and text.startswith(start_quote)
            and text.endswith(end_quote)
        ):
            return text[1:-1].strip()

    return text


# =========================================================
# 자동 번역 Cog
# =========================================================

class AutoTranslator(commands.Cog):

    def __init__(
        self,
        bot: commands.Bot
    ):
        self.bot = bot

        self.semaphore = asyncio.Semaphore(
            MAX_CONCURRENT_TRANSLATIONS
        )

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

        common_rules = (
            "당신은 Discord 실시간 채팅 전용 번역기입니다.\n"
            f"입력 언어: {source_language}\n"
            f"출력 언어: {target_language}\n\n"

            "다음 규칙을 반드시 지키세요:\n"
            "1. 번역문만 출력하세요.\n"
            "2. 질문에 대답하거나 대화를 이어가지 마세요.\n"
            "3. 설명, 해설, 경고, 제목, 따옴표를 추가하지 마세요.\n"
            "4. 사용자에게 원문을 다시 입력하라고 하지 마세요.\n"
            "5. 원문에 없는 정보나 감정을 만들어내지 마세요.\n"
            "6. 단어를 1대1로 억지 대응하지 마세요.\n"
            "7. 가능한 한 쉬운 일상 단어를 사용하세요.\n"
            "8. 가능한 한 짧고 읽기 쉬운 문장을 사용하세요.\n"
            "9. 긴 한 문장보다 자연스러운 짧은 문장으로 나누세요.\n"
            "10. 원문의 의미와 뉘앙스는 유지하세요.\n"
            "11. 구어체는 자연스러운 구어체로 번역하세요.\n"
            "12. 격식체는 자연스러운 격식체로 번역하세요.\n"
            "13. 무례함, 친근함, 장난스러움, 진지함의 정도를 유지하세요.\n"
            "14. Discord, Roblox, 게임 용어는 실제 채팅 표현처럼 번역하세요.\n"
            "15. 문장 안의 ㅋㅋ, ㅎㅎ, ㄹㅇ, ㅇㅇ, ㄴㄴ, ㅈㅅ, ㄱㄱ 같은 "
            "초성 표현은 문맥에 맞는 자연스러운 표현으로 바꾸세요.\n"
            "16. 이모지와 이모티콘은 출력하지 마세요.\n"
            "17. 입력이 의미 없는 오타나 무작위 문자열이면 정확히 "
            f"{SKIP_RESPONSE}만 출력하세요.\n"
        )

        if summarize:
            task_instruction = (
                common_rules
                + "\n이 메시지는 3문장 이상입니다.\n"
                "모든 문장을 그대로 옮기지 말고 핵심 의미와 요청사항만 "
                "짧게 요약 번역하세요.\n"
                "날짜, 시간, 숫자, 가격, 조건, 마감일은 빠뜨리지 마세요.\n"
                "결과는 가능하면 1~3개의 짧은 문장으로 작성하세요."
            )

            max_tokens = 250

        else:
            task_instruction = (
                common_rules
                + "\n이 메시지는 짧은 일반 채팅입니다.\n"
                "내용을 생략하거나 요약하지 말고 전체 의미를 번역하세요.\n"
                "직역보다 실제 사용자가 채팅에서 쓸 법한 자연스러운 표현을 "
                "우선하세요.\n"
                "단, 의미를 과장하거나 바꾸지는 마세요."
            )

            max_tokens = 150

        response = await self.client.responses.create(
            model=OPENAI_MODEL,
            instructions=task_instruction,
            input=text,
            max_output_tokens=max_tokens
        )

        result = response.output_text.strip()

        if not result:
            return SKIP_RESPONSE

        return remove_wrapping_quotes(
            result
        )

    @commands.Cog.listener()
    async def on_message(
        self,
        message: discord.Message
    ):

        if message.author.bot:
            return

        if message.guild is None:
            return

        if message.channel.id not in {
            KOREAN_CHANNEL_ID,
            ENGLISH_CHANNEL_ID
        }:
            return

        if (
            message.stickers
            and not message.content.strip()
        ):
            return

        if not message.content.strip():
            return

        raw_content = message.content.strip()

        if raw_content.startswith(
            ("!", "?", ".", "/")
        ):
            return

        content = clean_message_text(
            raw_content
        )

        if not content:
            return

        # 초성만 있는 메시지는 전부 무시
        if is_only_jamo_message(
            content
        ):
            return

        if is_low_information_message(
            content
        ):
            return

        if (
            message.channel.id
            == KOREAN_CHANNEL_ID
        ):
            if not is_valid_korean_channel_message(
                content
            ):
                return

            source_language = "한국어"
            target_language = "자연스러운 영어"
            flag = "🇺🇸"

        else:
            if not is_valid_english_channel_message(
                content
            ):
                return

            source_language = "영어"
            target_language = "자연스러운 한국어"
            flag = "🇰🇷"

        summarize = should_summarize(
            content
        )

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

            translated = translated.strip()

            if (
                translated.upper()
                == SKIP_RESPONSE.upper()
            ):
                return

            if appears_to_be_meta_response(
                translated
            ):
                print(
                    "[자동 번역 차단] 설명형 응답: "
                    f"{translated[:120]}"
                )
                return

            # 한국어 → 영어 결과 검증
            if (
                message.channel.id
                == KOREAN_CHANNEL_ID
                and not LATIN_PATTERN.search(
                    translated
                )
            ):
                print(
                    "[자동 번역 차단] 영어 결과 아님: "
                    f"{translated[:120]}"
                )
                return

            # 영어 → 한국어 결과 검증
            if (
                message.channel.id
                == ENGLISH_CHANNEL_ID
                and not HANGUL_SYLLABLE_PATTERN.search(
                    translated
                )
            ):
                print(
                    "[자동 번역 차단] 한국어 결과 아님: "
                    f"{translated[:120]}"
                )
                return

            if (
                translated.casefold()
                == content.casefold()
            ):
                return

            if summarize:
                output = (
                    f"{flag} **요약:** {translated}"
                )
            else:
                output = (
                    f"{flag} {translated}"
                )

            if len(output) > 2000:
                output = (
                    output[:1997]
                    + "..."
                )

            await message.reply(
                output,
                mention_author=False,
                silent=True,
                allowed_mentions=(
                    discord.AllowedMentions.none()
                )
            )

        except asyncio.TimeoutError:
            print(
                "[자동 번역 시간 초과] "
                f"message_id={message.id}"
            )

        except discord.Forbidden:
            print(
                "[자동 번역 권한 오류] "
                f"channel_id={message.channel.id}"
            )

        except discord.HTTPException as error:
            print(
                f"[Discord 전송 오류] {error}"
            )

        except Exception as error:
            print(
                "[자동 번역 오류] "
                f"{type(error).__name__}: {error}"
            )


async def setup(
    bot: commands.Bot
):
    await bot.add_cog(
        AutoTranslator(bot)
    )
