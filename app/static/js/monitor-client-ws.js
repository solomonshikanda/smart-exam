// static/js/monitor-ws.js
export async function startMonitorSignaling(examId, studentEmail, monitorId, videoEl) {
  const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
  const url = `${proto}//${location.host}//monitor/monitor/${encodeURIComponent(examId)}/${encodeURIComponent(studentEmail)}/${encodeURIComponent(monitorId)}`;
  const ws = new WebSocket(url);

  const pc = new RTCPeerConnection({
    iceServers: [{ urls: 'stun:stun.l.google.com:19302' }] // add TURN for production
  });

  pc.ontrack = (ev) => {
    // attach remote stream to video element
    if (videoEl) {
      videoEl.srcObject = ev.streams[0];
      videoEl.autoplay = true;
      videoEl.playsInline = true;
    }
  };

  pc.onicecandidate = (e) => {
    if (e.candidate && ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify({
        type: 'ice',
        monitor_id: monitorId,
        candidate: e.candidate
      }));
    }
  };

  ws.onopen = () => console.log('Monitor signaling connected', url);

  ws.onmessage = async (ev) => {
    const msg = JSON.parse(ev.data);
    console.log('monitor ws msg', msg);

    if (msg.type === 'offer' && msg.offer) {
      // Student initiated offer (we expect an offer forwarded by server)
      const offerDesc = new RTCSessionDescription(msg.offer);
      await pc.setRemoteDescription(offerDesc);
      const answer = await pc.createAnswer();
      await pc.setLocalDescription(answer);

      // send answer to server (server forwards to student)
      ws.send(JSON.stringify({
        type: 'answer',
        monitor_id: monitorId,
        answer: { sdp: answer.sdp, type: answer.type }
      }));
    } else if (msg.type === 'ice' && msg.candidate) {
      try {
        await pc.addIceCandidate(msg.candidate);
      } catch (e) {
        console.warn('monitor addIceCandidate failed', e);
      }
    }
  };

  ws.onclose = () => {
    console.log('Monitor ws closed');
    try { pc.close(); } catch {}
  };

  return { ws, pc };
}
