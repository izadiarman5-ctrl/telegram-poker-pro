from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from itertools import combinations
from collections import Counter
from typing import Optional
import random
import asyncio
import time

app = FastAPI(title="Poker Pro", version="4.0")

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

# =========================================================
# STATE
# =========================================================

players = {}

game = {
    "started": False,
    "stage": "waiting",

    "deck": [],
    "community_cards": [],

    "pot": 0,

    "dealer_index": 0,

    "current_player": None,
    "current_bet": 0,

    "acted": set(),

    "hand_number": 0,

    "turn_started_at": None,
    "turn_deadline": None,

    "winner": None,
    "message": "",

    "showdown": False,
}

connections = set()

# =========================================================
# MODELS
# =========================================================

class JoinRequest(BaseModel):
    user_id: str
    name: str = "Player"


class ActionRequest(BaseModel):
    user_id: str
    action: str
    amount: Optional[int] = 0

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
# CARD
# =========================================================

def card_rank(card):
    return RANKS.index(card[0]) + 2


def card_suit(card):
    return card[1]

# =========================================================
# PLAYERS
# =========================================================

def active_players():
    return [
        p for p in players.values()
        if not p["folded"]
    ]


def actionable_players():
    return [
        p for p in players.values()
        if not p["folded"]
        and not p["all_in"]
        and p["chips"] > 0
    ]


def bots():
    return [
        p for p in players.values()
        if p["is_bot"]
    ]


def real_players():
    return [
        p for p in players.values()
        if not p["is_bot"]
    ]


def add_bots():

    while len(players) < MAX_PLAYERS:

        number = len(bots())

        if number >= len(BOT_NAMES):
            break

        bot_id = f"bot_{number + 1}"

        players[bot_id] = {
            "user_id": bot_id,
            "name": BOT_NAMES[number],
            "chips": STARTING_CHIPS,
            "cards": [],
            "folded": False,
            "all_in": False,
            "bet": 0,
            "total_bet": 0,
            "is_bot": True,
            "is_dealer": False,
        }


def public_players():

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
            "is_dealer": p["is_dealer"],
            "is_turn": (
                p["user_id"]
                == game["current_player"]
            ),
            "cards": (
                p["cards"]
                if not p["is_bot"]
                else []
            ),
        })

    return result

# =========================================================
# BETTING
# =========================================================

def highest_bet():

    values = [
        p["bet"]
        for p in players.values()
        if not p["folded"]
    ]

    return max(values) if values else 0


def put_chips(p, amount):

    amount = max(0, int(amount))

    amount = min(
        amount,
        p["chips"]
    )

    p["chips"] -= amount
    p["bet"] += amount
    p["total_bet"] += amount

    if p["chips"] == 0:
        p["all_in"] = True

    return amount


def collect_bets():

    for p in players.values():

        if p["bet"] > 0:

            game["pot"] += p["bet"]
            p["bet"] = 0


def reset_round_bets():

    for p in players.values():
        p["bet"] = 0

    game["current_bet"] = 0
    game["acted"] = set()

# =========================================================
# TURN
# =========================================================

def set_turn(user_id):

    game["current_player"] = user_id

    now = time.time()

    game["turn_started_at"] = now
    game["turn_deadline"] = now + TURN_TIME


def remaining_time():

    deadline = game["turn_deadline"]

    if not deadline:
        return 0

    return max(
        0,
        int(deadline - time.time())
    )


