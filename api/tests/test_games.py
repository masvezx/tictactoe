import unittest
from concurrent.futures import ThreadPoolExecutor
from functools import lru_cache
from time import monotonic
from unittest.mock import patch

from fastapi.testclient import TestClient

import api.main as main
from api.games import GAME_LIFETIME_SECONDS, GameStore, computer_move, winning_line


class GameApiTests(unittest.TestCase):
    def setUp(self):
        self.store_patch = patch.object(main, "store", GameStore())
        self.store_patch.start()
        self.addCleanup(self.store_patch.stop)
        self.client = TestClient(main.app)

    @staticmethod
    def headers(session):
        return {"Authorization": f"Bearer {session['player_token']}"}

    def create(self, mode="friend", difficulty="medium", mark="X"):
        response = self.client.post("/api/games", json={"mode": mode, "difficulty": difficulty, "mark": mark})
        self.assertEqual(response.status_code, 201)
        return response.json()

    def friend_game(self):
        host = self.create()
        response = self.client.post(host["invitation_url"] + "/join")
        self.assertEqual(response.status_code, 200)
        return host, response.json()

    def move(self, host, player, cell):
        return self.client.post(
            f"/api/games/{host['game']['game_id']}/moves",
            json={"cell": cell}, headers=self.headers(player),
        )

    def test_new_links_and_single_guest(self):
        host = self.create()
        another = self.create()
        self.assertNotEqual(host["invitation_url"], another["invitation_url"])
        self.assertNotEqual(host["player_token"], another["player_token"])
        preview = self.client.get(host["invitation_url"])
        self.assertEqual(preview.json()["status"], "waiting")
        self.assertNotIn("player_token", preview.json())
        self.assertEqual(self.move(host, host, 0).status_code, 409)
        guest = self.client.post(host["invitation_url"] + "/join").json()
        self.assertEqual(guest["your_mark"], "O")
        self.assertNotEqual(host["player_token"], guest["player_token"])
        self.assertEqual(self.client.post(host["invitation_url"] + "/join").status_code, 409)

    def test_two_simultaneous_joins_claim_only_one_seat(self):
        host = self.create()
        with ThreadPoolExecutor(max_workers=2) as pool:
            responses = list(pool.map(
                lambda _: self.client.post(host["invitation_url"] + "/join"), range(2),
            ))
        self.assertEqual(sorted(response.status_code for response in responses), [200, 409])

    def test_two_simultaneous_moves_use_only_one_turn(self):
        host, guest = self.friend_game()
        with ThreadPoolExecutor(max_workers=2) as pool:
            responses = list(pool.map(lambda cell: self.move(host, host, cell), (0, 1)))
        self.assertEqual(sorted(response.status_code for response in responses), [200, 409])
        state = self.client.get(
            f"/api/games/{host['game']['game_id']}", headers=self.headers(guest),
        ).json()
        self.assertEqual(state["board"].count("X"), 1)
        self.assertEqual(state["turn"], "O")

    def test_authentication_and_input_validation(self):
        host, guest = self.friend_game()
        path = f"/api/games/{host['game']['game_id']}"
        self.assertEqual(self.client.get(path).status_code, 401)
        self.assertEqual(self.client.get(path, headers={"Authorization": "Bearer wrong"}).status_code, 401)
        self.assertEqual(self.client.get(
            path, headers=[(b"authorization", b"Bearer \xc3\xa9")],
        ).status_code, 401)
        other = self.create()
        self.assertEqual(self.move(host, other, 0).status_code, 401)
        for cell in (-1, 9, True, "0", 1.5):
            with self.subTest(cell=cell):
                self.assertEqual(self.move(host, host, cell).status_code, 422)
        self.assertEqual(self.move(host, guest, 0).status_code, 409)
        self.assertEqual(self.move(host, host, 0).status_code, 200)
        self.assertEqual(self.move(host, host, 1).status_code, 409)
        self.assertEqual(self.move(host, guest, 0).status_code, 409)
        state = self.client.get(path, headers=self.headers(guest)).json()
        self.assertEqual(state["board"], ["X"] + [None] * 8)
        self.assertEqual(state["turn"], "O")
        self.assertEqual(self.client.get("/api/games/missing", headers=self.headers(host)).status_code, 404)
        self.assertEqual(self.client.post("/api/invitations/missing/join").status_code, 404)
        self.assertEqual(self.client.post("/api/games", json={"mode": "invalid"}).status_code, 422)
        self.assertEqual(self.client.post("/api/games", json={"mode": "computer", "difficulty": "invalid"}).status_code, 422)

    def test_friend_win_and_no_moves_after_finish(self):
        host, guest = self.friend_game()
        for player, cell in ((host, 0), (guest, 3), (host, 1), (guest, 4), (host, 2)):
            response = self.move(host, player, cell)
            self.assertEqual(response.status_code, 200)
        state = response.json()
        self.assertEqual((state["status"], state["winner"], state["turn"]), ("won", "X", None))
        self.assertEqual(state["winning_cells"], [0, 1, 2])
        self.assertEqual(self.move(host, guest, 5).status_code, 409)

    def test_friend_draw(self):
        host, guest = self.friend_game()
        for index, cell in enumerate((0, 1, 2, 4, 3, 5, 7, 6, 8)):
            response = self.move(host, host if index % 2 == 0 else guest, cell)
            self.assertEqual(response.status_code, 200)
        state = response.json()
        self.assertEqual((state["status"], state["winner"], state["turn"]), ("draw", None, None))
        self.assertEqual(state["winning_cells"], [])

    def test_host_can_choose_noughts_and_guest_starts(self):
        host = self.create(mark="O")
        guest = self.client.post(host["invitation_url"] + "/join").json()
        self.assertEqual(host["your_mark"], "O")
        self.assertEqual(guest["your_mark"], "X")
        self.assertEqual(self.move(host, host, 0).status_code, 409)
        self.assertEqual(self.move(host, guest, 0).json()["board"][0], "X")
        self.assertEqual(self.client.post("/api/games", json={"mode": "friend", "mark": "invalid"}).status_code, 422)

    def test_rematches_preserve_room_and_alternate_marks_and_first_player(self):
        for ending in ("won", "draw"):
            with self.subTest(ending=ending):
                host, guest = self.friend_game()
                path = f"/api/games/{host['game']['game_id']}"
                for round_number in (1, 2):
                    first, second = (host, guest) if round_number == 1 else (guest, host)
                    cells = (0, 3, 1, 4, 2) if ending == "won" else (0, 1, 2, 4, 3, 5, 7, 6, 8)
                    for index, cell in enumerate(cells):
                        response = self.move(host, first if index % 2 == 0 else second, cell)
                        self.assertEqual(response.status_code, 200)
                    self.assertEqual(response.json()["status"], ending)
                    response = self.client.post(path + "/rematch", headers=self.headers(host), json={"round_number": round_number})
                    self.assertEqual(response.status_code, 200)
                    state = response.json()
                    self.assertEqual(state["game_id"], host["game"]["game_id"])
                    self.assertEqual(state["round_number"], round_number + 1)
                    self.assertEqual(state["board"], [None] * 9)
                    self.assertEqual((state["status"], state["turn"], state["winner"], state["winning_cells"]), ("playing", "X", None, []))
                    self.assertEqual(state["your_mark"], "O" if round_number == 1 else "X")
                    guest_state = self.client.get(path, headers=self.headers(guest)).json()
                    self.assertEqual(guest_state["your_mark"], "X" if round_number == 1 else "O")
                    self.assertEqual(self.client.post(host["invitation_url"] + "/join").status_code, 409)
                    self.assertEqual(self.client.get(host["invitation_url"]).json()["round_number"], round_number + 1)
                self.assertEqual(self.move(host, guest, 0).status_code, 409)
                self.assertEqual(self.move(host, host, 0).status_code, 200)

    def test_rematch_validation_and_simultaneous_requests(self):
        host, guest = self.friend_game()
        path = f"/api/games/{host['game']['game_id']}/rematch"
        options = {"round_number": 1}
        self.assertEqual(self.client.post(path, json=options).status_code, 401)
        other = self.create()
        self.assertEqual(self.client.post(path, json=options, headers=self.headers(other)).status_code, 401)
        self.assertEqual(self.client.post(path, json=options, headers=self.headers(host)).status_code, 409)
        for player, cell in ((host, 0), (guest, 3), (host, 1), (guest, 4), (host, 2)):
            self.assertEqual(self.move(host, player, cell).status_code, 200)
        for number in (0, True, "1"):
            self.assertEqual(self.client.post(path, json={"round_number": number}, headers=self.headers(host)).status_code, 422)
        self.assertEqual(self.client.post(path, json={"round_number": 2}, headers=self.headers(host)).status_code, 409)
        with ThreadPoolExecutor(max_workers=2) as pool:
            responses = list(pool.map(lambda player: self.client.post(path, json=options, headers=self.headers(player)), (host, guest)))
        self.assertEqual(sorted(response.status_code for response in responses), [200, 409])
        # A delayed request must not reset an already finished subsequent round.
        for player, cell in ((guest, 0), (host, 3), (guest, 1), (host, 4), (guest, 2)):
            self.assertEqual(self.move(host, player, cell).status_code, 200)
        self.assertEqual(self.client.post(path, json=options, headers=self.headers(host)).status_code, 409)

    def test_computer_can_play_crosses_when_human_chooses_noughts(self):
        for difficulty in ("easy", "medium", "unbeatable"):
            with self.subTest(difficulty=difficulty):
                host = self.create("computer", difficulty, "O")
                self.assertEqual(host["your_mark"], "O")
                self.assertEqual(host["game"]["board"].count("X"), 1)
                self.assertEqual(host["game"]["turn"], "O")
                cell = host["game"]["board"].index(None)
                response = self.move(host, host, cell)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json()["board"].count("X"), 2)
                self.assertEqual(response.json()["board"][cell], "O")
                self.assertEqual(response.json()["turn"], "O")

    def test_computer_replies_in_same_request(self):
        for difficulty in ("easy", "medium", "unbeatable"):
            with self.subTest(difficulty=difficulty):
                host = self.create("computer", difficulty)
                self.assertIsNone(host["invitation_url"])
                state = self.move(host, host, 0).json()
                self.assertEqual(state["board"][0], "X")
                self.assertEqual(state["board"].count("O"), 1)
                self.assertEqual(state["turn"], "X")

    def test_computer_does_not_move_after_human_wins(self):
        host = self.create("computer", "easy")
        with patch("api.games.computer_move", side_effect=[3, 4]) as choose:
            for cell in (0, 1, 2):
                response = self.move(host, host, cell)
                self.assertEqual(response.status_code, 200)
        state = response.json()
        self.assertEqual((state["status"], state["winner"]), ("won", "X"))
        self.assertEqual(state["board"].count("O"), 2)
        self.assertEqual(choose.call_count, 2)

    def test_expired_invitation_and_game(self):
        host = self.create()
        with patch("api.games.monotonic", return_value=monotonic() + GAME_LIFETIME_SECONDS):
            self.assertEqual(self.client.get(host["invitation_url"]).status_code, 404)
            self.assertEqual(self.client.get(
                f"/api/games/{host['game']['game_id']}", headers=self.headers(host),
            ).status_code, 404)


