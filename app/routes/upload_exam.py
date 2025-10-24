from fastapi import APIRouter, Request, UploadFile, Form, HTTPException
from fastapi.responses import RedirectResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from google.cloud import storage, firestore
from datetime import datetime
import uuid
import logging
import google.generativeai as genai
import json

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")

@router.get("/")
async def upload_exam_page(request: Request):
    return templates.TemplateResponse("upload_exam.html", {"request": request})


@router.post("/upload")
async def upload_exam(
    request: Request,
    exam_title: str = Form(...),
    exam_file: UploadFile = Form(...)
):
    try:
        # Read file content
        file_content = await exam_file.read()
        if not file_content:
            raise HTTPException(status_code=400, detail="Uploaded file is empty.")

        file_extension = exam_file.filename.split('.')[-1] if exam_file.filename else 'txt'
        file_id = str(uuid.uuid4())
        file_name = f"{file_id}.{file_extension}"

        # Upload to GCS
        bucket_name = "instagram_task"
        storage_client = storage.Client()
        bucket = storage_client.bucket(bucket_name)
        blob = bucket.blob(f"exams/{file_name}")
        blob.upload_from_string(file_content, content_type=exam_file.content_type)

        # Decode file content 
        try:
            file_text = file_content.decode("utf-8")
        except UnicodeDecodeError:
            raise HTTPException(status_code=400, detail="Unsupported file encoding. Use UTF-8 text files.")

        # Generate questions using Gemini
        model = genai.GenerativeModel("gemini-2.5-flash")

        prompt = f"""
        You are an exam generator. Based on the following exam material, create randomized questions based on request outlined by user:
        
        --- Begin Content ---
        {file_text[:4000]} # Gemini token limit: truncate if needed
        --- End Content ---

        Return the questions in JSON format. Do not include any additional text, explanations, or code fences (```json). The format should be a JSON array containing objects. Each object must have a 'question' key. The objects can also have a 'type' key with a value of either 'multiple_choice' or 'short_answer'. If the type is 'multiple_choice', include an 'options' key with an array of strings.
        """

        response = model.generate_content(prompt)
        
        # Clean the string by removing Markdown code fences if they exist
        # and then attempt to parse the JSON.
        try:
            raw_text = response.text.strip()
            # Check for and remove the JSON code
            if raw_text.startswith("```json"):
                raw_text = raw_text.lstrip("```json").rstrip("`")
            
            generated_questions = json.loads(raw_text)
            logging.info("Successfully parsed Gemini's JSON response.")
        except json.JSONDecodeError as e:
            logging.error(f"Failed to parse Gemini's JSON response: {e}")
            logging.error(f"Gemini response text: {response.text}")
            # Fallback to storing the raw string if parsing fails
            generated_questions = response.text 
       

        # Store metadata + Gemini questions in Firestore
        db = firestore.Client()
        doc_ref = db.collection("exams").document(file_id)
        doc_ref.set({
            "created_at": datetime.utcnow(),
            "title": exam_title,
            "filename": file_name,
            "original_name": exam_file.filename,
            "content_type": exam_file.content_type,
            "url": blob.public_url,
            "uploaded_by": "admin@lecture",  
            "questions_generated": generated_questions,
        })

        logging.info(f"Uploaded: {file_name} | Size: {len(file_content)} bytes | URL: {blob.public_url}")

        return RedirectResponse(url="/exams/", status_code=303)

    except Exception as e:
        logging.error(f"Upload failed: {str(e)}")
        return JSONResponse(status_code=500, content={"detail": "Upload failed", "error": str(e)})