from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.responses import HTMLResponse
from typing import Optional, Dict
from db import Base, engine, SessionLocal
from sqlalchemy import select
from db import SessionLocal
from models import User, Game
from schemas import UserRegister, UserLogin, UserOut
from security import hash_password, verify_password
import chess
import time
import uvicorn
import asyncio
import secrets
from contextlib import asynccontextmanager

@asynccontextmanager
async def lifespan(_: FastAPI):
    Base.metadata.create_all(bind=engine)
    yield

app = FastAPI(lifespan=lifespan)

board = chess.Board()
clients: set[WebSocket] = set()
ws_player: Dict[WebSocket, str] = {} # ws -> player_id
white_user_id: Optional[str] = None
black_user_id: Optional[str] = None
board_lock = asyncio.Lock() # secured from two user to moves at same time

# Gets game state from db, creates new state if doesn't exist
def get_or_create_game(db):
    game = db.scalar(select(Game).where(Game.id == 1))

    if game is None:
        game = Game(
            id=1,
            fen=chess.Board().fen(),
            status="active",
        )
        db.add(game)
        db.commit()
        db.refresh(game)

    return game

def side_for_role(role: str) -> Optional[bool]:
    if role == "white":
        return chess.WHITE
    if role == "black":
        return chess.BLACK
    return None

# checks role of current player
def role_for_user_id(user_id: str) -> str:
    if user_id == white_user_id:
        return "white"
    if user_id == black_user_id:
        return "black"
    return "spectator"

# if player is new, assigns free role
def assign_role(user_id: str) -> str:
    global white_user_id, black_user_id

    r = role_for_user_id(user_id)
    if r != "spectator":
        return r
    if white_user_id is None:
        white_user_id = user_id
        return "white"
    if black_user_id is None:
        black_user_id = user_id
        return "black"
    return "spectator"

