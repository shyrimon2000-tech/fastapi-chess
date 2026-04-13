const WS_URL = (location.protocol === "https:" ? "wss://" : "ws://") + location.host + "/ws";

const authScreen = document.getElementById("authScreen");
const menuScreen = document.getElementById("menuScreen");
const gameScreen = document.getElementById("gameScreen");

const authStatus = document.getElementById("authStatus");
const menuStatus = document.getElementById("menuStatus");

const usernameInput = document.getElementById("usernameInput");
const passwordInput = document.getElementById("passwordInput");
const roomNameInput = document.getElementById("roomNameInput");

const menuUsername = document.getElementById("menuUsername");
const menuUserId = document.getElementById("menuUserId");
const gameUsername = document.getElementById("gameUsername");
const roomIdLabel = document.getElementById("roomIdLabel");

const roleEl = document.getElementById("role");
const turnEl = document.getElementById("turn");
const statusEl = document.getElementById("status");
const boardEl = document.getElementById("board");
const roomsListEl = document.getElementById("roomsList");

let ws = null;
let selected = null;
let role = "spectator";
let turn = "white";
let lastFEN = "8/8/8/8/8/8/8/8 w - - 0 1";

let currentUser = {
  id: sessionStorage.getItem("user_id") ? Number(sessionStorage.getItem("user_id")) : null,
  username: sessionStorage.getItem("username") || null
};

let currentRoomId = sessionStorage.getItem("room_id") ? Number(sessionStorage.getItem("room_id")) : null;

const PIECE = {
  "p": "♟",
  "r": "♜",
  "n": "♞",
  "b": "♝",
  "q": "♛",
  "k": "♚",
  "P": "♙",
  "R": "♖",
  "N": "♘",
  "B": "♗",
  "Q": "♕",
  "K": "♔"
};

function showScreen(screen) {
  authScreen.classList.remove("active");
  menuScreen.classList.remove("active");
  gameScreen.classList.remove("active");
  screen.classList.add("active");
}

function setStatus(el, text, isError = false) {
  el.textContent = text || "";
  el.classList.toggle("error", !!isError);
}

function updateUserLabels() {
  menuUsername.textContent = currentUser.username || "-";
  menuUserId.textContent = currentUser.id ?? "-";
  gameUsername.textContent = currentUser.username || "-";
}

function saveUser(user) {
  currentUser.id = Number(user.id);
  currentUser.username = user.username;
  sessionStorage.setItem("user_id", String(currentUser.id));
  sessionStorage.setItem("username", currentUser.username);
  updateUserLabels();
}

function clearUser() {
  currentUser.id = null;
  currentUser.username = null;
  currentRoomId = null;
  sessionStorage.removeItem("user_id");
  sessionStorage.removeItem("username");
  sessionStorage.removeItem("room_id");
}

function saveRoomId(roomId) {
  currentRoomId = Number(roomId);
  sessionStorage.setItem("room_id", String(currentRoomId));
  roomIdLabel.textContent = currentRoomId;
}

function clearRoomId() {
  currentRoomId = null;
  sessionStorage.removeItem("room_id");
  roomIdLabel.textContent = "-";
}

function resetLocalGameUiState() {
  role = "spectator";
  turn = "white";
  selected = null;
  roleEl.textContent = "?";
  turnEl.textContent = "?";
  statusEl.textContent = "-";
}

async function apiPost(url, body) {
  const res = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body)
  });

  const data = await res.json();
  if (!res.ok) {
    throw new Error(data.detail || data.message || "Request failed");
  }
  return data;
}

async function apiGet(url) {
  const res = await fetch(url);
  const data = await res.json();
  if (!res.ok) {
    throw new Error(data.detail || data.message || "Request failed");
  }
  return data;
}

function sqName(file, rank) {
  const files = "abcdefgh";
  return files[file] + (8 - rank);
}

function parseFENBoard(fen) {
  const boardPart = fen.split(" ")[0];
  const rows = boardPart.split("/");
  const out = [];

  for (let r = 0; r < 8; r++) {
    const row = [];
    for (const ch of rows[r]) {
      if (ch >= "1" && ch <= "8") {
        for (let i = 0; i < Number(ch); i++) row.push(null);
      } else {
        row.push(ch);
      }
    }
    out.push(row);
  }
  return out;
}

function renderBoard(fen) {
  const grid = parseFENBoard(fen);
  boardEl.innerHTML = "";

  for (let r = 0; r < 8; r++) {
    for (let f = 0; f < 8; f++) {
      const sq = document.createElement("div");
      const name = sqName(f, r);
      const isDark = (r + f) % 2 === 1;

      sq.className = "sq " + (isDark ? "dark" : "light") + (selected === name ? " sel" : "");
      sq.dataset.sq = name;
      sq.addEventListener("click", onSquareClick);

      const p = grid[r][f];
      if (p) {
        const span = document.createElement("span");
        span.textContent = PIECE[p];
        span.className = (p === p.toUpperCase()) ? "piece-white" : "piece-black";
        sq.appendChild(span);
      }

      boardEl.appendChild(sq);
    }
  }
}

