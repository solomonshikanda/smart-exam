from fastapi import APIRouter, Request, HTTPException, Depends
from google.cloud import firestore
import google.generativeai as genai
import re
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
import datetime
import google.oauth2.id_token
from google.auth.transport import requests
import difflib
import json
import logging
from typing import List, Dict, Any, Optional

# Templates & router
router = APIRouter()
templates = Jinja2Templates(directory="app/templates")
firebase_request = requests.Request()

# Replace with your admin emails (or fetch from config)
ADMIN_EMAILS = {"admin@co.uk.com", "teacher@example.com"}

# verify token (same as you had)
async def verify_token(id_token: str):
    if not id_token:
        return None
    try:
        decoded_token = google.oauth2.id_token.verify_firebase_token(id_token, firebase_request)
        return decoded_token
    except Exception:
        return None

# ---------- Helpers ----------

def parse_model_json_or_fallback(text: str) -> Dict[str, Any]:
    """
    Try to extract a JSON object from the model text. If that fails, attempt to
    parse a leading 'X / 10' score and return minimal structured feedback.
    """
    # Try to find the first {...} JSON block
    try:
        json_text_match = re.search(r"(\{(?:.|\n)*\})", text)
        if json_text_match:
            payload = json.loads(json_text_match.group(1))
            return payload if isinstance(payload, dict) else {}
    except Exception as e:
        logging.debug("JSON parse failed: %s", e)

    # Fallback: parse score like "8 / 10" and return the rest as reason
    match = re.search(r"(\d+)\s*/\s*10", text)
    if match:
        score = int(match.group(1))
        reason = text[match.end():].strip() or text.strip()
        return {"score": score, "reason": reason, "feedback": text.strip()}
    # Ultimate fallback
    return {"score": 0, "reason": "Could not parse model output", "feedback": text.strip()}

def normalize_text(s: Optional[str]) -> str:
    if not s:
        return ""
    # Lowercase, remove extra whitespace, strip punctuation except internal apostrophes
    normalized = re.sub(r"\s+", " ", s.lower()).strip()
    return normalized

def similarity(a: str, b: str) -> float:
    """Return similarity ratio between 0 and 1 using difflib."""
    return difflib.SequenceMatcher(None, a, b).ratio()

def find_plagiarism_for_response(db: firestore.Client, current_doc_id: str, threshold: float = 0.8) -> List[Dict[str, Any]]:
    """
    Compare the submitted answers in `current_doc_id` against other exam_responses in the DB.
    Returns a list of matches: {other_id, question_index, similarity, other_answer_snippet}
    """
    matches = []
    exam_ref = db.collection("exam_responses")
    current_doc = exam_ref.document(current_doc_id).get()
    if not current_doc.exists:
        return matches
    cur_data = current_doc.to_dict()
    cur_responses = cur_data.get("responses", [])
    # Pre-normalize answers
    for i, cur_item in enumerate(cur_responses):
        cur_answer = normalize_text(cur_item.get("submitted_answer", ""))
        if not cur_answer:
            continue
        # Compare to all others
        other_docs = exam_ref.stream()
        for other in other_docs:
            other_id = other.id
            if other_id == current_doc_id:
                continue
            other_data = other.to_dict()
            other_responses = other_data.get("responses", [])
            # Try to find best matching question in other doc
            for j, other_item in enumerate(other_responses):
                other_answer = normalize_text(other_item.get("submitted_answer", ""))
                if not other_answer:
                    continue
                sim = similarity(cur_answer, other_answer)
                if sim >= threshold:
                    matches.append({
                        "exam_response_id": other_id,
                        "question_index": i,
                        "other_question_index": j,
                        "similarity": round(sim, 3),
                        "other_answer_preview": other_answer[:200],
                        "other_email": other_data.get("email", "N/A"),
                    })
    return matches

# ---------- Prompt templates & grading helpers ----------

def build_grading_prompt(question: str, answer: str, q_type: str = "", options: List[str] = None) -> str:
    """
    Ask the model to produce a JSON output with:
    {
      "score": X,           # integer 0-10
      "reason": "text",     # short reason why this score
      "feedback": "text"    # suggestions to improve
    }
    """
    options_text = ""
    if q_type == "multiple_choice" and options:
        options_text = f"\nOptions: {', '.join(options)}"

    prompt = f"""
You are a strict but fair grading assistant. For the provided question and the student's answer, return a JSON object ONLY (no extra commentary)
with the following fields:
- score: integer between 0 and 10 (inclusive).
- reason: 1-2 sentence explanation justifying the score.
- feedback: 1-2 short actionable suggestions on how the student could improve.

Respond only with a single JSON object.

Question: {question}
Student answer: {answer}
Type: {q_type}
{options_text}
"""
    return prompt.strip()

# ---------- Pydantic payloads ----------

class GradeInput(BaseModel):
    exam_response_id: str

class ApproveInput(BaseModel):
    exam_response_id: str
    approve: bool = True

# ---------- Endpoints ----------

