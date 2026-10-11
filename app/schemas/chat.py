from pydantic import BaseModel, ConfigDict, Field


class ChatRequest(BaseModel):
    model_config = ConfigDict(
        str_strip_whitespace=True,
        json_schema_extra={
            "examples": [
                {
                    "question": "How does authentication work?",
                    "conversation_id": None,
                    "document_ids": [1, 2],
                }
            ]
        },
    )

    question: str = Field(..., min_length=1, max_length=8000)
    conversation_id: int | None = None
    document_ids: list[int] | None = None


class SourceResponse(BaseModel):
    document_id: int
    filename: str | None
    page_number: int | None
    chunk_id: int
    similarity_score: float
    content: str


class ChatResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    answer: str
    conversation_id: int
    sources: list[SourceResponse]
