from fastapi import APIRouter ,requests
from fastapi.requests import Request
from fastapi.templating import Jinja2Templates
from fastapi.responses import RedirectResponse ,HTMLResponse
import google.oauth2.id_token
from google.auth.transport import requests
from fastapi import APIRouter, Response
from fastapi.responses import RedirectResponse




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

@router.get("/")
async def logout(request: Request):
    response = RedirectResponse("/login", status_code=302)
    response.delete_cookie("idToken")  
    return response
            

   
