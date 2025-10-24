from fastapi import FastAPI
from app.routes import exams,upload_exam,websocket_router,logout,monitor_router,proctor,grading, login, register, dashboard
from app.services import firebase, gemini
from fastapi.templating import Jinja2Templates
from fastapi.responses import HTMLResponse ,RedirectResponse
import google.oauth2.id_token
from google.auth.transport import requests
from google.cloud import firestore
from google.cloud import storage
from fastapi.staticfiles import StaticFiles
from fastapi.requests import Request
from fastapi.staticfiles import StaticFiles
import os
from google.cloud import firestore

os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = "C:\\Users\\UNKNOWNCODER254\\Desktop\\fastApiProject3\\backend\\instagram-457916-firebase.json"
db = firestore.Client()


templates = Jinja2Templates(directory="app/templates")
from fastapi import FastAPI, WebSocket
from fastapi.middleware.cors import CORSMiddleware


app = FastAPI(title="Smart Internet Exam System")
origins = [
    "https://127.0.0.1:8000",
    "http://localhost:8000"
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.mount("/static", StaticFiles(directory="app/static"), name="static")


# Register routers
app.include_router(websocket_router.router,prefix="/websocket", tags=["WebSocket"])
app.include_router(exams.router, prefix="/exams", tags=["Exams"])
app.include_router(proctor.router, prefix="/proctor", tags=["Proctoring"])
app.include_router(grading.router, prefix="/grading", tags=["Grading"])
app.include_router(login.router, prefix="/login", tags=["Login"])
app.include_router(logout.router, prefix="/logout", tags=["Logout"])
app.include_router(register.router, prefix="/register", tags=["Register"])
app.include_router(dashboard.router, prefix="/dashboard", tags=["Dashboard"])
app.include_router(monitor_router.router, prefix="/monitor", tags=["monitor"])
app.include_router(upload_exam.router, prefix="/upload-exams", tags=["upload-exams"])
firebase_request = requests.Request()
# ------------------ Helper Functions ------------------

async def verify_token(id_token: str):
    if not id_token:
        return None
    try:
        decoded_token = google.oauth2.id_token.verify_firebase_token(id_token, firebase_request)
        return decoded_token
    except ValueError:
        return None


@app.get("/",response_class=HTMLResponse)
async def root(request:Request):
    id_token = request.cookies.get("idToken")
    user_info = await verify_token(id_token)
    print(user_info)
    if not user_info:
        return templates.TemplateResponse("login.html", {"request": request})

    else:
        return templates.TemplateResponse("register.html", {"request": request})

