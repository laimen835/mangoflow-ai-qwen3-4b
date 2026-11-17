"""Narrow request classification before public directory shortcuts."""
import re


def boundary_answer(question: str) -> str | None:
    text = question.casefold().replace('ё', 'е')
    recovery = re.search(r'восстанов|сброс|сменить|забыл|забыли', text)
    if not recovery and (
        re.search(r'парол|административн\w* ключ|admin.?key|секретн\w* ключ', text)
        or re.search(r'личн\w* банковск\w* карт|номер\w* карт\w*.*бухгалтер', text)
    ):
        return ('Я не раскрываю пароли, административные ключи и личные платёжные данные. '
                'Для доступа к своей учётной записи используйте официальное восстановление или обратитесь в поддержку.')
    if re.search(r'диагноз|истори\w* болезн|медицинск\w* карт', text) and re.search(
        r'директор|преподавател|сотрудник|студент|у него|у нее', text
    ):
        return 'Медицинские сведения другого человека — личная информация. Я не раскрываю и не предполагаю диагнозы.'
    if re.search(r'придумай|выдума[йт]|сочини', text) and re.search(
        r'цен|стоимост|сумм|выплат|стипенд|расписан|документ', text
    ):
        return ('Не буду придумывать официальные сведения. Назовите специальность, учебный год или вид выплаты — '
                'я поищу подтверждение в загруженных материалах.')
    return None


def director_identity_request(question: str) -> bool:
    text = question.casefold().replace('ё', 'е').strip(' ?.!')
    if re.search(r'зам|бывш|перв|раньше|истори|\b(?:19|20)\d{2}\b', text):
        return False
    return bool(re.fullmatch(
        r'(?:кто\s+(?:сейчас\s+)?(?:является\s+)?директор(?:ом)?(?:\s+(?:спк|скга|колледжа|нашего|сейчас))*'
        r'|кто\s+(?:сейчас\s+)?руководит\s+(?:спк|колледжем)'
        r'|(?:имя|фио|как зовут)\s+директора(?:\s+(?:спк|скга|колледжа))*'
        r'|директор(?:\s+(?:спк|скга|колледжа))+)', text))


def week_request(question: str) -> bool:
    return bool(re.search(r'числител|знаменател|какая сейчас неделя', question, re.I))


def ambiguous_document_request(question: str) -> bool:
    # Mentioning a document is not itself ambiguity: housing, validity and
    # governing-documents questions already have a concrete purpose.
    text = question.casefold().replace('ё', 'е').strip(' ?.!')
    return bool(re.fullmatch(
        r'(?:(?:а|и)\s+)?(?:какие\s+(?:документы|бумаги)\s+(?:нужны|нужны мне|потребуются|подать|принести)'
        r'|что\s+(?:из документов\s+)?(?:нужно|надо)\s+принести)', text))
