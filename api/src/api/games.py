import secrets
from dataclasses import dataclass, field
from threading import Lock
from time import monotonic
from typing import Literal

from fastapi import HTTPException
from pydantic import BaseModel, Field

Mark = Literal["X", "O"]
Mode = Literal["friend", "computer"]
Difficulty = Literal["easy", "medium", "unbeatable"]
Status = Literal["waiting", "playing", "won", "draw"]

WINNING_LINES = (
    (0, 1, 2), (3, 4, 5), (6, 7, 8),
    (0, 3, 6), (1, 4, 7), (2, 5, 8),
    (0, 4, 8), (2, 4, 6),
)
GAME_LIFETIME_SECONDS = 24 * 60 * 60
MAX_GAMES = 1000


class CreateGame(BaseModel):
    mode: Mode
    difficulty: Difficulty = "medium"


class Move(BaseModel):
    cell: int = Field(ge=0, le=8, strict=True)


class GameState(BaseModel):
    game_id: str
    mode: Mode
    difficulty: Difficulty | None
    board: list[Mark | None]
    status: Status
    turn: Mark | None
    winner: Mark | None
    winning_cells: list[int]


class PlayerSession(BaseModel):
    player_token: str
    your_mark: Mark
    game: GameState


class CreatedGame(PlayerSession):
    invitation_url: str | None


@dataclass
class Game:
    game_id: str
    mode: Mode
    difficulty: Difficulty | None
    x_token: str
    invitation_token: str | None
    o_token: str | None = None
    board: list[Mark | None] = field(default_factory=lambda: [None] * 9)
    status: Status = "waiting"
    turn: Mark | None = "X"
    winner: Mark | None = None
    winning_cells: list[int] = field(default_factory=list)
    created_at: float = field(default_factory=monotonic)

    def state(self) -> GameState:
        return GameState(
            game_id=self.game_id,
            mode=self.mode,
            difficulty=self.difficulty,
            board=self.board.copy(),
            status=self.status,
            turn=self.turn if self.status == "playing" else None,
            winner=self.winner,
            winning_cells=self.winning_cells.copy(),
        )


def winning_line(board: list[Mark | None]) -> tuple[int, int, int] | None:
    for line in WINNING_LINES:
        if board[line[0]] is not None and board[line[0]] == board[line[1]] == board[line[2]]:
            return line
    return None


def minimax(board: list[Mark | None], turn: Mark) -> int:
    line = winning_line(board)
    if line is not None:
        return 1 if board[line[0]] == "O" else -1
    free = [cell for cell, mark in enumerate(board) if mark is None]
    if not free:
        return 0
    scores = []
    for cell in free:
        next_board = board.copy()
        next_board[cell] = turn
        scores.append(minimax(next_board, "X" if turn == "O" else "O"))
    return max(scores) if turn == "O" else min(scores)


def computer_move(board: list[Mark | None], difficulty: Difficulty) -> int:
    free = [cell for cell, mark in enumerate(board) if mark is None]
    if difficulty == "easy":
        return secrets.choice(free)
    if difficulty == "medium":
        for mark in ("O", "X"):
            for cell in free:
                next_board = board.copy()
                next_board[cell] = mark
                if winning_line(next_board) is not None:
                    return cell
        return secrets.choice(free)
    scores: dict[int, int] = {}
    for cell in free:
        next_board = board.copy()
        next_board[cell] = "O"
        scores[cell] = minimax(next_board, "X")
    return max(scores, key=lambda cell: scores[cell])


class GameStore:
    def __init__(self) -> None:
        self._games: dict[str, Game] = {}
        self._invitations: dict[str, str] = {}
        self._lock = Lock()

    def _remove_expired(self) -> None:
        now = monotonic()
        expired = [key for key, game in self._games.items()
                   if now - game.created_at >= GAME_LIFETIME_SECONDS]
        for key in expired:
            game = self._games.pop(key)
            if game.invitation_token is not None:
                self._invitations.pop(game.invitation_token, None)

    def _get(self, game_id: str) -> Game:
        self._remove_expired()
        game = self._games.get(game_id)
        if game is None:
            raise HTTPException(404, "Game not found or expired")
        return game

    def _invited_game(self, invitation_token: str) -> Game:
        self._remove_expired()
        game_id = self._invitations.get(invitation_token)
        if game_id is None:
            raise HTTPException(404, "Invitation not found or expired")
        return self._get(game_id)

    @staticmethod
    def _authenticate(game: Game, token: str) -> Mark:
        if not token.isascii():
            raise HTTPException(401, "Invalid player token")
        if secrets.compare_digest(game.x_token, token):
            return "X"
        if game.o_token is not None and secrets.compare_digest(game.o_token, token):
            return "O"
        raise HTTPException(401, "Invalid player token")

    def create(self, options: CreateGame) -> tuple[PlayerSession, str | None]:
        with self._lock:
            self._remove_expired()
            if len(self._games) >= MAX_GAMES:
                raise HTTPException(503, "Game capacity reached; try again later")
            invitation_token = secrets.token_urlsafe(32) if options.mode == "friend" else None
            game = Game(
                game_id=secrets.token_urlsafe(24),
                mode=options.mode,
                difficulty=options.difficulty if options.mode == "computer" else None,
                x_token=secrets.token_urlsafe(32),
                invitation_token=invitation_token,
                status="playing" if options.mode == "computer" else "waiting",
            )
            self._games[game.game_id] = game
            if invitation_token is not None:
                self._invitations[invitation_token] = game.game_id
            return PlayerSession(player_token=game.x_token, your_mark="X", game=game.state()), invitation_token

    def preview(self, invitation_token: str) -> GameState:
        with self._lock:
            return self._invited_game(invitation_token).state()

    def join(self, invitation_token: str) -> PlayerSession:
        with self._lock:
            game = self._invited_game(invitation_token)
            if game.status != "waiting":
                raise HTTPException(409, "Invitation already used")
            game.o_token = secrets.token_urlsafe(32)
            game.status = "playing"
            return PlayerSession(player_token=game.o_token, your_mark="O", game=game.state())

    def read(self, game_id: str, token: str) -> GameState:
        with self._lock:
            game = self._get(game_id)
            self._authenticate(game, token)
            return game.state()

    @staticmethod
    def _place(game: Game, cell: int, mark: Mark) -> None:
        game.board[cell] = mark
        line = winning_line(game.board)
        if line is not None:
            game.status = "won"
            game.winner = mark
            game.winning_cells = list(line)
            game.turn = None
        elif all(value is not None for value in game.board):
            game.status = "draw"
            game.turn = None
        else:
            game.turn = "O" if mark == "X" else "X"

    def move(self, game_id: str, token: str, cell: int) -> GameState:
        with self._lock:
            game = self._get(game_id)
            mark = self._authenticate(game, token)
            if game.status != "playing":
                raise HTTPException(409, "Game is not in progress")
            if game.turn != mark:
                raise HTTPException(409, "It is not your turn")
            if game.board[cell] is not None:
                raise HTTPException(409, "Cell is already occupied")
            self._place(game, cell, mark)
            if game.mode == "computer" and game.status == "playing":
                assert game.difficulty is not None
                self._place(game, computer_move(game.board, game.difficulty), "O")
            return game.state()
