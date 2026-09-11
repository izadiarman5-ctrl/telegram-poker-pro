from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional
import random
import uuid


app = FastAPI(
    title="Poker Pro Backend",
    version="1.0.0"
)


# =========================================================
# CORS
# =========================================================

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


# =========================================================
# GAME SETTINGS
# =========================================================

MAX_PLAYERS = 6
STARTING_CHIPS = 1000
SMALL_BLIND = 10
BIG_BLIND = 20

RANKS = [
    "2", "3", "4", "5", "6", "7",
    "8", "9", "T", "J", "Q", "K", "A"
]

SUITS = ["♠", "♥", "♦", "♣"]


# =========================================================
# GAME STATE
# =========================================================

players = {}

game = {
    "room_id": "main_table",
    "started": False,
    "stage": "waiting",
    "deck": [],
    "community_cards": [],
    "pot": 0,
    "current_player": None,
    "current_bet": 0,
    "dealer_index": 0,
    "winner": None,
    "message": ""
}


# =========================================================
# MODELS
# =========================================================

class JoinRequest(BaseModel):
    user_id: str
    name: str = "Player"


class ActionRequest(BaseModel):
    user_id: str
    action: str
    amount: int = 0


# =========================================================
# DECK
# =========================================================

def create_deck():
    deck = []

    for rank in RANKS:
        for suit in SUITS:
            deck.append(f"{rank}{suit}")

    random.shuffle(deck)

    return deck


def reset_deck():
    game["deck"] = create_deck()


# =========================================================
# PLAYER HELPERS
# =========================================================

def active_players():
    return [
        p for p in players.values()
        if not p["folded"]
    ]


def get_player_list():
    result = []

    for player in players.values():

        result.append({
            "user_id": player["user_id"],
            "name": player["name"],
            "chips": player["chips"],
            "cards": player["cards"],
            "bet": player["bet"],
            "folded": player["folded"],
            "all_in": player["all_in"],
            "is_turn": (
                game["current_player"] == player["user_id"]
            )
        })

    return result


# =========================================================
# ROUTES
# =========================================================

@app.get("/")
def root():

    return {
        "status": "online",
        "service": "Poker Pro Backend",
        "version": "1.0.0"
    }


@app.get("/health")
def health():

    return {
        "status": "ok"
    }


# =========================================================
# JOIN TABLE
# =========================================================

@app.post("/join")
def join_table(data: JoinRequest):

    if data.user_id not in players:

        if len(players) >= MAX_PLAYERS:

            return {
                "success": False,
                "message": "Table is full"
            }

        players[data.user_id] = {
            "user_id": data.user_id,
            "name": data.name,
            "chips": STARTING_CHIPS,
            "cards": [],
            "folded": False,
            "all_in": False,
            "bet": 0,
            "total_bet": 0
        }

    return {
        "success": True,
        "player": players[data.user_id],
        "players": get_player_list()
    }


# =========================================================
# PLAYERS
# =========================================================

@app.get("/players")
def players_route():

    return {
        "success": True,
        "players": get_player_list()
    }


# =========================================================
# START GAME
# =========================================================

