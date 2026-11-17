from __future__ import annotations
import re
import time
from dataclasses import dataclass,field
from difflib import SequenceMatcher
from src.core.rag.bm25_search import bm25_search,query_terms
from src.core.rag.vector_search import vector_search,SearchHit
from src.core.rag.rrf import reciprocal_rank_fusion
from src.core.rag.query import expand_query,focus_terms
from src.core.rag.profiles import state_for,prepare,fingerprint
from src.core.rag.reranker import rerank
from src.core.rag.legacy_retriever import HybridRetriever as LegacyRetriever


@dataclass
class RetrievalResult:
    hits:list[SearchHit]=field(default_factory=list)
    status:str='no_match'
    diagnostics:dict=field(default_factory=dict)

    def usage(self):
        return {'retrieval_status':self.status,'search_ms':self.diagnostics.get('total_ms',0),
                'reranker_status':self.diagnostics.get('reranker','not_used'),
                'search_stages_ms':self.diagnostics.get('timings_ms',{})}


class HybridRetriever:
    def __init__(self,db,embedder,settings,*,include_test=False,index_slot='active',strategy=None):
        self._db=db;self._embedder=embedder;self._settings=settings
        self._include_test=include_test;self._slot=index_slot;self._strategy=strategy
        self.last_result=None
        self.unit_keys=()

    async def search(self,query,top_k=None):
        return (await self.retrieve(query,top_k)).hits

    async def retrieve(self,query,top_k=None):
        started=time.perf_counter()
        diag={'timings_ms':{},'candidates':[],'reranker':'not_used','unit_keys':list(self.unit_keys)}
        try:
            state=await state_for(self._db,self._settings)
            profile=state.candidate_profile if self._slot=='candidate' else state.active_profile
            if not profile:raise ValueError('Candidate index is not built')
            diag.update(profile=profile['name'],profile_fingerprint=fingerprint(profile),index_slot=self._slot)
            if (self._strategy or self._settings.retrieval_policy)=='multi_query':
                from src.core.rag.multi_query import multi_query_search
                hits,details=await multi_query_search(self._db,self._embedder,self._settings,query,profile,
                    unit_keys=self.unit_keys,include_test=self._include_test,candidate=self._slot=='candidate',top_k=min(top_k or 4,4))
                diag.update(details)
                status='matched' if hits else 'unavailable' if details.get('errors') else 'no_match'
                return self._result(hits,status,diag,started)
            if getattr(self._settings,'academy_rerank_enabled',False):
                from src.core.rag.academy_retrieval import academy_search
                hits,details=await academy_search(self._db,self._embedder,self._settings,query,
                    unit_keys=self.unit_keys,include_test=self._include_test,top_k=min(top_k or 4,4))
                if details.get('reranker')=='unavailable':
                    old=LegacyRetriever(self._db,self._embedder,self._settings,
                                        include_test=self._include_test,unit_keys=self.unit_keys)
                    hits=await old.search(query,top_k=min(top_k or 4,4))
                    details.update(fallback='legacy',fallback_diagnostics=old.last_diagnostics)
                diag.update(details)
                return self._result(hits,'matched' if hits else 'no_match',diag,started)
            if (self._strategy or self._settings.retrieval_policy)=='evidence' and not self.unit_keys:
                from src.core.rag.evidence_search import EvidenceSearch
                hits,details=await EvidenceSearch(self._db,self._embedder,self._settings,include_test=self._include_test).search(
                    query,profile,top_k=min(top_k or 4,4),candidate=self._slot=='candidate')
                diag.update(details)
                return self._result(hits,'matched' if hits else 'no_match',diag,started)
            if (self._strategy or profile.get('policy','legacy'))=='legacy' or self.unit_keys:
                old=LegacyRetriever(self._db,self._embedder,self._settings,include_test=self._include_test,unit_keys=self.unit_keys)
                hits=await old.search(query,top_k=min(top_k or 4,4))
                diag.update(old.last_diagnostics)
                return self._result(hits,'matched' if hits else 'no_match',diag,started)
            return await self._semantic(query,profile,diag,started,min(top_k or 4,4))
        except Exception as exc:
            diag['error']=type(exc).__name__
            return self._result([],'unavailable',diag,started)

    def _result(self,hits,status,diag,started):
        diag['total_ms']=round((time.perf_counter()-started)*1000,2)
        result=RetrievalResult(hits,status,diag);self.last_result=result
        return result

    async def _semantic(self,query,profile,diag,started,top_k):
        search_query=expand_query(query)
        vectors=[];lexical=[];focused=[];vector_error=False;lexical_error=False
        before=time.perf_counter()
        try:
            vector=await self._embedder.embed_one(prepare(search_query,profile,'query'))
            vectors=await vector_search(self._db,vector,limit=self._settings.top_k_vector,
                include_test=self._include_test,candidate=self._slot=='candidate',unit_keys=self.unit_keys)
        except Exception as exc:
            vector_error=True;diag['vector_error']=type(exc).__name__
        diag['timings_ms']['vector']=round((time.perf_counter()-before)*1000,2)
        before=time.perf_counter()
        try:
            lexical=await bm25_search(self._db,query=search_query,limit=self._settings.top_k_bm25,include_test=self._include_test,unit_keys=self.unit_keys)
            focus=focus_terms(search_query)
            if focus:focused=await bm25_search(self._db,query=' '.join(focus),limit=20,include_test=self._include_test,unit_keys=self.unit_keys)
        except Exception as exc:
            lexical_error=True;diag['lexical_error']=type(exc).__name__
        diag['timings_ms']['lexical']=round((time.perf_counter()-before)*1000,2)
        if vector_error and lexical_error:return self._result([],'unavailable',diag,started)
        diag['counts']={'vector':len(vectors),'lexical':len(lexical),'focused':len(focused)}
        candidates=reciprocal_rank_fusion([vectors,lexical,focused],k=self._settings.rrf_k,top_k=60)
        terms=query_terms(search_query)
        # Identifier matches and lexical coverage are soft ranking signals,
        # never a mandatory gate for semantic matches.
        for hit in candidates:
            text=(hit.title+' '+hit.content).casefold()
            words=re.findall(r'[а-яёa-z0-9]+',text)
            coverage=sum(t in text if '.' in t else any(w.startswith(t[:5]) for w in words) for t in terms)/max(len(terms),1)
            hit.raw_score+=0.025*coverage+0.08*sum(t in text for t in terms if '.' in t)
        candidates.sort(key=lambda h:h.raw_score,reverse=True)
        candidates=candidates[:self._settings.reranker_candidates]
        # Deduplicate full repeats before spending cross-encoder time.
        unique=[]
        for hit in candidates:
            canonical=re.sub(r'\s+',' ',hit.content).strip().casefold()
            if any(canonical==c for _,c in unique):continue
            unique.append((hit,canonical))
        candidates=[h for h,_ in unique]
        before=time.perf_counter()
        try:
            scores=await rerank(query,[h.title+'\n'+re.sub(r'\s+',' ',h.content) for h in candidates],self._settings.reranker_directory)
            if len(scores)!=len(candidates):raise ValueError('Invalid reranker batch')
            for hit,score in zip(candidates,scores):hit.rerank_score=float(score)
            candidates.sort(key=lambda h:h.rerank_score,reverse=True)
            threshold=float(profile.get('rerank_threshold',0.0))
            accepted=[h for h in candidates if h.rerank_score>=threshold]
            diag.update(reranker='ready',threshold=threshold)
        except Exception as exc:
            diag.update(reranker='unavailable',reranker_error=type(exc).__name__)
            # Degraded hybrid mode retains lexical evidence or a strong vector
            # match; its scores are not treated as fact confidence.
            similarity={h.chunk_id:h.raw_score for h in vectors}
            lex_ids={h.chunk_id for h in lexical+focused}
            accepted=[h for h in candidates if h.chunk_id in lex_ids or similarity.get(h.chunk_id,0)>=float(profile.get('fallback_vector_threshold',0.8))]
        diag['timings_ms']['rerank']=round((time.perf_counter()-before)*1000,2)
        chosen=[]
        for hit in accepted:
            normalized=re.sub(r'\s+',' ',hit.content).strip()
            if any(hit.document_id==h.document_id and SequenceMatcher(None,normalized,re.sub(r'\s+',' ',h.content).strip()).ratio()>0.9 for h in chosen):continue
            chosen.append(hit)
            if len(chosen)>=top_k:break
        ids={h.chunk_id for h in chosen}
        diag['candidates']=[{'chunk_id':h.chunk_id,'document_id':h.document_id,'title':h.title,'url':h.url,
            'hybrid_score':h.raw_score,'rerank_score':h.rerank_score,
            'vector_similarity':next((v.raw_score for v in vectors if v.chunk_id==h.chunk_id),None),
            'lexical_score':next((v.raw_score for v in lexical if v.chunk_id==h.chunk_id),None),'selected':h.chunk_id in ids,
            'reason':'selected' if h.chunk_id in ids else 'below_threshold' if h not in accepted else 'duplicate_or_limit'} for h in candidates]
        for rank,hit in enumerate(chosen,1):hit.rank=rank
        return self._result(chosen,'matched' if chosen else 'no_match',diag,started)
