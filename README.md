# AI-Powered Knowledge Base & Document Assistant

This project implements a manual RAG pipeline for PDF and DOCX knowledge bases. It provides authentication, document ingestion, text extraction, chunking, embeddings, vector search, and LLM-powered Q&A with source citations.

## Overview

The app allows authenticated users to upload documents, process them in the background, generate embeddings, and ask questions grounded in those documents. The system never relies on a pre-built RAG framework; instead, the pipeline is implemented manually with FastAPI, SQLAlchemy, PostgreSQL, pgvector, and an LLM provider.

## Architecture

### Document processing

```text
Document Upload
      |
      v
Text Extraction
      |
      v
Text Chunking
      |
      v
Embedding API
      |
      v
PostgreSQL + pgvector
```

### Question answering

```text
User Question
      |
      v
Question Embedding
      |
      v
Vector Similarity Search
      |
      v
Top Relevant Chunks
      |
      v
Prompt + Context
      |
      v
LLM
      |
      v
Answer + Citations
```

## Project Structure

```text
app/
  api/routes/auth.py
  api/routes/documents.py
  api/routes/chat.py
  core/config.py
  core/security.py
  db/database.py
  db/models/
  repositories/
  schemas/
  services/
  utils/file_parser.py
app/main.py
requirements.txt
.env.example
README.md
```

## Database Schema

- users
- documents
- document_chunks
- conversations
- messages
- message_sources

## Local Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

## PostgreSQL Setup

```bash
# Install PostgreSQL
sudo apt update
sudo apt install postgresql postgresql-contrib

# Create the database and user
sudo -u postgres psql -c "CREATE USER knowledge_user WITH PASSWORD 'knowledge_pass';"
sudo -u postgres psql -c "CREATE DATABASE knowledge_base OWNER knowledge_user;"

# Install the pgvector server extension package matching your PostgreSQL version.
# The first Alembic migration enables the extension in the project database.
```

Configure `.env` with a SQLAlchemy psycopg v3 URL:

```env
DATABASE_URL=postgresql+psycopg://knowledge_user:knowledge_pass@localhost:5432/knowledge_base
```

Apply the schema migration before starting the API:

```bash
alembic upgrade head
uvicorn app.main:app --reload
```

SQLite is used for local tests and creates its tables automatically. PostgreSQL does not use `create_all`; its schema and pgvector extension are managed through Alembic migrations.

## Authentication Flow

- User registers via /auth/register
- Password is hashed with bcrypt
- Login returns a JWT token
- Protected routes require a Bearer token

## Document Processing Flow

1. Upload PDF/DOCX
2. Save DB record with status PENDING
3. Background task kicks off processing
4. Extract text from document
5. Split into chunks
6. Generate embeddings
7. Store chunk embeddings
8. Mark status COMPLETED

DOCX extraction reads paragraphs and tables in document order. Table rows are serialized into pipe-separated text so headers and cell values are retained in stored chunks and available to retrieval. Nested tables in cells are also extracted. DOCX pagination is not stable across renderers, so these chunks use page number 1; PDF extraction retains the page number reported by the PDF parser.

## Chunking Strategy

This app uses custom chunking instead of LangChain. The algorithm attempts to keep sentences together while respecting a maximum chunk size and overlap. This avoids splitting words or awkward mid-sentence boundaries where possible.

## Embedding Generation

Embeddings are generated through `EmbeddingService`, which depends on an embedding-provider interface rather than coupling document processing or retrieval directly to a vendor. `EMBEDDING_PROVIDER=openai` selects the OpenAI implementation; `EMBEDDING_PROVIDER=gemini` selects Google's Gemini implementation using the `google-genai` SDK. OpenAI uses `EMBEDDING_API_KEY` (or the shared `LLM_API_KEY`); Gemini uses `GEMINI_API_KEY` (or `EMBEDDING_API_KEY`). Set `EMBEDDING_MODEL` to a model supported by the selected provider. For example, Gemini supports `gemini-embedding-001`. The embedding model and vector width are selected with `EMBEDDING_MODEL` and `EMBEDDING_DIMENSIONS`; the database vector column and the provider output must use the same width. Changing the width of an existing database requires a migration and re-embedding its documents. Missing credentials and malformed provider results raise explicit errors; they do not silently produce fake production embeddings.

