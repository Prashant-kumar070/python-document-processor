# Learning Guide: AI-Powered Knowledge Base & Document Assistant

This guide explains how this repository works as a Python backend and as a
manually implemented Retrieval-Augmented Generation (RAG) system. It follows the
code that is here today; it is not a description of an idealized RAG framework.
The application does not use LangChain, LlamaIndex, Haystack, or another
pre-built RAG framework.

## 1. What the application does

An authenticated user uploads a PDF or DOCX file. The application extracts
text, divides that text into chunks, generates a vector embedding for each
chunk, and stores the chunk and vector in the database. The user can then ask
questions. The application embeds the question, searches only that user's
document chunks, puts sufficiently relevant chunks into an LLM prompt, and
returns the answer together with source metadata.

The application also provides:

- User registration and login with bcrypt password hashing and JWT bearer
  tokens.
- Per-user document and conversation access.
- Background document processing using FastAPI `BackgroundTasks`.
- Conversation and message persistence.
- Source-chunk relationships for assistant messages.
- Selectable chat-generation providers: OpenAI, Gemini, Groq, and mock.
- Selectable embedding providers: OpenAI, Gemini, and mock.
- PostgreSQL/pgvector schema management through Alembic, plus SQLite support
  for local tests.
- FastAPI-generated OpenAPI docs at `/docs`.

## 2. Big-picture architecture

```text
                         DOCUMENT INGESTION

Client
  |
  | POST /documents + PDF/DOCX
  v
FastAPI documents route
  |
  v
DocumentService.save_upload()
  |  saves file and creates PENDING document row
  v
FastAPI BackgroundTasks
  |
  v
DocumentService.process_document()
  |
  +--> file_parser.py       PDF/DOCX -> text (and PDF page numbers)
  |
  +--> ChunkingService      text -> ordered chunks and source metadata
  |
  +--> EmbeddingService     chunks -> vectors
  |
  +--> ChunkRepository      chunk records + vectors -> database
  |
  v
PostgreSQL + pgvector


                         QUESTION ANSWERING

Client
  |
  | POST /chat + JWT
  v
FastAPI chat route
  |
  v
RagService.answer_question()
  |
  +--> EmbeddingService       question -> query vector
  |
  +--> VectorSearchService    pgvector cosine search, user scoped, top K
  |
  +--> relevance cutoff       discard results below MIN_SIMILARITY_SCORE
  |
  +--> context builder        label real chunks as SOURCE 1, SOURCE 2, ...
  |
  +--> LLMService             grounded prompt -> selected chat provider
  |
  +--> ConversationRepository save messages and actual source relationships
  |
  v
Answer + real retrieved source metadata
```

The route handles HTTP concerns such as authentication and request validation.
Service classes coordinate business behavior. Repositories persist and retrieve
database objects. SQLAlchemy models describe the relational schema.

## 3. Repository map

```text
app/
  main.py                         Create the FastAPI application and include routes
  api/routes/
    auth.py                       Register, login, and current-user endpoints
    documents.py                  Upload/list/read/delete documents
    chat.py                       Chat and conversation endpoints
  core/
    config.py                     Environment-backed settings
    security.py                   Password hashing and JWT authentication
  db/
    database.py                   SQLAlchemy engine, sessions, declarative Base
    models/
      user.py                     User table
      document.py                 Uploaded document and processing status
      chunk.py                    Text chunk and pgvector embedding
      conversation.py             Conversation table
      message.py                  User/assistant message table
      message_source.py           Assistant message -> retrieved chunk mapping
  repositories/
    document_repository.py        Document database operations
    chunk_repository.py           Chunk/vector database operations
    conversation_repository.py    Conversation, message, and source persistence
  schemas/
    auth.py                       Auth request and response validation
    document.py                   Document response schemas
    chat.py                       Chat request and response schemas
  services/
    document_service.py           Ingestion/processing orchestration
    chunking_service.py            Custom character-based chunker
    embedding_service.py           Embedding provider abstraction
    vector_search_service.py       Similarity search using pgvector
    vector_service.py              Compatibility wrapper for old call sites
    rag_service.py                 Manual RAG orchestration and context building
    llm_service.py                 Selectable chat-generation providers
  utils/
    file_parser.py                 PDF and DOCX text extraction
alembic/
  versions/0001_initial_schema.py  Initial schema, including pgvector extension
tests/                             Unit, integration, and API tests
```

## 4. Important concepts