def next_player(start_id=None):

    ids = list(players.keys())

    if not ids:
        return None

    if start_id is None:
        start_id = game["current_player"]

    if start_id not in ids:
        start_index = 0
    else:
        start_index = ids.index(start_id)

    for offset in range(1, len(ids) + 1):

        index = (
            start_index + offset
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

# =========================================================
# HAND EVALUATION
# =========================================================

def evaluate_five(cards):

    values = sorted(
        [card_rank(c) for c in cards],
        reverse=True
    )

    suits = [
        card_suit(c)
        for c in cards
    ]

    counts = Counter(values)

    flush = len(set(suits)) == 1

    unique = sorted(
        set(values),
        reverse=True
    )

    straight_high = None

    if 14 in unique:
        unique.append(1)

    for i in range(len(unique) - 4):

        five = unique[i:i + 5]

        if five[0] - five[4] == 4:
            straight_high = five[0]
            break

    # Straight Flush
    if flush and straight_high:
        return (8, straight_high)

    # Four of a kind
    quads = sorted(
        [
            value
            for value, count in counts.items()
            if count == 4
        ],
        reverse=True
    )

    if quads:

        quad = quads[0]

        kicker = max(
            value
            for value in values
            if value != quad
        )

        return (
            7,
            quad,
            kicker
        )

    # Full House
    trips = sorted(
        [
            value
            for value, count in counts.items()
            if count >= 3
        ],
        reverse=True
    )

    pairs = sorted(
        [
            value
            for value, count in counts.items()
            if count >= 2
        ],
        reverse=True
    )

    if trips:

        trip = trips[0]

        pair_candidates = [
            value
            for value in pairs
            if value != trip
        ]

        if pair_candidates:

            return (
                6,
                trip,
                pair_candidates[0]
            )

    # Flush
    if flush:
        return (
            5,
            *values
        )

    # Straight
    if straight_high:
        return (
            4,
            straight_high
        )

    # Three of a kind
    if trips:

        trip = trips[0]

        kickers = [
            value
            for value in values
            if value != trip
        ][:2]

        return (
            3,
            trip,
            *kickers
        )

    # Two pair
    pair_values = sorted(
        [
            value
            for value, count in counts.items()
            if count == 2
        ],
        reverse=True
    )

    if len(pair_values) >= 2:

        p1 = pair_values[0]
        p2 = pair_values[1]

        kicker = max(
            value
            for value in values
            if value != p1
            and value != p2
        )

        return (
            2,
            p1,
            p2,
            kicker
        )

    # Pair
    if len(pair_values) == 1:

        pair = pair_values[0]

        kickers = [
            value
            for value in values
            if value != pair
        ][:3]

        return (
            1,
            pair,
            *kickers
        )

    # High card
    return (
        0,
        *values
    )


def best_hand(cards):

    if len(cards) < 5:
        return evaluate_five(cards)

    best = None

    for combo in combinations(cards, 5):

        score = evaluate_five(
            list(combo)
        )

        if (
            best is None
            or score > best
        ):
            best = score

    return best

# =========================================================
# SHOWDOWN
# =========================================================

def showdown():

    contenders = [
        p for p in players.values()
        if not p["folded"]
    ]

    if not contenders:
        return

    # One player remaining
    if len(contenders) == 1:

        winner = contenders[0]

        winner["chips"] += game["pot"]

        game["winner"] = winner["name"]
        game["message"] = (
            f"{winner['name']} wins "
            f"{game['pot']} chips"
        )

        game["pot"] = 0
        game["showdown"] = True

        return

    results = []

    for p in contenders:

        cards = (
            p["cards"]
            + game["community_cards"]
        )

        score = best_hand(cards)

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

    if not winners:
        return

    share = game["pot"] // len(winners)
    remainder = game["pot"] % len(winners)

    for index, winner in enumerate(winners):

        winner["chips"] += share

        if index < remainder:
            winner["chips"] += 1

    game["winner"] = ", ".join(
        winner["name"]
        for winner in winners
    )

    game["message"] = (
        f"Winner: {game['winner']}"
    )

    game["pot"] = 0
    game["showdown"] = True

# =========================================================
# DEAL
# =========================================================

def deal_hole_cards():

    for p in players.values():

        p["cards"] = [
            game["deck"].pop(),
            game["deck"].pop()
        ]


def burn_card():

    if game["deck"]:
        game["deck"].pop()


def deal_flop():

    burn_card()

    game["community_cards"] = [
        game["deck"].pop(),
        game["deck"].pop(),
        game["deck"].pop()
    ]

    game["stage"] = "flop"


def deal_turn():

    burn_card()

    game["community_cards"].append(
        game["deck"].pop()
    )

    game["stage"] = "turn"


def deal_river():

    burn_card()

    game["community_cards"].append(
        game["deck"].pop()
    )

    game["stage"] = "river"

# =========================================================
# BETTING ROUND
# =========================================================

def betting_round_finished():

    active = [
        p for p in players.values()
        if not p["folded"]
    ]

    if len(active) <= 1:
        return True

    actionable = [
        p for p in active
        if not p["all_in"]
    ]

    if not actionable:
        return True

    target = highest_bet()

    for p in actionable:

        if p["bet"] != target:
            return False

        if p["user_id"] not in game["acted"]:
            return False

    return True


def finish_current_street():

    collect_bets()

    if len(active_players()) <= 1:

        showdown()

        game["started"] = False

        return

    if game["stage"] == "preflop":

        deal_flop()

    elif game["stage"] == "flop":

        deal_turn()

    elif game["stage"] == "turn":

        deal_river()

    elif game["stage"] == "river":

        showdown()

        game["started"] = False

        return

    reset_round_bets()

    # First player after dealer
    ids = list(players.keys())

    if not ids:
        return

    dealer = game["dealer_index"]

    for offset in range(1, len(ids) + 1):

        index = (
            dealer + offset
        ) % len(ids)

        p = players[ids[index]]

        if (
            not p["folded"]
            and not p["all_in"]
            and p["chips"] > 0
        ):

            set_turn(
                p["user_id"]
            )

            return

# =========================================================
# ACTION
# =========================================================

def perform_action(
    user_id,
    action,
    amount=0
):

    if not game["started"]:

        return {
            "success": False,
            "message": "Game is not running"
        }

    if game["current_player"] != user_id:

        return {
            "success": False,
            "message": "Not your turn"
        }

    p = players.get(user_id)

    if not p:

        return {
            "success": False,
            "message": "Player not found"
        }

    if p["folded"] or p["all_in"]:

        return {
            "success": False,
            "message": "Invalid player"
        }

    # Timeout
    if remaining_time() <= 0:

        current = highest_bet()

        if p["bet"] == current:
            action = "check"
        else:
            action = "fold"

    current_bet = highest_bet()

    action = action.lower().strip()

    # =====================================================
    # FOLD
    # =====================================================

    if action == "fold":

        p["folded"] = True

        game["acted"].add(
            user_id
        )

    # =====================================================
    # CHECK
    # =====================================================

    elif action == "check":

        if p["bet"] != current_bet:

            return {
                "success": False,
                "message": "Cannot check"
            }

        game["acted"].add(
            user_id
        )

    # =====================================================
    # CALL
    # =====================================================

    elif action == "call":

        needed = (
            current_bet
            - p["bet"]
        )

        put_chips(
            p,
            needed
        )

        game["acted"].add(
            user_id
        )

    # =====================================================
    # RAISE
    # =====================================================

    elif action == "raise":

        try:
            target = int(amount or 0)
        except:
            target = 0

        minimum_raise = (
            current_bet
            + BIG_BLIND
        )

        target = max(
            target,
            minimum_raise
        )

        if target <= p["bet"]:

            return {
                "success": False,
                "message": "Raise is too small"
            }

        needed = (
            target
            - p["bet"]
        )

        if needed >= p["chips"]:

            put_chips(
                p,
                p["chips"]
            )

        else:

            put_chips(
                p,
                needed
            )

        game["current_bet"] = max(
            game["current_bet"],
            p["bet"]
        )

        # Everyone else must act again
        game["acted"] = {
            user_id
        }

    # =====================================================
    # ALL IN
    # =====================================================

    elif action == "allin":

        old_bet = p["bet"]

        put_chips(
            p,
            p["chips"]
        )

        if p["bet"] > current_bet:

            game["current_bet"] = p["bet"]

            game["acted"] = {
                user_id
            }

        else:

            game["acted"].add(
                user_id
            )

    else:

        return {
            "success": False,
            "message": "Unknown action"
        }

    # =====================================================
    # PLAYER FOLDED / ROUND END
    # =====================================================

    if len(active_players()) <= 1:

        collect_bets()

        showdown()

        game["started"] = False
        game["current_player"] = None

        return {
            "success": True
        }

    # =====================================================
    # STREET END
    # =====================================================

    if betting_round_finished():

        finish_current_street()

        return {
            "success": True
        }

    # =====================================================
    # NEXT PLAYER
    # =====================================================

    nxt = next_player(user_id)

    if nxt:

        set_turn(nxt)

    else:

        finish_current_street()

    return {
        "success": True
    }

# =========================================================
# START HAND
# =========================================================

def start_hand():

    eligible = [
        p for p in players.values()
        if p["chips"] > 0
    ]

    if len(eligible) < 2:

        return {
            "success": False,
            "message": "Need at least 2 players"
        }

    # Reset
    game["started"] = True
    game["stage"] = "preflop"
    game["deck"] = new_deck()
    game["community_cards"] = []
    game["pot"] = 0
    game["current_player"] = None
    game["current_bet"] = 0
    game["acted"] = set()
    game["winner"] = None
    game["message"] = ""
    game["showdown"] = False
    game["hand_number"] += 1

    for p in players.values():

        p["cards"] = []
        p["folded"] = False
        p["all_in"] = False
        p["bet"] = 0
        p["total_bet"] = 0
        p["is_dealer"] = False

    # Deal cards
    deal_hole_cards()

    ids = list(players.keys())

    # Move dealer
    game["dealer_index"] = (
        game["dealer_index"]
        % len(ids)
    )

    dealer_index = game["dealer_index"]

    ids_count = len(ids)

    small_index = (
        dealer_index + 1
    ) % ids_count

    big_index = (
        dealer_index + 2
    ) % ids_count

    dealer = players[
        ids[dealer_index]
    ]

    small = players[
        ids[small_index]
    ]

    big = players[
        ids[big_index]
    ]

    dealer["is_dealer"] = True

    # Small blind
    put_chips(
        small,
        SMALL_BLIND
    )

    # Big blind
    put_chips(
        big,
        BIG_BLIND
    )

    game["current_bet"] = (
        big["bet"]
    )

    # First action after BB
    first = None

    for offset in range(
        1,
        ids_count + 1
    ):

        index = (
            big_index + offset
        ) % ids_count

        p = players[
            ids[index]
        ]

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
        "success": True,
        "message": "Hand started"
    }

