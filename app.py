from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.responses import HTMLResponse
from typing import Optional, Dict
from db import Base, engine, SessionLocal
from sqlalchemy import select
from db import SessionLocal
from datetime import datetime, timedelta, timezone
from models import User, Game, Room, RoomMember
from schemas import UserRegister, UserLogin, UserOut, RoomCreate, RoomJoin
from security import hash_password, verify_password
import chess
import time
import uvicorn
import asyncio
import secrets
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi import Request
from contextlib import asynccontextmanager
from sqlalchemy import select, delete

@asynccontextmanager
async def lifespan(_: FastAPI):
    Base.metadata.create_all(bind=engine)
    yield

app = FastAPI(lifespan=lifespan)

#mounting dirs with frontend
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

board = chess.Board()
clients: set[WebSocket] = set()
ws_player: Dict[WebSocket, int] = {} # ws -> player_id
ws_room: Dict[WebSocket, int] = {}
white_user_id: Optional[int] = None
black_user_id: Optional[int] = None
disconnect_tasks: dict[tuple[int, int], asyncio.Task] = {}
board_lock = asyncio.Lock() # secured from two user to moves at same time

# Gets game state from db, creates new state if doesn't exist
def get_or_create_game(db, room_id: int):
    game = db.scalar(select(Game).where(Game.room_id == room_id))

    if game is None:
        game = Game(
            room_id=room_id,
            fen=chess.Board().fen(),
            status="waiting",
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
def role_for_user_id(user_id: int) -> str:
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
def index(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})

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

@app.post("/rooms")
def create_room(data: RoomCreate):
    db = SessionLocal()
    try:
        room_name = data.name.strip() if data.name else f"Room by user {data.user_id}"

        room = Room(
            name=room_name,
            created_by_user_id=data.user_id,
            status="open",
        )
        db.add(room)
        db.commit()
        db.refresh(room)

        game = Game(
            room_id=room.id,
            white_user_id=data.user_id,
            black_user_id=None,
            fen=chess.Board().fen(),
            status="waiting",
        )
        db.add(game)
        db.commit()
        db.refresh(game)

        return {
            "room_id": room.id,
            "name": room.name,
            "status": room.status,
            "white_user_id": game.white_user_id,
            "black_user_id": game.black_user_id,
        }
    finally:
        db.close()

@app.get("/rooms")
def list_rooms():
    db = SessionLocal()
    try:
        rooms = db.scalars(select(Room)).all()

        result = []

        for room in rooms:
            game = db.scalar(select(Game).where(Game.room_id == room.id))

            if game is None:
                db.delete(room)
                db.commit()
                continue

            if game.white_user_id is None and game.black_user_id is None:
                cleanup_room_if_empty(db, room.id)
                continue

            result.append({
                "room_id": room.id,
                "name": room.name,
                "status": room.status,
                "white_user_id": game.white_user_id,
                "black_user_id": game.black_user_id,
            })

        return result

    finally:
        db.close()

@app.post("/rooms/{room_id}/join")
def join_room(room_id: int, data: RoomJoin):
    db = SessionLocal()
    try:
        room = db.scalar(select(Room).where(Room.id == room_id))
        if room is None:
            raise HTTPException(status_code=404, detail="Room not found")

        game = db.scalar(select(Game).where(Game.room_id == room_id))
        if game is None:
            raise HTTPException(status_code=404, detail="Game not found")

        role = "spectator"

        if game.white_user_id == data.user_id:
            role = "white"
        elif game.black_user_id == data.user_id:
            role = "black"
        elif game.white_user_id is None:
            game.white_user_id = data.user_id
            role = "white"
        elif game.black_user_id is None:
            game.black_user_id = data.user_id
            role = "black"
        else:
            role = "spectator"

        if game.white_user_id is not None and game.black_user_id is not None:
            game.status = "active"
            room.status = "active"
        else:
            game.status = "waiting"
            room.status = "open"

        db.commit()

        return {
            "room_id": room.id,
            "role": role,
            "white_user_id": game.white_user_id,
            "black_user_id": game.black_user_id,
            "game_status": game.status,
            "room_status": room.status,
        }
    finally:
        db.close()