### 4.1 RAG: Retrieval-Augmented Generation

RAG is a design pattern with two main stages:

1. **Retrieval:** find stored information related to the user's question.
2. **Generation:** provide that information to a language model so it can
   compose an answer.

The model is not expected to know the user's private documents already. The
application retrieves relevant pieces and includes them in the request.

This repository implements those stages itself:

- Retrieval is implemented in `VectorSearchService`.
- Context and prompt construction are implemented in `RagService`.
- Provider calls are implemented in `LLMService`.

RAG is not a guarantee that an LLM can never hallucinate. This implementation
reduces that risk by only calling the LLM when retrieved chunks meet the
configured similarity threshold, telling the model to use only the current
document context, and returning the actual retrieved source records.

### 4.2 Documents, chunks, and chunking

A **document** is the uploaded file and its owner/status metadata.

A **chunk** is a smaller text passage extracted from a document. The whole file
is not sent to the LLM for every question because:

- LLM context windows are limited.
- Sending entire files is expensive and slow.
- Smaller passages make it easier to find and cite relevant information.

`ChunkingService` is a custom implementation. It:

- Splits on sentence-ending punctuation when possible.
- Tries to keep sentences together.
- Uses a maximum configured size and a configurable overlap.
- Splits unusually long sentences at word boundaries where possible.
- Preserves the page number provided by the parser.
- Assigns sequential chunk indexes within the parsed document.

Current settings are character counts, not token counts:

```env
CHUNK_SIZE=1000
CHUNK_OVERLAP=150
```

Therefore “1000” means approximately 1000 Python string characters, not 1000
LLM tokens. A character-based limit is easy to understand but is only an
approximation of token size; languages and symbols tokenize differently.

**Overlap** repeats a small tail of one chunk at the start of the next. For
example:

```text
Chunk 1: ... A signed token is verified by the server.
Chunk 2: ... token is verified by the server. The server checks the expiry.
```

This preserves context when an important explanation crosses a chunk boundary.
The overlap can also duplicate some information and slightly increase storage
and embedding cost.

### 4.3 Parsing and source metadata

`file_parser.py` returns a list of page-like text records:

```python
[
    {"page_number": 1, "text": "Text from page 1"},
    {"page_number": 2, "text": "Text from page 2"},
]
```

- **PDF:** `pypdf` extracts text separately from each page and assigns its page
  number. This reads a PDF's text layer; scanned pages that are only images need
  OCR, which is not implemented.
- **DOCX:** `python-docx` extracts body paragraphs and table rows in document
  order, including nested tables. Header and footer text are also extracted.
  Table rows are serialized into readable pipe-separated text. DOCX does not
  expose stable printed page numbers because pagination depends on the renderer,
  fonts, and layout, so DOCX chunks use page number 1.

“Extract the whole document” is bounded by the parser formats and structures
the current code knows about. It does not currently perform OCR on embedded
images or scanned PDFs, and may not extract text stored in every specialized
Word object (for example, some text boxes or embedded objects).

### 4.4 Embeddings and vectors

An **embedding** is a list of numbers produced from text. A model maps text with
similar meanings to vectors that tend to be near each other in a high-dimensional
space. For example, “reset my password” may be close to “forgotten credentials”
even though the exact words differ.

Example shape only:

```text
"A JWT is a signed bearer token"
    -> [0.012, -0.08, 0.44, ...]
```

The real embedding vector is model-specific and has many dimensions. This app
stores vectors in the `document_chunks.embedding` pgvector column.

`EmbeddingService` provides:

```python
generate_embedding(text) -> list[float]
generate_embeddings(list_of_texts) -> list[list[float]]
```

The service validates vector width and numeric values. Providers are selected
independently from the LLM provider:

| Setting | Meaning |
| --- | --- |
| `EMBEDDING_PROVIDER` | `openai`, `gemini`, or `mock` |
| `EMBEDDING_MODEL` | Model accepted by the selected embedding provider |
| `EMBEDDING_DIMENSIONS` | Expected vector width; must match PostgreSQL's vector column |
| `EMBEDDING_API_KEY` | Embedding credential where configured |

Gemini's implementation requests `RETRIEVAL_DOCUMENT` embeddings for stored
chunks and `RETRIEVAL_QUERY` embeddings for questions. OpenAI uses its embeddings
API. The mock provider produces deterministic vectors for tests; those vectors
are not semantic embeddings and should not be used to evaluate real retrieval
quality.

