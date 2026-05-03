"""
五子棋联机游戏 — 服务器端。
支持多房间、悔棋、认输、心跳检测、落子超时、计分持久化。
"""

import socket
import threading
import time
import os
from collections import namedtuple
from protocol import (
    recv_message, pack_message,
    CMD_CONNECT, CMD_PLACE, CMD_UNDO, CMD_RESIGN,
    CMD_BROADCAST, CMD_HEARTBEAT, CMD_ERROR, CMD_GAME_START, CMD_UNDO_RESULT,
    CMD_REMATCH, CMD_REMATCH_ACK, CMD_TIME_LIMIT,
)

HOST = "0.0.0.0"
PORT = 9527
BOARD_SIZE = 15
TIMEOUT_SECONDS = 30        # 落子超时
HEARTBEAT_TIMEOUT = 15      # 心跳超时
MAX_UNDO_PER_GAME = 3       # 每局每方最多悔棋次数
UNDO_REQUEST_TIMEOUT = 15   # 悔棋请求超时（秒）
SCORE_FILE = "score.txt"
MAX_NICKNAME_LEN = 20

# 方向向量：水平、垂直、左上-右下、右上-左下
DIRECTIONS = [(0, 1), (1, 0), (1, 1), (1, -1)]

Player = namedtuple("Player", ["conn", "addr", "nickname", "color"])


# ── 游戏房间 ──────────────────────────────────────────────
class GameRoom:
    """单个房间，包含棋盘状态、两名玩家。"""
    def __init__(self, room_id: int):
        self.room_id = room_id
        self.players: list[Player | None] = [None, None]  # 黑(1) / 白(2)
        self.board = [[0] * BOARD_SIZE for _ in range(BOARD_SIZE)]
        self.current_turn = 1           # 1=黑棋先行
        self.game_over = False
        self.winner: int | None = None  # 0=平局, 1=黑胜, 2=白胜
        self.last_move: tuple | None = None  # (row, col) 供悔棋用
        self.lock = threading.Lock()
        self.turn_start_time = 0.0
        self.undo_count = {1: 0, 2: 0}  # 每方已悔棋次数
        self.move_history: list[tuple[int, int, int]] = []  # [(color, row, col), ...]
        self.rematch_ready: set[int] = set()  # 已准备再来一局的玩家颜色 (1=黑,2=白)
        self.time_limit = TIMEOUT_SECONDS
        self.pending_undo: tuple[int, int] | None = None  # (请求方颜色, 响应方颜色)
        self.pending_undo_since = 0.0

    def reset(self):
        """重置房间状态，保留玩家。黑棋先行。"""
        self.board = [[0] * BOARD_SIZE for _ in range(BOARD_SIZE)]
        self.current_turn = 1
        self.game_over = False
        self.winner = None
        self.last_move = None
        self.turn_start_time = time.time()
        self.undo_count = {1: 0, 2: 0}
        self.move_history = []
        self.rematch_ready = set()
        self.time_limit = TIMEOUT_SECONDS
        self.pending_undo = None
        self.pending_undo_since = 0.0


# ── 游戏逻辑助手 ──────────────────────────────────────────
def check_win(board, row: int, col: int, color: int) -> bool:
    """仅检查 (row,col) 所在四个方向是否有五连子。"""
    for dr, dc in DIRECTIONS:
        count = 1
        # 正方向
        r, c = row + dr, col + dc
        while 0 <= r < BOARD_SIZE and 0 <= c < BOARD_SIZE and board[r][c] == color:
            count += 1
            r += dr; c += dc
        # 反方向
        r, c = row - dr, col - dc
        while 0 <= r < BOARD_SIZE and 0 <= c < BOARD_SIZE and board[r][c] == color:
            count += 1
            r -= dr; c -= dc
        if count >= 5:
            return True
    return False


def is_board_full(board) -> bool:
    for r in range(BOARD_SIZE):
        for c in range(BOARD_SIZE):
            if board[r][c] == 0:
                return False
    return True


