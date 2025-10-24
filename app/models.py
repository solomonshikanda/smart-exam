from pydantic import BaseModel

class Exam(BaseModel):
    name: str
    questions: list

class Submission(BaseModel):
    user_id: str
    answers: dict