**Important model consistency rule:** document and question embeddings must
come from compatible embedding models and use the same dimensions. If the
provider/model changes, existing chunk embeddings generally need to be
regenerated. Changing `EMBEDDING_DIMENSIONS` also requires a database migration
and re-embedding stored chunks.

### 4.5 Vector storage and pgvector

The relational table stores both the chunk text and its vector:

```text
document_chunks
  id
  document_id
  chunk_index
  content
  embedding VECTOR(EMBEDDING_DIMENSIONS)
  page_number
  created_at
```

The vector is stored beside the chunk it represents. A foreign key connects a
chunk to its document. A unique constraint on `(document_id, chunk_index)`
prevents duplicate indexes inside one document.

Alembic migration `0001_initial_schema` enables PostgreSQL's `vector` extension
and creates the tables. Run `alembic upgrade head` to apply it. PostgreSQL does
not have the tables automatically created by `create_all`; SQLite tables are
created for local testing.

### 4.6 Similarity search and cosine distance

The question is converted to an embedding using the same embedding service.
`VectorSearchService` asks PostgreSQL/pgvector to order stored vectors by cosine
distance:

```text
embedding <=> query_embedding
```

Cosine distance is lower when vector direction is closer. The code reports
similarity as:

```text
cosine_similarity = 1 - cosine_distance
```

The database query joins chunks to documents and filters on `documents.user_id`.
That owner filter is essential: one user must not retrieve another user's
private content. If `document_ids` are supplied, the service also checks that
every requested document belongs to the authenticated user before searching.

`TOP_K` sets the maximum number of nearest chunks returned. `MIN_SIMILARITY_SCORE`
is a second filter applied by `RagService` after retrieval:

```env
TOP_K=5
MIN_SIMILARITY_SCORE=0.30
```

The cutoff is a project setting, not a universal truth. Similarity scores vary
by embedding model and data, so it should be tuned against representative
questions. SQLite has a Python cosine calculation only to support local tests;
PostgreSQL/pgvector is the intended database vector-search path.

Vector search is different from normal SQL substring search. A SQL `LIKE`
condition looks for matching characters/words. Vector similarity compares
learned semantic representations and may find related language without an exact
keyword match.

### 4.7 Prompt construction and grounded generation

`RagService` builds a context block from the chunks returned by the database:

```text
[SOURCE 1]
Document: authentication-guide.pdf
Page: 4
Chunk ID: 51
Content:
JWT authentication uses a signed bearer token...
```

The current question follows the context. A separate system instruction tells
the selected provider to answer only from the current document context, avoid
inventing facts, and reference valid source numbers. Previous conversation
messages can clarify follow-up questions but are explicitly not evidence.

The LLM service does not query the database. It receives a system instruction
and a list of messages and returns text. `LLM_PROVIDER` selects one of:

| `LLM_PROVIDER` | Implementation |
| --- | --- |
| `openai` | OpenAI Chat Completions client |
| `gemini` | Google GenAI `generate_content` client |
| `groq` | OpenAI-compatible client pointed at Groq's API |
| `mock` | Fixed diagnostic response for tests/local development |

Chat provider settings are separate from embedding settings:

```env
LLM_API_KEY=...
LLM_PROVIDER=gemini
LLM_MODEL=gemini-3.1-pro-preview
```

Use a key and model valid for the selected provider. The mock provider is useful
for testing the API path without remote chat generation, but its fixed reply is
not a RAG answer.

### 4.8 Citations: what is and is not guaranteed

After retrieval, the backend constructs `sources` from the actual returned
database rows. Each source includes the document ID/name, page, chunk ID, and
similarity score. These source rows are saved in `message_sources` against the
assistant message.

This means the backend source list refers to actual retrieved chunks, rather
than inventing database references from LLM output. The model is instructed to
write labels such as `[Source 1]`; however, the application does not currently
parse and validate every citation label inside generated answer text. The
returned source list is authoritative for which chunks were supplied to the
model.

### 4.9 Conversation history

`Conversation` belongs to one user. `Message` records user and assistant
content. When a conversation ID is included:

1. The repository checks that the conversation belongs to the current user.
2. It fetches up to `CONVERSATION_HISTORY_LIMIT` latest messages.
3. The RAG prompt includes that bounded history before the latest question and
   newly retrieved context.

History is kept separate from vector retrieval. The application still creates
a question embedding and performs document retrieval for each new question.
History can help resolve “what about the next step?” but should not substitute
for retrieved document evidence.