@app.get("/", response_class=HTMLResponse)
def index():
    return """
<!doctype html>
<html>
<head>
  <meta charset="utf-8"/>
  <title>Web Chess (MVP)</title>
  <style>
    body { font-family: sans-serif; margin: 20px; }
    #bar { margin-bottom: 12px; }
    #auth { margin-bottom: 16px; }
    #auth input { margin-right: 8px; margin-bottom: 8px; }
    #board {
      width: 480px; height: 480px;
      display: grid;
      grid-template-columns: repeat(8, 1fr);
      border: 2px solid #111;
      user-select: none;
    }
    .sq {
      display:flex; align-items:center; justify-content:center;
      font-size: 34px;
    }
    .dark { background:#769656; }
    .light { background:#eeeed2; }
    .sel { outline: 3px solid #f00; }
  </style>
</head>
<body>
  <div id="auth">
    <input id="username" placeholder="username" />
    <input id="password" type="password" placeholder="password" />
    <button id="registerBtn">Register</button>
    <button id="loginBtn">Login</button>
    <button id="guestBtn">Guest</button>
    <button id="logoutBtn">Logout</button>
    <div><b>User:</b> <span id="userInfo">not authorized</span></div>
  </div>

  <div id="bar">
    <b>Role:</b> <span id="role">?</span> |
    <b>Turn:</b> <span id="turn">?</span> |
    <b>Status:</b> <span id="status">-</span>
  </div>

  <div id="board"></div>

<script>
  const WS_URL = (location.protocol === "https:" ? "wss://" : "ws://") + location.host + "/ws";

  let userId = sessionStorage.getItem("user_id");
  if (userId !== null) {
    userId = Number(userId);
  }

  let usernameStored = sessionStorage.getItem("username");
  let role = "spectator";
  let turn = "white";
  let selected = null;
  let lastFEN = "8/8/8/8/8/8/8/8 w - - 0 1";
  let ws = null;

  const roleEl = document.getElementById("role");
  const turnEl = document.getElementById("turn");
  const statusEl = document.getElementById("status");
  const boardEl = document.getElementById("board");
  const userInfoEl = document.getElementById("userInfo");

  const usernameInput = document.getElementById("username");
  const passwordInput = document.getElementById("password");
  const registerBtn = document.getElementById("registerBtn");
  const loginBtn = document.getElementById("loginBtn");
  const guestBtn = document.getElementById("guestBtn");
  const logoutBtn = document.getElementById("logoutBtn");

  const PIECE = {
    "p":"♟","r":"♜","n":"♞","b":"♝","q":"♛","k":"♚",
    "P":"♙","R":"♖","N":"♘","B":"♗","Q":"♕","K":"♔"
  };

  function updateUserInfo() {
    if (userId) {
      userInfoEl.textContent = usernameStored ? `${usernameStored} (id=${userId})` : `id=${userId}`;
    } else {
      userInfoEl.textContent = "not authorized";
    }
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
        const p = grid[r][f];
        sq.textContent = p ? PIECE[p] : "";
        sq.dataset.sq = name;
        sq.addEventListener("click", onSquareClick);
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

    if (!userId) {
      statusEl.textContent = "Register, login, or enter as guest first";
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

  async function apiPost(path, body) {
    const res = await fetch(path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body)
    });

    const data = await res.json();

    if (!res.ok) {
      throw new Error(data.detail || "Request failed");
    }

    return data;
  }

  function saveUser(data) {
    userId = Number(data.id);
    usernameStored = data.username;
    sessionStorage.setItem("user_id", String(userId));
    sessionStorage.setItem("username", usernameStored);
    updateUserInfo();
  }

  function connectWS() {
    if (!userId) {
      statusEl.textContent = "No user_id. Please login/register/guest first";
      return;
    }

    if (ws && (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING)) {
      return;
    }

    ws = new WebSocket(WS_URL);

    ws.onopen = () => {
      ws.send(JSON.stringify({ type: "hello", user_id: userId }));
    };

    ws.onmessage = (ev) => {
      const msg = JSON.parse(ev.data);

      if (msg.type === "hello_ack") {
        return;
      }

      if (msg.type === "role") {
        role = msg.role;
        roleEl.textContent = role;
        return;
      }

      if (msg.type === "state") {
        lastFEN = msg.fen;
        turn = msg.turn;
        turnEl.textContent = turn;
        let st = [];
        if (msg.is_check) st.push("check");
        if (msg.is_checkmate) st.push("checkmate");
        if (msg.is_stalemate) st.push("stalemate");
        statusEl.textContent = st.length ? st.join(", ") : "-";
        renderBoard(lastFEN);
        return;
      }

      if (msg.type === "error") {
        statusEl.textContent = "ERROR: " + msg.message;
        return;
      }

      console.log("Unhandled:", msg);
    };

    ws.onerror = () => {
      statusEl.textContent = "WS error";
    };

    ws.onclose = () => {
      statusEl.textContent = "WebSocket closed";
    };
  }

  registerBtn.addEventListener("click", async () => {
    try {
      const username = usernameInput.value.trim();
      const password = passwordInput.value;

      if (!username || !password) {
        statusEl.textContent = "Username and password required";
        return;
      }

      const data = await apiPost("/register", { username, password });
      saveUser(data);
      statusEl.textContent = "Registered successfully";
      connectWS();
    } catch (err) {
      statusEl.textContent = "Register error: " + err.message;
    }
  });

  loginBtn.addEventListener("click", async () => {
    try {
      const username = usernameInput.value.trim();
      const password = passwordInput.value;

      if (!username || !password) {
        statusEl.textContent = "Username and password required";
        return;
      }

      const data = await apiPost("/login", { username, password });
      saveUser(data);
      statusEl.textContent = "Logged in successfully";
      connectWS();
    } catch (err) {
      statusEl.textContent = "Login error: " + err.message;
    }
  });

  guestBtn.addEventListener("click", async () => {
    try {
      const data = await apiPost("/guest", {});
      saveUser(data);
      statusEl.textContent = "Guest login successful";
      connectWS();
    } catch (err) {
      statusEl.textContent = "Guest error: " + err.message;
    }
  });

  logoutBtn.addEventListener("click", () => {
    if (ws) {
      ws.close();
      ws = null;
    }

    sessionStorage.removeItem("user_id");
    sessionStorage.removeItem("username");
    userId = null;
    usernameStored = null;
    role = "spectator";
    turn = "white";
    selected = null;
    roleEl.textContent = "?";
    turnEl.textContent = "?";
    statusEl.textContent = "Logged out";
    updateUserInfo();
    renderBoard(lastFEN);
  });

  updateUserInfo();
  renderBoard(lastFEN);

  if (userId) {
    connectWS();
  }
</script>
</body>
</html>
"""

@app.post("/register", response_model=UserOut)
def register(user_data: UserRegister):
    db = SessionLocal()
    try:
        existing_user = db.scalar(
            select(User).where(User.username == user_data.username)
        )
        if existing_user:
            raise HTTPException(status_code=400, detail="Username already exists")
        user = User(
            username=user_data.username,
            hashed_password=hash_password(user_data.password),
            is_guest=False,
        )
        db.add(user)
        db.commit()
        db.refresh(user)
        return user
    finally:
        db.close()

@app.post("/login", response_model=UserOut)
def login(user_data: UserLogin):
    db = SessionLocal()
    try:
        user = db.scalar(
            select(User).where(User.username == user_data.username)
        )
        if not user or not verify_password(user_data.password, user.hashed_password):
            raise HTTPException(status_code=401, detail="Invalid username or password")
        return user
    finally:
        db.close()

@app.post("/guest", response_model=UserOut)
def guest_login():
    db = SessionLocal()
    try:
        guest_name = f"guest_{secrets.token_urlsafe(4)}"
        user = User(
            username=guest_name,
            hashed_password=hash_password(secrets.token_urlsafe(16)),
            is_guest=True,
        )
        db.add(user)
        db.commit()
        db.refresh(user)
        return user
    finally:
        db.close()