def board_to_str(board) -> str:
    """棋盘状态序列化为字符串: 每行用逗号分隔，行间用分号分隔。"""
    rows = []
    for r in range(BOARD_SIZE):
        rows.append(",".join(str(board[r][c]) for c in range(BOARD_SIZE)))
    return ";".join(rows)


def str_to_board(s: str):
    """从字符串反序列化棋盘，返回二维列表。"""
    board = [[0] * BOARD_SIZE for _ in range(BOARD_SIZE)]
    rows = s.split(";")
    for r, row_str in enumerate(rows):
        if r >= BOARD_SIZE:
            break
        cols = row_str.split(",")
        for c, val in enumerate(cols):
            if c >= BOARD_SIZE:
                break
            board[r][c] = int(val)
    return board


def is_valid_nickname(nickname: str) -> bool:
    """昵称限制：非空、长度可控，且不含协议/存储分隔符。"""
    if not nickname or len(nickname) > MAX_NICKNAME_LEN:
        return False
    banned = {",", "|", "\n", "\r"}
    return not any(ch in nickname for ch in banned)


# ── 全局状态 ──────────────────────────────────────────────
rooms: dict[int, GameRoom] = {}
rooms_lock = threading.Lock()
next_room_id = 1
score_lock = threading.Lock()
server_running = True


