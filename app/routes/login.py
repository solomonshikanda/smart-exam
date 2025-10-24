from fastapi import APIRouter, Request
from fastapi.templating import Jinja2Templates
from fastapi.responses import RedirectResponse, HTMLResponse
import google.oauth2.id_token
import logging
from google.auth.transport import requests as google_requests  # alias to avoid collisions

templates = Jinja2Templates(directory="app/templates")
router = APIRouter()

# Create the request adapter ONCE (before using it)
firebase_request = google_requests.Request()

def verify_token(id_token: str):
    if not id_token:
        logging.info("No idToken cookie present")
        return None
    try:
        decoded = google.oauth2.id_token.verify_firebase_token(
            id_token, firebase_request,
        )
        logging.info("Token verified for uid=%s", decoded.get("sub"))
        return decoded
    except Exception as e:
        logging.exception("Token verification failed")
        return None

@router.get("/", response_class=HTMLResponse)
async def root(request: Request):
    id_token = request.cookies.get("idToken")
    user_info = verify_token(id_token)
    if user_info:
        # Optional: use info = user_info.get("email") if needed
        return RedirectResponse(url="/dashboard/", status_code=302)
    # Not authenticated -> render login page
    return templates.TemplateResponse("login.html", {"request": request})

@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    # Separate /login route if your frontend redirects here
    return templates.TemplateResponse("login.html", {"request": request})

@router.post("/logout")
@router.get("/logout")  # allow GET too if your button does a simple link
async def logout():
    # Clear the cookie and redirect to /login
    resp = RedirectResponse(url="/login", status_code=303)
    # Use the same path you used when setting the cookie (likely "/")
    resp.delete_cookie(key="idToken", path="/")
    return resp