class ComputerTests(unittest.TestCase):
    def test_medium_wins_before_blocking_and_blocks_threats(self):
        self.assertEqual(computer_move(["O", "O", None, "X", "X", None, None, None, "X"], "medium"), 2)
        self.assertEqual(computer_move(["X", "X", None, None, "O", None, None, None, None], "medium"), 2)

    def test_easy_uses_only_free_cells(self):
        board = ["X", "O", "X", "X", "O", "O", "O", "X", None]
        self.assertEqual(computer_move(board, "easy"), 8)

    def test_computer_crosses_win_before_blocking(self):
        board = ["X", "X", None, "O", "O", None, None, None, None]
        for difficulty in ("medium", "unbeatable"):
            self.assertEqual(computer_move(board, difficulty, "X"), 2)

    def test_unbeatable_crosses_never_lose(self):
        @lru_cache(maxsize=None)
        def explore(position):
            board = list(position)
            line = winning_line(board)
            if line is not None:
                self.assertEqual(board[line[0]], "X")
                return
            if None not in board:
                return
            board[computer_move(board, "unbeatable", "X")] = "X"
            if winning_line(board) is not None or None not in board:
                return
            for cell, value in enumerate(board):
                if value is None:
                    next_board = board.copy()
                    next_board[cell] = "O"
                    explore(tuple(next_board))

        explore((None,) * 9)

    def test_unbeatable_never_loses_against_any_human_moves(self):
        @lru_cache(maxsize=None)
        def explore(position):
            board = list(position)
            line = winning_line(board)
            if line is not None:
                self.assertNotEqual(board[line[0]], "X", f"Computer lost: {board}")
                return
            for cell, value in enumerate(board):
                if value is not None:
                    continue
                next_board = board.copy()
                next_board[cell] = "X"
                line = winning_line(next_board)
                self.assertIsNone(line, f"Human can win: {next_board}")
                if None in next_board:
                    computer_cell = computer_move(next_board, "unbeatable")
                    self.assertIsNone(next_board[computer_cell])
                    next_board[computer_cell] = "O"
                explore(tuple(next_board))

        explore((None,) * 9)


if __name__ == "__main__":
    unittest.main()