# ── 计分持久化 ────────────────────────────────────────────
def load_scores() -> dict:
    """从 score.txt 加载计分。格式: 昵称,胜,平,负"""
    scores = {}
    if not os.path.exists(SCORE_FILE):
        return scores
    try:
        with open(SCORE_FILE, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                parts = line.split(",")
                if len(parts) == 4:
                    nickname, wins, draws, losses = parts
                    scores[nickname] = {
                        "wins": int(wins), "draws": int(draws), "losses": int(losses)
                    }
    except Exception:
        pass
    return scores


def save_scores(scores: dict):
    """将计分写入 score.txt。"""
    try:
        with open(SCORE_FILE, "w", encoding="utf-8") as f:
            for nick, rec in scores.items():
                f.write(f"{nick},{rec['wins']},{rec['draws']},{rec['losses']}\n")
    except Exception as e:
        print(f"[警告] 保存计分失败: {e}")


def update_score(nickname: str, key: str):
    """key: 'wins', 'draws', 'losses'"""
    with score_lock:
        scores = load_scores()
        if nickname not in scores:
            scores[nickname] = {"wins": 0, "draws": 0, "losses": 0}
        scores[nickname][key] += 1
        save_scores(scores)


# ── 房间匹配 ──────────────────────────────────────────────
def find_or_create_room() -> GameRoom:
    """为玩家找到可加入的房间（优先加入未满房间）。"""
    global next_room_id
    with rooms_lock:
        for room in rooms.values():
            with room.lock:
                if None in room.players and not room.game_over:
                    return room
        room = GameRoom(next_room_id)
        rooms[next_room_id] = room
        next_room_id += 1
        return room


def cleanup_room(room: GameRoom):
    """清理空房间。"""
    with rooms_lock:
        with room.lock:
            has_players = any(p is not None for p in room.players)
        if not has_players:
            rooms.pop(room.room_id, None)


# ── 广播函数 ──────────────────────────────────────────────
def send_to_player(player: Player, msg: bytes):
    """向单个玩家发送报文，自动处理异常。"""
    try:
        player.conn.sendall(msg)
    except Exception:
        pass


def broadcast_to_room(room: GameRoom, msg: bytes):
    """向房间内所有玩家广播。"""
    for p in room.players:
        if p is not None:
            send_to_player(p, msg)


def send_error(player: Player, error_msg: str):
    send_to_player(player, pack_message(CMD_ERROR, error_msg))


def broadcast_state(room: GameRoom):
    """广播棋盘状态及当前回合/结果。"""
    with room.lock:
        board_str = board_to_str(room.board)
        turn_str = {1: "黑棋", 2: "白棋"}.get(room.current_turn, "未知")
        if room.game_over:
            if room.winner == 0:
                result = "平局"
            elif room.winner == 1:
                result = "黑棋获胜"
            elif room.winner == 2:
                result = "白棋获胜"
            else:
                result = "未分胜负"
        else:
            result = "未分胜负"
        data = f"{board_str}|{turn_str}|{result}|{room.current_turn}|{room.time_limit}"
    broadcast_to_room(room, pack_message(CMD_BROADCAST, data))


def broadcast_time_limit(room: GameRoom, seconds: int, operator: str):
    """广播限时设置生效结果。"""
    broadcast_to_room(room, pack_message(CMD_TIME_LIMIT, f"{seconds}|{operator}"))


def broadcast_game_start(room: GameRoom):
    """通知房间内所有客户端游戏开始，带上各自颜色。"""
    for i, p in enumerate(room.players):
        if p is not None:
            color = i + 1  # 1=黑, 2=白
            color_name = {1: "黑棋", 2: "白棋"}[color]
            # 发送对手昵称和己方颜色
            opponent = room.players[1 - i]
            opp_name = opponent.nickname if opponent else "等待中"
            data = f"{color}|{color_name}|{opp_name}"
            send_to_player(p, pack_message(CMD_GAME_START, data))
    broadcast_state(room)


# ── 处理客户端逻辑 ────────────────────────────────────────
def handle_client(conn: socket.socket, addr):
    """子线程：处理单个客户端完整生命周期。"""
    print(f"[连接] {addr} 已连接")
    conn.settimeout(HEARTBEAT_TIMEOUT)
    player = Player(conn=conn, addr=addr, nickname="", color=0)
    room: GameRoom | None = None
    player_index: int | None = None

    try:
        # ── 等待 CONNECT 指令 ──
        result = recv_message(conn)
        if result is None:
            print(f"[连接] {addr} 未发送连接指令即断开")
            return
        cmd, nickname = result
        if cmd != CMD_CONNECT or not nickname.strip():
            conn.close()
            return
        nickname = nickname.strip()
        if not is_valid_nickname(nickname):
            try:
                conn.sendall(pack_message(
                    CMD_ERROR,
                    f"昵称不合法：长度需为1~{MAX_NICKNAME_LEN}，且不能包含逗号/竖线/换行。"
                ))
            except Exception:
                pass
            conn.close()
            return
        player = player._replace(nickname=nickname)
        print(f"[连接] {nickname} ({addr}) 加入")

        # 分配房间：仅用锁保护房间数据结构的更新，broadcast 函数内部自己加锁
        room = find_or_create_room()
        with room.lock:
            for i in range(2):
                if room.players[i] is None:
                    room.players[i] = player
                    player_index = i
                    player = player._replace(color=i + 1)
                    break
            is_game_ready = all(p is not None for p in room.players)
            if is_game_ready:
                room.turn_start_time = time.time()

        if is_game_ready:
            print(f"[房间{room.room_id}] 游戏开始: {room.players[0].nickname} vs {room.players[1].nickname}")
            broadcast_game_start(room)  # broadcast_game_start → broadcast_state 内部自带锁
        else:
            print(f"[房间{room.room_id}] {nickname} 作为玩家{player_index + 1}({player.color})加入")
            send_to_player(player, pack_message(CMD_ERROR, "等待对手加入..."))

        # ── 主消息循环 ──
        while True:
            result = recv_message(conn)
            if result is None:
                break
            cmd, payload = result

            if cmd == CMD_HEARTBEAT:
                # 心跳响应
                send_to_player(player, pack_message(CMD_HEARTBEAT))
                continue

            if room is None:
                continue

            # ── 落子 ──
            if cmd == CMD_PLACE:
                if player_index is None:
                    continue
                expired_undo_requester: Player | None = None
                with room.lock:
                    if room.game_over:
                        send_error(player, "游戏已结束")
                        continue
                    if room.current_turn != player.color:
                        send_error(player, "现在不是您的回合")
                        continue
                    try:
                        parts = payload.split(",")
                        row, col = int(parts[0]), int(parts[1])
                    except (ValueError, IndexError):
                        send_error(player, "落子数据格式错误")
                        continue
                    if not (0 <= row < BOARD_SIZE and 0 <= col < BOARD_SIZE):
                        send_error(player, "落子位置越界")
                        continue
                    if room.board[row][col] != 0:
                        send_error(player, "该位置已有棋子")
                        continue
                    # 若存在待处理悔棋，且被请求方选择直接落子，则请求自动失效
                    if room.pending_undo is not None:
                        requester_color, responder_color = room.pending_undo
                        if player.color == responder_color:
                            room.pending_undo = None
                            room.pending_undo_since = 0.0
                            expired_undo_requester = room.players[requester_color - 1]
                    # 合法落子
                    room.board[row][col] = player.color
                    room.last_move = (row, col)
                    room.move_history.append((player.color, row, col))
                    # 胜负判定
                    is_win = check_win(room.board, row, col, player.color)
                    print(f"[SERVER] {player.nickname} 放置 ({row}, {col})，颜色 {player.color}，五连检查: {is_win}")

                    if is_win:
                        room.game_over = True
                        room.winner = player.color
                        winner_name = player.nickname
                        loser_name = room.players[1 - player_index].nickname if room.players[1 - player_index] else ""
                        print(f"[SERVER] 房间{room.room_id} 游戏结束！{winner_name} 赢了")
                        update_score(winner_name, "wins")
                        if loser_name:
                            update_score(loser_name, "losses")
                    elif is_board_full(room.board):
                        room.game_over = True
                        room.winner = 0
                        update_score(room.players[0].nickname, "draws")
                        update_score(room.players[1].nickname, "draws")
                    else:
                        room.current_turn = 3 - room.current_turn  # 切换回合
                        room.turn_start_time = time.time()
                if expired_undo_requester:
                    send_to_player(expired_undo_requester, pack_message(CMD_UNDO, "expired"))
                    send_to_player(player, pack_message(CMD_UNDO, "expired"))
                broadcast_state(room)

            # ── 悔棋 ──
            elif cmd == CMD_UNDO:
                if player_index is None:
                    continue
                broadcast_after_undo = False
                with room.lock:
                    if room.game_over:
                        send_error(player, "游戏已结束，不能悔棋")
                        continue
                    action = payload.strip().lower() or "request"

                    # 发起悔棋请求：A 走完一步后，在 B 走之前，A 请求 B 同意
                    if action == "request":
                        if room.pending_undo is not None:
                            send_error(player, "已有待处理的悔棋请求")
                            continue
                        if room.last_move is None or not room.move_history:
                            send_error(player, "没有可以悔棋的步骤")
                            continue
                        last_color, _, _ = room.move_history[-1]
                        if last_color != player.color:
                            send_error(player, "只能请求悔掉自己刚刚落的一步")
                            continue
                        # 只能在对手尚未落子前申请：当前回合必须已经切到对手
                        if room.current_turn == player.color:
                            send_error(player, "请在对手回合开始后申请悔棋")
                            continue
                        if room.undo_count[player.color] >= MAX_UNDO_PER_GAME:
                            send_error(player, f"您本局已使用 {MAX_UNDO_PER_GAME} 次悔棋机会")
                            continue
                        responder_color = 3 - player.color
                        room.pending_undo = (player.color, responder_color)
                        room.pending_undo_since = time.time()
                        responder = room.players[responder_color - 1]
                        if responder:
                            send_to_player(
                                responder,
                                pack_message(CMD_UNDO, f"request|{player.nickname}")
                            )
                        send_to_player(player, pack_message(CMD_UNDO, "waiting_self"))
                        continue

                    # 对方同意 / 拒绝
                    if action in ("yes", "no"):
                        if room.pending_undo is None:
                            send_error(player, "当前没有待处理的悔棋请求")
                            continue
                        requester_color, responder_color = room.pending_undo
                        if player.color != responder_color:
                            send_error(player, "只有被请求方可以响应悔棋")
                            continue
                        requester = room.players[requester_color - 1]
                        room.pending_undo = None
                        room.pending_undo_since = 0.0

                        if action == "no":
                            if requester:
                                send_to_player(requester, pack_message(CMD_UNDO, "reject"))
                            send_to_player(player, pack_message(CMD_UNDO, "rejected"))
                            continue

                        # action == "yes": 仅撤销请求方刚刚那一步
                        if not room.move_history:
                            send_error(player, "没有可悔棋步")
                            continue
                        last_color, row, col = room.move_history[-1]
                        if last_color != requester_color:
                            send_error(player, "悔棋请求已失效")
                            continue
                        if not (0 <= row < BOARD_SIZE and 0 <= col < BOARD_SIZE):
                            send_error(player, "悔棋位置异常")
                            continue
                        room.board[row][col] = 0
                        room.move_history.pop()
                        room.last_move = room.move_history[-1][1:] if room.move_history else None
                        room.current_turn = requester_color
                        room.undo_count[requester_color] += 1
                        room.turn_start_time = time.time()
                        if requester:
                            send_to_player(requester, pack_message(CMD_UNDO, "accepted"))
                        send_to_player(player, pack_message(CMD_UNDO, "accepted"))
                        broadcast_after_undo = True
                    else:
                        send_error(player, "悔棋指令格式错误")
                        continue

                if broadcast_after_undo:
                    broadcast_state(room)
            
            # ── 认输 ──
            elif cmd == CMD_RESIGN:
                if player_index is None:
                    continue
                with room.lock:
                    if room.game_over:
                        send_error(player, "游戏已结束")
                        continue
                    room.game_over = True
                    room.winner = 3 - player.color  # 对手获胜
                    update_score(player.nickname, "losses")
                    opp = room.players[1 - player_index]
                    if opp:
                        update_score(opp.nickname, "wins")
                broadcast_state(room)

            # ── 再来一局 ──
            elif cmd == CMD_REMATCH:
                if player_index is None:
                    continue
                start_new_game = False
                with room.lock:
                    if not room.game_over:
                        send_error(player, "游戏尚未结束")
                        continue
                    choice = payload.strip().lower()
                    if choice == "yes":
                        room.rematch_ready.add(player.color)
                        opp = room.players[1 - player_index]
                        if opp:
                            send_to_player(opp, pack_message(
                                CMD_REMATCH_ACK, f"waiting|{player.color}"))
                        send_to_player(player, pack_message(
                            CMD_REMATCH_ACK, "waiting_self"))
                        if room.rematch_ready == {1, 2}:
                            room.reset()
                            start_new_game = True
                    elif choice == "no":
                        opp = room.players[1 - player_index]
                        if opp:
                            send_to_player(opp, pack_message(CMD_REMATCH_ACK, "reject"))
                        send_to_player(player, pack_message(CMD_REMATCH_ACK, "reject_self"))
                if start_new_game:
                    time.sleep(0.3)
                    broadcast_game_start(room)

            # ── 限时设置 ──
            elif cmd == CMD_TIME_LIMIT:
                if player_index is None:
                    continue
                with room.lock:
                    if room.game_over:
                        send_error(player, "游戏已结束，不能修改限时")
                        continue
                    if room.current_turn != player.color:
                        send_error(player, "仅当前回合玩家可修改限时")
                        continue
                    try:
                        seconds = int(payload.strip())
                    except ValueError:
                        send_error(player, "限时设置格式错误")
                        continue
                    if seconds not in (30, 60, 90):
                        send_error(player, "仅支持 30/60/90 秒")
                        continue
                    room.time_limit = seconds
                    room.turn_start_time = time.time()
                broadcast_time_limit(room, seconds, player.nickname)
                broadcast_state(room)

    except Exception as e:
        print(f"[异常] {player.nickname} ({addr}): {e}")
    finally:
        print(f"[断开] {player.nickname} ({addr}) 断开连接")
        if room is not None:
            if player_index is not None:
                with room.lock:
                    was_still_playing = not room.game_over
                    if was_still_playing:
                        room.game_over = True
                        room.winner = None  # 异常终止
                if was_still_playing:
                    opp = room.players[1 - player_index] if room.players else None
                    if opp:
                        send_to_player(opp, pack_message(CMD_ERROR, "对手已断开连接，游戏终止"))
                    broadcast_state(room)
                # 重置本方的 rematch 标记
                with room.lock:
                    room.rematch_ready.discard(player.color)
                    room.players[player_index] = None
            cleanup_room(room)
        try:
            conn.close()
        except Exception:
            pass


# ── 超时检测线程 ──────────────────────────────────────────
def timeout_monitor():
    """定时检查所有房间的落子超时。"""
    while server_running:
        time.sleep(5)
        broadcast_rooms: list[GameRoom] = []
        undo_timeout_pairs: list[tuple[Player | None, Player | None]] = []
        with rooms_lock:
            for room in list(rooms.values()):
                with room.lock:
                    # 悔棋请求超时：自动拒绝并清理
                    if room.pending_undo is not None and room.pending_undo_since > 0:
                        if time.time() - room.pending_undo_since > UNDO_REQUEST_TIMEOUT:
                            requester_color, responder_color = room.pending_undo
                            requester = room.players[requester_color - 1]
                            responder = room.players[responder_color - 1]
                            room.pending_undo = None
                            room.pending_undo_since = 0.0
                            undo_timeout_pairs.append((requester, responder))

                    if room.game_over or None in room.players:
                        continue
                    elapsed = time.time() - room.turn_start_time
                    if elapsed > room.time_limit:
                        current_color = room.current_turn
                        current_idx = 0 if room.players[0] and room.players[0].color == current_color else 1
                        timeout_player = room.players[current_idx]
                        if timeout_player:
                            print(f"[超时] {timeout_player.nickname} 落子超时")
                            room.game_over = True
                            room.winner = 3 - current_color
                            update_score(timeout_player.nickname, "losses")
                            opp = room.players[1 - current_idx]
                            if opp:
                                update_score(opp.nickname, "wins")
                            broadcast_rooms.append(room)
        for requester, responder in undo_timeout_pairs:
            if requester:
                send_to_player(requester, pack_message(CMD_UNDO, "timeout"))
            if responder:
                send_to_player(responder, pack_message(CMD_UNDO, "timeout"))
        for room in broadcast_rooms:
            broadcast_state(room)


def shutdown_server(server_socket: socket.socket):
    """主动关闭服务器：停止接入并断开现有连接。"""
    global server_running
    server_running = False

    try:
        server_socket.close()
    except Exception:
        pass

    with rooms_lock:
        current_rooms = list(rooms.values())
    for room in current_rooms:
        with room.lock:
            players = [p for p in room.players if p is not None]
            room.players = [None, None]
            room.game_over = True
            room.pending_undo = None
            room.pending_undo_since = 0.0
        for p in players:
            try:
                send_to_player(p, pack_message(CMD_ERROR, "服务器已关闭"))
            except Exception:
                pass
            try:
                p.conn.close()
            except Exception:
                pass
    with rooms_lock:
        rooms.clear()


def accept_loop(server_socket: socket.socket):
    """后台接入循环：主线程可专注控制台输入。"""
    while server_running:
        try:
            conn, addr = server_socket.accept()
        except OSError:
            # socket 已关闭或监听被中止
            break
        t = threading.Thread(target=handle_client, args=(conn, addr), daemon=True)
        t.start()


# ── 主函数 ────────────────────────────────────────────────
def main():
    global server_running
    server_running = True
    server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server_socket.bind((HOST, PORT))
    server_socket.listen(10)
    print(f"[服务器] 监听 {HOST}:{PORT}")
    print("[服务器] 输入 exit / quit / q 可主动退出")

    # 启动超时监控线程
    threading.Thread(target=timeout_monitor, daemon=True, name="TimeoutMonitor").start()

    # 后台接入线程，避免阻塞主线程的控制台输入
    threading.Thread(target=accept_loop, args=(server_socket,), daemon=True, name="AcceptLoop").start()

    try:
        while server_running:
            try:
                cmd = input().strip().lower()
            except EOFError:
                break
            except Exception:
                continue
            if cmd in {"exit", "quit", "q"}:
                print("[服务器] 收到退出指令，正在关闭...")
                shutdown_server(server_socket)
                break
    except KeyboardInterrupt:
        print("\n[服务器] 正在关闭...")
        shutdown_server(server_socket)
    finally:
        if server_running:
            shutdown_server(server_socket)


if __name__ == "__main__":
    main()