@router.post("/grade_responses")
async def grade_responses(payload: GradeInput):
    """
    Grade a single exam response and store structured feedback + plagiarism matches.
    """
    db = firestore.Client()
    doc_ref = db.collection("exam_responses").document(payload.exam_response_id)
    doc = doc_ref.get()

    if not doc.exists:
        raise HTTPException(status_code=404, detail="Exam response not found")

    data = doc.to_dict()
    responses = data.get("responses", [])

    model = genai.GenerativeModel("gemini-1.5-flash")

    graded_results = []
    total_score = 0
    num_questions_graded = 0

    for idx, item in enumerate(responses):
        question = item.get("question", "")
        answer = item.get("submitted_answer", "")
        q_type = item.get("type", "")
        options = item.get("options", [])

        if not question or not answer:
            # keep placeholder result so indices align
            graded_results.append({
                "question": question,
                "submitted_answer": answer,
                "score": None,
                "reason": None,
                "feedback": None
            })
            continue

        prompt = build_grading_prompt(question, answer, q_type, options)
        try:
            response = model.generate_content(prompt)
            text = response.text.strip()
            parsed = parse_model_json_or_fallback(text)
            score = int(parsed.get("score", 0))
            reason = parsed.get("reason", "")
            fb = parsed.get("feedback", "")
        except Exception as e:
            logging.exception("Error grading question %s: %s", question, e)
            score = 0
            reason = "Grading failed due to internal error."
            fb = "Please contact instructor."

        total_score += score
        num_questions_graded += 1

        graded_results.append({
            "question": question,
            "submitted_answer": answer,
            "score": score,
            "reason": reason,
            "feedback": fb
        })

    final_score = round((total_score / (num_questions_graded * 10)) * 100, 2) if num_questions_graded > 0 else 0

    # Plagiarism check (threshold default 0.8)
    plagiarism_matches = find_plagiarism_for_response(db, payload.exam_response_id, threshold=0.8)

    # Update Firestore doc
    doc_ref.update({
        "grading": graded_results,
        "final_score": final_score,
        "plagiarism_matches": plagiarism_matches,
        "approved": False,
        "approved_by": None,
        "approval_at": None,
        "graded_at": datetime.datetime.utcnow().isoformat()
    })

    return {
        "exam_response_id": payload.exam_response_id,
        "final_score": final_score,
        "grading": graded_results,
        "plagiarism_matches": plagiarism_matches
    }

@router.post("/grade_all_responses")
async def grade_all_responses():
    """
    Grades all exam responses in the collection. Returns a summary.
    """
    db = firestore.Client()
    exam_responses_ref = db.collection("exam_responses")
    all_responses = exam_responses_ref.stream()

    graded_results_summary = []

    model = genai.GenerativeModel("gemini-1.5-flash")

    for doc in all_responses:
        exam_response_id = doc.id
        exam_data = doc.to_dict()
        responses = exam_data.get("responses", [])

        graded_results = []
        total_score = 0
        num_questions_graded = 0

        for item in responses:
            question = item.get("question", "")
            answer = item.get("submitted_answer", "")
            q_type = item.get("type", "")
            options = item.get("options", [])

            if not question or not answer:
                graded_results.append({
                    "question": question,
                    "submitted_answer": answer,
                    "score": None,
                    "reason": None,
                    "feedback": None
                })
                continue

            prompt = build_grading_prompt(question, answer, q_type, options)
            try:
                response = model.generate_content(prompt)
                parsed = parse_model_json_or_fallback(response.text.strip())
                score = int(parsed.get("score", 0))
                reason = parsed.get("reason", "")
                fb = parsed.get("feedback", "")
            except Exception as e:
                logging.exception("Error grading question for ID %s: %s", exam_response_id, e)
                score = 0
                reason = "Grading failed due to internal error."
                fb = "Please contact instructor."

            total_score += score
            num_questions_graded += 1
            graded_results.append({
                "question": question,
                "submitted_answer": answer,
                "score": score,
                "reason": reason,
                "feedback": fb
            })

        final_score = round((total_score / (num_questions_graded * 10)) * 100, 2) if num_questions_graded > 0 else 0

        # Plagiarism check
        doc_ref = exam_responses_ref.document(exam_response_id)
        plagiarism_matches = find_plagiarism_for_response(db, exam_response_id, threshold=0.8)

        doc_ref.update({
            "grading": graded_results,
            "final_score": final_score,
            "plagiarism_matches": plagiarism_matches,
            "approved": False,
            "approved_by": None,
            "approval_at": None,
            "graded_at": datetime.datetime.utcnow().isoformat()
        })

        graded_results_summary.append({
            "exam_response_id": exam_response_id,
            "final_score": final_score,
            "plagiarism_matches_count": len(plagiarism_matches)
        })

    return {"message": f"Successfully graded {len(graded_results_summary)} exam responses.", "results": graded_results_summary}




# ---------- Viewing endpoints (unchanged but now show new fields) ----------

