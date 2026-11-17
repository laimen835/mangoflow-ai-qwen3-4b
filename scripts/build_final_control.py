"""Freeze different public-source facts before the final reranker comparison.

Evaluation only: this file and its output are never imported by runtime code.
"""
import hashlib
import json
from pathlib import Path
import re

def norm(value):
    return re.sub(r'\s+',' ',value).casefold().strip()

FACTS=[
('146','В каком году был образован Инженерный институт СКГА?','был образован в 2018 году'),
('146','Какие два института объединили при образовании Инженерного института?','объединения Института строительства и электроэнергетики и Инженерно‑технологического института'),
('146','В каком году учреждён механико-технологический факультет из истории Инженерного института?','был учреждён в 1974 году'),
('146','Кто стал первым деканом механико-технологического факультета по истории Инженерного института?','Н. А. Штепин'),
('146','Когда по истории нынешнего Инженерного института на базе механико-технологического факультета сформировали Инженерно-технологический институт?','В 2010 году на базе факультета был сформирован Инженерно‑технологический институт'),
('146','В каком году выделили Энергостроительный факультет в истории Инженерного института?','В 1982 году'),
('146','Когда был образован Институт строительства и электроэнергетики по истории Инженерного института?','В 2009 году на базе подразделения был образован Институт строительства и электроэнергетики'),
('147','Какой датой принято решение о создании нынешнего Института открытого образования?','от 30.09.2020 года № 01'),
('147','Какое распоряжение Правительства РФ упомянуто в истории Института открытого образования за 2006 год?','от 9.03.2006г. № 306–р'),
('147','Каким приказом создан КЧ МРЦПК и ППК в истории Института открытого образования?','№ 441/о от 14.06.06 г.'),
('147','Когда создали факультет повышения квалификации и профессиональной переподготовки кадров по истории ИОО?','№ 644/о от 18.10.2004 г.'),
('147','Какой датой по истории Института открытого образования принято решение о переименовании центра в Институт повышения квалификации и дополнительного общего и профессионального образования?','от 26.11.2009 года протокол № 3'),
('147','Какие три вида программ дополнительного профессионального образования перечисляет Институт открытого образования?','программы повышения квалификации, программы профессиональной переподготовки, стажировка'),
('149','С какого года ведёт историю Институт экономики и управления СКГА?','ведет свою историю с 1991 года'),
('149','Кто был организатором и первым деканом учетно-финансового факультета по истории Института экономики и управления?','Константин Максимович Мацев'),
('149','В каком году учетно-финансовый факультет реорганизован в Экономический институт КЧГТА?','В 2010 году факультет был реорганизован в Экономический институт КЧГТА'),
('149','Когда Экономический институт получил название «Институт экономики и управления»?','В 2014 году Экономический институт получил свое современное название'),
('149','В каком году проект студентов Института экономики и управления выиграл субсидию ПРДСО?','В 2017 году'),
('149','Каков размер выигранной студентами Института экономики и управления субсидии на окружной форум финансовой грамотности?','1 миллиона рублей'),
('149','С какого года студенты Института экономики и управления участвуют в форуме «Машук»?','С 2010 года студенты института активно участвуют'),
]

def main():
    import argparse
    parser=argparse.ArgumentParser()
    parser.add_argument('--source-report',required=True)
    parser.add_argument('--out',required=True)
    args=parser.parse_args()
    destination=Path(args.out)
    if destination.exists():
        raise ValueError('Refusing to overwrite a frozen control')
    source=json.loads(Path(args.source_report).read_text(encoding='utf-8'))
    documents={d['url'].rstrip('/').rsplit('/',1)[-1]:d for d in source['documents']}
    cases=[]
    for index,(unit,question,quote) in enumerate(FACTS,1):
        document=documents[unit]
        body=Path(document['text_file']).read_text(encoding='utf-8')
        if norm(quote) not in norm(body):
            raise ValueError('Gold quote not present in captured public source: '+str(index))
        for variant in ('clean','noisy'):
            asked=question if variant=='clean' else 'пж подскажи, '+question[0].lower()+question[1:].replace('институт','инстетут').replace('Институт','инстетут')
            cases.append({'id':f'final-{index:02}-{variant}','fact_id':f'final-{index:02}',
                'split':'control','question':asked,'answerable':True,'url':document['url'],
                'quote':quote,'history':[],'source_sha256':document['text_sha256']})
    result={'version':'final-different-facts-v2','activation_approved':False,'cases':cases,
        'method':'20 different facts from three separately captured institute pages; paired clean/noisy formulations are correlated. Frozen before final V4/reranker comparison. Source text is not a guarantee of present-day validity.'}
    destination.parent.mkdir(parents=True,exist_ok=True)
    destination.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print('Frozen',len(cases),'questions;',hashlib.sha256(destination.read_bytes()).hexdigest())

if __name__=='__main__':
    main()