## 5. Walkthrough A: uploading and processing a document

### Step A1: HTTP upload route

The user calls `POST /documents` with a multipart file and a bearer JWT. The
route in `app/api/routes/documents.py` gets the authenticated `User` through
dependency injection and creates a `DocumentService`.

### Step A2: save the uploaded file and metadata

`DocumentService.save_upload()`:

1. Accepts only `.pdf` and `.docx` filename extensions.
2. Enforces `MAX_UPLOAD_SIZE` when the upload size is available.
3. Saves bytes beneath `UPLOAD_DIR`.
4. Inserts a `documents` row owned by the current user with `PENDING` status.

The original filename is display metadata. The saved path name is generated by
the app and used to find the file later.

### Step A3: schedule work

The route registers `DocumentService.process_document()` with FastAPI
`BackgroundTasks` and returns an upload response. This avoids doing parsing and
embedding work as part of the route's normal response logic.

FastAPI `BackgroundTasks` is a simple in-process mechanism, not a durable task
queue. A process crash can lose work; a production system with large workloads
would normally move this function behind a queue, keeping the processing
business logic reusable.

### Step A4: extract and split text

`process_document()` marks the record `PROCESSING`, selects the parser by file
extension, and passes the extracted page records to
`ChunkingService.chunk_pages()`. Each output chunk includes its sequence index,
content, and page number.

### Step A5: embed and persist

`EmbeddingService.generate_embeddings()` receives the list of chunk texts. The
service returns one vector per text and validates width and numeric values.
`ChunkRepository.replace_document_chunks()` replaces existing chunks for that
document in one database transaction. If the replacement fails, it rolls back
so the previous stored chunks are not partly deleted.

After persistence, `processed_at` is set and document status becomes
`COMPLETED`. If processing raises an exception, status is set to `FAILED` and
the exception text is recorded in `processing_error`.

## 6. Walkthrough B: asking a question

### Step B1: validate and authenticate

The user calls `POST /chat` with a JWT and JSON similar to:

```json
{
  "question": "How does authentication work?",
  "conversation_id": null,
  "document_ids": [1, 2]
}
```

Pydantic validates this as `ChatRequest`. The JWT dependency resolves the user.
The question and optional document IDs are passed to `RagService`.

### Step B2: check conversation ownership and read limited history

If a conversation ID was provided, `RagService` verifies ownership and retrieves
only the configured number of recent messages. A conversation belonging to
another user is not accepted.

### Step B3: embed the question and retrieve chunks

The question goes through `EmbeddingService.generate_embedding()`. Its vector
goes to `VectorSearchService.search()` with:

- The authenticated user's ID.
- Optional document IDs.
- The configured top-K count.

The vector service validates the document IDs and executes cosine-distance
search. PostgreSQL search filters `documents.user_id` in SQL, not afterward in
application code.

### Step B4: apply relevance threshold

`RagService` removes results below `MIN_SIMILARITY_SCORE`. If no results remain,
it returns the fixed insufficient-information response and does not call the
LLM. This prevents a no-context question from falling through to an LLM general
knowledge answer.

### Step B5: construct context, invoke model

Each selected chunk is labeled with a source number, filename, page, and actual
chunk ID. The LLM provider gets the grounding system instruction, bounded
history, and a final user message containing the current question and retrieved
context.

### Step B6: save messages and sources

The repository stores the user question, assistant response, and source rows as
one exchange. The API response contains the answer, conversation ID, and
backend-constructed source list, including the text supplied to the LLM:

```json
{
  "answer": "JWT authentication uses a signed bearer token [Source 1].",
  "conversation_id": 10,
  "sources": [
    {
      "document_id": 1,
      "filename": "authentication-guide.pdf",
      "page_number": 4,
      "chunk_id": 51,
      "similarity_score": 0.91,
      "content": "JWT authentication uses a signed bearer token..."
    }
  ]
}
```

When no chunks meet the threshold, `answer` is the fixed
`INSUFFICIENT_CONTEXT_ANSWER`, `sources` is empty, and the exchange is still
saved in the conversation. The LLM is not called for that question.

## 7. Database tables

| Table | Purpose |
| --- | --- |
| `users` | Account identity, email, password hash, creation time |
| `documents` | Owner, filename/type, status, processing error, timestamps |
| `document_chunks` | Extracted text chunks, page/index, vector embedding |
| `conversations` | User-owned chat threads |
| `messages` | User and assistant messages in a conversation |
| `message_sources` | Link from an assistant message to the retrieved chunk and score |

