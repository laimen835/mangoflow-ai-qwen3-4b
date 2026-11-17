"""Source admission: preserve legacy SPO reviews and bind general reviews to content."""
from sqlalchemy import and_, func, not_, or_
from src.core.db.models import Document


def source_review_valid(meta, content_hash):
    review=meta.get('source_review') or {}
    return (review.get('coverage')=='full_document' and review.get('text_verified') is True
        and review.get('applicability_verified') is True
        and bool(meta.get('unit_key')) and review.get('unit_key')==meta.get('unit_key')
        and len(str(meta.get('raw_sha256','')))==64 and review.get('raw_sha256')==meta['raw_sha256']
        and bool(content_hash) and review.get('text_sha256')==content_hash
        and bool(meta.get('extraction_version')) and review.get('extraction_version')==meta['extraction_version'])


def real_document_filter():
    generic=Document.meta.contains({'review_policy':'source_text_v1'})
    required=or_(Document.meta.contains({'scholarship_material':True}),Document.meta.contains({'review_required':True}))
    review=Document.meta['source_review']
    bound_review=and_(Document.meta.contains({'source_review':{'coverage':'full_document','text_verified':True,'applicability_verified':True}}),
        review['unit_key'].astext==Document.meta['unit_key'].astext,
        func.length(Document.meta['raw_sha256'].astext)==64,
        review['raw_sha256'].astext==Document.meta['raw_sha256'].astext,
        review['text_sha256'].astext==Document.content_hash,
        Document.meta['extraction_version'].astext.is_not(None),
        review['extraction_version'].astext==Document.meta['extraction_version'].astext)
    legacy=and_(not_(generic),or_(not_(required),Document.meta.contains({'review':{'scope':'spo_confirmed','text_verified':True}})))
    general=and_(generic,or_(not_(required),bound_review))
    return and_(Document.source_type!='test',
        not_(Document.title.bool_op('~')(r'(^|[ :])[Тт][Ее][Сс][Тт]([ :]|$)')),
        not_(Document.meta.contains({'is_test':True})),or_(legacy,general))


def exclusion_reasons(doc):
    reasons=[]
    meta=doc.meta or {}
    if is_test_document(doc):
        reasons.append('Тестовый материал')
    if meta.get('scholarship_material') or meta.get('review_required'):
        if meta.get('review_policy')=='source_text_v1':
            review=meta.get('source_review') or {}
            if review.get('coverage')!='full_document' or review.get('text_verified') is not True:
                reasons.append('Текст всего материала не проверен; проверка отдельных страниц не допускает весь документ')
            if review.get('applicability_verified') is not True or review.get('unit_key')!=meta.get('unit_key'):
                reasons.append('Применимость к указанному подразделению не подтверждена')
            if (len(str(meta.get('raw_sha256','')))!=64 or review.get('raw_sha256')!=meta.get('raw_sha256')
                    or not doc.content_hash or review.get('text_sha256')!=doc.content_hash
                    or not meta.get('extraction_version') or review.get('extraction_version')!=meta.get('extraction_version')):
                reasons.append('Проверка не связана с текущим файлом, текстом и версией извлечения')
        else:
            review=meta.get('review') or {}
            if review.get('scope')!='spo_confirmed':
                reasons.append('Применимость к СПО/СПК не подтверждена')
            if review.get('text_verified') is not True:
                reasons.append('Извлечённый текст не проверен')
    return reasons


def is_test_document(doc):
    import re
    return (doc.source_type=='test' or bool((doc.meta or {}).get('is_test'))
        or bool(re.search(r'(^|[ :])тест([ :]|$)',doc.title,re.I)))
