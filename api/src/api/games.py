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
    mark: Mark = "X"


class Rematch(BaseModel):
    round_number: int = Field(ge=1, strict=True)


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
    round_number: int
    your_mark: Mark | None


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
    x_token: str | None
    invitation_token: str | None
    o_token: str | None = None
    board: list[Mark | None] = field(default_factory=lambda: [None] * 9)
    status: Status = "waiting"
    turn: Mark | None = "X"
    winner: Mark | None = None
    winning_cells: list[int] = field(default_factory=list)
    created_at: float = field(default_factory=monotonic)
    round_number: int = 1

    def state(self, your_mark: Mark | None = None) -> GameState:
        return GameState(
            game_id=self.game_id,
            mode=self.mode,
            difficulty=self.difficulty,
            board=self.board.copy(),
            status=self.status,
            turn=self.turn if self.status == "playing" else None,
            winner=self.winner,
            winning_cells=self.winning_cells.copy(),
            round_number=self.round_number,
            your_mark=your_mark,
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


def computer_move(board: list[Mark | None], difficulty: Difficulty, mark: Mark = "O") -> int:
    free = [cell for cell, mark in enumerate(board) if mark is None]
    if difficulty == "easy":
        return secrets.choice(free)
    if difficulty == "medium":
        for candidate in (mark, "X" if mark == "O" else "O"):
            for cell in free:
                next_board = board.copy()
                next_board[cell] = candidate
                if winning_line(next_board) is not None:
                    return cell
        return secrets.choice(free)
    scores: dict[int, int] = {}
    for cell in free:
        next_board = board.copy()
        next_board[cell] = mark
        scores[cell] = minimax(next_board, "X" if mark == "O" else "O")
    choose = max if mark == "O" else min
    return choose(scores, key=lambda cell: scores[cell])


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
        if game.x_token is not None and secrets.compare_digest(game.x_token, token):
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
            token = secrets.token_urlsafe(32)
            game = Game(
                game_id=secrets.token_urlsafe(24),
                mode=options.mode,
                difficulty=options.difficulty if options.mode == "computer" else None,
                x_token=token if options.mark == "X" else None,
                o_token=token if options.mark == "O" else None,
                invitation_token=invitation_token,
                status="playing" if options.mode == "computer" else "waiting",
            )
            self._games[game.game_id] = game
            if invitation_token is not None:
                self._invitations[invitation_token] = game.game_id
            elif options.mark == "O":
                assert game.difficulty is not None
                self._place(game, computer_move(game.board, game.difficulty, "X"), "X")
            return PlayerSession(player_token=token, your_mark=options.mark, game=game.state(options.mark)), invitation_token

    def preview(self, invitation_token: str) -> GameState:
        with self._lock:
            return self._invited_game(invitation_token).state()

    def join(self, invitation_token: str) -> PlayerSession:
        with self._lock:
            game = self._invited_game(invitation_token)
            if game.status != "waiting":
                raise HTTPException(409, "Invitation already used")
            token = secrets.token_urlsafe(32)
            mark: Mark = "O" if game.x_token is not None else "X"
            if mark == "O":
                game.o_token = token
            else:
                game.x_token = token
            game.status = "playing"
            return PlayerSession(player_token=token, your_mark=mark, game=game.state(mark))

    def read(self, game_id: str, token: str) -> GameState:
        with self._lock:
            game = self._get(game_id)
            mark = self._authenticate(game, token)
            return game.state(mark)

    def rematch(self, game_id: str, token: str, round_number: int) -> GameState:
        with self._lock:
            game = self._get(game_id)
            self._authenticate(game, token)
            if game.mode != "friend" or game.status not in ("won", "draw"):
                raise HTTPException(409, "A finished friend round is required")
            if game.round_number != round_number:
                raise HTTPException(409, "Round has already changed")
            game.x_token, game.o_token = game.o_token, game.x_token
            game.board = [None] * 9
            game.status = "playing"
            game.turn = "X"
            game.winner = None
            game.winning_cells = []
            game.round_number += 1
            return game.state(self._authenticate(game, token))

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
                computer_mark: Mark = "O" if mark == "X" else "X"
                self._place(game, computer_move(game.board, game.difficulty, computer_mark), computer_mark)
            return game.state(mark)
