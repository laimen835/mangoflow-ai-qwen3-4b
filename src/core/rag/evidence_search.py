"""Experimental corpus BM25 + dense retrieval, without mandatory word gates.

Russian morphological lemmas provide consistent word forms. Query expansion is a
ranking signal only; returned facts always remain verbatim indexed content.
"""
from __future__ import annotations
from collections import Counter,OrderedDict
from dataclasses import dataclass
from difflib import get_close_matches,SequenceMatcher
from functools import lru_cache
import hashlib,math,re,time
from sqlalchemy import select,cast,Text,func
from src.core.db.models import Chunk,Document,DocumentStatus
from src.core.rag.document_policy import real_document_filter
from src.core.rag.vector_search import SearchHit,vector_search
from src.core.rag.profiles import prepare
from src.core.rag.query import expand_query
from src.core.rag.bm25_search import query_terms,_QUERY_STOP

_CACHE=OrderedDict()


@lru_cache(maxsize=1)
def _morphology():
    from pymorphy3 import MorphAnalyzer
    return MorphAnalyzer()


@lru_cache(maxsize=50000)
def normal_word(word):
    word=word.casefold().replace('ё','е')
    if re.fullmatch('[а-я]+',word):return _morphology().parse(word)[0].normal_form.replace('ё','е')
    return word


@lru_cache(maxsize=1)
def normal_stop():return {normal_word(w) for w in _QUERY_STOP}|{'я','свой','другой','этот','быть','надо','назвать','название','предусмотренный','после'}


def word_terms(text):
    # Links are destinations, not evidence that the document contains a fact.
    text=re.sub(r'https?://\S+',' ',text)
    result=Counter(normal_word(t) for t in query_terms(text))
    return Counter({w:n for w,n in result.items() if w not in normal_stop()})


def search_expansion(query):
    result=expand_query(query)
    rules=[
        (r'реша[её]т|решают','решение принимается'),
        (r'степенд|хорош.*уч[её]б','академическая стипендия'),
        (r'материальн.*помощ|денежн.*помощ','материальная поддержка'),
        (r'возглав|руководит|руководительниц','директор'),
        (r'ночлег|поселит|жить|жиль[её]','общежитие проживание'),
        (r'к.*вуз|структур.*учрежден|образовательн.*учрежден','академия подразделение'),
        (r'истори|появил.*(?:политех|техникум)|дата.*создан','основание техникума'),
        (r'учебн.*заведен|учебн.*баз','колледж'),
    ]
    return result+'\n'+' '.join(v for p,v in rules if re.search(p,query,re.I))


@dataclass
class Entry:
    hit:SearchHit
    body:Counter
    title:Counter


@dataclass
class Catalog:
    entries:list[Entry]
    frequencies:Counter
    average_length:float


async def catalog(db,include_test=False):
    condition=[Document.status==DocumentStatus.INDEXED]
    if not include_test:condition.append(real_document_filter())
    stamp=(await db.execute(select(func.count(Chunk.id),func.max(Chunk.id),func.max(Document.updated_at))
        .join(Document,Document.id==Chunk.document_id).where(*condition))).one()
    engine=db.get_bind();url=str(engine.url) if engine is not None else str(id(db))
    key=(hashlib.sha256(url.encode()).hexdigest(),include_test)
    cached=_CACHE.get(key)
    if cached and cached[0]==stamp and time.monotonic()-cached[1]<30:return cached[2]
    # No vector columns are transferred when loading the lexical corpus.
    rows=(await db.execute(select(Chunk.id,Chunk.document_id,Chunk.content,Chunk.meta,
        Document.title,Document.source_url,Document.meta).join(Document,Document.id==Chunk.document_id).where(*condition).order_by(Chunk.id))).all()
    entries=[];df=Counter()
    for cid,did,content,cm,title,url,dm in rows:
        body=word_terms(content)
        years=' '.join(re.findall(r'\b20\d{2}\b',str((dm or {}).get('review',{}).get('document_period',''))))
        title_terms=word_terms(title+' '+years)
        hit=SearchHit(cid,did,content,title,url,0,0,'corpus_bm25',meta={**(cm or {}),
            'document_review':(dm or {}).get('review'),'scholarship_material':(dm or {}).get('scholarship_material',False),
            'source_type':dm.get('source', '') if dm else ''})
        entries.append(Entry(hit,body,title_terms));df.update(set(body)|set(title_terms))
    result=Catalog(entries,df,sum(sum(e.body.values()) for e in entries)/max(1,len(entries)))
    _CACHE[key]=(stamp,time.monotonic(),result);_CACHE.move_to_end(key)
    while len(_CACHE)>6:_CACHE.popitem(last=False)
    return result