function myCanMove() {
  if (!ws || ws.readyState !== WebSocket.OPEN) return false;
  if (role !== "white" && role !== "black") return false;
  return role === turn;
}

function onSquareClick(e) {
  const sq = e.currentTarget.dataset.sq;

  console.log("CLICK role=", role, "turn=", turn, "canMove=", myCanMove());

  if (!currentUser.id) {
    statusEl.textContent = "You must login first";
    return;
  }

  if (!myCanMove()) {
    statusEl.textContent = "You can't move now";
    return;
  }

  if (selected === null) {
    selected = sq;
    statusEl.textContent = "Selected: " + selected;
    renderBoard(lastFEN);
    return;
  }

  const from = selected;
  const to = sq;
  selected = null;

  statusEl.textContent = "Sending move: " + from + to;
  ws.send(JSON.stringify({ type: "move", uci: from + to }));
  renderBoard(lastFEN);
}

async function registerUser() {
  try {
    const username = usernameInput.value.trim();
    const password = passwordInput.value;

    if (!username || !password) {
      setStatus(authStatus, "Username and password required", true);
      return;
    }

    const data = await apiPost("/register", { username, password });
    saveUser(data);
    setStatus(authStatus, "Registered successfully");
    showScreen(menuScreen);
    await loadRooms();
  } catch (err) {
    setStatus(authStatus, "Register error: " + err.message, true);
  }
}

async function loginUser() {
  try {
    const username = usernameInput.value.trim();
    const password = passwordInput.value;

    if (!username || !password) {
      setStatus(authStatus, "Username and password required", true);
      return;
    }

    const data = await apiPost("/login", { username, password });
    saveUser(data);
    setStatus(authStatus, "Logged in successfully");
    showScreen(menuScreen);
    await loadRooms();
  } catch (err) {
    setStatus(authStatus, "Login error: " + err.message, true);
  }
}

async function guestLogin() {
  try {
    const data = await apiPost("/guest", {});
    saveUser(data);
    setStatus(authStatus, "Entered as guest");
    showScreen(menuScreen);
    await loadRooms();
  } catch (err) {
    setStatus(authStatus, "Guest error: " + err.message, true);
  }
}

async function loadRooms() {
  try {
    const rooms = await apiGet("/rooms");
    roomsListEl.innerHTML = "";

    if (!rooms.length) {
      roomsListEl.innerHTML = '<div class="muted" style="text-align:center;">No rooms yet</div>';
      return;
    }

    for (const room of rooms) {
      const roomId = Number(room.room_id ?? room.id);
      const roomName = room.name ?? ("Room " + roomId);
      const roomStatus = room.room_status ?? room.status ?? "-";

      const item = document.createElement("div");
      item.className = "room-item";
      item.innerHTML = `
        <div class="room-item-head">
          <div>
            <div><b>${roomName}</b></div>
            <div class="room-meta">Room ID: ${roomId} | Status: ${roomStatus}</div>
          </div>
          <div class="room-actions">
            <button data-room-id="${roomId}" class="join-room-btn">Join Room</button>
          </div>
        </div>
        <div class="room-meta">
          White: ${room.white_user_id ?? "-"} |
          Black: ${room.black_user_id ?? "-"}
        </div>
      `;

      roomsListEl.appendChild(item);
    }

    document.querySelectorAll(".join-room-btn").forEach(btn => {
      btn.addEventListener("click", async () => {
        const roomId = Number(btn.dataset.roomId);
        await joinRoom(roomId);
      });
    });

  } catch (err) {
    setStatus(menuStatus, "Load rooms error: " + err.message, true);
  }
}

async function createRoom() {
  try {
    if (!currentUser.id) {
      setStatus(menuStatus, "You must login first", true);
      return;
    }

    const name = roomNameInput.value.trim();
    const data = await apiPost("/rooms", {
      user_id: currentUser.id,
      name: name || null
    });

    const roomId = Number(data.room_id ?? data.id);
    await joinRoom(roomId);
  } catch (err) {
    setStatus(menuStatus, "Create room error: " + err.message, true);
  }
}

async function quickGame() {
  try {
    if (!currentUser.id) {
      setStatus(menuStatus, "You must login first", true);
      return;
    }

    const data = await apiPost("/rooms/quick", {
      user_id: currentUser.id
    });

    const roomId = Number(data.room_id);
    await joinRoom(roomId);
  } catch (err) {
    setStatus(menuStatus, "Quick game error: " + err.message, true);
  }
}

