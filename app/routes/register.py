from fastapi import APIRouter ,requests
from fastapi.requests import Request
from fastapi.templating import Jinja2Templates
templates = Jinja2Templates(directory="app/templates")
from fastapi.responses import RedirectResponse ,HTMLResponse
import google.oauth2.id_token
from google.auth.transport import requests

router = APIRouter()
firebase_request = requests.Request()
async def verify_token(id_token: str):
    if not id_token:
        return None
    try:
        decoded_token = google.oauth2.id_token.verify_firebase_token(id_token, firebase_request)
        return decoded_token
    except ValueError:
        return None
@router.get("/")
async def analyze_behavior(request: Request):
    id_token = request.cookies.get("idToken")
    user_info = await verify_token(id_token)
    if user_info:
        email_content= user_info.get('email')
    else:
        email_content=""
    return templates.TemplateResponse("register.html",{
        "request": request,
        "email": email_content
    })