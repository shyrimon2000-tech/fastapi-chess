from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
from typing import Optional, Dict
import chess
import time
import uvicorn
import asyncio
import secrets

app = FastAPI()

board = chess.Board()
clients: set[WebSocket] = set()
ws_player: Dict[WebSocket, str] = {} # ws -> player_id
white_pid: Optional[str] = None
black_pid: Optional[str] = None
board_lock = asyncio.Lock() # secured from two user to moves at same time

def side_for_role(role: str) -> Optional[bool]:
    if role == "white":
        return chess.WHITE
    if role == "black":
        return chess.BLACK
    return None

# checks role of current player
def role_for_pid(pid: str) -> str:
    if pid == white_pid:
        return "white"
    if pid == black_pid:
        return "black"
    return "spectator"

# if player is new, assigns free role
def assign_role(pid: str) -> str:
    global white_pid, black_pid

    r = role_for_pid(pid)
    if r != "spectator":
        return r
    if white_pid is None:
        white_pid = pid
        return "white"
    if black_pid is None:
        black_pid = pid
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
  <div id="bar">
    <b>Role:</b> <span id="role">?</span> |
    <b>Turn:</b> <span id="turn">?</span> |
    <b>Status:</b> <span id="status">-</span>
  </div>

  <div id="board"></div>

<script>
  const WS_URL = (location.protocol === "https:" ? "wss://" : "ws://") + location.host + "/ws";

  // ВАЖНО: для теста вкладками используй sessionStorage (вкладка=отдельный игрок)
  let pid = sessionStorage.getItem("pid");
  let role = "spectator";
  let turn = "white";
  let selected = null; // например "e2"

  const roleEl = document.getElementById("role");
  const turnEl = document.getElementById("turn");
  const statusEl = document.getElementById("status");
  const boardEl = document.getElementById("board");

  const PIECE = {
    "p":"♟","r":"♜","n":"♞","b":"♝","q":"♛","k":"♚",
    "P":"♙","R":"♖","N":"♘","B":"♗","Q":"♕","K":"♔"
  };

  function sqName(file, rank) { // file 0..7, rank 0..7 (rank 0 = 8)
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
    return out; // 8x8
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
    if (role !== "white" && role !== "black") return false;
    return role === turn;
  }

  function onSquareClick(e) {
    const sq = e.currentTarget.dataset.sq;

    if (!myCanMove()) {
      statusEl.textContent = "You can't move now";
      return;
    }

    // 1-й клик: выбрать "from"
    if (selected === null) {
      selected = sq;
      statusEl.textContent = "Selected: " + selected;
      renderBoard(lastFEN);
      return;
    }

    // 2-й клик: отправить ход from->to
    const from = selected;
    const to = sq;
    selected = null;
    statusEl.textContent = "Sending move: " + from + to;

    ws.send(JSON.stringify({ type: "move", uci: from + to }));
    renderBoard(lastFEN);
  }

  let lastFEN = "8/8/8/8/8/8/8/8 w - - 0 1";

  const ws = new WebSocket(WS_URL);

  ws.onopen = () => {
    ws.send(JSON.stringify({ type: "hello", player_id: pid }));
  };

  ws.onmessage = (ev) => {
    const msg = JSON.parse(ev.data);

    if (msg.type === "hello_ack") {
      sessionStorage.setItem("pid", msg.player_id);
      pid = msg.player_id;
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
</script>
</body>
</html>
"""


@app.get("/health")
def health():
    return {"status": "ok", "ts": int(time.time())}

def state_payload(): # status of player
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

    pid = hello.get("player_id")
    if not pid:
        pid = secrets.token_urlsafe(8)

    ws_player[ws] = pid
    await ws.send_json({"type": "hello_ack", "player_id": pid})
    # ----------------------------------------------
    
    # players role
    pid = ws_player[ws]
    if not pid:
        await ws.send_json({"type": "error", "message": "no player_id"})
        await ws.close()
        return
    role = assign_role(pid)

    # сразу отправим текущее состояние
    await ws.send_json({"type": "role", "role": role})
    await ws.send_json(state_payload())

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
                role = role_for_pid(pid) if pid is not None else "spectator"
                side = side_for_role(role)


                async with board_lock:
                    err = None
                    payload = None

                    if side is None: # проверяем является ли клиент зрителем
                        err = {"type": "error", "message": "spectators cannot move"}
                        payload = None

                    elif board.turn != side: # проверяем должен ли ходить клиент
                        err = {"type": "error", "message": "not your turn"}
                        payload = None

                    elif move not in board.legal_moves: # прверяем есть ли такой ход 
                        err = {"type": "error", "message": "illegal move"}
                        payload = state_payload()
                    
                    else:
                        board.push(move) # ход
                        err = None
                        payload = state_payload()

                    # отправляем новое состояние этому клиенту
                if err:
                    await ws.send_json(err)
                    if payload is not None:
                        await ws.send_json(payload)
                else:
                    await broadcast(payload)

            elif mtype == "reset":
                async with board_lock:
                    board.reset()
                    payload = state_payload()
                await broadcast(payload)

            else:
                await ws.send_json({"type": "error", "message": f"unknown type: {mtype}"})

    except WebSocketDisconnect:
        pass

    finally:  # конец игры. отключение, чистка данных клиента
        clients.discard(ws)
        pid = ws_player.pop(ws, None)
        
        # cleaning slot if player disconnected
        global white_pid, black_pid
        if pid and not pid_is_connected(pid):
            if white_pid == pid:
                white_pid = None
            if black_pid == pid:
                black_pid = None

def main():
    uvicorn.run("app:app", host="0.0.0.0", port=8000, reload=True)

if __name__ == "__main__":
    main()