async function joinRoom(roomId) {
  try {
    if (!currentUser.id) {
      setStatus(menuStatus, "You must login first", true);
      return;
    }

    await apiPost(`/rooms/${roomId}/join`, {
      user_id: currentUser.id
    });

    saveRoomId(roomId);
    startGameSocket();
    showScreen(gameScreen);
    setStatus(menuStatus, "");
  } catch (err) {
    setStatus(menuStatus, "Join room error: " + err.message, true);
  }
}

function startGameSocket() {
  if (!currentUser.id || !currentRoomId) return;

  if (ws) {
    try { ws.close(); } catch (_) {}
    ws = null;
  }

  const userId = currentUser.id;
  const roomId = currentRoomId;

  ws = new WebSocket(WS_URL);

  ws.onopen = () => {
    ws.send(JSON.stringify({
      type: "hello",
      user_id: userId,
      room_id: roomId
    }));
  };

  ws.onmessage = (ev) => {
    const msg = JSON.parse(ev.data);

    if (msg.type === "hello_ack") return;

    if (msg.type === "role") {
      role = msg.role;
      roleEl.textContent = role;
      console.log("ROLE =", role);
      return;
    }

    if (msg.type === "state") {
      lastFEN = msg.fen;
      turn = msg.turn;
      turnEl.textContent = turn;
      console.log("TURN =", turn, "FEN =", msg.fen);

      const st = [];
      if (msg.is_check) st.push("check");
      if (msg.is_checkmate) st.push("checkmate");
      if (msg.is_stalemate) st.push("stalemate");
      statusEl.textContent = st.length ? st.join(", ") : "-";

      renderBoard(lastFEN);
      return;
    }

    if (msg.type === "error") {
      statusEl.textContent = "ERROR: " + msg.message;
      console.log("WS ERROR =", msg.message);
      return;
    }

    if (msg.type === "game_over") {
      statusEl.textContent = msg.message || "Game over";
      alert(msg.message || "Game over");
      return;
    }

    if (msg.type === "room_closed") {
      alert(msg.message || "Room closed");

      if (ws) {
        try { ws.close(); } catch (_) {}
        ws = null;
      }

      resetLocalGameUiState();
      clearRoomId();
      showScreen(menuScreen);
      loadRooms();
      return;
    }

    console.log("Unhandled:", msg);
  };

  ws.onerror = () => {
    statusEl.textContent = "WebSocket error";
  };

  ws.onclose = () => {
    if (gameScreen.classList.contains("active")) {
      statusEl.textContent = "Disconnected";
    }
  };
}

function resetGame() {
  if (!ws || ws.readyState !== WebSocket.OPEN) return;
  ws.send(JSON.stringify({ type: "reset" }));
}

async function leaveToMenu() {
  if (ws) {
    try { ws.close(); } catch (_) {}
    ws = null;
  }

  resetLocalGameUiState();
  showScreen(menuScreen);
  loadRooms();
}

async function leaveGame() {
  try {
    if (currentUser.id && currentRoomId) {
      await apiPost(`/rooms/${currentRoomId}/leave`, {
        user_id: currentUser.id
      });
    }
  } catch (err) {
    console.error("Leave game error:", err.message);
  }

  if (ws) {
    try { ws.close(); } catch (_) {}
    ws = null;
  }

  resetLocalGameUiState();
  clearRoomId();
  showScreen(menuScreen);
  loadRooms();
}

function logout() {
  if (ws) {
    try { ws.close(); } catch (_) {}
    ws = null;
  }

  clearUser();
  resetLocalGameUiState();
  clearRoomId();
  lastFEN = "8/8/8/8/8/8/8/8 w - - 0 1";
  renderBoard(lastFEN);

  usernameInput.value = "";
  passwordInput.value = "";

  setStatus(authStatus, "");
  setStatus(menuStatus, "");
  showScreen(authScreen);
}

document.getElementById("registerBtn").addEventListener("click", registerUser);
document.getElementById("loginBtn").addEventListener("click", loginUser);
document.getElementById("guestBtn").addEventListener("click", guestLogin);

document.getElementById("quickGameBtn").addEventListener("click", quickGame);
document.getElementById("createRoomBtn").addEventListener("click", createRoom);
document.getElementById("refreshRoomsBtn").addEventListener("click", loadRooms);

document.getElementById("logoutBtn").addEventListener("click", logout);
document.getElementById("resetBtn").addEventListener("click", resetGame);
document.getElementById("backToMenuBtn").addEventListener("click", leaveToMenu);
document.getElementById("leaveGameBtn").addEventListener("click", leaveGame);

updateUserLabels();
renderBoard(lastFEN);

if (currentUser.id) {
  showScreen(menuScreen);
  loadRooms();

  if (currentRoomId) {
    saveRoomId(currentRoomId);
    startGameSocket();
    showScreen(gameScreen);
  }
} else {
  showScreen(authScreen);
}
