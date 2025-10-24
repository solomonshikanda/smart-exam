import os
import json
import asyncio
import base64
import time
from typing import Dict, Tuple, Set
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from google.cloud import storage
from google.cloud import firestore

# Initialize Google Cloud Storage and Firestore clients
router = APIRouter()
try:
    storage_client = storage.Client()
    db = firestore.Client()
    print("Cloud clients initialized successfully.")
except Exception as e:
    print(f"Error initializing Google Cloud clients. Please ensure you have "
          f"the 'GOOGLE_APPLICATION_CREDENTIALS' environment variable set. Error: {e}")

# The name of your GCS bucket. You MUST change this to your bucket name.
BUCKET_NAME = "monitor_exams"

# In-memory registries (single-process). Use Redis/pubsub for multi-instance.
# Observers (lightweight viewers of thumbnails/heartbeats)
# key = (exam_id, student_email) -> set of WebSocket
observers: Dict[Tuple[str, str], Set[WebSocket]] = {}

# Student signaling socket (for monitor relay)
# key = (exam_id, student_email) -> WebSocket (student connection that will create offers)
student_signal_ws: Dict[Tuple[str, str], WebSocket] = {}

# Monitor signaling sockets (to receive offers/answers/ice relays)
# key = (exam_id, student_email, monitor_id) -> WebSocket
monitor_ws: Dict[Tuple[str, str, str], WebSocket] = {}

# Persistent WebSocket for event polling
persistent_ws: Dict[str, WebSocket] = {}  # key = student email

def obs_key(exam_id: str, student_email: str):
    return (exam_id, student_email)

def monitor_key(exam_id: str, student_email: str, monitor_id: str):
    return (exam_id, student_email, monitor_id)

async def broadcast_to_observers(exam_id: str, student_email: str, message: dict):
    """Send JSON message to all connected observer sockets for a student."""
    key = obs_key(exam_id, student_email)
    conns = observers.get(key)
    if not conns:
        return
    dead = []
    text = json.dumps(message)
    for ws in list(conns):
        try:
            await ws.send_text(text)
        except Exception:
            dead.append(ws)
    for d in dead:
        conns.discard(d)
    if not conns:
        observers.pop(key, None)

async def upload_to_gcs(exam_id: str, student_email: str, file_type: str, data: bytes) -> str:
    """
    Uploads a file to Google Cloud Storage and returns its public URL.
    This function requires the GCS bucket to be publicly accessible.
    """
    try:
        bucket = storage_client.bucket(BUCKET_NAME)
        # Use a timestamp to ensure a unique filename
        timestamp = int(time.time() * 1000)
        file_path = f"exams/{exam_id}/{student_email}/{file_type}_{timestamp}.jpeg"
        blob = bucket.blob(file_path)

        # Upload the file from the byte string
        blob.upload_from_string(data, content_type="image/jpeg")

        # Make the blob publicly accessible for easy display on the frontend
        blob.make_public()
        
        return blob.public_url
    except Exception as e:
        print(f"Error uploading to GCS: {e}")
        return "" # Return empty string on failure

# -----------------------------------
# Events endpoint (students call this)
# receives heartbeats and thumbnails and broadcasts to observers
# -----------------------------------
@router.websocket("/events/{exam_id}/{student_email}")
async def events_ws(websocket: WebSocket, exam_id: str, student_email: str):
    await websocket.accept()
    try:
        while True:
            raw = await websocket.receive_text()
            try:
                payload = json.loads(raw)
            except Exception:
                continue

            typ = payload.get("type")

            # Lightweight heartbeat and thumbnail snapshot
            if typ == "heartbeat":
                # Create a document in Firestore to log the event
                doc_ref = db.collection("proctor_events").document()
                
                log_data = {
                    "student_email": student_email,
                    "exam_id": exam_id,
                    "timestamp": firestore.SERVER_TIMESTAMP,
                    "tab_status": payload.get("tab_status", "unknown")
                }
                
                snapshot = payload.get("snapshot")
                thumbnail_url = ""
                if snapshot and snapshot.startswith("data:image"):
                    # Extract the base64 part and decode
                    header, encoded_data = snapshot.split(",", 1)
                    decoded_data = base64.b64decode(encoded_data)
                    
                    # Upload the thumbnail and get the URL
                    thumbnail_url = await upload_to_gcs(exam_id, student_email, "thumbnail", decoded_data)
                    log_data["thumbnail_url"] = thumbnail_url
                
                # Save the log to Firestore
                doc_ref.set(log_data)
                
                # Broadcast the data to observers for immediate display
                await broadcast_to_observers(exam_id, student_email, {
                    "type": "heartbeat",
                    "tab_status": log_data["tab_status"],
                    "thumbnail_url": thumbnail_url,
                    "meta": payload.get("meta", {})
                })

            elif typ == "visibility":
                # Create a log entry for visibility changes
                db.collection("proctor_events").add({
                    "student_email": student_email,
                    "exam_id": exam_id,
                    "timestamp": firestore.SERVER_TIMESTAMP,
                    "event_type": "visibility_change",
                    "detail": payload.get("tab_status")
                })
                await broadcast_to_observers(exam_id, student_email, {
                    "type": "event",
                    "event_type": "visibility_change",
                    "detail": payload.get("tab_status")
                })
            else:
                # Forward any other event types to observers
                await broadcast_to_observers(exam_id, student_email, {
                    "type": "event",
                    "event_type": payload.get("type"),
                    "detail": payload.get("detail")
                })
    except WebSocketDisconnect:
        pass
    except Exception as e:
        print(f"Events WS error: {e}")
    finally:
        try:
            await websocket.close()
        except Exception:
            pass