# =========================================================
# GAME STATE
# =========================================================

def game_state():

    return {
        "started": game["started"],
        "stage": game["stage"],
        "community_cards": game["community_cards"],
        "pot": game["pot"],
        "current_player": game["current_player"],
        "current_bet": highest_bet(),
        "winner": game["winner"],
        "message": game["message"],
        "hand_number": game["hand_number"],
        "turn_time": remaining_time(),
        "turn_deadline": game["turn_deadline"],
        "showdown": game["showdown"],
        "players": public_players(),
    }

# =========================================================
# API
# =========================================================

@app.get("/")
def root():

    return {
        "status": "online",
        "message": "Poker Pro Backend",
        "version": "4.0"
    }


@app.get("/health")
def health():

    return {
        "status": "ok",
        "version": "4.0"
    }


@app.post("/join")
def join(req: JoinRequest):

    if req.user_id in players:

        return {
            "success": True,
            "message": "Already joined",
            "players": public_players()
        }

    if len(players) >= MAX_PLAYERS:

        return {
            "success": False,
            "message": "Table is full"
        }

    players[req.user_id] = {
        "user_id": req.user_id,
        "name": req.name or "Player",
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
        "players": public_players()
    }


@app.get("/players")
def get_players():

    return {
        "success": True,
        "players": public_players()
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


@app.post("/start")
async def start():

    result = start_hand()

    if result["success"]:

        asyncio.create_task(
            bot_controller()
        )

    return {
        **result,
        "game": game_state()
    }


@app.post("/action")
async def action(req: ActionRequest):

    result = perform_action(
        req.user_id,
        req.action,
        req.amount or 0
    )

    if result["success"]:

        asyncio.create_task(
            bot_controller()
        )

    return {
        **result,
        "game": game_state()
    }


# Compatibility endpoint.
# Normal gameplay does NOT need this.
@app.post("/next-card")
def next_card():

    return {
        "success": False,
        "message": "Street advances automatically"
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
    game["acted"] = set()
    game["winner"] = None
    game["message"] = ""
    game["hand_number"] = 0
    game["turn_started_at"] = None
    game["turn_deadline"] = None
    game["showdown"] = False

    return {
        "success": True,
        "message": "Game reset"
    }

# =========================================================
# BOT AI
# =========================================================

def bot_decision(p):

    current = highest_bet()

    to_call = (
        current
        - p["bet"]
    )

    # Basic strength evaluation
    cards = (
        p["cards"]
        + game["community_cards"]
    )

    score = 0

    if len(cards) >= 5:

        hand = best_hand(cards)

        score = hand[0]

    else:

        if len(p["cards"]) == 2:

            r1 = card_rank(
                p["cards"][0]
            )

            r2 = card_rank(
                p["cards"][1]
            )

            if r1 == r2:
                score = 3
            elif r1 >= 12 or r2 >= 12:
                score = 2
            else:
                score = 1

    # Strong hand
    if score >= 4:

        if p["chips"] > to_call:

            return (
                "raise",
                current + BIG_BLIND
            )

        return (
            "call",
            0
        )

    # Medium hand
    if score >= 2:

        choices = [
            ("call", 0),
            ("call", 0),
            ("raise", current + BIG_BLIND),
        ]

        return random.choice(
            choices
        )

    # Weak hand
    if to_call == 0:

        choices = [
            ("check", 0),
            ("check", 0),
            ("raise", current + BIG_BLIND),
        ]

        return random.choice(
            choices
        )

    if to_call <= BIG_BLIND:

        return (
            "call",
            0
        )

    return (
        random.choice(
            ["fold", "call"]
        ),
        0
    )


async def bot_controller():

    # Prevent two bot controllers
    # from running simultaneously.
    await asyncio.sleep(0.3)

    safety = 0

    while game["started"]:

        safety += 1

        if safety > 100:
            break

        pid = game["current_player"]

        if not pid:
            break

        p = players.get(pid)

        if not p:
            break

        if not p["is_bot"]:
            break

        await asyncio.sleep(
            random.uniform(
                1.5,
                3.5
            )
        )

        if not game["started"]:
            break

        if game["current_player"] != pid:
            continue

        action, amount = bot_decision(p)

        result = perform_action(
            pid,
            action,
            amount
        )

        if not result["success"]:
            break

# =========================================================
# TIMER
# =========================================================

async def timer_watchdog():

    while True:

        await asyncio.sleep(1)

        if not game["started"]:
            continue

        pid = game["current_player"]

        if not pid:
            continue

        if remaining_time() > 0:
            continue

        p = players.get(pid)

        if not p:
            continue

        # Timeout rule:
        # Check when possible,
        # otherwise Fold.
        current = highest_bet()

        if p["bet"] == current:
            timeout_action = "check"
        else:
            timeout_action = "fold"

        perform_action(
            pid,
            timeout_action,
            0
        )

        if game["started"]:
            asyncio.create_task(
                bot_controller()
            )

# =========================================================
# WEBSOCKET
# =========================================================

@app.websocket("/ws")
async def websocket_endpoint(
    websocket: WebSocket
):

    await websocket.accept()

    connections.add(
        websocket
    )

    try:

        while True:

            await websocket.send_json(
                game_state()
            )

            await asyncio.sleep(1)

    except WebSocketDisconnect:

        connections.discard(
            websocket
        )

    except Exception:

        connections.discard(
            websocket
        )

# =========================================================
# STARTUP
# =========================================================

@app.on_event("startup")
async def startup():

    asyncio.create_task(
        timer_watchdog()
    )