Relationships:

```text
users 1 ---- many documents
users 1 ---- many conversations
documents 1 ---- many document_chunks
conversations 1 ---- many messages
messages 1 ---- many message_sources
document_chunks 1 ---- many message_sources
```

`message_sources` deliberately points to the chunk row. It does not copy the
chunk text into the source relationship. Deleting referenced chunks may affect
historical citation detail; because the foreign key has no cascade rule, trying
to delete a document whose chunks are still referenced by message sources can
be rejected by the database. The schema does not currently maintain immutable
snapshots of source content in conversation history.

## 8. API features

| Method | Endpoint | Purpose |
| --- | --- | --- |
| `POST` | `/auth/register` | Create an account |
| `POST` | `/auth/login` | Verify password and return JWT |
| `GET` | `/auth/me` | Return current authenticated user |
| `POST` | `/documents` | Upload PDF/DOCX and queue processing |
| `GET` | `/documents` | List current user's documents/statuses |
| `GET` | `/documents/{document_id}` | Get owned document status and chunk count |
| `DELETE` | `/documents/{document_id}` | Delete owned document, chunks, and file |
| `POST` | `/chat` | Ask a question and receive answer/sources |
| `GET` | `/conversations` | List current user's conversations |
| `GET` | `/conversations/{conversation_id}` | Read an owned conversation and messages |
| `DELETE` | `/conversations/{conversation_id}` | Delete an owned conversation |

Interactive API documentation is available at `/docs` while the app is running.

## 9. Configuration

| Variable | What it controls |
| --- | --- |
| `DATABASE_URL` | SQLAlchemy database connection |
| `JWT_SECRET` | Signs and verifies access tokens; replace the development value |
| `JWT_ALGORITHM` | JWT signing algorithm |
| `JWT_EXPIRATION_MINUTES` | Token lifetime |
| `MAX_UPLOAD_SIZE` | Maximum upload size in bytes |
| `UPLOAD_DIR` | Where uploaded files are stored |
| `CHUNK_SIZE` | Approximate maximum chunk characters |
| `CHUNK_OVERLAP` | Approximate repeated characters between adjacent chunks |
| `EMBEDDING_PROVIDER` | `openai`, `gemini`, or `mock` |
| `EMBEDDING_API_KEY` | Embedding API key; OpenAI also falls back to `LLM_API_KEY` |
| `GEMINI_API_KEY` | Gemini embedding credential; Gemini embeddings also fall back to `EMBEDDING_API_KEY` |
| `EMBEDDING_MODEL` | Embedding model name |
| `EMBEDDING_DIMENSIONS` | Embedding width and pgvector column width |
| `LLM_PROVIDER` | `openai`, `gemini`, `groq`, or `mock` |
| `LLM_API_KEY` | Chat-generation API key, including Gemini chat generation |
| `LLM_MODEL` | Chat model name accepted by the chosen LLM provider |
| `TOP_K` | Maximum nearest chunks to retrieve |
| `MIN_SIMILARITY_SCORE` | Lowest returned cosine similarity accepted as context |
| `CONVERSATION_HISTORY_LIMIT` | Maximum previous messages included in generation |

Do not commit `.env` or disclose API keys. Keep `.env.example` as a template
without real credentials.

## 10. Running and testing

Typical development workflow:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Configure `.env`, install PostgreSQL and its pgvector server extension, create
the database, then run:

```bash
alembic upgrade head
uvicorn app.main:app --reload
```

For tests, SQLite can be used to avoid a PostgreSQL server. The test suite mocks
provider calls or explicitly uses deterministic mock embeddings:

```bash
DATABASE_URL=sqlite:////tmp/kb-tests.db \
EMBEDDING_PROVIDER=mock \
python -m pytest -q
```

Use a fresh temporary SQLite path if the tests share a persistent local
database: the simple API tests create fixed email addresses and will report a
duplicate user if rerun against a database containing those users.

Tests are organized around:

- `test_chunking_service.py`: chunk boundaries, overlap, page numbers, size limits.
- `test_embedding_service.py`: provider behavior and one-embedding-per-chunk processing.
- `test_chunk_repository.py`: vector persistence and transactional replacement.
- `test_vector_search_service.py`: ranking, document ownership, pgvector SQL shape.
- `test_rag_service.py`: prompt context, question embedding, source persistence,
  threshold behavior, and bounded history.