@app.post("/rooms/{room_id}/leave")
async def leave_room(room_id: int, data: RoomJoin):
    db = SessionLocal()
    try:
        room = db.scalar(select(Room).where(Room.id == room_id))
        game = db.scalar(select(Game).where(Game.room_id == room_id))

        if room is None or game is None:
            return {"ok": True, "deleted": False}

        user_id = data.user_id

        member = db.scalar(
            select(RoomMember).where(
                RoomMember.room_id == room_id,
                RoomMember.user_id == user_id
            )
        )

        if member:
            member.is_connected = False
            member.is_active = False
            member.disconnect_deadline = None
            member.left_at = datetime.now(timezone.utc)

        opponent_exists = False
        if game.white_user_id == user_id and game.black_user_id is not None:
            opponent_exists = True
        elif game.black_user_id == user_id and game.white_user_id is not None:
            opponent_exists = True

        db.commit()

    finally:
        db.close()

    if opponent_exists:
        await close_room_with_winner(
            room_id=room_id,
            leaver_user_id=user_id,
            reason="player_left"
        )
        return {"ok": True, "deleted": True}

    db = SessionLocal()
    try:
        deleted = cleanup_room_if_empty_or_single(db, room_id, user_id)
    finally:
        db.close()

    if deleted:
        await broadcast_to_room(room_id, {
            "type": "room_closed",
            "message": "Room closed"
        })

        sockets_to_close = [c for c in list(clients) if ws_room.get(c) == room_id]
        for c in sockets_to_close:
            try:
                await c.close()
            except Exception:
                pass
            clients.discard(c)
            ws_player.pop(c, None)
            ws_room.pop(c, None)

    return {"ok": True, "deleted": deleted}

@app.post("/rooms/quick")
def quick_game(data: RoomJoin):
    db = SessionLocal()
    try:
        rooms = db.scalars(select(Room).where(Room.status.in_(["open", "active"]))).all()

        for room in rooms:
            game = db.scalar(select(Game).where(Game.room_id == room.id))
            if game is None:
                continue

            if game.white_user_id == data.user_id:
                role = "white"
            elif game.black_user_id == data.user_id:
                role = "black"
            elif game.black_user_id is None:
                game.black_user_id = data.user_id
                game.status = "active"
                room.status = "active"
                db.commit()
                return {
                    "room_id": room.id,
                    "role": "black",
                }

        room = Room(
            name=f"Quick room by user {data.user_id}",
            created_by_user_id=data.user_id,
            status="open",
        )
        db.add(room)
        db.commit()
        db.refresh(room)

        game = Game(
            room_id=room.id,
            white_user_id=data.user_id,
            black_user_id=None,
            fen=chess.Board().fen(),
            status="waiting",
        )
        db.add(game)
        db.commit()

        return {
            "room_id": room.id,
            "role": "white",
        }
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
def user_is_connected(user_id: int) -> bool:
    return user_id in ws_player.values()
"""
async def delayed_disconnect_cleanup(room_id: int, user_id: int, delay: int = 30):
    await asyncio.sleep(delay)

    if user_id in ws_player.values():
        disconnect_tasks.pop((room_id, user_id), None)
        return

    db = SessionLocal()
    try:
        game = db.scalar(select(Game).where(Game.room_id == room_id))
        member = db.scalar(
            select(RoomMember).where(
                RoomMember.room_id == room_id,
                RoomMember.user_id == user_id
            )
        )

        if member:
            member.is_connected = False
            member.is_active = False
            member.disconnect_deadline = None
            member.left_at = datetime.now(timezone.utc)

        if not game:
            return

        opponent_exists = False
        if game.white_user_id == user_id and game.black_user_id is not None:
            opponent_exists = True
        elif game.black_user_id == user_id and game.white_user_id is not None:
            opponent_exists = True

        db.commit()

    finally:
        db.close()

    if opponent_exists:
        await close_room_with_winner(
            room_id=room_id,
            leaver_user_id=user_id,
            reason="disconnect_timeout"
        )
    else:
        db = SessionLocal()
        try:
            deleted = cleanup_room_if_empty_or_single(db, room_id, user_id)
        finally:
            db.close()

        if deleted:
            await broadcast_to_room(room_id, {
                "type": "room_closed",
                "message": "Room closed"
            })

            sockets_to_close = [c for c in list(clients) if ws_room.get(c) == room_id]
            for c in sockets_to_close:
                try:
                    await c.close()
                except Exception:
                    pass
                clients.discard(c)
                ws_player.pop(c, None)
                ws_room.pop(c, None)

    disconnect_tasks.pop((room_id, user_id), None)
"""

