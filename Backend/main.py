from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import random


app = FastAPI(
    title="Poker Pro Backend",
    version="2.0.0"
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
# SETTINGS
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


BOT_NAMES = [
    "Alex",
    "Daniel",
    "Mike",
    "Chris",
    "James"
]


# =========================================================
# PLAYERS
# =========================================================

players = {}


# =========================================================
# GAME
# =========================================================

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
    "message": "",
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
            deck.append(rank + suit)

    random.shuffle(deck)

    return deck


# =========================================================
# PLAYER HELPERS
# =========================================================

def active_players():

    return [
        p for p in players.values()
        if not p["folded"]
    ]


def real_players():

    return [
        p for p in players.values()
        if not p["is_bot"]
    ]


def bot_players():

    return [
        p for p in players.values()
        if p["is_bot"]
    ]


def player_list():

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
            "is_bot": player["is_bot"],
            "is_turn": (
                game["current_player"]
                == player["user_id"]
            )
        })

    return result


# =========================================================
# ADD BOTS
# =========================================================

def add_bots():

    existing_bot_count = len(
        bot_players()
    )

    needed = MAX_PLAYERS - len(players)

    for i in range(needed):

        bot_number = existing_bot_count + i

        if bot_number >= len(BOT_NAMES):
            break

        bot_id = f"bot_{bot_number + 1}"

        if bot_id in players:
            continue

        players[bot_id] = {
            "user_id": bot_id,
            "name": BOT_NAMES[bot_number],
            "chips": STARTING_CHIPS,
            "cards": [],
            "folded": False,
            "all_in": False,
            "bet": 0,
            "total_bet": 0,
            "is_bot": True,
        }


# =========================================================
# ROOT
# =========================================================

@app.get("/")
def root():

    return {
        "status": "online",
        "service": "Poker Pro Backend",
        "version": "2.0.0",
    }


# =========================================================
# HEALTH
# =========================================================

@app.get("/health")
def health():

    return {
        "status": "ok"
    }


# =========================================================
# JOIN
# =========================================================

@app.post("/join")
def join(data: JoinRequest):

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
            "total_bet": 0,
            "is_bot": False,
        }

    # Fill remaining seats with bots
    add_bots()

    return {
        "success": True,
        "player": players[data.user_id],
        "players": player_list(),
    }


# =========================================================
# PLAYERS
# =========================================================

