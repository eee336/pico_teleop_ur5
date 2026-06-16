const $ = (id) => document.getElementById(id);

const els = {
  wsDot: $("wsDot"),
  wsState: $("wsState"),
  robotMode: $("robotMode"),
  operatorState: $("operatorState"),
  calibrationState: $("calibrationState"),
  deadmanState: $("deadmanState"),
  streamState: $("streamState"),
  notice: $("notice"),
  handState: $("handState"),
  clientState: $("clientState"),
  messageState: $("messageState"),
  tcpPose: $("tcpPose"),
  enterXrBtn: $("enterXrBtn"),
  calibrateBtn: $("calibrateBtn"),
  enableBtn: $("enableBtn"),
  disableBtn: $("disableBtn"),
  resetBtn: $("resetBtn"),
  canvas: $("xrCanvas"),
};

let socket = null;
let reconnectTimer = null;
let lastStatus = null;
let xrSession = null;
let xrRefSpace = null;
let xrGl = null;
let buttonEdges = new Map();

function setNotice(text, tone = "") {
  els.notice.textContent = text;
  els.notice.className = `notice ${tone}`.trim();
}

function setClass(el, tone) {
  el.classList.remove("good", "warn", "bad");
  if (tone) el.classList.add(tone);
}

function formatPose(pose) {
  if (!pose) return "--";
  return pose.map((v) => Number(v).toFixed(3)).join(", ");
}

function renderStatus(status) {
  lastStatus = status;
  els.robotMode.textContent = status.real_robot ? "RTDE live" : "Dry-run";
  els.operatorState.textContent = status.operator_enabled ? "Enabled" : "Disabled";
  els.calibrationState.textContent = status.calibrated ? "Ready" : "Waiting";
  els.deadmanState.textContent = status.deadman ? "Held" : "Released";
  els.streamState.textContent = status.stale ? "Stale" : status.active ? "Active" : "Live";
  els.handState.textContent = status.dominant_hand ?? "--";
  els.clientState.textContent = String(status.clients ?? 0);
  els.messageState.textContent = String(status.messages ?? 0);
  els.tcpPose.textContent = formatPose(status.tcp_pose);

  setClass(els.operatorState, status.operator_enabled ? "good" : "bad");
  setClass(els.calibrationState, status.calibrated ? "good" : "warn");
  setClass(els.deadmanState, status.deadman ? "good" : "bad");
  setClass(els.streamState, status.stale ? "bad" : status.active ? "good" : "warn");

  if (status.last_error) {
    setNotice(status.last_error, "bad");
  } else if (status.real_robot && status.active) {
    setNotice("Robot motion active.", "good");
  } else if (!status.calibrated) {
    setNotice("Controller pose required before calibration.", "");
  } else {
    setNotice(status.operator_enabled ? "Motion enabled." : "Motion disabled.", status.operator_enabled ? "good" : "");
  }
}

function wsUrl() {
  const proto = window.location.protocol === "https:" ? "wss" : "ws";
  return `${proto}://${window.location.host}/ws`;
}

function connectSocket() {
  clearTimeout(reconnectTimer);
  socket = new WebSocket(wsUrl());

  socket.addEventListener("open", () => {
    els.wsDot.classList.add("on");
    els.wsDot.classList.remove("off");
    els.wsState.textContent = "Connected";
    setNotice("Gateway connected.", "good");
  });

  socket.addEventListener("message", (event) => {
    const message = JSON.parse(event.data);
    if (message.status) renderStatus(message.status);
    if (message.type === "error") setNotice(message.message, "bad");
  });

  socket.addEventListener("close", () => {
    els.wsDot.classList.remove("on");
    els.wsState.textContent = "Disconnected";
    setNotice("Reconnecting to gateway...", "");
    reconnectTimer = setTimeout(connectSocket, 1000);
  });

  socket.addEventListener("error", () => {
    setNotice("WebSocket error.", "bad");
  });
}

function sendJson(message) {
  if (!socket || socket.readyState !== WebSocket.OPEN) return false;
  socket.send(JSON.stringify(message));
  return true;
}

async function postControl(action) {
  if (sendJson({ type: "control", action })) return;
  const path = action === "reset-calibration" ? "/api/reset-calibration" : `/api/${action}`;
  const response = await fetch(path, { method: "POST" });
  const data = await response.json();
  renderStatus(data);
}

async function pollStatus() {
  try {
    const response = await fetch("/api/status");
    if (response.ok) renderStatus(await response.json());
  } catch {
    // WebSocket reconnection carries the visible network state.
  }
}