@app.get("/health")
def health():
    return {"status": "ok", "ts": int(time.time())}

def state_payload(board: chess.Board): # status of player
    return {"type": "state", "fen": board.fen(),
            "turn": "white" if board.turn == chess.WHITE else "black",
            "is_check": board.is_check(),
            "is_checkmate": board.is_checkmate(),
            "is_stalemate": board.is_stalemate()
            }

# check if player connected
def pid_is_connected(pid: str) -> bool: 
    return pid in ws_player.values()

async def broadcast(payload: dict): # send payload to all connected clients
    dead = []
    for c in list(clients):
        try:
            await c.send_json(payload)
        except Exception:
            dead.append(c)
    for c in dead:
        clients.discard(c)

@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket):
    await ws.accept()
    clients.add(ws)
    
    # ---- handshake: first message must be hello ----
    hello = await ws.receive_json()
    if hello.get("type") != "hello":
        await ws.send_json({"type": "error", "message": "expected hello first"})
        await ws.close()
        return

    user_id = hello.get("user_id")
    if user_id is None:
        await ws.send_json({"type": "error", "message": "user_id required"})
        await ws.close()
        return
    try:
        user_id = int(user_id)
    except (TypeError, ValueError):
        await ws.send_json({"type": "error", "message": "user_id must be int"})
        await ws.close()
        return

    ws_player[ws] = user_id
    await ws.send_json({"type": "hello_ack", "user_id": user_id})
    # ----------------------------------------------
    
    # players role
    role = assign_role(user_id)

    db = SessionLocal()
    try:
        game = get_or_create_game(db)

        if role == "white" and game.white_user_id is None:
            game.white_user_id = user_id
        elif role == "black" and game.black_user_id is None:
            game.black_user_id = user_id

        db.commit()

        fen = game.fen
    finally:
        db.close()

    #creating new game or pulling existing state from db
    board = chess.Board(fen)
    await ws.send_json({"type": "role", "role": role})
    await ws.send_json(state_payload(board))

    try:
        while True:
            msg = await ws.receive_json()
            mtype = msg.get("type")

            if mtype == "move":
                uci = msg.get("uci")
                if not uci:
                    await ws.send_json({"type": "error", "message": "uci is required"})
                    continue

                try:
                    move = chess.Move.from_uci(uci)
                except Exception:
                    await ws.send_json({"type": "error", "message": "bad uci format"})
                    continue

                pid = ws_player.get(ws)
                role = role_for_user_id(pid) if pid is not None else "spectator"
                side = side_for_role(role)

                err = None
                payload = None

                async with board_lock:
                    db = SessionLocal()
                    try:
                        game = db.scalar(select(Game).where(Game.id == 1))
                        if game is None:
                            game = Game(
                                id=1,
                                fen=chess.Board().fen(),
                                status="active",
                            )
                            db.add(game)
                            db.commit()
                            db.refresh(game)

                        board = chess.Board(game.fen)

                        if side is None:
                            err = {"type": "error", "message": "spectators cannot move"}

                        elif board.turn != side:
                            err = {"type": "error", "message": "not your turn"}

                        elif move not in board.legal_moves:
                            err = {"type": "error", "message": "illegal move"}
                            payload = state_payload(board)

                        else:
                            board.push(move)
                            game.fen = board.fen()

                            if board.is_checkmate():
                                game.status = "checkmate"
                            elif board.is_stalemate():
                                game.status = "stalemate"
                            else:
                                game.status = "active"

                            db.commit()
                            payload = state_payload(board)

                    finally:
                        db.close()

                if err:
                    await ws.send_json(err)
                    if payload is not None:
                        await ws.send_json(payload)
                    continue

                if payload is not None:
                    await broadcast(payload)

            elif mtype == "reset":
                async with board_lock:
                    db = SessionLocal()
                    try:
                        game = db.scalar(select(Game).where(Game.id == 1))
                        if game is None:
                            game = Game(
                                id=1,
                                fen=chess.Board().fen(),
                                status="active",
                            )
                            db.add(game)
                            db.commit()
                            db.refresh(game)
                        else:
                            board = chess.Board()
                            game.fen = board.fen()
                            game.status = "active"
                            db.commit()
                        board = chess.Board(game.fen)
                        payload = state_payload(board)

                    finally:
                        db.close()
                await broadcast(payload)

            else:
                await ws.send_json({"type": "error", "message": f"unknown type: {mtype}"})

    except WebSocketDisconnect:
        pass

    finally:  # конец игры. отключение, чистка данных клиента
        clients.discard(ws)
        pid = ws_player.pop(ws, None)
        
        # cleaning slot if player disconnected
        global white_user_id, black_user_id
        if pid and not pid_is_connected(pid):
            if white_user_id == pid:
                white_user_id = None
            if black_user_id == pid:
                black_user_id = None

def main():
    uvicorn.run("app:app", host="0.0.0.0", port=8000, reload=True)

if __name__ == "__main__":
    main()
