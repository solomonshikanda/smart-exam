from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import RedirectResponse, HTMLResponse
from fastapi.templating import Jinja2Templates
from google.cloud import firestore
from google.auth.transport import requests
import google.oauth2.id_token
import google.generativeai as genai
from dateutil.parser import parse
import uuid, json, logging
from datetime import datetime

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")
firebase_request = requests.Request()
db = firestore.Client()
from datetime import timedelta
import datetime
from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from google.cloud import firestore, storage


db = firestore.Client()
bucket = storage.Client().bucket("monitor_exams")  # replace with your bucket name

@router.get("/monitor/{exam_id}")
async def get_monitor_data(exam_id: str):
    try:
        # Query latest proctor_events grouped by student
        events_ref = db.collection("proctor_events").where("exam_id", "==", exam_id)
        docs = events_ref.stream()

        latest_events = {}
        for doc in docs:
            data = doc.to_dict()
            student = data["student_email"]
            ts = data.get("timestamp")
            if student not in latest_events or ts > latest_events[student]["timestamp"]:
                latest_events[student] = {
                    "tab_status": data.get("tab_status", "unknown"),
                    "timestamp": ts.isoformat() if hasattr(ts, "isoformat") else str(ts),
                }

        # Attach snapshot from bucket
        for student in latest_events.keys():
            blob_path = f"{exam_id}/{student}.png"  # adjust if your naming is different
            blob = bucket.blob(blob_path)
            if blob.exists():
                url = blob.generate_signed_url(expiration=timedelta(minutes=30))
                latest_events[student]["snapshot_url"] = url
            else:
                latest_events[student]["snapshot_url"] = None

        return JSONResponse(latest_events)

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
# -------------------------
# Authentication
# -------------------------
async def verify_token(id_token: str):
    if not id_token:
        return None
    try:
        return google.oauth2.id_token.verify_firebase_token(id_token, firebase_request)
    except ValueError:
        return None

# -------------------------
# Check if user has taken exam
# -------------------------
def has_user_taken_exam(user_email: str, exam_id: str):
    """
    Returns True if user has already taken the exam.
    """
    user_doc = db.collection("exams").document(exam_id).collection("users").document(user_email).get()
    return user_doc.exists

# -------------------------
# Get user's in-progress or submitted exam status
# -------------------------
def get_user_exam_status(user_uid: str, exam_id: str):
    """
    Returns the status of a specific exam for this user.
    - 1 = in-progress
    - 'submitted' = completed
    - None = not started
    """
    instances = db.collection("exams") \
                  .document(exam_id) \
                  .collection("instances") \
                  .where("user_id", "==", user_uid) \
                  .stream()
    for inst in instances:
        data = inst.to_dict()
        return data.get("status")  # 1 or "submitted"
    return None

# -------------------------
# Delete exam (admin)
# -------------------------
@router.post("/{exam_id}/delete")
async def delete_exam(request: Request, exam_id: str):
    id_token = request.cookies.get("idToken")
    user_info = await verify_token(id_token)
    if not user_info:
        return RedirectResponse("/login", status_code=302)
    
    db.collection("exams").document(exam_id).delete()
    return RedirectResponse("/exams", status_code=303)

# -------------------------
# Take exam
# -------------------------
@router.get("/{exam_id}", response_class=HTMLResponse)
async def take_exam_page(request: Request, exam_id: str):
    id_token = request.cookies.get("idToken")
    user_info = await verify_token(id_token)
    if not user_info:
        return RedirectResponse("/login", status_code=302)

    exam_snap = db.collection("exams").document(exam_id).get()
    if not exam_snap.exists:
        raise HTTPException(status_code=404, detail="Exam not found.")
    exam_base = exam_snap.to_dict()

    # Check active instance
    active_instance_query = (
        db.collection("exams").document(exam_id)
        .collection("instances")
        .where("user_id", "==", user_info.get("uid"))
        .where("status", "==", 1)
        .limit(1)
        .stream()
    )
    active_instance_doc = next(active_instance_query, None)

    if active_instance_doc:
        instance = active_instance_doc.to_dict()
        random_exam_id = instance.get("instance_id")
        return templates.TemplateResponse(
            "take_exam.html",
            {
                "request": request,
                "email": user_info.get("email"),
                "exam_id": exam_id,
                "random_exam_id": random_exam_id,
                "title": exam_base.get("title", "Exam"),
                "exam": exam_base,
                "questions": instance.get("questions_generated", []),
                "status": 1,
            },
        )

    # Create new exam instance
    random_exam_id = str(uuid.uuid4())
    file_text = exam_base.get("content", exam_base)

    # Generate questions via Gemini
    model = genai.GenerativeModel("gemini-2.5-flash")
    prompt = f"""
    You are an exam generator. Based on the following exam material, create new randomized questions:

    --- Begin Content ---
    {file_text}
    --- End Content ---

    Return JSON format only. Each item must have 'question'. Include 'type' ('multiple_choice' or 'short_answer'); if 'multiple_choice', include 'options'.
    """
    response = model.generate_content(prompt)
    try:
        raw_text = response.text.strip()
        if raw_text.startswith("```json"):
            raw_text = raw_text.lstrip("```json").rstrip("`")
        generated_questions = json.loads(raw_text)
    except json.JSONDecodeError:
        logging.exception("Failed to parse Gemini JSON; storing raw text.")
        generated_questions = response.text

    # Save instance
    inst_ref = db.collection("exams").document(exam_id).collection("instances").document(random_exam_id)
    inst_ref.set({
        "instance_id": random_exam_id,
        "exam_id": exam_id,
        "created_at": datetime.utcnow(),
        "questions_generated": generated_questions,
        "user_id": user_info.get("uid"),
        "email": user_info.get("email"),
        "status": 1,
    })

    return templates.TemplateResponse(
        "take_exam.html",
        {
            "request": request,
            "email": user_info.get("email"),
            "exam_id": exam_id,
            "random_exam_id": random_exam_id,
            "title": exam_base.get("title", "Exam"),
            "exam": exam_base,
            "questions": generated_questions,
            "status": 1,
        },
    )