function readButton(gamepad, index) {
  const button = gamepad?.buttons?.[index];
  if (!button) return 0;
  return Math.max(button.value ?? 0, button.pressed ? 1 : 0);
}

function risingEdge(handedness, name, pressed) {
  const key = `${handedness}:${name}`;
  const previous = buttonEdges.get(key) ?? false;
  buttonEdges.set(key, pressed);
  return pressed && !previous;
}

function sendPose(inputSource, pose) {
  const transform = pose.transform;
  const position = transform.position;
  const orientation = transform.orientation;
  const gamepad = inputSource.gamepad;
  const handedness = inputSource.handedness || "none";
  const grip = readButton(gamepad, 1);
  const trigger = readButton(gamepad, 0);
  const primary = readButton(gamepad, 4) > 0.5;
  const secondary = readButton(gamepad, 5) > 0.5;

  const calibrate = risingEdge(handedness, "primary", primary);
  if (risingEdge(handedness, "secondary", secondary)) {
    sendJson({ type: "control", action: "disable" });
  }

  sendJson({
    type: "pose",
    t: performance.now() / 1000,
    handedness,
    position: [position.x, position.y, position.z],
    orientation: [orientation.x, orientation.y, orientation.z, orientation.w],
    buttons: {
      deadman: grip > 0.55,
      grip,
      trigger,
      primary,
      secondary,
    },
    calibrate,
  });
}

function drawXRFrame(frame) {
  if (!xrGl || !xrSession) return;
  const layer = xrSession.renderState.baseLayer;
  xrGl.bindFramebuffer(xrGl.FRAMEBUFFER, layer.framebuffer);
  const active = lastStatus?.active;
  const enabled = lastStatus?.operator_enabled;
  if (active) {
    xrGl.clearColor(0.04, 0.22, 0.16, 1.0);
  } else if (enabled) {
    xrGl.clearColor(0.18, 0.16, 0.06, 1.0);
  } else {
    xrGl.clearColor(0.05, 0.07, 0.08, 1.0);
  }
  xrGl.clear(xrGl.COLOR_BUFFER_BIT | xrGl.DEPTH_BUFFER_BIT);

  for (const inputSource of xrSession.inputSources) {
    const targetHand = lastStatus?.dominant_hand || "right";
    if (targetHand !== "none" && inputSource.handedness !== targetHand) continue;
    const space = inputSource.gripSpace || inputSource.targetRaySpace;
    if (!space) continue;
    const inputPose = frame.getPose(space, xrRefSpace);
    if (inputPose) sendPose(inputSource, inputPose);
  }
}

function onXRFrame(time, frame) {
  drawXRFrame(frame);
  xrSession?.requestAnimationFrame(onXRFrame);
}

async function enterXR() {
  if (!navigator.xr) {
    setNotice("WebXR unavailable on this browser or origin.", "bad");
    return;
  }
  const supported = await navigator.xr.isSessionSupported("immersive-vr");
  if (!supported) {
    setNotice("Immersive VR session is unavailable.", "bad");
    return;
  }

  xrGl = els.canvas.getContext("webgl", { xrCompatible: true, antialias: true });
  if (!xrGl) {
    setNotice("Could not create WebGL context.", "bad");
    return;
  }
  await xrGl.makeXRCompatible();

  xrSession = await navigator.xr.requestSession("immersive-vr", {
    optionalFeatures: ["local-floor", "bounded-floor"],
  });
  document.body.classList.add("xr-active");
  xrSession.updateRenderState({ baseLayer: new XRWebGLLayer(xrSession, xrGl) });
  xrRefSpace = await xrSession.requestReferenceSpace("local-floor").catch(() => xrSession.requestReferenceSpace("local"));

  xrSession.addEventListener("end", () => {
    document.body.classList.remove("xr-active");
    xrSession = null;
    xrRefSpace = null;
    setNotice("VR session ended.");
  });

  setNotice("VR session running.", "good");
  xrSession.requestAnimationFrame(onXRFrame);
}

els.enterXrBtn.addEventListener("click", enterXR);
els.calibrateBtn.addEventListener("click", () => postControl("calibrate"));
els.enableBtn.addEventListener("click", () => postControl("enable"));
els.disableBtn.addEventListener("click", () => postControl("disable"));
els.resetBtn.addEventListener("click", () => postControl("reset-calibration"));

connectSocket();
pollStatus();
setInterval(pollStatus, 1200);