@app.post("/start")
def start_game():

    if len(players) < 2:

        return {
            "success": False,
            "message": "Need at least 2 players"
        }

    reset_deck()

    game["started"] = True
    game["stage"] = "preflop"
    game["community_cards"] = []
    game["pot"] = 0
    game["winner"] = None
    game["message"] = ""

    # Reset players
    for player in players.values():

        player["cards"] = []
        player["folded"] = False
        player["all_in"] = False
        player["bet"] = 0
        player["total_bet"] = 0

    # Deal 2 cards
    for _ in range(2):

        for player in players.values():

            if game["deck"]:
                player["cards"].append(
                    game["deck"].pop()
                )

    player_ids = list(players.keys())

    # Dealer
    game["dealer_index"] %= len(player_ids)

    # Small blind
    sb_index = (
        game["dealer_index"] + 1
    ) % len(player_ids)

    # Big blind
    bb_index = (
        game["dealer_index"] + 2
    ) % len(player_ids)

    sb_player = players[player_ids[sb_index]]
    bb_player = players[player_ids[bb_index]]

    sb_amount = min(
        SMALL_BLIND,
        sb_player["chips"]
    )

    bb_amount = min(
        BIG_BLIND,
        bb_player["chips"]
    )

    sb_player["chips"] -= sb_amount
    sb_player["bet"] = sb_amount
    sb_player["total_bet"] = sb_amount

    bb_player["chips"] -= bb_amount
    bb_player["bet"] = bb_amount
    bb_player["total_bet"] = bb_amount

    game["pot"] = sb_amount + bb_amount
    game["current_bet"] = bb_amount

    # Player after big blind starts
    first_player_index = (
        bb_index + 1
    ) % len(player_ids)

    game["current_player"] = (
        player_ids[first_player_index]
    )

    return {
        "success": True,
        "started": True,
        "stage": game["stage"],
        "community_cards": game["community_cards"],
        "pot": game["pot"],
        "current_player": game["current_player"],
        "current_bet": game["current_bet"],
        "winner": None,
        "message": "Game started"
    }


# =========================================================
# GAME STATE
# =========================================================

@app.get("/game")
def get_game():

    return {
        "success": True,
        "started": game["started"],
        "stage": game["stage"],
        "community_cards": game["community_cards"],
        "pot": game["pot"],
        "current_player": game["current_player"],
        "current_bet": game["current_bet"],
        "winner": game["winner"],
        "message": game["message"],
        "dealer_index": game["dealer_index"],
        "players": get_player_list()
    }


# =========================================================
# MY CARDS
# =========================================================

@app.get("/my-cards")
def my_cards(user_id: str):

    if user_id not in players:

        return {
            "success": False,
            "message": "Player not found"
        }

    return {
        "success": True,
        "cards": players[user_id]["cards"]
    }


# =========================================================
# NEXT COMMUNITY CARD
# =========================================================

@app.post("/next-card")
def next_card():

    if not game["started"]:

        return {
            "success": False,
            "message": "Game has not started"
        }

    if len(active_players()) <= 1:

        return {
            "success": False,
            "message": "Not enough active players"
        }

    if game["stage"] == "preflop":

        if len(game["deck"]) < 3:

            return {
                "success": False,
                "message": "Not enough cards"
            }

        for _ in range(3):

            game["community_cards"].append(
                game["deck"].pop()
            )

        game["stage"] = "flop"

    elif game["stage"] == "flop":

        if not game["deck"]:

            return {
                "success": False,
                "message": "Deck empty"
            }

        game["community_cards"].append(
            game["deck"].pop()
        )

        game["stage"] = "turn"

    elif game["stage"] == "turn":

        if not game["deck"]:

            return {
                "success": False,
                "message": "Deck empty"
            }

        game["community_cards"].append(
            game["deck"].pop()
        )

        game["stage"] = "river"

    elif game["stage"] == "river":

        game["stage"] = "showdown"

        return {
            "success": True,
            "stage": game["stage"],
            "community_cards": game["community_cards"],
            "message": "Showdown"
        }

    return {
        "success": True,
        "stage": game["stage"],
        "community_cards": game["community_cards"],
        "pot": game["pot"]
    }


# =========================================================
# PLAYER ACTION
# =========================================================

