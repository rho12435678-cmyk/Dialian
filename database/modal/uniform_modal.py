import discord

from database.modal.simple_ticket_modal import SimpleTicketModal


class UniformModal(SimpleTicketModal):

    MODAL_TITLE = "👕 Roblox 복장 커미션 신청서"
    FORM_TITLE = "📋 Roblox 복장 커미션 신청서"
    FIELD_NAME = "👕 원하는 복장 내용"