@router.get("/")
async def view_all_results(request: Request):
    id_token = request.cookies.get("idToken")
    user_info = await verify_token(id_token)
    if not user_info:
        # render login page (or raise)
        return templates.TemplateResponse("login.html", {"request": request})

    db = firestore.Client()
    docs = db.collection("exam_responses").stream()

    all_results = []
    for doc in docs:
        data = doc.to_dict()
        all_results.append({
            "exam_response_id": doc.id,
            "email": data.get("email", "N/A"),
            "final_score": data.get("final_score", 0),
            "plagiarism_matches_count": len(data.get("plagiarism_matches", [])),
            "approved": data.get("approved", False),
            "submitted_at": data.get("submitted_at", "N/A"),
        })
    email_content = user_info.get('email')
    return templates.TemplateResponse("all_results.html", {
        "request": request,
        "all_results": all_results,
        "email": email_content
    })

@router.get("/view_results/{exam_response_id}")
async def view_results(exam_response_id: str, request: Request):
    db = firestore.Client()
    doc_ref = db.collection("exam_responses").document(exam_response_id)
    doc = doc_ref.get()

    if not doc.exists:
        raise HTTPException(status_code=404, detail="Exam response not found")

    data = doc.to_dict()
    # Include plagiarism_matches and approved fields in template
    return templates.TemplateResponse("results.html", {
        "request": request,
        "exam_id": exam_response_id,
        "email": data.get("email", "N/A"),
        "submitted_at": data.get("submitted_at", "N/A"),
        "final_score": data.get("final_score", 0),
        "grading": data.get("grading", []),
        "plagiarism_matches": data.get("plagiarism_matches", []),
        "approved": data.get("approved", False),
        "approved_by": data.get("approved_by"),
        "approval_at": data.get("approval_at")
    })
from pydantic import BaseModel
from fastapi import HTTPException

class ApproveInput(BaseModel):
    exam_response_id: str
    approve: bool = True

class UpdateScoreInput(BaseModel):
    exam_response_id: str
    final_score: float


@router.post("/approve_response")
async def approve_response(payload: ApproveInput, request: Request):
    """
    Approve/unapprove a graded response.
    Allows ANY authenticated user. Records who approved/unapproved and when.
    """
    # verify token
    id_token = request.cookies.get("idToken")
    user_info = await verify_token(id_token)
    if not user_info:
        logging.info("approve_response: unauthenticated request")
        raise HTTPException(status_code=401, detail="Unauthorized")

    caller_email = user_info.get("email", "unknown")
    logging.info("approve_response called by %s for %s (approve=%s)",
                 caller_email, payload.exam_response_id, payload.approve)

    db = firestore.Client()
    doc_ref = db.collection("exam_responses").document(payload.exam_response_id)

    # ensure document exists
    doc = doc_ref.get()
    if not doc.exists:
        raise HTTPException(status_code=404, detail="Exam response not found")

    # prepare update
    update_data = {
        "approved": bool(payload.approve),
        "approved_by": caller_email if payload.approve else None,
        "approval_at": datetime.datetime.utcnow().isoformat() if payload.approve else None,
    }

    try:
        # update approval fields
        doc_ref.update(update_data)

        # append approval history (uses ArrayUnion)
        history_entry = {
            "action": "approve" if payload.approve else "unapprove",
            "by": caller_email,
            "at": datetime.datetime.utcnow().isoformat()
        }
        doc_ref.update({
            "approval_history": firestore.ArrayUnion([history_entry])
        })
    except Exception as e:
        logging.exception("approve_response: failed to update Firestore: %s", e)
        raise HTTPException(status_code=500, detail=f"Failed to update approval: {e}")

    # return tidy JSON for frontend
    return {
        "exam_response_id": payload.exam_response_id,
        "approve": payload.approve,
        "approved_by": update_data["approved_by"],
        "approval_at": update_data["approval_at"]
    }

@router.post("/update_final_score")
async def update_final_score(payload: UpdateScoreInput, request: Request):
    """
    Update final_score for an exam response. Any authenticated user can call this.
    Records who edited the score and when (audit trail).
    """
    id_token = request.cookies.get("idToken")
    user_info = await verify_token(id_token)
    if not user_info:
        raise HTTPException(status_code=401, detail="Unauthorized")

    email = user_info.get("email", "unknown")
    if payload.final_score < 0 or payload.final_score > 100:
        raise HTTPException(status_code=400, detail="final_score must be between 0 and 100")

    db = firestore.Client()
    doc_ref = db.collection("exam_responses").document(payload.exam_response_id)
    doc = doc_ref.get()
    if not doc.exists:
        raise HTTPException(status_code=404, detail="Exam response not found")

    update_data = {
        "final_score": round(float(payload.final_score), 2),
        "final_score_edited_by": email,
        "final_score_edited_at": datetime.datetime.utcnow().isoformat()
    }

    try:
        doc_ref.update(update_data)
        # append to history array
        history_entry = {
            "action": "final_score_edit",
            "by": email,
            "final_score": update_data["final_score"],
            "at": datetime.datetime.utcnow().isoformat()
        }
        doc_ref.update({
            "final_score_history": firestore.ArrayUnion([history_entry])
        })
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to update final_score: {e}")

    return {"exam_response_id": payload.exam_response_id, "final_score": update_data["final_score"]}
