# Tic-Tac-Toe API

FastAPI backend for a 3x3 game. No accounts are required. Test through `/docs`
or use the React frontend in [../web](../web/README.md).
The existing greeting endpoint is preserved.

## Run

```powershell
uv sync
uv run fastapi dev src\api\main.py
```

Open http://127.0.0.1:8000/docs. For another device on the same trusted network:

```powershell
uv run fastapi dev src\api\main.py --host 0.0.0.0
```

Both players must use the server's reachable LAN address, such as
`http://192.168.1.10:8000`, not `localhost` or `0.0.0.0`. Internet play requires
deployment on a reachable HTTPS server. Links use the address of the creation
request; behind a reverse proxy, configure the public host and trusted forwarded
headers correctly. This prototype is not hardened for unrestricted public use.

## Container

From the repository root:

```powershell
docker build --platform linux/amd64 -t tictactoe-api .\api
docker run --rm -p 8000:8000 tictactoe-api
```

Open http://localhost:8000/docs. The image installs dependencies from `uv.lock`,
runs as a non-root user, and starts Uvicorn on `0.0.0.0:8000` with one worker
and no development reload.

For Azure Container Apps, set the backend ingress target port to `8000`.
Use one replica (min and max both `1`) and one active revision without traffic
splitting: game state is in memory and is lost on restart or deployment.
If the frontend is in the same Container Apps environment, backend ingress can
be internal. Set the frontend container's `BACKEND_HOST` to the backend ingress
FQDN, without a scheme or path. nginx proxies `/api` over HTTPS, so browser CORS
is not needed. See the [frontend container instructions](../web/README.md).

## API flow

1. `POST /api/games` with `{"mode":"friend"}` creates a game. The response contains
   `game`, `your_mark: "X"`, a private `player_token`, and an `invitation_url` to copy
   and send. Every creation generates a fresh game, token, and invitation.
2. Opening the invitation with `GET` previews the game as JSON; it does not claim
   a seat. The React frontend turns it into a browser invitation with a copy
   button and a Join button. `POST <invitation_url>/join` claims the one guest seat and returns
   the guest's private token and `your_mark: "O"`. A used invitation cannot claim
   another seat. Anyone holding an unused invitation can claim it.
3. Each player keeps their own token and uses `Authorization: Bearer <player_token>`
   for `GET /api/games/{game_id}` and `POST /api/games/{game_id}/moves`.
   In Swagger `/docs`, click **Authorize** and enter the token.
4. Send `{"cell":0}` to make a move. Cells are zero-based, left-to-right:

   ```text
   0 1 2
   3 4 5
   6 7 8
   ```

   X starts after the guest joins. Poll the game endpoint about once per second
   while waiting for the other player. Moves return the updated game.
5. A finished game has `status: "won"` or `"draw"`, `turn: null`, and, for wins,
   `winner` and `winning_cells`. A rematch is a new `POST /api/games` request;
   share its new invitation. Old games remain unchanged.

Never share a player token with the opponent or put it in invitation URLs.
There is no token recovery: losing a token means starting a new game.

## Computer mode

Create with `{"mode":"computer","difficulty":"unbeatable"}`. The human is X,
the computer is O, and `invitation_url` is null. Each valid human move includes
the computer's response in the same API request, unless the game has ended.

- `easy`: random legal move.
- `medium` (default): win immediately, otherwise block an immediate win,
  otherwise choose a random legal move.
- `unbeatable`: minimax; the computer can win or draw, but cannot lose.

Difficulty applies only to computer games. The board and turn are managed by
the server, never accepted from clients.

## Errors and storage

- `401`: missing or invalid player token, including a token from another game.
- `404`: unknown or expired game/invitation.
- `409`: waiting/finished game, wrong turn, occupied cell, or already-used invitation.
- `422`: invalid mode, difficulty, or cell (only integers 0 through 8 are accepted).
- `503`: the in-memory limit of 1,000 games has been reached.

Games expire 24 hours after creation and are removed on subsequent requests.
All games disappear on server restart, including development auto-reload. Run
only **one worker/process**: in-memory games are not shared between workers.
Tokens and invitations are randomly generated; concurrent joins and moves are
serialized to prevent two clients from claiming the same seat or turn.
Cross-origin browser access is not enabled: serve the future UI from the same
origin, or explicitly configure trusted frontend origins when it is added.

## Tests

```powershell
uv run python -m unittest discover -s tests -v
```