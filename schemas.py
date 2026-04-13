from pydantic import BaseModel, ConfigDict, Field

class UserRegister(BaseModel):
    username: str
    password: str = Field(min_length=6, max_length=64)

class UserLogin(BaseModel):
    username: str
    password: str

class UserOut(BaseModel):
    id: int
    username: str
    is_guest: bool

    model_config = ConfigDict(from_attributes=True)

class RoomCreate(BaseModel):
    user_id: int
    name: str | None = None

class RoomJoin(BaseModel):
    user_id: int

class RoomOut(BaseModel):
    id: int
    name: str
    status: str
    white_user_id: int | None
    black_user_id: int | None