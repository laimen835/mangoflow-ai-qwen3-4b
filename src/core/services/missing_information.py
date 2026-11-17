"""Helpful, source-grounded next steps without answering from model memory."""
from dataclasses import dataclass
import re
from sqlalchemy import select
from src.core.db.models import Document,DocumentStatus
from src.core.rag.ncsa_source import SEED
from src.core.rag.scholarship_source import GRANTS
from src.core.services.request_scope import ambiguous_document_request
from src.core.rag.answerability import asks_application_recipient

ADMISSION='https://ncsa.ru/institutes/spk/postupayushchim-v-spk/'


@dataclass
class MissingInformation:
    text:str
    kind:str
    document:Document|None=None


async def missing_information(db,question,*,status='no_match',group=None,units=()):
    if status=='unavailable':
        return MissingInformation('Сейчас не удаётся выполнить поиск по материалам академии. '
            'Это временная техническая ошибка, а не подтверждение отсутствия сведений. '
            'Пожалуйста, повторите вопрос немного позже.','search_error')
    if units and any(u.key != 'spk' for u in units):
        sections = '; '.join(f'{u.name}: {u.url}' for u in units)
        return MissingInformation('В загруженных материалах выбранного подразделения я не нашёл достаточного подтверждения. '
            'Сведения другого института или колледжа подставлять не буду. '
            'Общий раздел для проверки (сам по себе не подтверждает ответ): ' + sections, 'no_confirmation')
    text=question.casefold()
    if any(w in text for w in ('пары','занятия')) and not group and not re.search(r'\d{2}\.\d{2}\.\d{2}-\d{3}',text):
        return MissingInformation('Уточните, пожалуйста, вашу группу — например, 09.02.07-251. '
            'Это поможет найти документ нужного отделения.','clarification')
    if ambiguous_document_request(question):
        return MissingInformation('Для чего нужны документы: для поступления в колледж или для оформления во время учёбы?',
                                  'clarification')
    admission=any(w in text for w in ('поступ','абитуриент','приём','прием','стоимост','бюджетных мест','платить'))
    scholarships=any(w in text for w in ('стипенд','степенд','материальн','социальная поддержка'))
    url=GRANTS if scholarships else ADMISSION if admission else SEED
    doc=(await db.execute(select(Document).where(Document.source_url==url,Document.source_type.in_(['ncsa_web','ncsa_scholarship']),
        Document.status==DocumentStatus.INDEXED).order_by(Document.id).limit(1))).scalar_one_or_none()
    subject=('подтверждённого адресата заявления' if asks_application_recipient(question) else
             'точную стоимость или сумму оплаты' if any(w in text for w in ('стоимост','рубл','стоит','цен')) else
             'подтверждённые сроки для указанного года' if any(w in text for w in ('срок','числа','2030')) else
             'подтверждённую информацию по этому вопросу')
    answer=f'Я не нашёл в загруженных материалах {subject}. Не буду подставлять предположения.'
    if doc:
        label='Общий раздел о стипендиях и поддержке' if scholarships else 'Общий раздел для поступающих' if admission else 'Общая страница колледжа'
        answer+=f'\n\n{label}: {doc.source_url}\nЭто справочный раздел для дальнейшей проверки, а не подтверждение отсутствующих сведений.'
    else:
        answer+='\nНужен актуальный документ колледжа с этими сведениями; его можно добавить в базу после проверки.'
    return MissingInformation(answer,'missing_fact',doc)


def is_full_refusal(answer):
    text=answer.strip().casefold()
    return len(answer)<650 and any(text.startswith(s) for s in (
        'я не наш','не нашёл','не нашел','в базе знаний нет','в контексте нет информации'))
