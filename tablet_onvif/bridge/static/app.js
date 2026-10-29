"use strict";
let config,
  available = [],
  token = sessionStorage.getItem("tablet-onvif-token") || "",
  authorized = false,
  editing = null,
  polling = false;
const $ = (s) => document.querySelector(s),
  views = new Map();
function el(tag, text, cls) {
  const e = document.createElement(tag);
  if (text !== undefined) e.textContent = text;
  if (cls) e.className = cls;
  return e;
}
function say(text) {
  $("#message").textContent = text;
  $("#message").hidden = !text;
}
async function api(path, options = {}) {
  const r = await fetch(`api/${path}`, {
    ...options,
    cache: "no-store",
    headers: { Authorization: `Bearer ${token}`, ...options.headers },
  });
  if (!r.ok) {
    let message = "Could not complete the request.";
    try {
      message = (await r.json()).error || message;
    } catch {}
    const error = new Error(
      r.status === 401
        ? "Open this page through Home Assistant, or enter your admin token."
        : message,
    );
    error.status = r.status;
    throw error;
  }
  return r;
}
function jsonOptions(method, data) {
  return {
    method,
    headers: { "Content-Type": "application/json", "X-Tablet-ONVIF": "1" },
    body: JSON.stringify(data),
  };
}
function stopView(view) {
  clearTimeout(view.frameTimer);
  view.frameController?.abort();
  view.frameController = null;
  if (view.frameUrl) URL.revokeObjectURL(view.frameUrl);
  view.frameUrl = null;
  if (view.image) {
    view.image.onerror = null;
    view.image.removeAttribute("src");
    view.image.remove();
    view.image = null;
  }
  view.live.textContent = "Live view";
  view.previewError = "";
  view.error.textContent = "";
}
function framePreview(view, camera) {
  // Some browsers/ingress proxies reject an otherwise working multipart stream.
  // Use the encoder's JPEGs without opening more connections to the tablet.
  const image = view.image;
  image.onerror = null;
  image.removeAttribute("src");
  const controller = new AbortController();
  view.frameController = controller;
  const refreshFrame = async () => {
    let nextUrl;
    try {
      const response = await api(`preview/${encodeURIComponent(camera.id)}`, {
        signal: controller.signal,
      });
      nextUrl = URL.createObjectURL(await response.blob());
      if (controller.signal.aborted) return;
      image.src = nextUrl;
      await image.decode();
      if (controller.signal.aborted) return;
      if (view.frameUrl) URL.revokeObjectURL(view.frameUrl);
      view.frameUrl = nextUrl;
      nextUrl = null;
      view.previewError = "";
      view.error.textContent =
        "Compatibility preview · updates once per second.";
    } catch (e) {
      if (controller.signal.aborted) return;
      image.removeAttribute("src");
      if (view.frameUrl) URL.revokeObjectURL(view.frameUrl);
      view.frameUrl = null;
      view.previewError = e.status
        ? e.message
        : "The browser could not display the preview frame.";
      view.error.textContent = `${view.previewError} Retrying…`;
    } finally {
      if (nextUrl) URL.revokeObjectURL(nextUrl);
      if (!controller.signal.aborted)
        view.frameTimer = setTimeout(refreshFrame, 1000);
    }
  };
  view.error.textContent = "Switching to compatibility preview…";
  refreshFrame();
}
function clearViews() {
  for (const view of views.values()) stopView(view);
  views.clear();
  $("#status").replaceChildren();
}
function sourceFields() {
  $("#source-fields").hidden = !$("#source-auth").checked;
  $("#source-user").required = $("#source-auth").checked;
  const c = editing?.camera;
  $("#source-login-hint").hidden = !$("#source-auth").checked || Boolean(c);
}
function setup(tablet, camera = null) {
  editing = { tablet, camera };
  $("#setup-title").textContent = camera ? "Camera settings" : "Enable camera";
  $("#camera-name").value = camera?.name || tablet.name;
  $("#source-auth").checked = Boolean(camera?.source_username);
  $("#source-user").value = camera?.source_username || "";
  $("#source-pass").value = "";
  $("#source-pass").placeholder = camera?.source_password_set
    ? "Saved — leave blank to keep"
    : "";
  $("#bridge-host").value = camera?.advertise_host || "";
  $("#setup-error").textContent = "";
  $("#enable-camera").textContent = camera ? "Save changes" : "Enable camera";
  $("#remove-camera").hidden = !camera;
  sourceFields();
  $("#setup-dialog details").open = false;
  $("#setup-dialog").showModal();
}
function renderTablets() {
  const root = $("#tablets");
  root.replaceChildren();
  const unused = available.filter(
    (t) => !config.cameras.some((c) => c.stream_entity === t.stream_entity),
  );
  for (const t of unused) {
    const row = el("div", undefined, "tablet"),
      body = el("div");
    body.append(el("h3", t.name));
    const ready =
      t.stream_url_available &&
      !["unknown", "unavailable"].includes(t.stream_state);
    body.append(
      el(
        "p",
        ready ? "Camera stream found" : "Enable Camera Stream on this tablet",
      ),
    );
    const add = el("button", "Set up", "secondary");
    add.onclick = () => setup(t);
    row.append(body, add);
    root.append(row);
  }
  if (!unused.length)
    root.append(
      el(
        "p",
        available.length
          ? "All discovered tablets have been added."
          : "Waiting for tablets. Enable the camera sensors in the Companion App, then refresh.",
        "small",
      ),
    );
}
async function connection(camera) {
  try {
    const details = await (
      await api(`connection/${encodeURIComponent(camera.id)}`)
    ).json();
    $("#connect-host").value = details.host;
    $("#connect-user").value = details.username;
    $("#connect-pass").value = details.password;
    $("#login-hint").textContent = details.uses_tablet_login
      ? "Use the same username and password as the tablet's camera stream."
      : "Use the saved NVR login below.";
    $("#connect-pass").type = "password";
    $("#show-password").textContent = "Show password";
    $("#connect-url").value = details.url;
    $("#connect-port").textContent = `ONVIF port: ${details.port}`;
    $("#copy-status").textContent = "";
    document.querySelectorAll("#connect-dialog details").forEach((details) => {
      details.open = false;
    });
    $("#connect-dialog").showModal();
  } catch (e) {
    say(e.message);
  }
}
function createView(camera) {
  const card = el("article", undefined, "camera-card"),
    body = el("div", undefined, "camera-body"),
    head = el("div", undefined, "section-head"),
    title = el("h3"),
    settings = el("button", "⋯", "icon-button"),
    info = el("p", undefined, "status-line"),
    error = el("p", undefined, "error"),
    actions = el("div", undefined, "actions"),
    live = el("button", "Live view", "secondary"),
    connect = el("button", "Connect to NVR"),
    preview = el("div", undefined, "camera-preview");
  settings.setAttribute("aria-label", `Settings for ${camera.name}`);
  settings.onclick = () =>
    setup({ stream_entity: camera.stream_entity, name: camera.name }, camera);
  connect.onclick = () => connection(camera);
  head.append(title, settings);
  actions.append(live, connect);
  body.append(head, info, error, actions);
  card.append(body, preview);
  const view = {
    card,
    title,
    info,
    error,
    live,
    preview,
    image: null,
    previewError: "",
    loading: false,
  };
  live.onclick = async () => {
    if (view.image) {
      stopView(view);
      return;
    }
    live.disabled = true;
    view.loading = true;
    view.previewError = "";
    error.textContent = "Connecting to your tablet…";
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
        framePreview(view, camera);
      };
      image.src = `api/live/${encodeURIComponent(camera.id)}`;
      preview.append(image);
      live.textContent = "Stop live view";
      error.textContent = "";
    } catch (e) {
      view.previewError = e.message;
      error.textContent = e.message;
    } finally {
      live.disabled = false;
      view.loading = false;
    }
  };
  views.set(camera.id, view);
  $("#status").append(card);
  return view;
}
async function status() {
  const s = await (await api("status")).json();
  $("#connection").textContent = s.ha_connected
    ? "Home Assistant connected"
    : "Connecting to Home Assistant…";
  $("#connection").dataset.online = String(s.ha_connected);
  $("#errors").textContent = [s.ha_error, ...s.errors]
    .filter(Boolean)
    .join("\n");
  const ids = new Set(config.cameras.map((c) => c.id));
  for (const [id, view] of views) {
    if (!ids.has(id)) {
      stopView(view);
      view.card.remove();
      views.delete(id);
    }
  }
  $("#status .empty")?.remove();
  for (const camera of config.cameras) {
    const c = s.cameras.find((x) => x.id === camera.id),
      view = views.get(camera.id) || createView(camera);
    view.title.textContent = camera.name;
    view.info.textContent = !camera.enabled
      ? "Camera paused"
      : c?.video_available
        ? `Video ready · Motion ${c.motion_available ? (c.motion ? "detected" : "clear") : "unavailable"}`
        : "Waiting for camera video";
    if (!view.image && !view.live.disabled)
      view.error.textContent = view.previewError || c?.error || "";
    if (!view.loading) view.live.disabled = !camera.enabled;
  }
  if (!config.cameras.length)
    $("#status").append(
      el(
        "div",
        "No cameras yet. Choose a tablet below to get started.",
        "empty",
      ),
    );
}
async function refresh() {
  available = await (await api("tablets")).json();
  renderTablets();
}
async function load() {
  try {
    config = await (await api("config")).json();
    $("#ha-url").value = config.ha_url;
    $("#rtsp-port").value = config.rtsp_port;
    $("#interface").value = config.discovery_interface;
    clearViews();
    await refresh();
    await status();
    authorized = true;
    $("#login").hidden = true;
    $("#content").hidden = false;
    say("");
  } catch (e) {
    if (e.status === 401) $("#login").hidden = false;
    say(e.message);
  }
}
$("#login-form").onsubmit = (e) => {
  e.preventDefault();
  token = $("#token").value;
  sessionStorage.setItem("tablet-onvif-token", token);
  load();
};
$("#refresh").onclick = () => refresh().catch((e) => say(e.message));
$("#source-auth").onchange = sourceFields;
$("#close-setup").onclick = () => $("#setup-dialog").close();
$("#setup-dialog").addEventListener("close", () => {
  $("#source-pass").value = "";
});
$("#setup-form").onsubmit = async (e) => {
  e.preventDefault();
  const button = $("#enable-camera");
  button.disabled = true;
  $("#setup-error").textContent = "";
  try {
    await api(
      "setup",
      jsonOptions("POST", {
        stream_entity: editing.tablet.stream_entity,
        name: $("#camera-name").value,
        source_auth: $("#source-auth").checked,
        source_username: $("#source-user").value,
        source_password: $("#source-pass").value,
        bridge_host: $("#bridge-host").value.trim(),
      }),
    );
    $("#setup-dialog").close();
    await load();
    say(
      "Camera enabled. Open Live view to check the picture, then Connect to NVR.",
    );
  } catch (e) {
    $("#setup-error").textContent = e.message;
  } finally {
    button.disabled = false;
  }
};
$("#remove-camera").onclick = async () => {
  if (
    !editing?.camera ||
    !confirm(`Remove ${editing.camera.name} from this bridge?`)
  )
    return;
  try {
    const next = {
      ...config,
      cameras: config.cameras.filter((c) => c.id !== editing.camera.id),
    };
    await api("config", jsonOptions("PUT", next));
    $("#setup-dialog").close();
    await load();
  } catch (e) {
    $("#setup-error").textContent = e.message;
  }
};
$("#close-connect").onclick = () => $("#connect-dialog").close();
$("#connect-dialog").addEventListener("close", () => {
  $("#connect-pass").value = "";
});
$("#show-password").onclick = () => {
  const field = $("#connect-pass");
  field.type = field.type === "password" ? "text" : "password";
  $("#show-password").textContent =
    field.type === "password" ? "Show password" : "Hide password";
};
$("#copy-password").onclick = async () => {
  try {
    await navigator.clipboard.writeText($("#connect-pass").value);
    $("#copy-status").textContent = "Password copied.";
  } catch {
    $("#connect-pass").focus();
    $("#connect-pass").select();
    $("#copy-status").textContent =
      "Password selected. Press Ctrl+C or Command+C to copy.";
  }
};
$("#network-form").onsubmit = async (e) => {
  e.preventDefault();
  const button = e.submitter;
  button.disabled = true;
  try {
    await api(
      "config",
      jsonOptions("PUT", {
        ...config,
        ha_url: $("#ha-url").value,
        rtsp_port: Number($("#rtsp-port").value),
        discovery_interface: $("#interface").value,
      }),
    );
    await load();
    say("Network settings saved. Cameras are reconnecting.");
  } catch (e) {
    say(e.message);
  } finally {
    button.disabled = false;
  }
};
setInterval(async () => {
  if (!authorized || polling) return;
  polling = true;
  try {
    await status();
    await refresh();
  } catch (e) {
    say(e.message);
  } finally {
    polling = false;
  }
}, 5000);
load();