@app.post("/action")
def player_action(data: ActionRequest):

    user_id = data.user_id
    action = data.action.lower()

    if user_id not in players:

        return {
            "success": False,
            "message": "Player not found"
        }

    if not game["started"]:

        return {
            "success": False,
            "message": "Game has not started"
        }

    if game["current_player"] != user_id:

        return {
            "success": False,
            "message": "Not your turn"
        }

    player = players[user_id]

    # -----------------------------------------------------
    # FOLD
    # -----------------------------------------------------

    if action == "fold":

        player["folded"] = True

        game["message"] = (
            f"{player['name']} folded"
        )

    # -----------------------------------------------------
    # CHECK
    # -----------------------------------------------------

    elif action == "check":

        if player["bet"] < game["current_bet"]:

            return {
                "success": False,
                "message": "Cannot check"
            }

        game["message"] = (
            f"{player['name']} checked"
        )

    # -----------------------------------------------------
    # CALL
    # -----------------------------------------------------

    elif action == "call":

        needed = (
            game["current_bet"] -
            player["bet"]
        )

        needed = min(
            needed,
            player["chips"]
        )

        player["chips"] -= needed
        player["bet"] += needed
        player["total_bet"] += needed
        game["pot"] += needed

        game["message"] = (
            f"{player['name']} called"
        )

    # -----------------------------------------------------
    # RAISE
    # -----------------------------------------------------

    elif action == "raise":

        raise_amount = max(
            data.amount,
            BIG_BLIND
        )

        new_bet = (
            game["current_bet"] +
            raise_amount
        )

        needed = new_bet - player["bet"]

        needed = min(
            needed,
            player["chips"]
        )

        player["chips"] -= needed
        player["bet"] += needed
        player["total_bet"] += needed
        game["pot"] += needed

        game["current_bet"] = player["bet"]

        game["message"] = (
            f"{player['name']} raised"
        )

    # -----------------------------------------------------
    # ALL IN
    # -----------------------------------------------------

    elif action == "allin":

        amount = player["chips"]

        player["chips"] = 0
        player["bet"] += amount
        player["total_bet"] += amount
        player["all_in"] = True

        game["pot"] += amount

        if player["bet"] > game["current_bet"]:

            game["current_bet"] = player["bet"]

        game["message"] = (
            f"{player['name']} is all-in"
        )

    else:

        return {
            "success": False,
            "message": "Invalid action"
        }

    advance_turn()

    return {
        "success": True,
        "action": action,
        "pot": game["pot"],
        "current_player": game["current_player"],
        "current_bet": game["current_bet"],
        "stage": game["stage"],
        "message": game["message"]
    }


# =========================================================
# TURN MANAGEMENT
# =========================================================

def advance_turn():

    player_ids = list(players.keys())

    if not player_ids:
        return

    current_index = player_ids.index(
        game["current_player"]
    )

    for i in range(1, len(player_ids) + 1):

        next_index = (
            current_index + i
        ) % len(player_ids)

        next_id = player_ids[next_index]

        player = players[next_id]

        if not player["folded"] and not player["all_in"]:

            game["current_player"] = next_id

            return

    # No player can act
    game["stage"] = "showdown"
    game["current_player"] = None


# =========================================================
# RESET GAME
# =========================================================

@app.post("/reset")
def reset_game():

    game["started"] = False
    game["stage"] = "waiting"
    game["deck"] = []
    game["community_cards"] = []
    game["pot"] = 0
    game["current_player"] = None
    game["current_bet"] = 0
    game["winner"] = None
    game["message"] = ""

    for player in players.values():

        player["cards"] = []
        player["folded"] = False
        player["all_in"] = False
        player["bet"] = 0
        player["total_bet"] = 0
        player["chips"] = STARTING_CHIPS

    return {
        "success": True,
        "message": "Game reset"
    }


# =========================================================
# WEBSOCKET
# =========================================================

connected_clients = set()


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):

    await websocket.accept()

    connected_clients.add(websocket)

    try:

        while True:

            await websocket.receive_text()

            state = {
                "type": "game_update",
                "game": {
                    "started": game["started"],
                    "stage": game["stage"],
                    "community_cards": game["community_cards"],
                    "pot": game["pot"],
                    "current_player": game["current_player"],
                    "current_bet": game["current_bet"],
                    "winner": game["winner"]
                },
                "players": get_player_list()
            }

            await websocket.send_json(state)

    except WebSocketDisconnect:

        connected_clients.discard(websocket)


# =========================================================
# SERVER
# =========================================================

if __name__ == "__main__":

    import uvicorn

    uvicorn.run(
        app,
        host="0.0.0.0",
        port=8000
    )