def score_catalog(data,terms):
    n=len(data.entries);k1=1.2;b=.75
    weights={t:math.log(1+(n-data.frequencies[t]+.5)/(data.frequencies[t]+.5)) for t in terms if data.frequencies[t]}
    normalizer=max(sum(weights.values())*(k1+1),1e-9)
    scored=[]
    for entry in data.entries:
        length=sum(entry.body.values());raw=0;matched=[]
        for term,idf in weights.items():
            tf=entry.body[term]
            if tf:
                raw+=idf*tf*(k1+1)/(tf+k1*(1-b+b*length/max(1,data.average_length)))
                matched.append(term)
            if entry.title[term]:raw+=.12*idf
        scored.append((entry,raw/normalizer,matched))
    return sorted(scored,key=lambda r:r[1],reverse=True)


class EvidenceSearch:
    def __init__(self,db,embedder,settings,*,include_test=False):
        self.db=db;self.embedder=embedder;self.settings=settings;self.include_test=include_test

    async def search(self,query,profile,*,top_k=4,candidate=False):
        diag={'strategy':'evidence-v3','reranker':'not_used','timings_ms':{},'candidates':[]}
        start=time.perf_counter();data=await catalog(self.db,self.include_test)
        expanded=search_expansion(query)
        terms=word_terms(expanded)
        # Fix absent lexemes only, retaining the exact query and identifiers.
        corrections={}
        for term in list(terms):
            if term not in data.frequencies and len(term)>=5 and term.isalpha() and not term.isascii():
                nearby=get_close_matches(term,data.frequencies.keys(),n=1,cutoff=.84)
                if nearby:terms[nearby[0]]+=1;corrections[term]=nearby[0]
        ranked=score_catalog(data,terms)
        documents={}
        for e in data.entries:
            if e.hit.document_id not in documents:documents[e.hit.document_id]=Entry(e.hit,Counter(),e.title)
            documents[e.hit.document_id].body.update(e.body)
        doc_entries=list(documents.values());doc_df=Counter()
        for e in doc_entries:doc_df.update(set(e.body)|set(e.title))
        doc_data=Catalog(doc_entries,doc_df,sum(sum(e.body.values()) for e in doc_entries)/max(1,len(doc_entries)))
        doc_scores={e.hit.document_id:s for e,s,_ in score_catalog(doc_data,terms)}
        diag['query_corrections']=corrections
        diag['timings_ms']['corpus_bm25']=round((time.perf_counter()-start)*1000,2)
        dense=[];start=time.perf_counter()
        try:
            vector=await self.embedder.embed_one(prepare(expanded,profile,'query'))
            dense=await vector_search(self.db,vector,limit=40,include_test=self.include_test,candidate=candidate)
        except Exception as exc:diag['vector_error']=type(exc).__name__
        diag['timings_ms']['vector']=round((time.perf_counter()-start)*1000,2)
        similarities={h.chunk_id:h.raw_score for h in dense}
        byid={e.hit.chunk_id:(e,s,m) for e,s,m in ranked}
        pool={e.hit.chunk_id for e,s,m in ranked[:40] if m}
        pool.update(similarities)
        candidates=[]
        for cid in pool:
            if cid not in byid:continue
            entry,lexical,matched=byid[cid];similarity=similarities.get(cid,0)
            # Strong semantic evidence can survive absent shared words. Weak
            # semantic proximity alone cannot turn unrelated documents into facts.
            accepted=bool(matched) and lexical>=.025 or similarity>=.80
            score=.7*lexical+.3*doc_scores.get(entry.hit.document_id,0)+.12*max(0,similarity-.4)
            if re.search(r'кто|зовут|имя',query,re.I) and re.search(r'директор|руководит|возглав|руководитель',query,re.I):
                if re.search(r'\b[А-ЯЁ][а-яё-]+\s+[А-ЯЁ][а-яё-]+\s+[А-ЯЁ][а-яё-]+',entry.hit.content):score+=.18
            clause=re.search(r'\b\d{1,2}\.\d{1,2}\b',query)
            if clause and clause.group() in str(entry.hit.meta.get('section','')):score+=.25
            candidates.append((entry,score,accepted,lexical,similarity,matched))
        candidates.sort(key=lambda r:(-r[1],r[0].hit.chunk_id))
        chosen=[]
        for entry,score,accepted,lexical,similarity,matched in candidates:
            hit=entry.hit
            if not accepted:continue
            if sum(h.document_id==hit.document_id for h in chosen)>=2:continue
            canonical=re.sub(r'\s+',' ',hit.content).casefold().strip()
            if any(SequenceMatcher(None,canonical,re.sub(r'\s+',' ',h.content).casefold().strip()).ratio()>.88 for h in chosen):continue
            chosen.append(SearchHit(hit.chunk_id,hit.document_id,hit.content,hit.title,hit.url,len(chosen)+1,score,'evidence_bm25+vector',meta=hit.meta))
            if len(chosen)>=min(top_k,4):break
        # Fill unused slots only after allowing other documents a fair chance.
        for entry,score,accepted,lexical,similarity,matched in candidates:
            if len(chosen)>=min(top_k,4):break
            hit=entry.hit
            if not accepted or any(h.chunk_id==hit.chunk_id for h in chosen):continue
            if any(SequenceMatcher(None,re.sub(r'\s+',' ',hit.content),re.sub(r'\s+',' ',h.content)).ratio()>.88 for h in chosen):continue
            chosen.append(SearchHit(hit.chunk_id,hit.document_id,hit.content,hit.title,hit.url,len(chosen)+1,score,'evidence_bm25+vector',meta=hit.meta))
        selected={h.chunk_id for h in chosen}
        # Restore a split paragraph around an accepted fragment, but do not
        # append another numbered section or a different specialty code.
        for h in chosen:
            section=h.meta.get('section')
            neighbors=[e.hit for e in data.entries if e.hit.document_id==h.document_id]
            position=next(i for i,n in enumerate(neighbors) if n.chunk_id==h.chunk_id)
            support=[h.chunk_id];parts=[h.content]
            for index in [position-1,position+1]:
                if not 0<=index<len(neighbors):continue
                other=neighbors[index]
                if section and other.meta.get('section')!=section:continue
                codes=set(re.findall(r'\d{2}\.\d{2}\.\d{2}(?:-\d{3})?',h.content))
                other_codes=set(re.findall(r'\d{2}\.\d{2}\.\d{2}(?:-\d{3})?',other.content))
                if codes and other_codes and codes!=other_codes:continue
                extra=re.sub(r'\s+',' ',other.content).strip()
                if len(extra)>650 or sum(map(len,parts))+len(extra)>1300:continue
                if index<position:parts.insert(0,extra)
                else:parts.append(extra)
                support.append(other.chunk_id)
            h.content='\n'.join(parts);h.meta={**h.meta,'supporting_chunk_ids':support}
        diag['candidates']=[{'chunk_id':e.hit.chunk_id,'title':e.hit.title,'url':e.hit.url,'score':s,
            'bm25_score':l,'document_score':doc_scores.get(e.hit.document_id,0),'vector_similarity':v,'matched_lexemes':m,'selected':e.hit.chunk_id in selected,
            'reason':'selected' if e.hit.chunk_id in selected else 'below_evidence_threshold' if not a else 'duplicate_or_limit'} for e,s,a,l,v,m in candidates]
        return chosen,diag
