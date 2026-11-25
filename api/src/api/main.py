from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from api.games import CreatedGame, CreateGame, GameState, GameStore, Move, PlayerSession

app = FastAPI(title="Tic-Tac-Toe API")
store = GameStore()
bearer = HTTPBearer(auto_error=False)


def player_token(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
) -> str:
    if credentials is None:
        raise HTTPException(401, "Player token required", headers={"WWW-Authenticate": "Bearer"})
    return credentials.credentials


PlayerToken = Annotated[str, Depends(player_token)]


@app.get("/api/read_root")
async def read_root():
    return {"message": "Hello, Masha!"}


@app.post("/api/games", response_model=CreatedGame, status_code=201)
def create_game(options: CreateGame, request: Request) -> CreatedGame:
    session, invitation_token = store.create(options)
    invitation_url = (
        str(request.url_for("preview_invitation", invitation_token=invitation_token))
        if invitation_token is not None else None
    )
    return CreatedGame(**session.model_dump(), invitation_url=invitation_url)


@app.get("/api/invitations/{invitation_token}", response_model=GameState)
def preview_invitation(invitation_token: str) -> GameState:
    return store.preview(invitation_token)


@app.post("/api/invitations/{invitation_token}/join", response_model=PlayerSession)
def join_game(invitation_token: str) -> PlayerSession:
    return store.join(invitation_token)


@app.get("/api/games/{game_id}", response_model=GameState)
def get_game(game_id: str, token: PlayerToken) -> GameState:
    return store.read(game_id, token)


@app.post("/api/games/{game_id}/moves", response_model=GameState)
def make_move(game_id: str, move: Move, token: PlayerToken) -> GameState:
    return store.move(game_id, token, move.cell)