The Gemini provider requests `RETRIEVAL_DOCUMENT` embeddings for uploaded chunks and `RETRIEVAL_QUERY` embeddings for questions. For tests that do not inject a provider, `EMBEDDING_PROVIDER=mock` explicitly selects deterministic mock vectors. Mock vectors are for testing only and do not provide semantic search quality.

## Chat Generation Providers

Chat generation is configured with the same three environment variables for every provider: `LLM_API_KEY`, `LLM_PROVIDER`, and `LLM_MODEL`. Supported provider values are `openai`, `gemini`, `groq`, and `mock`; `LLM_MODEL` must be supported by the selected provider.

- OpenAI: `LLM_PROVIDER=openai`, `LLM_MODEL=gpt-4o-mini`, and the OpenAI key in `LLM_API_KEY`.
- Gemini: `LLM_PROVIDER=gemini`, `LLM_MODEL=gemini-2.5-pro`, and the Gemini key in `LLM_API_KEY`. Uses the Google GenAI SDK.
- Groq: `LLM_PROVIDER=groq`, choose a chat model enabled for your Groq account, and put the Groq key in `LLM_API_KEY`. Uses Groq's OpenAI-compatible API endpoint.
- Mock: `LLM_PROVIDER=mock`; no chat API credentials are needed. The mock deliberately returns a fixed diagnostic response and is only for local development/tests.

Embedding selection uses the separate `EMBEDDING_PROVIDER`, `EMBEDDING_MODEL`, and embedding API key settings. Selecting Gemini embeddings does not select Gemini chat generation, and vice versa.

## Vector Similarity Search

`VectorSearchService` uses pgvector's cosine-distance operator (`<=>`) on PostgreSQL and orders results by nearest distance, returning cosine similarity as `1 - distance`. Every query joins through `documents` and filters on the authenticated user's ID; optional document filters are checked for ownership before searching. `TOP_K` controls the retrieval count. The SQLite implementation computes cosine similarity in Python only for local development and tests.

`MIN_SIMILARITY_SCORE` (default `0.30`) controls the relevance cutoff. If retrieval is empty or all retrieved chunks score below the cutoff, the RAG service returns the insufficient-information response without calling the LLM.

## RAG Architecture

The RAG pipeline is built manually:

1. Embed the user question
2. Retrieve the most relevant document chunks owned by the user
3. Drop results below the similarity cutoff
4. Build labeled context using actual chunk IDs, filenames, page numbers, and content
5. Send the context and question to the LLM with a grounding system instruction
6. Return backend-generated source metadata for those exact retrieved chunks

For an existing conversation, up to `CONVERSATION_HISTORY_LIMIT` recent messages are included to help resolve follow-up references. That history is not used as factual evidence: the system instruction requires answers to be grounded in the current retrieved document context.

## API Endpoints

- POST /auth/register
- POST /auth/login
- GET /auth/me
- POST /documents
- GET /documents
- GET /documents/{document_id}
- DELETE /documents/{document_id}
- POST /chat
- GET /conversations
- GET /conversations/{conversation_id}
- DELETE /conversations/{conversation_id}

### Chat request

```json
{
  "question": "How does authentication work?",
  "conversation_id": null,
  "document_ids": [1, 2]
}
```

The response's `sources` array is assembled from the actual retrieved chunks and includes their document IDs, filenames, page numbers, chunk IDs, and similarity scores. The assistant message and its source-chunk relations are stored together with the user message in a database transaction.

## Testing

```bash
pytest
```

## Notes

This first version intentionally keeps the design simple and production-friendly enough to be extended later with Celery, Redis, or a task queue.
