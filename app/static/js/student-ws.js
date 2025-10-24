// static/js/student-monitor-signaling.js
// Student registers a 'signal_student' socket to be able to create offers for monitors

export async function startStudentMonitorSignaling(examId, studentEmail) {
  const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
  const url = `${proto}//${location.host}/websocket/signal_student/${encodeURIComponent(examId)}/${encodeURIComponent(studentEmail)}`;
  const ws = new WebSocket(url);

  const peerConns = {}; // monitorId => RTCPeerConnection
  let localStream = null;

  ws.onopen = async () => {
    console.log("Student monitor-signaling WS connected");
    // ensure local media (may be already opened by your other code)
    try {
      if (!localStream) {
        localStream = await navigator.mediaDevices.getUserMedia({ video: true, audio: true });
        const preview = document.getElementById("camera-peek-video");
        if (preview) preview.srcObject = localStream;
      }
    } catch (e) {
      console.error("Failed to get local media for monitor streaming:", e);
      return;
    }
  };

  ws.onmessage = async (ev) => {
    const msg = JSON.parse(ev.data);
    console.log("student-signaling got:", msg);

    // Student should handle 'answer' & 'ice' messages from monitor (forwarded via server)
    if (msg.type === 'answer' && msg.monitor_id) {
      const monitorId = msg.monitor_id;
      const pc = peerConns[monitorId];
      if (!pc) { console.warn("No pc for monitor", monitorId); return; }
      await pc.setRemoteDescription(new RTCSessionDescription(msg.answer));
      console.log("Set remote answer for monitor", monitorId);
    } else if (msg.type === 'ice' && msg.monitor_id && msg.candidate) {
      const monitorId = msg.monitor_id;
      const pc = peerConns[monitorId];
      if (pc) {
        try { await pc.addIceCandidate(msg.candidate); } catch (e) { console.warn("addIce failed", e); }
      }
    }
  };

  ws.onclose = () => {
    console.log("Student monitor-signaling disconnected");
    for (const id in peerConns) {
      try { peerConns[id].close(); } catch {}
    }
  };

  // Called by the local server notice 'monitor-join' (handled via ws.onmessage above)
  // But we need a helper to create an offer (called when a monitor-join message arrives)
  async function createOfferForMonitor(monitorId) {
    if (!localStream) {
      try {
        localStream = await navigator.mediaDevices.getUserMedia({ video: true, audio: true });
      } catch (e) { console.error("getUserMedia failed", e); return; }
    }
    const pc = new RTCPeerConnection({
      iceServers: [{ urls: "stun:stun.l.google.com:19302" }] // add TURN in prod
    });
    peerConns[monitorId] = pc;

    // add local tracks
    localStream.getTracks().forEach(t => pc.addTrack(t, localStream));

    // relay ice candidates to monitor via server
    pc.onicecandidate = (e) => {
      if (e.candidate && ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({
          type: "ice",
          monitor_id: monitorId,
          candidate: e.candidate
        }));
      }
    };

    // create offer & send to server (server forwards to monitor)
    const offer = await pc.createOffer();
    await pc.setLocalDescription(offer);
    ws.send(JSON.stringify({
      type: "offer",
      monitor_id: monitorId,
      offer: { sdp: offer.sdp, type: offer.type }
    }));

    // cleanup when PC ends
    pc.onconnectionstatechange = () => {
      if (["closed", "failed", "disconnected"].includes(pc.connectionState)) {
        try { pc.close(); } catch {}
        delete peerConns[monitorId];
      }
    };
  }

  // Re-expose createOffer so incoming server messages can call it
  // (server will send a "monitor-join" message to this ws; you should call createOfferForMonitor when you see it)
  ws.createOfferForMonitor = createOfferForMonitor;

  return ws;
}