# -------------------------
# Submit exam
# -------------------------
@router.post("/{exam_id}/{random_exam_id}/submit", response_class=RedirectResponse)
async def submit_exam(request: Request, exam_id: str, random_exam_id: str):
    id_token = request.cookies.get("idToken")
    user_info = await verify_token(id_token)
    if not user_info:
        return RedirectResponse("/login", status_code=302)

    inst_ref = db.collection("exams").document(exam_id).collection("instances").document(random_exam_id)
    inst_snap = inst_ref.get()
    if not inst_snap.exists:
        raise HTTPException(status_code=404, detail="Exam instance not found.")

    instance = inst_snap.to_dict()
    questions = instance.get("questions_generated", [])
    form_data = await request.form()
    submitted_responses = []

    for i, q in enumerate(questions):
        field_name = f"question-{i + 1}"
        submitted_answer = form_data.get(field_name)
        item = {
            "index": i + 1,
            "question": (q.get("question") if isinstance(q, dict) else q),
            "type": (q.get("type") if isinstance(q, dict) else None),
            "submitted_answer": submitted_answer,
        }
        if isinstance(q, dict) and q.get("type") == "multiple_choice":
            item["options"] = q.get("options")
        submitted_responses.append(item)

    # Save responses
    db.collection("exam_responses").document(random_exam_id).set({
        "instance_id": random_exam_id,
        "exam_id": exam_id,
        "user_id": user_info.get("uid"),
        "email": user_info.get("email"),
        "submitted_at": datetime.utcnow().isoformat(),
        "responses": submitted_responses,
    })

    # Mark instance submitted
    inst_ref.update({"status": "submitted", "submitted_at": datetime.utcnow()})

    # Add user to exam 'users' collection
    db.collection("exams").document(exam_id).collection("users").document(user_info.get("email")).set({
        "email": user_info.get("email"),
        "submitted_at": datetime.utcnow()
    })

    return RedirectResponse("/dashboard", status_code=303)

# -------------------------
# List all exams
# -------------------------
@router.get("/", response_class=HTMLResponse)
async def list_exams(request: Request):
    id_token = request.cookies.get("idToken")
    user_info = await verify_token(id_token)
    if not user_info:
        return RedirectResponse("/login", status_code=302)

    current_user_email = user_info.get("email")
    current_user_id = user_info.get("uid")

    exams_ref = db.collection("exams")
    exams = []
    for doc in exams_ref.stream():
        data = doc.to_dict()
        data["id"] = doc.id
        created_at = data.get("created_at")
        if created_at:
            try:
                dt = parse(created_at) if isinstance(created_at, str) else created_at
                data["created_at"] = dt.strftime("%Y-%m-%d %H:%M:%S")
            except Exception:
                pass
        exams.append(data)

    # Map each exam -> whether user already took it
    exam_status_map = {}
    if current_user_email != "admin@co.uk":
        for exam in exams:
            exam_status_map[exam["id"]] = has_user_taken_exam(current_user_email, exam["id"])

    return templates.TemplateResponse(
        "exam.html",
        {
            "request": request,
            "exams": exams,
            "user": user_info,
            "email": current_user_email,
            "exam_status_map": exam_status_map,
        },
    )




# Monitor all students in an exam
from datetime import datetime

@router.get("/monitor/{exam_id}", response_class=HTMLResponse)
async def monitor_exam(request: Request, exam_id: str):
    id_token = request.cookies.get("idToken")
    user_info = await verify_token(id_token)
    if not user_info:
        return RedirectResponse("/login", status_code=302)

    # Only allow admin or monitors
    if user_info.get("email") != "admin@co.uk":
        raise HTTPException(status_code=403, detail="Not authorized to monitor.")

    # Get all users (students) under this exam
    users_ref = db.collection("exams").document(exam_id).collection("users")
    students = []
    for doc in users_ref.stream():
        data = doc.to_dict()
        data["email"] = doc.id  # doc.id is the student email
        # Convert any datetime objects to ISO strings
        for key, value in data.items():
            if isinstance(value, datetime):
                data[key] = value.isoformat()
        students.append(data)

    exam_snap = db.collection("exams").document(exam_id).get()
    exam_title = exam_snap.to_dict().get("title", "Exam") if exam_snap.exists else "Exam"

    return templates.TemplateResponse(
        "monitor_all.html",
        {
            "request": request,
            "exam_id": exam_id,
            "exam_title": exam_title,
            "students": students,
        },
    )