async def delayed_disconnect_cleanup(room_id: int, user_id: int, delay: int = 30):
    await asyncio.sleep(delay)

    still_connected_in_room = any(
        ws_player.get(c) == user_id and ws_room.get(c) == room_id
        for c in clients
    )

    if still_connected_in_room:
        disconnect_tasks.pop((room_id, user_id), None)
        return

    db = SessionLocal()
    try:
        game = db.scalar(select(Game).where(Game.room_id == room_id))
        member = db.scalar(
            select(RoomMember).where(
                RoomMember.room_id == room_id,
                RoomMember.user_id == user_id
            )
        )


        if member:
            member.is_connected = False
            member.is_active = False
            member.disconnect_deadline = None
            member.left_at = datetime.now(timezone.utc)

        if not game:
            db.commit()
            return

        opponent_exists = False

        if game.white_user_id == user_id and game.black_user_id is not None:
            opponent_exists = True
        elif game.black_user_id == user_id and game.white_user_id is not None:
            opponent_exists = True

        db.commit()

    finally:
        db.close()

    if opponent_exists:
        await close_room_with_winner(
            room_id=room_id,
            leaver_user_id=user_id,
            reason="disconnect_timeout"
        )
    else:
        db = SessionLocal()
        try:
            deleted = cleanup_room_if_empty_or_single(db, room_id, user_id)
        finally:
            db.close()

        if deleted:
            await broadcast_to_room(room_id, {
                "type": "room_closed",
                "message": "Room closed"
            })

            sockets_to_close = [c for c in list(clients) if ws_room.get(c) == room_id]

            for c in sockets_to_close:
                try:
                    await c.close()
                except Exception:
                    pass
                clients.discard(c)
                ws_player.pop(c, None)
                ws_room.pop(c, None)

    disconnect_tasks.pop((room_id, user_id), None)

async def broadcast_to_room(room_id: int, payload: dict): # send payload to all connected clients
    dead = []

    for c in list(clients):
        if ws_room.get(c) != room_id:
            continue

        try:
            await c.send_json(payload)
        except Exception:
            dead.append(c)

    for c in dead:
        clients.discard(c)
        ws_player.pop(c, None)
        ws_room.pop(c, None)

def get_or_create_room_member(db, room_id: int, user_id: int, role: str) -> RoomMember:
    member = db.scalar(
        select(RoomMember).where(
            RoomMember.room_id == room_id,
            RoomMember.user_id == user_id
        )
    )

    if member is None:
        member = RoomMember(
            room_id=room_id,
            user_id=user_id,
            role=role,
            is_connected=True,
            is_active=True,
            disconnect_deadline=None,
            left_at=None,
        )
        db.add(member)
        db.commit()
        db.refresh(member)
    else:
        member.role = role
        member.is_connected = True
        member.is_active = True
        member.disconnect_deadline = None
        member.left_at = None
        db.commit()

    return member

def cleanup_room_if_empty(db, room_id: int) -> bool:
    game = db.scalar(select(Game).where(Game.room_id == room_id))
    room = db.scalar(select(Room).where(Room.id == room_id))

    if not game:
        if room:
            db.delete(room)
            db.commit()
        return True

    if game.white_user_id is None and game.black_user_id is None:
        members = db.scalars(
            select(RoomMember).where(RoomMember.room_id == room_id)
        ).all()

        for member in members:
            db.delete(member)

        db.delete(game)

        if room:
            db.delete(room)

        db.commit()
        return True

    return False

