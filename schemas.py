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