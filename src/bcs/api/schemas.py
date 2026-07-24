from pydantic import BaseModel
from typing import List, Optional, Dict, Any


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
    mcq_id: Optional[str] = None
    episode_id: Optional[str] = None
    fact_ids: Optional[List[str]] = None


class GenerateResponse(BaseModel):
    mcqs: List[MCQOut]
    topic: str
    difficulty: str
    count: int
    error: Optional[str] = None
    kg_size: Optional[int] = None
    memory_size: Optional[int] = None
    generation_duration_ms: Optional[int] = None
    grounding_facts: Optional[List[str]] = None
    pipeline_run_id: Optional[str] = None


class TopicOut(BaseModel):
    topic: str
    fact_count: int


class HealthResponse(BaseModel):
    status: str
    kg_nodes: int
    kg_facts: int
    memory_size: int
    model: str


class FeedbackRequest(BaseModel):
    episode_id: str
    mcq_id: str
    fact_ids: Optional[List[str]] = None
    rating: int = 3
    category: Optional[str] = None
    comment: Optional[str] = None


class FeedbackResponse(BaseModel):
    id: int
    message: str


class FeedbackStatsResponse(BaseModel):
    total: int
    avg_rating: float
    distribution: Dict[str, int]
    categories: List[Dict[str, Any]]


class DatasetRecord(BaseModel):
    mcq_id: str
    episode_id: str
    question: str
    options: Any
    correct_answer: str
    difficulty: str
    quality_score: float
    format_score: float
    grounding_score: float
    clarity_score: float
    distractor_score: float
    regeneration_round: int
    topic: str
    timestamp: str
    avg_rating: Optional[float] = None
    rating_count: Optional[int] = None


class DatasetResponse(BaseModel):
    total: int
    min_quality: float
    records: List[DatasetRecord]


class AuthRegisterRequest(BaseModel):
    email: str
    password: str
    display_name: Optional[str] = None


class AuthLoginRequest(BaseModel):
    email: str
    password: str


class AuthResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user_id: str
    email: str
    display_name: Optional[str] = None


class UserOut(BaseModel):
    user_id: str
    email: str
    display_name: Optional[str] = None
    created_at: str
    is_active: bool
