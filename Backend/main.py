from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional
from collections import Counter
import random
import asyncio
import time

app = FastAPI(title="Poker Pro Backend", version="3.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# =========================
# SETTINGS
# =========================

MAX_PLAYERS = 6
STARTING_CHIPS = 1000

SMALL_BLIND = 10
BIG_BLIND = 20

TURN_TIME = 30

RANKS = "23456789TJQKA"
SUITS = ["♠", "♥", "♦", "♣"]

BOT_NAMES = [
    "Alex",
    "Daniel",
    "Mike",
    "Chris",
    "James",
]

# =========================
# STATE
# =========================

players = {}

game = {
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
    "hand_number": 0,
    "turn_started_at": None,
    "turn_deadline": None,
}

connections = set()


# =========================
# MODELS
# =========================

class JoinRequest(BaseModel):
    user_id: str
    name: str = "Player"


class ActionRequest(BaseModel):
    user_id: str
    action: str
    amount: Optional[int] = 0


# =========================
# DECK
# =========================

def create_deck():
    return [
        rank + suit
        for rank in RANKS
        for suit in SUITS
    ]


def shuffle_deck():
    deck = create_deck()
    random.shuffle(deck)
    return deck


# =========================
# CARD HELPERS
# =========================

def card_rank(card):
    return RANKS.index(card[0]) + 2


def card_suit(card):
    return card[1]


# =========================
# PLAYER HELPERS
# =========================

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


def active_players():
    return [
        p for p in players.values()
        if not p["folded"] and (p["chips"] > 0 or p["all_in"])
    ]


def playing_players():
    return [
        p for p in players.values()
        if not p["folded"]
    ]


def player_list():
    result = []

    for p in players.values():
        result.append({
            "user_id": p["user_id"],
            "name": p["name"],
            "chips": p["chips"],
            "bet": p["bet"],
            "total_bet": p["total_bet"],
            "folded": p["folded"],
            "all_in": p["all_in"],
            "is_bot": p["is_bot"],
            "is_turn": p["user_id"] == game["current_player"],
            "cards": p["cards"] if p["is_bot"] is False else [],
        })

    return result


# =========================
# BOTS
# =========================

def add_bots():

    while len(players) < MAX_PLAYERS:

        bot_number = len(bot_players())

        if bot_number >= len(BOT_NAMES):
            break

        bot_id = f"bot_{bot_number + 1}"

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


# =========================
# TURN SYSTEM
# =========================

def set_turn(user_id):

    game["current_player"] = user_id

    now = time.time()

    game["turn_started_at"] = now
    game["turn_deadline"] = now + TURN_TIME


def remaining_time():

    if not game["turn_deadline"]:
        return 0

    return max(
        0,
        int(game["turn_deadline"] - time.time())
    )


# =========================
# BETTING
# =========================

def highest_bet():

    if not players:
        return 0

    return max(
        p["bet"]
        for p in players.values()
        if not p["folded"]
    )


def collect_bets():

    for p in players.values():

        if p["bet"] > 0:

            game["pot"] += p["bet"]

            p["total_bet"] += p["bet"]
            p["bet"] = 0


# =========================
# HAND EVALUATION
# =========================

def evaluate_hand(cards):

    values = sorted(
        [card_rank(c) for c in cards],
        reverse=True
    )

    suits = [card_suit(c) for c in cards]

    counts = Counter(values)

    flush = len(set(suits)) == 1

    unique_values = sorted(set(values), reverse=True)

    straight_high = None

    if 14 in unique_values:
        unique_values.append(1)

    for i in range(len(unique_values) - 4):
        window = unique_values[i:i + 5]

        if window[0] - window[4] == 4:
            straight_high = window[0]
            break

    if straight_high and flush:
        return (8, straight_high)

    quads = [
        v for v, c in counts.items()
        if c == 4
    ]

    if quads:
        quad = max(quads)

        kicker = max(
            v for v in values
            if v != quad
        )

        return (7, quad, kicker)

    trips = sorted(
        [v for v, c in counts.items() if c >= 3],
        reverse=True
    )

    pairs = sorted(
        [v for v, c in counts.items() if c >= 2],
        reverse=True
    )

    if trips:

        trip = trips[0]

        remaining_pairs = [
            v for v in pairs
            if v != trip
        ]

        if remaining_pairs:
            return (6, trip, remaining_pairs[0])

    if flush:
        return (5, *values[:5])

    if straight_high:
        return (4, straight_high)

    if trips:
        trip = trips[0]

        kickers = [
            v for v in values
            if v != trip
        ][:2]

        return (3, trip, *kickers)

    pair_values = sorted(
        [v for v, c in counts.items() if c == 2],
        reverse=True
    )

    if len(pair_values) >= 2:

        p1 = pair_values[0]
        p2 = pair_values[1]

        kicker = max(
            v for v in values
            if v != p1 and v != p2
        )

        return (2, p1, p2, kicker)

    if len(pair_values) == 1:

        pair = pair_values[0]

        kickers = [
            v for v in values
            if v != pair
        ][:3]

        return (1, pair, *kickers)

    return (0, *values[:5])


def best_hand(cards):

    if len(cards) < 5:
        return evaluate_hand(cards)

    best = None

    from itertools import combinations

    for combo in combinations(cards, 5):

        score = evaluate_hand(list(combo))

        if best is None or score > best:
            best = score

    return best


# =========================
# WINNER
# =========================

def determine_winner():

    contenders = [
        p for p in players.values()
        if not p["folded"]
    ]

    if not contenders:
        return None

    if len(contenders) == 1:
        return contenders[0]

    results = []

    for p in contenders:

        all_cards = (
            p["cards"]
            + game["community_cards"]
        )

        score = best_hand(all_cards)

        results.append(
            (score, p)
        )

    results.sort(
        key=lambda x: x[0],
        reverse=True
    )

    best_score = results[0][0]

    winners = [
        p
        for score, p in results
        if score == best_score
    ]

    share = game["pot"] // len(winners)

    for winner in winners:
        winner["chips"] += share

    game["winner"] = ", ".join(
        p["name"]
        for p in winners
    )

    game["message"] = (
        f"Winner: {game['winner']}"
    )

    return winners


# =========================
# STREET
# =========================

def deal_cards():

    game["deck"] = shuffle_deck()

    for p in players.values():

        p["cards"] = [
            game["deck"].pop(),
            game["deck"].pop()
        ]

        p["folded"] = False
        p["all_in"] = False
        p["bet"] = 0
        p["total_bet"] = 0


def deal_flop():

    game["deck"].pop()

    game["community_cards"] = [
        game["deck"].pop(),
        game["deck"].pop(),
        game["deck"].pop(),
    ]

    game["stage"] = "flop"


def deal_turn():

    game["deck"].pop()

    game["community_cards"].append(
        game["deck"].pop()
    )

    game["stage"] = "turn"


def deal_river():

    game["deck"].pop()

    game["community_cards"].append(
        game["deck"].pop()
    )

    game["stage"] = "river"


# =========================
# NEXT PLAYER
# =========================

def next_player():

    ids = list(players.keys())

    if not ids:
        return None

    if game["current_player"] not in ids:
        return ids[0]

    current_index = ids.index(
        game["current_player"]
    )

    for i in range(1, len(ids) + 1):

        index = (
            current_index + i
        ) % len(ids)

        pid = ids[index]

        p = players[pid]

        if (
            not p["folded"]
            and not p["all_in"]
            and p["chips"] > 0
        ):
            return pid

    return None


# =========================
# STREET COMPLETE
# =========================

def betting_round_complete():

    active = [
        p for p in players.values()
        if not p["folded"]
        and not p["all_in"]
    ]

    if len(active) <= 1:
        return True

    target = highest_bet()

    for p in active:

        if p["bet"] != target:
            return False

    return False


# =========================
# ACTION
# =========================

def perform_action(user_id, action, amount=0):

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

    if remaining_time() <= 0:

        action = "fold"

    p = players.get(user_id)

    if not p:
        return {
            "success": False,
            "message": "Player not found"
        }

    if p["folded"] or p["all_in"]:
        return {
            "success": False,
            "message": "Invalid player state"
        }

    current_bet = highest_bet()

    # FOLD
    if action == "fold":

        p["folded"] = True

    # CHECK
    elif action == "check":

        if p["bet"] != current_bet:

            return {
                "success": False,
                "message": "Cannot check"
            }

    # CALL
    elif action == "call":

        needed = current_bet - p["bet"]

        if needed >= p["chips"]:

            p["bet"] += p["chips"]
            p["chips"] = 0
            p["all_in"] = True

        else:

            p["chips"] -= needed
            p["bet"] += needed

    # RAISE
    elif action == "raise":

        amount = int(amount or 0)

        target = max(
            current_bet + BIG_BLIND,
            amount
        )

        needed = target - p["bet"]

        if needed >= p["chips"]:

            p["bet"] += p["chips"]
            p["chips"] = 0
            p["all_in"] = True

        else:

            p["chips"] -= needed
            p["bet"] += needed

    # ALL IN
    elif action == "allin":

        p["bet"] += p["chips"]
        p["chips"] = 0
        p["all_in"] = True

    else:

        return {
            "success": False,
            "message": "Unknown action"
        }

    # One player left
    alive = [
        x for x in players.values()
        if not x["folded"]
    ]

    if len(alive) == 1:

        collect_bets()

        determine_winner()

        game["started"] = False

        return {
            "success": True,
            "message": "Hand finished"
        }

    # Continue
    collect_bets()

    nxt = next_player()

    if nxt:
        set_turn(nxt)

    return {
        "success": True
    }


# =========================
# BOT AI
# =========================

async def bot_turn():

    while game["started"]:

        pid = game["current_player"]

        if not pid:
            break

        p = players.get(pid)

        if not p:
            break

        if not p["is_bot"]:
            break

        await asyncio.sleep(
            random.uniform(1.5, 3.5)
        )

        if not game["started"]:
            break

        if remaining_time() <= 0:
            action = "fold"
        else:

            current_bet = highest_bet()

            if current_bet == p["bet"]:

                actions = [
                    "check",
                    "check",
                    "raise",
                ]

            else:

                actions = [
                    "call",
                    "call",
                    "fold",
                ]

            action = random.choice(actions)

        amount = (
            highest_bet() + BIG_BLIND
            if action == "raise"
            else 0
        )

        perform_action(
            pid,
            action,
            amount
        )

        # Stop if hand ended
        if not game["started"]:
            break


# =========================
# START HAND
# =========================

def start_hand():

    if len(players) < 2:

        return {
            "success": False,
            "message": "Need at least 2 players"
        }

    game["hand_number"] += 1

    game["started"] = True
    game["stage"] = "preflop"
    game["community_cards"] = []
    game["pot"] = 0
    game["winner"] = None
    game["message"] = ""

    deal_cards()

    ids = list(players.keys())

    dealer_index = (
        game["dealer_index"]
        % len(ids)
    )

    small_index = (
        dealer_index + 1
    ) % len(ids)

    big_index = (
        dealer_index + 2
    ) % len(ids)

    dealer = players[ids[dealer_index]]
    small = players[ids[small_index]]
    big = players[ids[big_index]]

    dealer["is_dealer"] = True

    for p in players.values():
        if p is not dealer:
            p["is_dealer"] = False

    # Small blind
    sb = min(
        SMALL_BLIND,
        small["chips"]
    )

    small["chips"] -= sb
    small["bet"] = sb

    if small["chips"] == 0:
        small["all_in"] = True

    # Big blind
    bb = min(
        BIG_BLIND,
        big["chips"]
    )

    big["chips"] -= bb
    big["bet"] = bb

    if big["chips"] == 0:
        big["all_in"] = True

    game["current_bet"] = bb

    # First action = player after big blind
    first = None

    for i in range(
        1,
        len(ids) + 1
    ):

        idx = (
            big_index + i
        ) % len(ids)

        p = players[ids[idx]]

        if (
            not p["folded"]
            and not p["all_in"]
            and p["chips"] > 0
        ):
            first = p["user_id"]
            break

    if first:
        set_turn(first)

    return {
        "success": True
    }


# =========================
# AUTO ADVANCE
# =========================

def advance_street():

    active = [
        p for p in players.values()
        if not p["folded"]
    ]

    if len(active) <= 1:

        collect_bets()
        determine_winner()
        game["started"] = False

        return

    # FLOP
    if game["stage"] == "preflop":

        collect_bets()
        deal_flop()

    # TURN
    elif game["stage"] == "flop":

        collect_bets()
        deal_turn()

    # RIVER
    elif game["stage"] == "turn":

        collect_bets()
        deal_river()

    # SHOWDOWN
    elif game["stage"] == "river":

        collect_bets()
        determine_winner()

        game["started"] = False

        return

    # Find next player
    nxt = next_player()

    if nxt:
        set_turn(nxt)


# =========================
# API
# =========================

@app.get("/")
def root():

    return {
        "status": "online",
        "message": "Poker Pro Backend",
        "version": "3.0"
    }


@app.get("/health")
def health():

    return {
        "status": "ok"
    }


@app.post("/join")
def join(req: JoinRequest):

    if req.user_id in players:

        return {
            "success": True,
            "message": "Already joined",
            "players": player_list()
        }

    if len(players) >= MAX_PLAYERS:

        return {
            "success": False,
            "message": "Table is full"
        }

    players[req.user_id] = {
        "user_id": req.user_id,
        "name": req.name,
        "chips": STARTING_CHIPS,
        "cards": [],
        "folded": False,
        "all_in": False,
        "bet": 0,
        "total_bet": 0,
        "is_bot": False,
        "is_dealer": False,
    }

    add_bots()

    return {
        "success": True,
        "players": player_list()
    }


@app.get("/players")
def get_players():

    return {
        "success": True,
        "players": player_list()
    }


@app.post("/start")
async def start():

    result = start_hand()

    if result.get("success"):

        asyncio.create_task(
            bot_turn()
        )

    return {
        **result,
        "game": game_state()
    }


@app.get("/game")
def get_game():

    return {
        "success": True,
        **game_state()
    }


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


@app.post("/action")
async def action(req: ActionRequest):

    result = perform_action(
        req.user_id,
        req.action.lower(),
        req.amount or 0
    )

    if result.get("success"):

        asyncio.create_task(
            bot_turn()
        )

    return {
        **result,
        "game": game_state()
    }


@app.post("/next-card")
async def next_card():

    if not game["started"]:

        return {
            "success": False,
            "message": "Game is not running"
        }

    advance_street()

    if game["started"]:

        asyncio.create_task(
            bot_turn()
        )

    return {
        "success": True,
        **game_state()
    }


@app.post("/reset")
def reset():

    global players

    players = {}

    game["started"] = False
    game["stage"] = "waiting"
    game["deck"] = []
    game["community_cards"] = []
    game["pot"] = 0
    game["current_player"] = None
    game["current_bet"] = 0
    game["winner"] = None
    game["message"] = ""
    game["turn_started_at"] = None
    game["turn_deadline"] = None

    return {
        "success": True,
        "message": "Game reset"
    }


# =========================
# GAME STATE
# =========================

def game_state():

    return {
        "started": game["started"],
        "stage": game["stage"],
        "community_cards": game["community_cards"],
        "pot": game["pot"],
        "current_player": game["current_player"],
        "current_bet": highest_bet()
        if players else 0,
        "winner": game["winner"],
        "message": game["message"],
        "hand_number": game["hand_number"],
        "turn_time": remaining_time(),
        "turn_deadline": game["turn_deadline"],
        "players": player_list(),
    }


# =========================
# WEBSOCKET
# =========================

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):

    await websocket.accept()

    connections.add(websocket)

    try:

        while True:

            await websocket.send_json(
                game_state()
            )

            await asyncio.sleep(1)

    except WebSocketDisconnect:

        connections.discard(websocket)

    except Exception:

        connections.discard(websocket)


# =========================
# TIMER WATCHDOG
# =========================

async def timer_watchdog():

    while True:

        await asyncio.sleep(1)

        if not game["started"]:
            continue

        pid = game["current_player"]

        if not pid:
            continue

        if remaining_time() <= 0:

            p = players.get(pid)

            if not p:
                continue

            # Automatic action
            if p["bet"] == highest_bet():
                action = "check"
            else:
                action = "fold"

            perform_action(
                pid,
                action,
                0
            )

            if game["started"]:

                asyncio.create_task(
                    bot_turn()
                )


@app.on_event("startup")
async def startup_event():

    asyncio.create_task(
        timer_watchdog()
    )
