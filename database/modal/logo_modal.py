import discord

from database.modal.simple_ticket_modal import SimpleTicketModal


class LogoModal(SimpleTicketModal):

    MODAL_TITLE = "🖌 로고 커미션 신청서"
    FORM_TITLE = "📋 로고 커미션 신청서"
    FIELD_NAME = "🖌 원하는 로고 내용"