from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from itertools import combinations
from collections import Counter
from typing import Optional
import random
import asyncio
import time

app = FastAPI(
    title="Poker Pro Backend",
    version="5.0"
)

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

AUTO_NEXT_HAND_DELAY = 5

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
# PLAYERS
# =========================================================

players = {}

# =========================================================
# GAME
# =========================================================

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

    "auto_hand_task": None,
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


def create_shuffled_deck():
    deck = create_deck()
    random.shuffle(deck)
    return deck


def rank_value(card):
    return RANKS.index(card[0]) + 2


def suit_value(card):
    return card[1]

# =========================================================
# PLAYER HELPERS
# =========================================================

def active_players():
    return [
        p for p in players.values()
        if not p["folded"]
    ]


def active_not_allin():
    return [
        p for p in players.values()
        if not p["folded"]
        and not p["all_in"]
        and p["chips"] > 0
    ]


def bot_players():
    return [
        p for p in players.values()
        if p["is_bot"]
    ]


def add_bots():

    while len(players) < MAX_PLAYERS:

        bot_number = len(bot_players())

        if bot_number >= len(BOT_NAMES):
            break

        bot_id = f"bot_{bot_number + 1}"

        if bot_id in players:
            break

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
                p["user_id"] ==
                game["current_player"]
            ),
        })

    return result

# =========================================================
# BETTING
# =========================================================

def highest_bet():

    bets = [
        p["bet"]
        for p in players.values()
        if not p["folded"]
    ]

    if not bets:
        return 0

    return max(bets)


def put_chips(player, amount):

    amount = int(amount or 0)

    if amount <= 0:
        return 0

    amount = min(
        amount,
        player["chips"]
    )

    player["chips"] -= amount
    player["bet"] += amount
    player["total_bet"] += amount

    if player["chips"] <= 0:

        player["chips"] = 0
        player["all_in"] = True

    return amount


def collect_bets():

    for p in players.values():

        if p["bet"] > 0:

            game["pot"] += p["bet"]
            p["bet"] = 0


def reset_round():

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
    game["turn_deadline"] = (
        now + TURN_TIME
    )


def time_left():

    if not game["turn_deadline"]:
        return 0

    return max(
        0,
        int(
            game["turn_deadline"]
            - time.time()
        )
    )


def next_actionable_player(
    from_user_id=None
):

    ids = list(players.keys())

    if not ids:
        return None

    if from_user_id in ids:

        start = ids.index(
            from_user_id
        )

    else:

        start = game["dealer_index"]

    for step in range(
        1,
        len(ids) + 1
    ):

        index = (
            start + step
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
        [rank_value(c) for c in cards],
        reverse=True
    )

    suits = [
        suit_value(c)
        for c in cards
    ]

    counts = Counter(values)

    flush = len(set(suits)) == 1

    unique = sorted(
        set(values),
        reverse=True
    )

    straight_high = None

    # Wheel A-2-3-4-5
    if 14 in unique:
        wheel = [14, 5, 4, 3, 2]

        if all(x in unique for x in wheel):
            straight_high = 5

    if straight_high is None:

        for high in range(14, 5 - 1, -1):

            required = [
                high - i
                for i in range(5)
            ]

            if all(
                x in unique
                for x in required
            ):

                straight_high = high
                break

    # Straight Flush
    if flush and straight_high:
        return (
            8,
            straight_high
        )

    # Four
    four = sorted(
        [
            value
            for value, count in counts.items()
            if count == 4
        ],
        reverse=True
    )

    if four:

        q = four[0]

        kicker = max(
            x for x in values
            if x != q
        )

        return (
            7,
            q,
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

        pair_options = [
            x
            for x in pairs
            if x != trip
        ]

        if pair_options:

            return (
                6,
                trip,
                pair_options[0]
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
            x
            for x in values
            if x != trip
        ][:2]

        return (
            3,
            trip,
            *kickers
        )

    # Two Pair
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
            x
            for x in values
            if x != p1
            and x != p2
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
            x
            for x in values
            if x != pair
        ][:3]

        return (
            1,
            pair,
            *kickers
        )

    # High Card
    return (
        0,
        *values
    )


def best_hand(cards):

    if len(cards) < 5:
        return evaluate_five(cards)

    best = None

    for combo in combinations(
        cards,
        5
    ):

        score = evaluate_five(
            list(combo)
        )

        if best is None or score > best:
            best = score

    return best

# =========================================================
# DEALING
# =========================================================

