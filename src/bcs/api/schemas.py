from pydantic import BaseModel
from typing import List, Optional


class GenerateRequest(BaseModel):
    topic: str = "Bangladesh Geography"
    difficulty: str = "medium"
    count: int = 1
    max_facts: int = 5


class OptionOut(BaseModel):
    key: str
    text: str


class MCQOut(BaseModel):
    question: str
    options: List[OptionOut]
    correct_answer: str
    difficulty: str
    quality_score: float
    explanation: str


class GenerateResponse(BaseModel):
    mcqs: List[MCQOut]
    topic: str
    difficulty: str
    count: int
    error: Optional[str] = None
    kg_size: Optional[int] = None
    memory_size: Optional[int] = None


class TopicOut(BaseModel):
    topic: str
    fact_count: int


class HealthResponse(BaseModel):
    status: str
    kg_nodes: int
    kg_facts: int
    memory_size: int
    model: str