from fastapi.responses import JSONResponse

@router.get("/poll_status/{exam_id}/{student_email}")
async def poll_status(exam_id: str, student_email: str):
    """
    Return the latest status (tab activity + thumbnail) for a student
    from Firestore.
    """
    try:
        query = db.collection("proctor_events")\
            .where("exam_id", "==", exam_id)\
            .where("student_email", "==", student_email)\
            .order_by("timestamp", direction=firestore.Query.DESCENDING)\
            .limit(1)

        latest = None
        for doc in query.stream():
            latest = doc.to_dict()
            latest["timestamp"] = latest["timestamp"].isoformat() if latest.get("timestamp") else None

        if not latest:
            return JSONResponse({"status": "offline"}, status_code=200)

        return JSONResponse({
            "status": "online",
            "tab_status": latest.get("tab_status", "unknown"),
            "thumbnail_url": latest.get("thumbnail_url"),
            "timestamp": latest.get("timestamp"),
        })
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)
# -----------------------------------
# Observer endpoint used by monitor page cards (lightweight)
# Monitors connect here to receive heartbeats/thumbnails/events
# -----------------------------------
@router.websocket("/stream/{exam_id}/{student_email}")
async def observer_ws(websocket: WebSocket, exam_id: str, student_email: str):
    await websocket.accept()
    key = obs_key(exam_id, student_email)
    s = observers.setdefault(key, set())
    s.add(websocket)
    try:
        # observers don't need to send; keep connection alive
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        s.discard(websocket)
        if not s:
            observers.pop(key, None)
    except Exception:
        s.discard(websocket)
        if not s:
            observers.pop(key, None)
    finally:
        try:
            await websocket.close()
        except Exception:
            pass

# -----------------------------------
# Student signaling socket: student registers here
# Server uses this socket to notify the student when a monitor wants to view
# -----------------------------------
@router.websocket("/signal_student/{exam_id}/{student_email}")
async def student_signaling(websocket: WebSocket, exam_id: str, student_email: str):
    await websocket.accept()
    key = (exam_id, student_email)
    student_signal_ws[key] = websocket
    try:
        while True:
            raw = await websocket.receive_text()
            try:
                msg = json.loads(raw)
            except Exception:
                continue

            monitor_id = msg.get("monitor_id")
            if not monitor_id:
                continue

            mk = monitor_key(exam_id, student_email, monitor_id)
            monitor_socket = monitor_ws.get(mk)
            if monitor_socket:
                try:
                    await monitor_socket.send_text(json.dumps(msg))
                except Exception:
                    monitor_ws.pop(mk, None)
    except WebSocketDisconnect:
        student_signal_ws.pop(key, None)
    except Exception as e:
        student_signal_ws.pop(key, None)
    finally:
        student_signal_ws.pop(key, None)

# -----------------------------------
# Monitor signaling socket: monitor connects here to request full stream.
# -----------------------------------
@router.websocket("/monitor/{exam_id}/{student_email}/{monitor_id}")
async def monitor_signaling(websocket: WebSocket, exam_id: str, student_email: str, monitor_id: str):
    await websocket.accept()

    mk = monitor_key(exam_id, student_email, monitor_id)
    monitor_ws[mk] = websocket

    sk = (exam_id, student_email)
    student_socket = student_signal_ws.get(sk)
    if student_socket:
        try:
            await student_socket.send_text(json.dumps({
                "type": "monitor-join",
                "monitor_id": monitor_id
            }))
        except Exception:
            student_signal_ws.pop(sk, None)

    try:
        while True:
            raw = await websocket.receive_text()
            try:
                msg = json.loads(raw)
            except Exception:
                continue

            msg["monitor_id"] = msg.get("monitor_id", monitor_id)

            sk = (exam_id, student_email)
            student_socket = student_signal_ws.get(sk)
            if student_socket:
                try:
                    await student_socket.send_text(json.dumps(msg))
                except Exception:
                    student_signal_ws.pop(sk, None)
            else:
                pass # Student not present
    except WebSocketDisconnect:
        monitor_ws.pop(mk, None)
    except Exception:
        monitor_ws.pop(mk, None)
    finally:
        monitor_ws.pop(mk, None)

# -----------------------------------
# Persistent WebSocket for event polling from Firestore
# -----------------------------------
@router.websocket("/persistent_socket/{student_email}")
async def persistent_socket(websocket: WebSocket, student_email: str):
    await websocket.accept()
    persistent_ws[student_email] = websocket
    
    try:
        # A simple polling approach. For a real-time system, a Firestore on_snapshot()
        # listener or a pub/sub model would be more efficient.
        while True:
            # Get the latest 5 events for this student, ordered by timestamp
            query = db.collection("proctor_events")\
                .where("student_email", "==", student_email)\
                .order_by("timestamp", direction=firestore.Query.DESCENDING)\
                .limit(5)
            
            new_events = []
            for doc in query.stream():
                data = doc.to_dict()
                # Firestore timestamp object needs to be converted to a string for JSON serialization
                data["timestamp"] = data["timestamp"].isoformat() if data.get("timestamp") else None
                new_events.append(data)
            
            # The client needs the events in chronological order, so reverse the list
            new_events.reverse()

            if new_events:
                try:
                    await websocket.send_text(json.dumps({"type": "proctor_events", "events": new_events}))
                except Exception:
                    break
            
            await asyncio.sleep(3) # Poll every 3 seconds
    except WebSocketDisconnect:
        print(f"Persistent WS disconnected for {student_email}")
    finally:
        persistent_ws.pop(student_email, None)
        try:
            await websocket.close()
        except:
            pass