def cleanup_room_if_empty_or_single(db, room_id: int, leaver_user_id: int | None = None) -> bool:
    game = db.scalar(select(Game).where(Game.room_id == room_id))
    room = db.scalar(select(Room).where(Room.id == room_id))

    if not game:
        if room:
            db.delete(room)
            db.commit()
        return True

    if leaver_user_id is not None:
        if game.white_user_id == leaver_user_id:
            game.white_user_id = None
        elif game.black_user_id == leaver_user_id:
            game.black_user_id = None

    if game.white_user_id is None and game.black_user_id is None:
        db.commit()
        db.execute(delete(RoomMember).where(RoomMember.room_id == room_id))
        db.execute(delete(Game).where(Game.room_id == room_id))
        db.execute(delete(Room).where(Room.id == room_id))
        db.commit()
        return True

    if (game.white_user_id is None) != (game.black_user_id is None):
        db.commit()
        db.execute(delete(RoomMember).where(RoomMember.room_id == room_id))
        db.execute(delete(Game).where(Game.room_id == room_id))
        db.execute(delete(Room).where(Room.id == room_id))
        db.commit()
        return True

    db.commit()
    return False

async def close_room_with_winner(room_id: int, leaver_user_id: int, reason: str):
    # 1) сначала определяем победителя и шлем game_over
    db = SessionLocal()
    try:
        game = db.scalar(select(Game).where(Game.room_id == room_id))
        room = db.scalar(select(Room).where(Room.id == room_id))

        if game is None or room is None:
            return

        winner_user_id = None
        winner_role = None

        if game.white_user_id == leaver_user_id:
            winner_user_id = game.black_user_id
            winner_role = "black"
        elif game.black_user_id == leaver_user_id:
            winner_user_id = game.white_user_id
            winner_role = "white"

        if winner_user_id is not None:
            game.status = "finished"
            room.status = "closed"
            db.commit()

            await broadcast_to_room(room_id, {
                "type": "game_over",
                "reason": reason,
                "winner_user_id": winner_user_id,
                "winner_role": winner_role,
                "message": f"{winner_role} wins: opponent left the room"
            })

    finally:
        db.close()

    # 2) затем шлем room_closed ПОКА сокеты еще живы
    await broadcast_to_room(room_id, {
        "type": "room_closed",
        "message": "Room closed"
    })

    # 3) после этого закрываем сокеты этой комнаты и вычищаем их из памяти
    sockets_to_close = [c for c in list(clients) if ws_room.get(c) == room_id]

    for c in sockets_to_close:
        try:
            await c.close()
        except Exception:
            pass
        clients.discard(c)
        ws_player.pop(c, None)
        ws_room.pop(c, None)

    # 4) отменяем все pending disconnect task по этой комнате
    for key, task in list(disconnect_tasks.items()):
        task_room_id, _ = key
        if task_room_id == room_id:
            task.cancel()
            disconnect_tasks.pop(key, None)

    # 5) только теперь удаляем все из БД
    db = SessionLocal()
    try:
        """members = db.scalars(
            select(RoomMember).where(RoomMember.room_id == room_id)
        ).all()

        for member in members:
            db.delete(member)

        game = db.scalar(select(Game).where(Game.room_id == room_id))
        room = db.scalar(select(Room).where(Room.id == room_id))

        if game is not None:
            db.delete(game)
        if room is not None:
            db.delete(room)

        db.commit()"""

        db.execute(
            delete(RoomMember).where(RoomMember.room_id == room_id)
        )
        db.execute(
            delete(Game).where(Game.room_id == room_id)
        )
        db.execute(
            delete(Room).where(Room.id == room_id)
        )
        db.commit()

    finally:
        db.close()

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

    # user id
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

    # room id
    room_id = hello.get("room_id")
    if room_id is None:
        await ws.send_json({"type": "error", "message": "room_id required"})
        await ws.close()
        return
    try:
        room_id = int(room_id)
    except (TypeError, ValueError):
        await ws.send_json({"type": "error", "message": "room_id must be int"})
        await ws.close()
        return

    task_key = (room_id, user_id)

    old_task = disconnect_tasks.pop(task_key, None)
    if old_task:
        old_task.cancel()

    db = SessionLocal()
    try:
        room = db.scalar(select(Room).where(Room.id == room_id))
        if room is None:
            await ws.send_json({"type": "error", "message": "room not found"})
            await ws.close()
            return

        game = get_or_create_game(db, room_id)

        if game.white_user_id == user_id:
            role = "white"
        elif game.black_user_id == user_id:
            role = "black"
        elif game.white_user_id is None:
            game.white_user_id = user_id
            role = "white"
        elif game.black_user_id is None:
            game.black_user_id = user_id
            role = "black"
        else:
            role = "spectator"

        if game.white_user_id is not None and game.black_user_id is not None:
            game.status = "active"
            room.status = "active"
        else:
            game.status = "waiting"
            room.status = "open"

        db.commit()

        get_or_create_room_member(db, room_id, user_id, role)

        fen = game.fen

    finally:
        db.close()

    ws_player[ws] = user_id
    ws_room[ws] = room_id

    await ws.send_json({"type": "hello_ack", "user_id": user_id, "room_id": room_id})
    await ws.send_json({"type": "role", "role": role})
    await ws.send_json(state_payload(chess.Board(fen)))

    # ----------------------------------------------

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

                user_id = ws_player.get(ws)
                room_id = ws_room.get(ws)

                if user_id is None or room_id is None:
                    await ws.send_json({"type": "error", "message": "user_id or room_id missing"})
                    continue

                err = None
                payload = None

                async with board_lock:
                    db = SessionLocal()
                    try:
                        game = get_or_create_game(db, room_id)

                        board = chess.Board(game.fen)

                        if game.white_user_id == user_id:
                            role = "white"
                        elif game.black_user_id == user_id:
                            role = "black"
                        else:
                            role = "spectator"

                        side = side_for_role(role)

                        if side is None:
                            err = {"type": "error", "message": "spectators cannot move"}

                        elif game.white_user_id is None or game.black_user_id is None:
                            err = {"type": "error", "message": "game has not started yet"}

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
                    await broadcast_to_room(room_id, payload)

            elif mtype == "reset":
                async with board_lock:
                    db = SessionLocal()
                    try:

                        game = get_or_create_game(db, room_id)

                        board = chess.Board()
                        game.fen = board.fen()
                        if game.white_user_id is not None and game.black_user_id is not None:
                            game.status = "active"
                        else:
                            game.status = "waiting"

                        db.commit()

                        new_board = chess.Board()
                        game.fen = new_board.fen()
                        payload = state_payload(new_board)

                    finally:
                        db.close()

                await broadcast_to_room(room_id, payload)

            else:
                await ws.send_json({"type": "error", "message": f"unknown type: {mtype}"})

    except WebSocketDisconnect:
        pass

    finally:
        clients.discard(ws)
        user_id = ws_player.pop(ws, None)
        room_id = ws_room.pop(ws, None)

        if user_id is not None and room_id is not None:
            still_connected = user_id in ws_player.values()

            if not still_connected:
                db = SessionLocal()
                try:
                    member = db.scalar(
                        select(RoomMember).where(
                            RoomMember.room_id == room_id,
                            RoomMember.user_id == user_id
                        )
                    )
                    if member:
                        member.is_connected = False
                        member.is_active = False
                        member.disconnect_deadline = datetime.now(timezone.utc) + timedelta(seconds=30)
                        db.commit()
                finally:
                    db.close()

                task_key = (room_id, user_id)

                old_task = disconnect_tasks.pop(task_key, None)
                if old_task:
                    old_task.cancel()

                disconnect_tasks[task_key] = asyncio.create_task(
                    delayed_disconnect_cleanup(room_id, user_id, delay=30)
                )
