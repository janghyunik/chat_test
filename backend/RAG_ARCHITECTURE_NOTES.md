# Advanced InformNote RAG Upgrade

## What changed

This backend upgrade moves the chatbot closer to a fine-tuning-like experience without actual fine-tuning.

### Implemented layers
- structured conversation memory
- multi-query retrieval
- dense + keyword hybrid retrieval
- two-pass reranking
- lightweight playbook synthesis from retrieved InformNote rows
- structured answer generation
- answer verification pass

## Main files
- `app/services/legacy/pg_vector_utils.py`
- `app/services/legacy/agentic_rag_graph.py`
- `app/services/chat_service.py`
- `.env.example`

## Runtime flow
1. read previous session memory
2. classify the question as inform/general
3. expand the query with conversation context
4. run hybrid retrieval against pgvector and keyword search
5. rerank candidates
6. infer dominant slots such as line/equipment/error
7. run a second focused retrieval pass
8. rerank again and build a playbook
9. generate a structured answer
10. verify unsupported claims and soften them if needed
11. save updated memory back into the chat session

## Current database assumptions
The following columns are used directly:
- `text`
- `날짜`
- `라인`
- `공정`
- `설비명`
- `에러명`
- `점검이력`
- `source`

## Optional tuning
You can tune these values in `.env`.

- `RAG_DENSE_TOP_K_PER_QUERY`
- `RAG_KEYWORD_TOP_K`
- `RAG_FIRST_PASS_KEEP`
- `RAG_FINAL_CONTEXT_DOCS`
- `ENABLE_RAG_VERIFIER`
