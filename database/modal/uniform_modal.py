from database.modal.gfx_modal import PurchaseModal


class UniformModal(PurchaseModal):

    MODAL_TITLE = "👕 Roblox 복장 커미션 신청서"

    FORM_TITLE = "📋 Roblox 복장 커미션 신청서"
    FIELD1 = "👕 복장 이름"
    FIELD2 = "🎽 복장 종류"
    FIELD3 = "🎨 원하는 복장 스타일"

    def __init__(self):
        super().__init__()

        self.roblox_nickname.label = "복장 이름"
        self.roblox_nickname.placeholder = "예: 경찰 제복"

        self.gfx_type.label = "복장 종류"
        self.gfx_type.placeholder = "예: 경찰 / 군복 / 카페"

        self.gfx_style.label = "원하는 복장 스타일"
        self.gfx_style.placeholder = "예: 검정색, 금색 포인트, 현실풍"