def deal_hole_cards():

    # Texas Hold'em:
    # each player receives exactly 2 cards.

    for _ in range(2):

        for p in players.values():

            if game["deck"]:

                p["cards"].append(
                    game["deck"].pop()
                )


def burn():

    if game["deck"]:
        game["deck"].pop()


def deal_flop():

    burn()

    game["community_cards"] = [
        game["deck"].pop(),
        game["deck"].pop(),
        game["deck"].pop(),
    ]

    game["stage"] = "flop"


def deal_turn():

    burn()

    game["community_cards"].append(
        game["deck"].pop()
    )

    game["stage"] = "turn"


def deal_river():

    burn()

    game["community_cards"].append(
        game["deck"].pop()
    )

    game["stage"] = "river"

# =========================================================
# BETTING ROUND LOGIC
# =========================================================

def round_complete():

    alive = active_players()

    if len(alive) <= 1:
        return True

    # If everybody remaining is all-in,
    # no more decisions are possible.
    if not active_not_allin():
        return True

    target = highest_bet()

    for p in alive:

        if p["all_in"]:
            continue

        if p["chips"] < 0:
            return False

        if p["bet"] != target:
            return False

        if p["user_id"] not in game["acted"]:
            return False

    return True


def prepare_next_street():

    # Move current street money to pot.
    collect_bets()

    alive = active_players()

    # Someone won by everyone else folding.
    if len(alive) <= 1:

        finish_hand()
        return

    # If all players are all-in,
    # deal remaining streets automatically.
    if not active_not_allin():

        if game["stage"] == "preflop":
            deal_flop()

        if game["stage"] == "flop":
            deal_turn()

        if game["stage"] == "turn":
            deal_river()

        if game["stage"] == "river":
            finish_hand()
            return

        # Keep dealing until river.
        prepare_next_street()
        return

    # Normal street progression.
    if game["stage"] == "preflop":

        deal_flop()

    elif game["stage"] == "flop":

        deal_turn()

    elif game["stage"] == "turn":

        deal_river()

    elif game["stage"] == "river":

        finish_hand()
        return

    else:

        return

    reset_round()

    # First player after dealer.
    pid = next_actionable_player(
        game["dealer_user_id"]
    )

    if pid:

        set_turn(pid)

# =========================================================
# SHOWDOWN
# =========================================================

def finish_hand():

    # Make sure all bets are in the pot.
    collect_bets()

    alive = active_players()

    if not alive:
        game["started"] = False
        game["current_player"] = None
        return

    # Only one player left.
    if len(alive) == 1:

        winner = alive[0]

        amount = game["pot"]

        winner["chips"] += amount

        game["winner"] = winner["name"]

        game["message"] = (
            f"{winner['name']} wins "
            f"{amount} chips"
        )

        game["pot"] = 0
        game["showdown"] = True
        game["started"] = False
        game["current_player"] = None

        schedule_next_hand()

        return

    results = []

    for p in alive:

        seven_cards = (
            p["cards"]
            + game["community_cards"]
        )

        score = best_hand(
            seven_cards
        )

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

    share = (
        game["pot"]
        // len(winners)
    )

    remainder = (
        game["pot"]
        % len(winners)
    )

    for i, winner in enumerate(winners):

        winner["chips"] += share

        if i < remainder:
            winner["chips"] += 1

    names = ", ".join(
        p["name"]
        for p in winners
    )

    game["winner"] = names

    game["message"] = (
        f"🏆 {names} won the pot"
    )

    game["pot"] = 0

    game["showdown"] = True
    game["started"] = False
    game["current_player"] = None

    schedule_next_hand()

# =========================================================
# AUTO NEXT HAND
# =========================================================

def schedule_next_hand():

    old_task = game.get(
        "auto_hand_task"
    )

    if old_task and not old_task.done():
        return

    game["auto_hand_task"] = (
        asyncio.create_task(
            auto_next_hand()
        )
    )


async def auto_next_hand():

    await asyncio.sleep(
        AUTO_NEXT_HAND_DELAY
    )

    if game["started"]:
        return

    eligible = [
        p
        for p in players.values()
        if p["chips"] > 0
    ]

    if len(eligible) < 2:
        return

    # Rotate dealer.
    ids = list(players.keys())

    if ids:

        game["dealer_index"] = (
            game["dealer_index"] + 1
        ) % len(ids)

    result = start_hand()

    if result["success"]:

        asyncio.create_task(
            bot_controller()
        )

# =========================================================
# START HAND
# =========================================================

