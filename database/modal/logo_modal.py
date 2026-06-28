import discord

from database.modal.gfx_modal import PurchaseModal


class LogoModal(PurchaseModal):

    MODAL_TITLE = "🖌 로고 커미션 신청서"
    FORM_TITLE = "📋 로고 커미션 신청서"

    FIELD1 = "🖌 로고 내용"
    FIELD2 = "➖"
    FIELD3 = "➖"

    def __init__(self):
        super().__init__()

        self.roblox_nickname.label = "원하는 로고 내용을 작성해주세요"
        self.roblox_nickname.placeholder = "원하는 색상, 분위기, 참고사항 등을 자유롭게 작성해주세요."
        self.roblox_nickname.max_length = 1000

        self.gfx_type.required = False
        self.gfx_type.default = "-"

        self.gfx_style.required = False
        self.gfx_style.default = "-"