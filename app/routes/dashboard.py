from fastapi import APIRouter ,requests
from fastapi.requests import Request
from fastapi.templating import Jinja2Templates
from fastapi.responses import RedirectResponse ,HTMLResponse
import google.oauth2.id_token
from google.auth.transport import requests

templates = Jinja2Templates(directory="app/templates")
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
import logging
@router.get("/",response_class=HTMLResponse)
async def analyze_behavior(request: Request):
    id_token = request.cookies.get("idToken")
    logging.debug("Incoming request cookie idToken present? %s", bool(id_token))
    user_info = await verify_token(id_token)
    if user_info:
        
        email_content= user_info.get('email')
        return templates.TemplateResponse("dashboard.html",{
            "request": request,
            "email": email_content
        })
    else:
        return RedirectResponse(url="/login", status_code=302)
