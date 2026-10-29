"use strict";
let config,
  token = sessionStorage.getItem("tablet-onvif-token") || "",
  authorized = false;
const $ = (s) => document.querySelector(s);
function say(text) {
  $("#message").textContent = text;
}
function element(tag, text, cls) {
  const e = document.createElement(tag);
  if (text !== undefined) e.textContent = text;
  if (cls) e.className = cls;
  return e;
}
async function api(path, options = {}) {
  const headers = { Authorization: `Bearer ${token}`, ...options.headers };
  const r = await fetch(`api/${path}`, {
    ...options,
    headers,
    cache: "no-store",
  });
  if (!r.ok) {
    let text = "Request failed";
    try {
      text = (await r.json()).error || text;
    } catch {}
    throw new Error(
      r.status === 401
        ? "Sign in with the bridge admin token, or open this page through Home Assistant ingress."
        : text,
    );
  }
  return r;
}
const fields = [
  ["id", "Camera ID", "text"],
  ["name", "Display name", "text"],
  ["advertise_host", "Bridge LAN IPv4", "text"],
  ["onvif_port", "ONVIF port", "number"],
  ["stream_entity", "Camera Stream entity", "text"],
  ["motion_entity", "Camera Motion entity", "text"],
  ["username", "NVR username", "text"],
  ["password", "NVR password (12+ characters)", "password"],
  ["source_url", "Source URL override (optional)", "url"],
  ["source_username", "iPad stream username", "text"],
  ["source_password", "iPad stream password", "password"],
  ["width", "Main width", "number"],
  ["height", "Main height", "number"],
  ["fps", "Frames per second", "number"],
  ["bitrate", "Main bitrate (kbps)", "number"],
];
function renderCameras() {
  $("#cameras").replaceChildren();
  config.cameras.forEach((c, index) => {
    const card = element("div", undefined, "camera"),
      row = element("div", undefined, "row");
    row.append(element("h3", c.name || "New camera"));
    const remove = element("button", "Remove", "secondary");
    remove.type = "button";
    remove.onclick = () => {
      config.cameras.splice(index, 1);
      renderCameras();
    };
    row.append(remove);
    card.append(row);
    const grid = element("div", undefined, "grid");
    fields.forEach(([key, title, type]) => {
      const label = element("label", title),
        input = document.createElement("input");
      input.type = type;
      input.value = c[key] ?? "";
      input.autocomplete = type === "password" ? "new-password" : "off";
      if (type === "password" && c[key + "_set"])
        input.placeholder = "Saved — leave blank to retain";
      input.oninput = () => {
        c[key] = type === "number" ? Number(input.value) : input.value;
      };
      label.append(input);
      grid.append(label);
    });
    const label = element("label"),
      checkbox = document.createElement("input");
    checkbox.type = "checkbox";
    checkbox.checked = c.enabled;
    checkbox.onchange = () => (c.enabled = checkbox.checked);
    label.append(checkbox, document.createTextNode("Camera enabled"));
    card.append(grid, label);
    $("#cameras").append(card);
  });
}
function addCamera(tablet) {
  const used = new Set(config.cameras.map((c) => c.id));
  let id = "tablet";
  let n = 1;
  while (used.has(id)) id = `tablet_${++n}`;
  const ports = new Set(config.cameras.map((c) => c.onvif_port));
  let port = 8101;
  while (ports.has(port)) port++;
  config.cameras.push({
    id,
    name: tablet?.name || "Wall tablet",
    stream_entity: tablet?.stream_entity || "",
    motion_entity: tablet?.motion_entity || "",
    advertise_host: "",
    onvif_port: port,
    username: `nvr_${id}`,
    password: "",
    source_url: "",
    source_username: "",
    source_password: "",
    width: 1280,
    height: 720,
    fps: 15,
    bitrate: 1800,
    enabled: true,
  });
  renderCameras();
}
async function tablets() {
  const items = await (await api("tablets")).json();
  $("#tablets").replaceChildren();
  if (!items.length)
    $("#tablets").append(
      element(
        "p",
        "No Companion camera devices found yet. Check the HA connection and enable camera sensors on the tablet.",
      ),
    );
  for (const t of items) {
    const row = element("div", undefined, "camera");
    row.append(
      element("h3", t.name),
      element(
        "p",
        `Stream: ${t.stream_state}; motion: ${t.motion_state}; stream address ${t.stream_url_available ? "available" : "not reported yet"}.`,
      ),
    );
    const button = element("button", "Use this tablet", "secondary");
    button.onclick = () => addCamera(t);
    row.append(button);
    $("#tablets").append(row);
  }
}
const liveViews = new Map();
function stopView(view) {
  if (view.image) {
    view.image.onerror = null;
    view.image.removeAttribute("src");
    view.image.remove();
    view.image = null;
  }
  view.button.textContent = "Show live stream";
}
function clearViews() {
  for (const view of liveViews.values()) stopView(view);
  liveViews.clear();
  $("#status").replaceChildren();
}
function createView(camera) {
  const card = element("div", undefined, "status-card"),
    title = element("h3"),
    info = element("p"),
    address = element("code"),
    error = element("p"),
    preview = element("div", undefined, "live-preview"),
    button = element("button", "Show live stream", "secondary"),
    note = element(
      "p",
      "Live preview reads the iPad directly. RTSP status above shows the NVR encoder separately.",
      "muted",
    );
  const view = {
    card,
    title,
    info,
    address,
    error,
    preview,
    button,
    image: null,
    previewError: "",
  };
  button.type = "button";
  button.onclick = async () => {
    if (view.image) {
      stopView(view);
      return;
    }
    button.disabled = true;
    view.previewError = "";
    error.textContent = "Connecting to the tablet camera…";
    try {
      await api(`source/${encodeURIComponent(camera.id)}`);
      await api("preview-session", {
        method: "POST",
        headers: { "X-Tablet-ONVIF": "1" },
      });
      const image = document.createElement("img");
      image.alt = `Live camera stream from ${camera.name}`;
      view.image = image;
      image.onerror = () => {
        stopView(view);
        view.previewError =
          "Live stream interrupted or unreadable. Keep the Companion App open, check the camera credentials, and try again.";
        error.textContent = view.previewError;
      };
      image.src = `api/live/${encodeURIComponent(camera.id)}`;
      preview.append(image);
      button.textContent = "Stop live stream";
      error.textContent = "";
    } catch (e) {
      view.previewError = e.message;
      error.textContent = e.message;
    } finally {
      button.disabled = false;
    }
  };
  card.append(title, info, address, error, button, note, preview);
  $("#status").append(card);
  liveViews.set(camera.id, view);
  return view;
}
async function status() {
  const s = await (await api("status")).json();
  $("#connection").textContent = s.ha_connected
    ? "Home Assistant connected"
    : "Home Assistant disconnected";
  $("#errors").textContent = [s.ha_error, ...s.errors]
    .filter(Boolean)
    .join(" · ");
  const cameras = config.cameras.filter((c) => c.enabled);
  const ids = new Set(cameras.map((c) => c.id));
  for (const [id, view] of liveViews) {
    if (!ids.has(id)) {
      stopView(view);
      view.card.remove();
      liveViews.delete(id);
    }
  }
  $("#status .empty-status")?.remove();
  for (const camera of cameras) {
    const c = s.cameras.find((x) => x.id === camera.id);
    const view = liveViews.get(camera.id) || createView(camera);
    view.title.textContent = camera.name;
    view.info.textContent = c
      ? `RTSP video ${c.video_available ? "live" : "unavailable"} · Motion ${c.motion_available ? (c.motion ? "active" : "clear") : "unavailable"} · NVR subscriptions ${c.subscriptions}`
      : "RTSP runtime unavailable. You can still check the iPad source using the live preview.";
    view.address.textContent = c?.onvif_url || "";
    // Keep the stream element mounted during each status poll.
    if (!view.image && !view.button.disabled)
      view.error.textContent = view.previewError || c?.error || "";
  }
  if (!cameras.length)
    $("#status").append(
      element(
        "p",
        "Add and save your first camera to start the bridge.",
        "empty-status",
      ),
    );
}
async function connect() {
  try {
    config = await (await api("config")).json();
    clearViews();
    $("#ha-url").value = config.ha_url;
    $("#rtsp-port").value = config.rtsp_port;
    $("#interface").value = config.discovery_interface;
    renderCameras();
    await tablets();
    await status();
    authorized = true;
    $("#login").hidden = true;
    $("#content").hidden = false;
    say("");
  } catch (e) {
    say(e.message);
  }
}
$("#login-form").onsubmit = (e) => {
  e.preventDefault();
  token = $("#token").value;
  sessionStorage.setItem("tablet-onvif-token", token);
  connect();
};
$("#ingress").onclick = () => {
  token = "";
  connect();
};
$("#add").onclick = () => addCamera();
$("#refresh").onclick = () => tablets().catch((e) => say(e.message));
$("#settings").onsubmit = async (e) => {
  e.preventDefault();
  $("#save").disabled = true;
  try {
    config.ha_url = $("#ha-url").value;
    config.rtsp_port = Number($("#rtsp-port").value);
    config.discovery_interface = $("#interface").value;
    await api("config", {
      method: "PUT",
      headers: { "Content-Type": "application/json", "X-Tablet-ONVIF": "1" },
      body: JSON.stringify(config),
    });
    await connect();
    say("Saved. The bridge is reconnecting to Home Assistant and the tablets.");
  } catch (e) {
    say(e.message);
  } finally {
    $("#save").disabled = false;
  }
};
setInterval(() => {
  if (authorized) status().catch((e) => say(e.message));
}, 5000);
connect();