- `test_file_parser.py`: DOCX paragraphs, tables, nested tables, headers, footers.
- `test_rag_pipeline_integration.py`: authenticated upload through processing,
  retrieval, response citations, and provider-error response.

## 11. What this project does not currently do

Knowing the boundaries is as important as knowing the implemented features:

- **No cross-document deduplication:** identical chunk text in two documents is
  stored as two chunk rows. The unique index constraint is only on each
  document's chunk index.
- **No OCR:** image-only/scanned PDFs and text inside document images are not
  converted to text.
- **No durable queue:** `BackgroundTasks` run in the web process and are not a
  substitute for a worker queue such as Celery/RQ.
- **No live provider guarantee:** tests mock network providers; a working key,
  model access, account quota, and billing are needed for real provider calls.
- **No citation-label verifier:** backend sources are real retrieved rows, but
  the LLM's textual `[Source N]` labels are instructed, not parsed and checked.
- **No token-aware chunking:** chunk sizing is currently by characters.
- **No automatic re-embedding:** changing embedding model/dimensions does not
  update existing vectors by itself.
- **No immutable source snapshot:** history stores links to chunk rows. The
  foreign key can prevent deletion of chunks referenced by old citations, and
  deleting a document does not preserve a copy of source content in history.
- **SQLite is a test/development fallback:** its similarity implementation
  scans stored vectors in Python and is not a production vector index.

## 12. Interview-ready explanations

### “What is an embedding?”

“An embedding is a numeric vector representing text in a semantic space. I
embed document chunks once when the document is processed, store those vectors
with pgvector, and embed each incoming question. The database compares the
query vector to stored chunk vectors so semantically related passages can be
retrieved even when wording is different.”

### “Why chunk documents?”

“Chunks are smaller retrieval units. They reduce prompt size and cost, improve
the chance that retrieval returns a focused passage, and make citations more
specific. We preserve overlap to reduce loss of meaning at boundaries. This
project's current chunk sizes are characters, which is simple but can be tuned
or replaced with token-aware sizing.”

### “How is RAG implemented without LangChain?”

“The application explicitly implements each step: parser extracts text,
ChunkingService splits it, EmbeddingService calls an embedding provider,
ChunkRepository stores content and pgvector vectors, VectorSearchService does
cosine-distance retrieval, RagService builds labeled context and enforces a
similarity cutoff, and LLMService calls the selected chat API. The route handles
authentication and HTTP errors; there is no opaque chain.”

### “How do you prevent cross-user retrieval?”

“The vector query joins chunks to their parent documents and filters by the
authenticated user's ID. If the caller supplies document IDs, we validate that
all of them belong to that user before running the vector search.”

### “How are citations reliable?”

“The API constructs the source list from rows returned by the database and
persists message-source foreign keys. The prompt asks the model to use the
matching source numbers, but the authoritative source metadata comes from the
retrieval result, not from model-generated identifiers.”

### “What happens if there is no relevant context?”

“We filter retrieved cosine scores against `MIN_SIMILARITY_SCORE`. If nothing
survives, we return the fixed insufficient-information answer and skip the LLM
call, avoiding a general-knowledge answer with no document evidence.”

### “Why separate services and repositories?”

“The route owns HTTP concerns; services coordinate business workflows;
repositories isolate database persistence; models define the schema. For
example, the document route does not know how to split text or call an embedding
provider. This separation makes the pipeline easier to test with fake
repositories and provider implementations.”

## 13. Suggested learning sequence

For a guided code-reading session, follow this order:

1. `app/api/routes/documents.py` — understand the upload HTTP endpoint.
2. `app/services/document_service.py` — follow the ingestion orchestration.
3. `app/utils/file_parser.py` — see how file formats become text.
4. `app/services/chunking_service.py` — study chunk boundaries and overlap.
5. `app/services/embedding_service.py` — compare a provider interface with
   concrete API implementations.
6. `app/db/models/chunk.py` and `app/repositories/chunk_repository.py` — inspect
   vector storage and transaction behavior.
7. `app/api/routes/chat.py` — understand authenticated question submission.
8. `app/services/rag_service.py` — follow embedding, retrieval, context,
   generation, and source persistence.
9. `app/services/vector_search_service.py` — inspect the owner-scoped cosine
   search.
10. `app/services/llm_service.py` — compare OpenAI-compatible, Gemini, Groq, and
    mock chat adapters.
11. The corresponding `tests/test_*.py` files — see how individual boundaries
    are tested without depending on external APIs.
