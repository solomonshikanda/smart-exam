from fastapi import APIRouter

router = APIRouter()

@router.post("/integrity-score")
def analyze_behavior(data: dict):
    # Placeholder: calculate integrity score from webcam/mic events
    return {"score": 0.85, "flags": ["Multiple faces detected"]}