@app.get("/players")
def get_players():

    return {
        "success": True,
        "players": player_list(),
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

    game["deck"] = create_deck()

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

    # Deal two cards to every player
    for _ in range(2):

        for player in players.values():

            if game["deck"]:
                player["cards"].append(
                    game["deck"].pop()
                )

    ids = list(players.keys())

    game["dealer_index"] %= len(ids)

    small_blind_index = (
        game["dealer_index"] + 1
    ) % len(ids)

    big_blind_index = (
        game["dealer_index"] + 2
    ) % len(ids)

    small_blind_player = players[
        ids[small_blind_index]
    ]

    big_blind_player = players[
        ids[big_blind_index]
    ]

    sb = min(
        SMALL_BLIND,
        small_blind_player["chips"]
    )

    bb = min(
        BIG_BLIND,
        big_blind_player["chips"]
    )

    small_blind_player["chips"] -= sb
    small_blind_player["bet"] = sb
    small_blind_player["total_bet"] = sb

    big_blind_player["chips"] -= bb
    big_blind_player["bet"] = bb
    big_blind_player["total_bet"] = bb

    game["pot"] = sb + bb
    game["current_bet"] = bb

    first_index = (
        big_blind_index + 1
    ) % len(ids)

    game["current_player"] = ids[first_index]

    return {
        "success": True,
        "started": True,
        "stage": game["stage"],
        "community_cards": [],
        "pot": game["pot"],
        "current_player": game["current_player"],
        "current_bet": game["current_bet"],
        "winner": None,
        "message": "Game started",
        "players": player_list(),
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
        "players": player_list(),
    }


# =========================================================
# MY CARDS
# =========================================================

@app.get("/my-cards")
def get_my_cards(user_id: str):

    if user_id not in players:

        return {
            "success": False,
            "message": "Player not found"
        }

    return {
        "success": True,
        "cards": players[user_id]["cards"],
    }


# =========================================================
# ACTION
# =========================================================

@app.post("/action")
def action(data: ActionRequest):

    if data.user_id not in players:

        return {
            "success": False,
            "message": "Player not found"
        }

    if not game["started"]:

        return {
            "success": False,
            "message": "Game has not started"
        }

    if game["current_player"] != data.user_id:

        return {
            "success": False,
            "message": "Not your turn"
        }

    player = players[data.user_id]

    action_name = data.action.lower()

    # -----------------------------------------------------
    # FOLD
    # -----------------------------------------------------

    if action_name == "fold":

        player["folded"] = True

        game["message"] = (
            f"{player['name']} folded"
        )

    # -----------------------------------------------------
    # CHECK
    # -----------------------------------------------------

    elif action_name == "check":

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

    elif action_name == "call":

        required = (
            game["current_bet"]
            - player["bet"]
        )

        required = min(
            required,
            player["chips"]
        )

        player["chips"] -= required
        player["bet"] += required
        player["total_bet"] += required

        game["pot"] += required

        if player["chips"] == 0:
            player["all_in"] = True

        game["message"] = (
            f"{player['name']} called"
        )

    # -----------------------------------------------------
    # RAISE
    # -----------------------------------------------------

    elif action_name == "raise":

        raise_amount = max(
            data.amount,
            BIG_BLIND
        )

        target_bet = (
            game["current_bet"]
            + raise_amount
        )

        required = (
            target_bet
            - player["bet"]
        )

        required = min(
            required,
            player["chips"]
        )

        player["chips"] -= required
        player["bet"] += required
        player["total_bet"] += required

        game["pot"] += required

        game["current_bet"] = player["bet"]

        if player["chips"] == 0:
            player["all_in"] = True

        game["message"] = (
            f"{player['name']} raised"
        )

    # -----------------------------------------------------
    # ALL IN
    # -----------------------------------------------------

    elif action_name == "allin":

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

    run_bots()

    return {
        "success": True,
        "action": action_name,
        "pot": game["pot"],
        "current_player": game["current_player"],
        "current_bet": game["current_bet"],
        "stage": game["stage"],
        "message": game["message"],
        "players": player_list(),
    }


# =========================================================
# TURN
# =========================================================

def advance_turn():

    ids = list(players.keys())

    if not ids:
        return

    if game["current_player"] not in ids:
        return

    current_index = ids.index(
        game["current_player"]
    )

    for step in range(1, len(ids) + 1):

        next_index = (
            current_index + step
        ) % len(ids)

        next_id = ids[next_index]

        next_player = players[next_id]

        if (
            not next_player["folded"]
            and not next_player["all_in"]
        ):

            game["current_player"] = next_id

            return

    game["current_player"] = None


# =========================================================
# BOT ENGINE
# =========================================================

def run_bots():

    safety = 0

    while (
        game["started"]
        and game["current_player"] is not None
        and safety < 10
    ):

        safety += 1

        current_id = game["current_player"]

        if current_id not in players:
            return

        bot = players[current_id]

        if not bot["is_bot"]:
            return

        if bot["folded"] or bot["all_in"]:

            advance_turn()
            continue

        # Random bot personality
        decision = random.choice([
            "call",
            "call",
            "check",
            "raise",
            "fold"
        ])

        # Bot cannot check if behind
        if (
            decision == "check"
            and bot["bet"] < game["current_bet"]
        ):
            decision = "call"

        # Fold is less common
        if decision == "fold":

            bot["folded"] = True

            game["message"] = (
                f"{bot['name']} folded"
            )

        elif decision == "check":

            game["message"] = (
                f"{bot['name']} checked"
            )

        elif decision == "call":

            required = (
                game["current_bet"]
                - bot["bet"]
            )

            required = min(
                required,
                bot["chips"]
            )

            bot["chips"] -= required
            bot["bet"] += required
            bot["total_bet"] += required

            game["pot"] += required

            if bot["chips"] == 0:
                bot["all_in"] = True

            game["message"] = (
                f"{bot['name']} called"
            )

        elif decision == "raise":

            raise_amount = BIG_BLIND

            target_bet = (
                game["current_bet"]
                + raise_amount
            )

            required = (
                target_bet
                - bot["bet"]
            )

            required = min(
                required,
                bot["chips"]
            )

            bot["chips"] -= required
            bot["bet"] += required
            bot["total_bet"] += required

            game["pot"] += required

            if bot["bet"] > game["current_bet"]:

                game["current_bet"] = bot["bet"]

            if bot["chips"] == 0:
                bot["all_in"] = True

            game["message"] = (
                f"{bot['name']} raised"
            )

        advance_turn()

        # If it becomes user's turn, stop
        if game["current_player"] is None:
            break

        if (
            game["current_player"] in players
            and not players[
                game["current_player"]
            ]["is_bot"]
        ):
            break


# =========================================================
# NEXT CARD
# =========================================================

@app.post("/next-card")
def next_card():

    if not game["started"]:

        return {
            "success": False,
            "message": "Game has not started"
        }

    active = active_players()

    if len(active) <= 1:

        return {
            "success": False,
            "message": "Not enough active players"
        }

    if not game["deck"]:

        return {
            "success": False,
            "message": "Deck empty"
        }

    # FLOP
    if game["stage"] == "preflop":

        for _ in range(3):

            game["community_cards"].append(
                game["deck"].pop()
            )

        game["stage"] = "flop"

    # TURN
    elif game["stage"] == "flop":

        game["community_cards"].append(
            game["deck"].pop()
        )

        game["stage"] = "turn"

    # RIVER
    elif game["stage"] == "turn":

        game["community_cards"].append(
            game["deck"].pop()
        )

        game["stage"] = "river"

    # SHOWDOWN
    elif game["stage"] == "river":

        game["stage"] = "showdown"

        return {
            "success": True,
            "stage": "showdown",
            "community_cards": game["community_cards"],
            "pot": game["pot"],
            "players": player_list(),
        }

    # New betting round
    for player in players.values():
        player["bet"] = 0

    game["current_bet"] = 0

    # Find next player
    ids = list(players.keys())

    if ids:

        dealer = game["dealer_index"]

        for i in range(1, len(ids) + 1):

            index = (
                dealer + i
            ) % len(ids)

            candidate = players[
                ids[index]
            ]

            if (
                not candidate["folded"]
                and not candidate["all_in"]
            ):

                game["current_player"] = (
                    candidate["user_id"]
                )

                break

    # Bots play automatically
    run_bots()

    return {
        "success": True,
        "stage": game["stage"],
        "community_cards": game["community_cards"],
        "pot": game["pot"],
        "current_player": game["current_player"],
        "players": player_list(),
    }


# =========================================================
# RESET
# =========================================================

@app.post("/reset")
def reset():

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
async def websocket_endpoint(
    websocket: WebSocket
):

    await websocket.accept()

    connected_clients.add(
        websocket
    )

    try:

        while True:

            await websocket.receive_text()

            await websocket.send_json({

                "type": "game_update",

                "game": {
                    "started":
                        game["started"],

                    "stage":
                        game["stage"],

                    "community_cards":
                        game["community_cards"],

                    "pot":
                        game["pot"],

                    "current_player":
                        game["current_player"],

                    "current_bet":
                        game["current_bet"],

                    "winner":
                        game["winner"],
                },

                "players":
                    player_list(),
            })

    except WebSocketDisconnect:

        connected_clients.discard(
            websocket
        )


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
