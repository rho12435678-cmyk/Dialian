from database.modal.gfx_modal import PurchaseModal


class LogoModal(PurchaseModal):

    def __init__(self):
        super().__init__()

        self.title = "🖌 로고 커미션 신청서"

        self.roblox_nickname.label = "로고 이름"
        self.roblox_nickname.placeholder = "예: My Server"

        self.gfx_type.label = "로고 종류"
        self.gfx_type.placeholder = "예: Discord 서버 로고"

        self.gfx_style.label = "원하는 로고 스타일"
        self.gfx_style.placeholder = "예: 미니멀, 네온, 게이밍"