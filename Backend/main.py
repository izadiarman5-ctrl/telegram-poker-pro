from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import random
from typing import Optional

app = FastAPI(title="Telegram Poker Pro Backend")


# =========================================================
# CORS
# =========================================================

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# =========================================================
# SETTINGS
# =========================================================

STARTING_CHIPS = 1000
SMALL_BLIND = 10
BIG_BLIND = 20

RANKS = "23456789TJQKA"
SUITS = ["♠", "♥", "♦", "♣"]


# =========================================================
# DATA
# =========================================================

players = {}

game = {
    "started": False,
    "deck": [],
    "community_cards": [],
    "pot": 0,
    "stage": "waiting",
    "current_player": None,
    "current_bet": 0,
    "dealer_index": 0,
    "acted_players": [],
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
    return [
        rank + suit
        for rank in RANKS
        for suit in SUITS
    ]


def new_deck():
    deck = create_deck()
    random.shuffle(deck)
    return deck


# =========================================================
# HELPERS
# =========================================================

def active_players():
    return [
        p for p in players.values()
        if not p["folded"] and p["chips"] > 0
    ]


def seated_players():
    return list(players.values())


def get_player_list():
    return list(players.values())


def next_player(current_id):
    ids = list(players.keys())

    if not ids:
        return None

    if current_id not in ids:
        return ids[0]

    index = ids.index(current_id)

    for step in range(1, len(ids) + 1):
        candidate = ids[(index + step) % len(ids)]

        p = players[candidate]

        if not p["folded"] and p["chips"] > 0:
            return candidate

    return None


def add_to_pot(player, amount):
    amount = min(amount, player["chips"])

    player["chips"] -= amount
    player["bet"] += amount
    player["total_bet"] += amount
    game["pot"] += amount

    if player["chips"] <= 0:
        player["all_in"] = True

    return amount


def reset_bets():
    for p in players.values():
        p["bet"] = 0


def reset_game_state():
    game["started"] = False
    game["deck"] = []
    game["community_cards"] = []
    game["pot"] = 0
    game["stage"] = "waiting"
    game["current_player"] = None
    game["current_bet"] = 0
    game["dealer_index"] = 0
    game["acted_players"] = []
    game["winner"] = None
    game["message"] = ""


# =========================================================
# DEAL PRIVATE CARDS
# =========================================================

def deal_private_cards():

    for p in players.values():
        p["cards"] = []

    for _ in range(2):
        for p in players.values():
            if not p["folded"]:
                if game["deck"]:
                    p["cards"].append(
                        game["deck"].pop()
                    )


# =========================================================
# COMMUNITY CARDS
# =========================================================

def deal_flop():
    if len(game["community_cards"]) >= 3:
        return False

    if len(game["deck"]) < 4:
        return False

    # Burn one
    game["deck"].pop()

    # Deal exactly 3 cards
    for _ in range(3):
        game["community_cards"].append(
            game["deck"].pop()
        )

    return True


def deal_turn():
    # Turn can only happen after exactly 3 flop cards
    if len(game["community_cards"]) != 3:
        return False

    if len(game["deck"]) < 2:
        return False

    # Burn one
    game["deck"].pop()

    # Deal EXACTLY ONE card
    game["community_cards"].append(
        game["deck"].pop()
    )

    return True


def deal_river():
    # River can only happen after exactly 4 cards
    if len(game["community_cards"]) != 4:
        return False

    if len(game["deck"]) < 2:
        return False

    # Burn one
    game["deck"].pop()

    # Deal EXACTLY ONE card
    game["community_cards"].append(
        game["deck"].pop()
    )

    return True


# =========================================================
# STAGE
# =========================================================

def set_stage(stage):
    game["stage"] = stage
    game["acted_players"] = []
    reset_bets()


def advance_stage():

    current_stage = game["stage"]

    # -----------------------------------------------------
    # PREFLOP -> FLOP
    # -----------------------------------------------------

    if current_stage == "preflop":

        if deal_flop():

            set_stage("flop")

            game["current_bet"] = 0

            game["current_player"] = first_active_player()

            game["message"] = "Flop dealt"

            return True

        return False

    # -----------------------------------------------------
    # FLOP -> TURN
    # -----------------------------------------------------

    if current_stage == "flop":

        # EXACTLY one card
        if deal_turn():

            set_stage("turn")

            game["current_bet"] = 0

            game["current_player"] = first_active_player()

            game["message"] = "Turn dealt"

            return True

        return False

    # -----------------------------------------------------
    # TURN -> RIVER
    # -----------------------------------------------------

    if current_stage == "turn":

        # EXACTLY one card
        if deal_river():

            set_stage("river")

            game["current_bet"] = 0

            game["current_player"] = first_active_player()

            game["message"] = "River dealt"

            return True

        return False

    # -----------------------------------------------------
    # RIVER -> WINNER
    # -----------------------------------------------------

    if current_stage == "river":

        determine_winner()

        return True

    return False


# =========================================================
# FIRST ACTIVE PLAYER
# =========================================================

def first_active_player():

    ids = list(players.keys())

    if not ids:
        return None

    start = game["dealer_index"]

    for i in range(len(ids)):

        index = (start + i) % len(ids)

        user_id = ids[index]

        p = players[user_id]

        if not p["folded"] and p["chips"] > 0:
            return user_id

    return None


# =========================================================
# WINNER
# =========================================================

def determine_winner():

    alive = [
        p for p in players.values()
        if not p["folded"]
    ]

    if not alive:
        game["stage"] = "winner"
        game["winner"] = None
        game["current_player"] = None
        game["message"] = "No winner"
        return

    # -----------------------------------------------------
    # Simple winner system
    #
    # This keeps the game flowing while the full
    # Texas Hold'em hand evaluator can be added later.
    # -----------------------------------------------------

    winner = random.choice(alive)

    winner["chips"] += game["pot"]

    won_amount = game["pot"]

    game["pot"] = 0
    game["stage"] = "winner"
    game["winner"] = winner["user_id"]
    game["current_player"] = None

    game["message"] = (
        f"{winner['name']} wins {won_amount} chips"
    )


# =========================================================
# BOT ACTION
# =========================================================

def bot_turn():

    if not game["started"]:
        return

    current_id = game["current_player"]

    if not current_id:
        return

    if current_id not in players:
        return

    p = players[current_id]

    # Human player
    if not current_id.startswith("bot_"):
        return

    if p["folded"] or p["chips"] <= 0:
        game["current_player"] = next_player(current_id)
        return

    # Random but simple bot decision
    choices = ["check", "call"]

    if game["current_bet"] > p["bet"]:
        action = random.choice(["call", "fold"])
    else:
        action = random.choice(choices)

    if action == "fold":

        p["folded"] = True
        game["acted_players"].append(current_id)

        game["message"] = (
            f"{p['name']} folded"
        )

    elif action == "call":

        required = (
            game["current_bet"] - p["bet"]
        )

        if required > 0:
            add_to_pot(p, required)

        game["acted_players"].append(current_id)

        game["message"] = (
            f"{p['name']} called"
        )

    else:

        game["acted_players"].append(current_id)

        game["message"] = (
            f"{p['name']} checked"
        )

    # Check if round is finished
    if betting_round_finished():

        advance_stage()

    else:

        game["current_player"] = next_player(
            current_id
        )


# =========================================================
# BETTING ROUND
# =========================================================

def betting_round_finished():

    alive = [
        p for p in players.values()
        if not p["folded"]
    ]

    if len(alive) <= 1:
        return True

    available = [
        p for p in alive
        if not p["all_in"] and p["chips"] > 0
    ]

    if not available:
        return True

    for p in available:

        if p["user_id"] not in game["acted_players"]:
            return False

        if p["bet"] != game["current_bet"]:
            return False

    return True


# =========================================================
# HOME
# =========================================================

@app.get("/")
def root():

    return {
        "status": "online",
        "message": "Poker backend is running",
        "version": "6.0"
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
def join(request: JoinRequest):

    user_id = str(request.user_id)

    if user_id in players:

        players[user_id]["name"] = request.name

    else:

        players[user_id] = {
            "user_id": user_id,
            "name": request.name,
            "chips": STARTING_CHIPS,
            "cards": [],
            "folded": False,
            "all_in": False,
            "bet": 0,
            "total_bet": 0,
        }

    return {
        "success": True,
        "player": players[user_id]
    }


# =========================================================
# PLAYERS
# =========================================================

@app.get("/players")
def get_players():

    result = []

    for p in players.values():

        item = dict(p)

        item["is_turn"] = (
            game["current_player"] ==
            p["user_id"]
        )

        result.append(item)

    return {
        "success": True,
        "players": result
    }


# =========================================================
# START
# =========================================================

@app.post("/start")
def start_game():

    if len(players) < 1:

        return {
            "success": False,
            "message": "No players"
        }

    # Reset player state
    for p in players.values():

        p["cards"] = []
        p["folded"] = False
        p["all_in"] = False
        p["bet"] = 0
        p["total_bet"] = 0

        if p["chips"] <= 0:
            p["chips"] = STARTING_CHIPS

    game["deck"] = new_deck()
    game["community_cards"] = []
    game["pot"] = 0
    game["current_bet"] = BIG_BLIND
    game["acted_players"] = []
    game["winner"] = None
    game["message"] = ""

    game["started"] = True
    game["stage"] = "preflop"

    deal_private_cards()

    ids = list(players.keys())

    dealer_index = game["dealer_index"] % len(ids)

    game["dealer_index"] = dealer_index

    # -----------------------------------------------------
    # Heads-up / simple blind setup
    # -----------------------------------------------------

    if len(ids) >= 2:

        small_id = ids[
            (dealer_index + 1) % len(ids)
        ]

        big_id = ids[
            (dealer_index + 2) % len(ids)
        ]

        small_player = players[small_id]
        big_player = players[big_id]

        add_to_pot(
            small_player,
            SMALL_BLIND
        )

        add_to_pot(
            big_player,
            BIG_BLIND
        )

        game["current_bet"] = BIG_BLIND

        game["current_player"] = next_player(
            big_id
        )

    else:

        player = players[ids[0]]

        add_to_pot(
            player,
            SMALL_BLIND
        )

        game["current_bet"] = SMALL_BLIND

        game["current_player"] = ids[0]

    game["message"] = "Preflop started"

    return {
        "success": True,
        "started": True,
        "stage": game["stage"],
        "community_cards": game["community_cards"],
        "pot": game["pot"],
        "current_player": game["current_player"],
        "current_bet": game["current_bet"],
        "winner": None,
        "message": game["message"],
        "dealer_index": game["dealer_index"]
    }


# =========================================================
# GAME
# =========================================================

@app.get("/game")
def get_game():

    current_name = None

    if game["current_player"] in players:

        current_name = players[
            game["current_player"]
        ]["name"]

    return {
        "success": True,
        "started": game["started"],
        "stage": game["stage"],
        "community_cards": game["community_cards"],
        "pot": game["pot"],
        "current_player": game["current_player"],
        "current_player_name": current_name,
        "current_bet": game["current_bet"],
        "winner": game["winner"],
        "message": game["message"],
        "dealer_index": game["dealer_index"]
    }


# =========================================================
# MY CARDS
# =========================================================

@app.get("/my-cards")
def my_cards(user_id: str):

    user_id = str(user_id)

    if user_id not in players:

        return {
            "success": False,
            "cards": [],
            "message": "Player not found"
        }

    return {
        "success": True,
        "cards": players[user_id]["cards"]
    }


# =========================================================
# ACTION
# =========================================================

@app.post("/action")
def player_action(request: ActionRequest):

    user_id = str(request.user_id)
    action = request.action.lower()
    amount = int(request.amount or 0)

    if user_id not in players:

        return {
            "success": False,
            "message": "Player not found"
        }

    if not game["started"]:

        return {
            "success": False,
            "message": "Game is not started"
        }

    if game["stage"] == "winner":

        return {
            "success": False,
            "message": "Game is finished"
        }

    if game["current_player"] != user_id:

        return {
            "success": False,
            "message": "Not your turn"
        }

    p = players[user_id]

    if p["folded"]:

        return {
            "success": False,
            "message": "Player folded"
        }

    # =====================================================
    # FOLD
    # =====================================================

    if action == "fold":

        p["folded"] = True

        game["acted_players"].append(
            user_id
        )

        alive = [
            x for x in players.values()
            if not x["folded"]
        ]

        if len(alive) == 1:

            winner = alive[0]

            winner["chips"] += game["pot"]

            won = game["pot"]

            game["pot"] = 0
            game["stage"] = "winner"
            game["winner"] = winner["user_id"]
            game["current_player"] = None

            game["message"] = (
                f"{winner['name']} wins {won} chips"
            )

        else:

            game["message"] = (
                f"{p['name']} folded"
            )

            game["current_player"] = next_player(
                user_id
            )

        return {
            "success": True,
            "action": "fold",
            "stage": game["stage"],
            "message": game["message"]
        }

    # =====================================================
    # CHECK
    # =====================================================

    if action == "check":

        if p["bet"] < game["current_bet"]:

            return {
                "success": False,
                "message": "Cannot check. Call required."
            }

        game["acted_players"].append(
            user_id
        )

        game["message"] = (
            f"{p['name']} checked"
        )

    # =====================================================
    # CALL
    # =====================================================

    elif action == "call":

        required = (
            game["current_bet"] -
            p["bet"]
        )

        if required < 0:
            required = 0

        if required > p["chips"]:
            required = p["chips"]

        add_to_pot(
            p,
            required
        )

        game["acted_players"].append(
            user_id
        )

        game["message"] = (
            f"{p['name']} called"
        )

    # =====================================================
    # RAISE
    # =====================================================

    elif action == "raise":

        if amount <= game["current_bet"]:

            return {
                "success": False,
                "message": "Raise must be higher than current bet"
            }

        required = (
            amount - p["bet"]
        )

        if required > p["chips"]:
            required = p["chips"]

        if required <= 0:

            return {
                "success": False,
                "message": "Invalid raise"
            }

        add_to_pot(
            p,
            required
        )

        game["current_bet"] = p["bet"]

        game["acted_players"] = [
            user_id
        ]

        game["message"] = (
            f"{p['name']} raised to {p['bet']}"
        )

    # =====================================================
    # ALL IN
    # =====================================================

    elif action == "allin":

        amount_to_put = p["chips"]

        if amount_to_put <= 0:

            return {
                "success": False,
                "message": "Already all-in"
            }

        add_to_pot(
            p,
            amount_to_put
        )

        if p["bet"] > game["current_bet"]:

            game["current_bet"] = p["bet"]

            game["acted_players"] = [
                user_id
            ]

        else:

            game["acted_players"].append(
                user_id
            )

        game["message"] = (
            f"{p['name']} is ALL-IN"
        )

    else:

        return {
            "success": False,
            "message": "Invalid action"
        }

    # =====================================================
    # END OF BETTING ROUND
    # =====================================================

    if betting_round_finished():

        if advance_stage():

            pass

        else:

            game["message"] = (
                "Could not advance stage"
            )

    else:

        game["current_player"] = next_player(
            user_id
        )

    # =====================================================
    # BOT
    # =====================================================

    # Run at most a few bot turns so we don't create
    # an infinite loop.
    for _ in range(6):

        current = game["current_player"]

        if not current:
            break

        if game["stage"] == "winner":
            break

        if not current.startswith("bot_"):
            break

        bot_turn()

    return {
        "success": True,
        "action": action,
        "stage": game["stage"],
        "community_cards": game["community_cards"],
        "pot": game["pot"],
        "current_player": game["current_player"],
        "current_bet": game["current_bet"],
        "winner": game["winner"],
        "message": game["message"]
    }


# =========================================================
# MANUAL NEXT
# =========================================================

@app.post("/next")
def manual_next():

    if not game["started"]:

        return {
            "success": False,
            "message": "Game is not started"
        }

    if game["stage"] == "winner":

        return {
            "success": False,
            "message": "Game is finished"
        }

    advanced = advance_stage()

    if not advanced:

        return {
            "success": False,
            "message": "Cannot advance stage"
        }

    return {
        "success": True,
        "stage": game["stage"],
        "community_cards": game["community_cards"],
        "pot": game["pot"],
        "current_player": game["current_player"],
        "winner": game["winner"],
        "message": game["message"]
    }


# =========================================================
# RESET
# =========================================================

@app.post("/reset")
def reset():

    reset_game_state()

    for p in players.values():

        p["cards"] = []
        p["folded"] = False
        p["all_in"] = False
        p["bet"] = 0
        p["total_bet"] = 0

    return {
        "success": True,
        "message": "Game reset"
    }