def start_hand():

    eligible = [
        p
        for p in players.values()
        if p["chips"] > 0
    ]

    if len(eligible) < 2:

        return {
            "success": False,
            "message": "Need at least 2 players"
        }

    ids = list(players.keys())

    if not ids:

        return {
            "success": False,
            "message": "No players"
        }

    game["started"] = True
    game["stage"] = "preflop"

    game["deck"] = (
        create_shuffled_deck()
    )

    game["community_cards"] = []

    game["pot"] = 0

    game["current_player"] = None

    game["current_bet"] = 0

    game["acted"] = set()

    game["winner"] = None
    game["message"] = ""
    game["showdown"] = False

    game["hand_number"] += 1

    # Reset players.
    for p in players.values():

        p["cards"] = []
        p["folded"] = False
        p["all_in"] = False
        p["bet"] = 0
        p["total_bet"] = 0
        p["is_dealer"] = False

    # Make sure dealer index is valid.
    game["dealer_index"] %= len(ids)

    dealer_index = game["dealer_index"]

    small_index = (
        dealer_index + 1
    ) % len(ids)

    big_index = (
        dealer_index + 2
    ) % len(ids)

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

    game["dealer_user_id"] = (
        dealer["user_id"]
    )

    # Deal 2 cards to every player.
    deal_hole_cards()

    # Blinds.
    put_chips(
        small,
        SMALL_BLIND
    )

    put_chips(
        big,
        BIG_BLIND
    )

    game["current_bet"] = (
        BIG_BLIND
    )

    # Preflop action starts left
    # of big blind.
    first = next_actionable_player(
        big["user_id"]
    )

    if first:

        set_turn(first)

    else:

        # Everyone somehow all-in.
        prepare_next_street()

    return {
        "success": True,
        "message": "Hand started"
    }

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

    if (
        game["current_player"]
        != user_id
    ):

        return {
            "success": False,
            "message": "Not your turn"
        }

    player = players.get(
        user_id
    )

    if not player:

        return {
            "success": False,
            "message": "Player not found"
        }

    if player["folded"]:

        return {
            "success": False,
            "message": "Player folded"
        }

    if player["all_in"]:

        return {
            "success": False,
            "message": "Player is all-in"
        }

    action = (
        action or ""
    ).lower().strip()

    current = highest_bet()

    # =====================================================
    # FOLD
    # =====================================================

    if action == "fold":

        player["folded"] = True

        game["acted"].add(
            user_id
        )

    # =====================================================
    # CHECK
    # =====================================================

    elif action == "check":

        if player["bet"] != current:

            return {
                "success": False,
                "message": "You cannot check"
            }

        game["acted"].add(
            user_id
        )

    # =====================================================
    # CALL
    # =====================================================

    elif action == "call":

        needed = (
            current
            - player["bet"]
        )

        put_chips(
            player,
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
            target = int(
                amount or 0
            )
        except:
            target = 0

        minimum = (
            current
            + BIG_BLIND
        )

        if target < minimum:

            target = minimum

        if target <= player["bet"]:

            return {
                "success": False,
                "message": "Raise is too small"
            }

        needed = (
            target
            - player["bet"]
        )

        if needed >= player["chips"]:

            put_chips(
                player,
                player["chips"]
            )

        else:

            put_chips(
                player,
                needed
            )

        game["current_bet"] = max(
            game["current_bet"],
            player["bet"]
        )

        # A raise means everyone else
        # has to act again.
        game["acted"] = {
            user_id
        }

    # =====================================================
    # ALL-IN
    # =====================================================

    elif action == "allin":

        old_current = current

        put_chips(
            player,
            player["chips"]
        )

        if player["bet"] > old_current:

            game["current_bet"] = (
                player["bet"]
            )

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
    # ONE PLAYER REMAINS
    # =====================================================

    if len(active_players()) <= 1:

        finish_hand()

        return {
            "success": True,
            "message": "Hand finished"
        }

    # =====================================================
    # ROUND COMPLETE
    # =====================================================

    if round_complete():

        prepare_next_street()

        return {
            "success": True,
            "message": "Street advanced"
        }

    # =====================================================
    # NEXT PLAYER
    # =====================================================

    nxt = next_actionable_player(
        user_id
    )

    if nxt:

        set_turn(nxt)

    else:

        prepare_next_street()

    return {
        "success": True
    }

# =========================================================
# BOT AI
# =========================================================

def bot_strength(player):

    cards = (
        player["cards"]
        + game["community_cards"]
    )

    # Preflop
    if len(game["community_cards"]) == 0:

        if len(player["cards"]) < 2:
            return 0

        a = rank_value(
            player["cards"][0]
        )

        b = rank_value(
            player["cards"][1]
        )

        if a == b:

            if a >= 11:
                return 4

            return 3

        if a >= 13 or b >= 13:
            return 3

        if a >= 11 or b >= 11:
            return 2

        return 1

    # Postflop
    if len(cards) >= 5:

        score = best_hand(
            cards
        )

        return score[0]

    return 1


def bot_decision(player):

    current = highest_bet()

    to_call = (
        current
        - player["bet"]
    )

    strength = bot_strength(
        player
    )

    # Very strong
    if strength >= 4:

        if player["chips"] > to_call:

            return (
                "raise",
                current + BIG_BLIND
            )

        return (
            "call",
            0
        )

    # Medium
    if strength >= 2:

        roll = random.random()

        if roll < 0.20:

            return (
                "raise",
                current + BIG_BLIND
            )

        if to_call == 0:

            return (
                "check",
                0
            )

        return (
            "call",
            0
        )

    # Weak
    if to_call == 0:

        if random.random() < 0.15:

            return (
                "raise",
                current + BIG_BLIND
            )

        return (
            "check",
            0
        )

    if to_call <= BIG_BLIND:

        return (
            "call",
            0
        )

    return (
        "fold",
        0
    )


async def bot_controller():

    await asyncio.sleep(0.5)

    safety = 0

    while game["started"]:

        safety += 1

        if safety > 200:
            break

        pid = game["current_player"]

        if not pid:
            break

        player = players.get(
            pid
        )

        if not player:
            break

        if not player["is_bot"]:
            break

        await asyncio.sleep(
            random.uniform(
                1.0,
                2.5
            )
        )

        if not game["started"]:
            break

        if (
            game["current_player"]
            != pid
        ):
            continue

        action, amount = (
            bot_decision(player)
        )

        result = perform_action(
            pid,
            action,
            amount
        )

        if not result["success"]:
            break

        # If action moved to another bot,
        # loop continues automatically.

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

        if time_left() > 0:
            continue

        player = players.get(pid)

        if not player:
            continue

        current = highest_bet()

        # Auto check if possible,
        # otherwise fold.
        if player["bet"] == current:

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
# GAME STATE
# =========================================================

def game_state():

    return {
        "success": True,

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
            highest_bet(),

        "winner":
            game["winner"],

        "message":
            game["message"],

        "hand_number":
            game["hand_number"],

        "turn_time":
            time_left(),

        "turn_deadline":
            game["turn_deadline"],

        "showdown":
            game["showdown"],

        "players":
            public_players(),
    }

# =========================================================
# API
# =========================================================

@app.get("/")
def root():

    return {
        "status": "online",
        "message": "Poker Pro Backend",
        "version": "5.0"
    }


@app.get("/health")
def health():

    return {
        "status": "ok",
        "version": "5.0"
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

    # Fill remaining seats.
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

    return game_state()


@app.get("/my-cards")
def get_my_cards(
    user_id: str
):

    if user_id not in players:

        return {
            "success": False,
            "message": "Player not found"
        }

    return {
        "success": True,
        "cards":
            players[user_id]["cards"]
    }


@app.post("/start")
def start():

    if game["started"]:

        return {
            "success": False,
            "message": "Game already started",
            "game": game_state()
        }

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
def action(
    req: ActionRequest
):

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
# Normal game does not need this.
@app.post("/next-card")
def next_card():

    return {
        "success": False,
        "message":
            "Street advances automatically in Poker Pro v5"
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
    game["dealer_index"] = 0
    game["current_player"] = None
    game["current_bet"] = 0
    game["acted"] = set()
    game["hand_number"] = 0
    game["turn_started_at"] = None
    game["turn_deadline"] = None
    game["winner"] = None
    game["message"] = ""
    game["showdown"] = False

    return {
        "success": True,
        "message": "Game reset"
    }

# =========================================================
# WEBSOCKET
# =========================================================

@app.websocket("/ws")
async def websocket_endpoint(
    websocket: WebSocket
):

    await websocket.accept()

    try:

        while True:

            await websocket.send_json(
                game_state()
            )

            await asyncio.sleep(1)

    except WebSocketDisconnect:

        pass

    except Exception:

        pass

# =========================================================
# STARTUP
# =========================================================

@app.on_event("startup")
async def startup():

    asyncio.create_task(
        timer_watchdog()
    )
