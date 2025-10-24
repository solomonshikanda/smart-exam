# websocket
import os
import json
import base64
import asyncio
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from google.cloud import storage, firestore
from aiortc import RTCPeerConnection, RTCSessionDescription, MediaStreamTrack
from aiortc.contrib.media import MediaRecorder

router = APIRouter()
db = firestore.Client()
storage_client = storage.Client()



# Events websocket 
@router.websocket("/events/{exam_id}/{student_email}")
async def events_ws(websocket: WebSocket, exam_id: str, student_email: str):
    await websocket.accept()
    bucket = storage_client.bucket("monitor_exams")
    student_doc_ref = db.collection("proctor_events").document(exam_id).collection("students").document(student_email)

    try:
        while True:
            data = await websocket.receive_text()
            try:
                payload = json.loads(data)
            except Exception:
                continue

            event_type = payload.get("type")
            if event_type == "heartbeat":
                tab_status = payload.get("tab_status")
                snapshot_path = None

                # handle snapshot
                snapshot = payload.get("snapshot")
                if snapshot and snapshot.startswith("data:image"):
                    header, b64 = snapshot.split(",", 1)
                    data_bytes = base64.b64decode(b64)
                    ts = int(asyncio.get_event_loop().time() * 1000)

                    snapshot_path = f"proctoring/{exam_id}/{student_email}/{ts}.jpg"
                    blob = bucket.blob(snapshot_path)
                    blob.upload_from_string(data_bytes, content_type="image/jpeg")

                    # also save/update latest.jpg
                    latest_blob = bucket.blob(f"proctoring/{exam_id}/{student_email}/latest.jpg")
                    latest_blob.upload_from_string(data_bytes, content_type="image/jpeg")

                # Update student doc (latest)
                student_doc_ref.set({
                    "exam_id": exam_id,
                    "student_email": student_email,
                    "latest_tab_status": tab_status,
                    "latest_snapshot_path": snapshot_path or f"proctoring/{exam_id}/{student_email}/latest.jpg",
                    "last_updated": firestore.SERVER_TIMESTAMP
                }, merge=True)

                # Append history
                student_doc_ref.collection("history").add({
                    "tab_status": tab_status,
                    "snapshot_path": snapshot_path,
                    "timestamp": firestore.SERVER_TIMESTAMP
                })

            elif event_type == "visibility":
                tab_status = payload.get("tab_status")
                student_doc_ref.set({
                    "exam_id": exam_id,
                    "student_email": student_email,
                    "latest_tab_status": tab_status,
                    "last_updated": firestore.SERVER_TIMESTAMP
                }, merge=True)
                student_doc_ref.collection("history").add({
                    "event": "visibility_change",
                    "tab_status": tab_status,
                    "timestamp": firestore.SERVER_TIMESTAMP
                })

    except WebSocketDisconnect:
        print(f"Events WS disconnected for {student_email}")
    except Exception as e:
        print("Events WS error:", e)


# -----------------------
# Signaling websocket (WebRTC) - uses aiortc
# -----------------------
# Keep track of students' peer connections and their remote tracks
student_pcs: dict[str, RTCPeerConnection] = {}
student_tracks: dict[str, list] = {}

@router.websocket("/signaling/{exam_id}/{student_email}")
async def signaling_ws(websocket: WebSocket, exam_id: str, student_email: str):
    """
    Student WebRTC publishing
    """
    await websocket.accept()
    pc = RTCPeerConnection()
    student_key = f"{exam_id}:{student_email}"
    student_pcs[student_key] = pc
    student_tracks[student_key] = []

    @pc.on("track")
    async def on_track(track):
        print(f"Student {student_email} track: {track.kind}")
        student_tracks[student_key].append(track)

    try:
        # receive student offer
        msg = await websocket.receive_text()
        data = json.loads(msg)
        offer = RTCSessionDescription(sdp=data["offer"]["sdp"], type=data["offer"]["type"])
        await pc.setRemoteDescription(offer)

        # create answer
        answer = await pc.createAnswer()
        await pc.setLocalDescription(answer)

        await websocket.send_text(json.dumps({
            "type": "answer",
            "answer": {"sdp": pc.localDescription.sdp, "type": pc.localDescription.type}
        }))

        # keep loop open for ICE candidates
        while True:
            msg = await websocket.receive_text()
            data = json.loads(msg)
            if data.get("type") == "ice":
                candidate = data.get("candidate")
                if candidate:
                    await pc.addIceCandidate(candidate)
    except WebSocketDisconnect:
        print(f"Student signaling disconnected: {student_email}")
    finally:
        student_pcs.pop(student_key, None)
        student_tracks.pop(student_key, None)
        await pc.close()
        try:
            await websocket.close()
        except:
            pass


@router.websocket("/monitor-signaling/{exam_id}/{student_email}")
async def monitor_signaling_ws(websocket: WebSocket, exam_id: str, student_email: str):
    """
    Monitor subscribes to a student's stream
    """
    await websocket.accept()
    monitor_pc = RTCPeerConnection()
    student_key = f"{exam_id}:{student_email}"

    # add student's existing tracks to this monitor
    for t in student_tracks.get(student_key, []):
        monitor_pc.addTrack(t)

    try:
        # receive monitor offer
        msg = await websocket.receive_text()
        data = json.loads(msg)
        offer = RTCSessionDescription(sdp=data["offer"]["sdp"], type=data["offer"]["type"])
        await monitor_pc.setRemoteDescription(offer)

        # create answer
        answer = await monitor_pc.createAnswer()
        await monitor_pc.setLocalDescription(answer)

        await websocket.send_text(json.dumps({
            "type": "answer",
            "answer": {"sdp": monitor_pc.localDescription.sdp, "type": monitor_pc.localDescription.type}
        }))

        # handle ICE candidates from monitor
        while True:
            msg = await websocket.receive_text()
            data = json.loads(msg)
            if data.get("type") == "ice":
                candidate = data.get("candidate")
                if candidate:
                    await monitor_pc.addIceCandidate(candidate)
    except WebSocketDisconnect:
        print(f"Monitor disconnected for {student_email}")
    finally:
        await monitor_pc.close()
        try:
            await websocket.close()
        except:
            pass
