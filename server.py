"""
五子棋联机游戏 — 服务器端。
支持多房间、观战、悔棋、认输、心跳检测、落子超时、计分持久化。
"""

import socket
import threading
import time
import sys
import os
from collections import namedtuple
from protocol import (
    recv_message, pack_message,
    CMD_CONNECT, CMD_PLACE, CMD_UNDO, CMD_RESIGN,
    CMD_BROADCAST, CMD_HEARTBEAT, CMD_ERROR, CMD_GAME_START, CMD_UNDO_RESULT,
    CMD_REMATCH, CMD_REMATCH_ACK,
)

HOST = "0.0.0.0"
PORT = 9527
BOARD_SIZE = 15
TIMEOUT_SECONDS = 30        # 落子超时
HEARTBEAT_TIMEOUT = 15      # 心跳超时
MAX_UNDO_PER_GAME = 3       # 每局每方最多悔棋次数
SCORE_FILE = "score.txt"

# 方向向量：水平、垂直、左上-右下、右上-左下
DIRECTIONS = [(0, 1), (1, 0), (1, 1), (1, -1)]

Player = namedtuple("Player", ["conn", "addr", "nickname", "color"])


# ── 游戏房间 ──────────────────────────────────────────────
class GameRoom:
    """单个房间，包含棋盘状态、两名玩家、旁观者列表。"""
    def __init__(self, room_id: int):
        self.room_id = room_id
        self.players: list[Player | None] = [None, None]  # 黑(1) / 白(2)
        self.spectators: list[Player] = []
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

    def reset(self):
        """重置房间状态，保留玩家和旁观者。黑棋先行。"""
        self.board = [[0] * BOARD_SIZE for _ in range(BOARD_SIZE)]
        self.current_turn = 1
        self.game_over = False
        self.winner = None
        self.last_move = None
        self.turn_start_time = time.time()
        self.undo_count = {1: 0, 2: 0}
        self.move_history = []
        self.rematch_ready = set()


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


# ── 全局状态 ──────────────────────────────────────────────
rooms: dict[int, GameRoom] = {}
rooms_lock = threading.Lock()
next_room_id = 1
score_lock = threading.Lock()


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


def update_score(nickname: str, result: str):
    """result: 'win', 'draw', 'loss'"""
    with score_lock:
        scores = load_scores()
        if nickname not in scores:
            scores[nickname] = {"wins": 0, "draws": 0, "losses": 0}
        scores[nickname][f"{result}s"] += 1
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
        if not has_players and not room.spectators:
            rooms.pop(room.room_id, None)


# ── 广播函数 ──────────────────────────────────────────────
def send_to_player(player: Player, msg: bytes):
    """向单个玩家发送报文，自动处理异常。"""
    try:
        player.conn.sendall(msg)
    except Exception:
        pass


def broadcast_to_room(room: GameRoom, msg: bytes):
    """向房间内所有玩家和旁观者广播。"""
    for p in room.players:
        if p is not None:
            send_to_player(p, msg)
    for p in room.spectators:
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
        data = f"{board_str}|{turn_str}|{result}|{room.current_turn}"
    broadcast_to_room(room, pack_message(CMD_BROADCAST, data))


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
    # 旁观者也收到开始通知
    for p in room.spectators:
        p1 = room.players[0]
        p2 = room.players[1]
        data = f"0|旁观|{p1.nickname if p1 else '?'} vs {p2.nickname if p2 else '?'}"
        send_to_player(p, pack_message(CMD_GAME_START, data))
    broadcast_state(room)


# ── 处理客户端逻辑 ────────────────────────────────────────
def handle_client(conn: socket.socket, addr):
    """子线程：处理单个客户端完整生命周期。"""
    print(f"[连接] {addr} 已连接")
    player = Player(conn=conn, addr=addr, nickname="", color=0)
    room: GameRoom | None = None
    player_index: int | None = None
    last_heartbeat = time.time()

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
            if player_index is None:
                room.spectators.append(player)
                is_spectator = True
                is_game_ready = False
            else:
                is_spectator = False
                is_game_ready = all(p is not None for p in room.players)
                if is_game_ready:
                    room.turn_start_time = time.time()

        if is_spectator:
            print(f"[房间{room.room_id}] {nickname} 作为旁观者加入")
            broadcast_state(room)  # broadcast_state 内部自带锁
        elif is_game_ready:
            print(f"[房间{room.room_id}] 游戏开始: {room.players[0].nickname} vs {room.players[1].nickname}")
            broadcast_game_start(room)  # broadcast_game_start → broadcast_state 内部自带锁
        else:
            print(f"[房间{room.room_id}] {nickname} 作为玩家{player_index + 1}({player.color})加入")
            send_to_player(player, pack_message(CMD_ERROR, "等待对手加入..."))

        last_heartbeat = time.time()

        # ── 主消息循环 ──
        while True:
            result = recv_message(conn)
            if result is None:
                break
            cmd, payload = result
            last_heartbeat = time.time()

            if cmd == CMD_HEARTBEAT:
                # 心跳响应
                send_to_player(player, pack_message(CMD_HEARTBEAT))
                continue

            if room is None:
                continue

            # 旁观者只能收广播、发心跳
            if player_index is None and cmd not in (CMD_HEARTBEAT,):
                send_error(player, "您是旁观者，无法操作")
                continue

            # ── 落子 ──
            if cmd == CMD_PLACE:
                if player_index is None:
                    continue
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
                    # 合法落子
                    room.board[row][col] = player.color
                    room.last_move = (row, col)
                    room.undo_count[player.color] = 0  # 落子后重置对方悔棋计数（只有对方能悔）
                    room.move_history.append((player.color, row, col))
                    # 胜负判定
                    if check_win(room.board, row, col, player.color):
                        room.game_over = True
                        room.winner = player.color
                        winner_name = player.nickname
                        loser_name = room.players[1 - player_index].nickname if room.players[1 - player_index] else ""
                        update_score(winner_name, "win")
                        update_score(loser_name, "loss")
                    elif is_board_full(room.board):
                        room.game_over = True
                        room.winner = 0
                        update_score(room.players[0].nickname, "draw")
                        update_score(room.players[1].nickname, "draw")
                    else:
                        room.current_turn = 3 - room.current_turn  # 切换回合
                        room.turn_start_time = time.time()
                broadcast_state(room)

            # ── 悔棋 ──
            elif cmd == CMD_UNDO:
                if player_index is None:
                    continue
                with room.lock:
                    if room.game_over:
                        send_error(player, "游戏已结束，不能悔棋")
                        continue
                    if room.last_move is None:
                        send_error(player, "没有可以悔棋的步骤")
                        continue
                    if room.undo_count[player.color] >= MAX_UNDO_PER_GAME:
                        send_error(player, f"您本局已使用 {MAX_UNDO_PER_GAME} 次悔棋机会")
                        continue
                    last_color = room.board[room.last_move[0]][room.last_move[1]]
                    if last_color == player.color:
                        send_error(player, "只有上一回合的玩家才能请求悔棋")
                        continue
                    # 执行悔棋
                    row, col = room.last_move
                    room.board[row][col] = 0
                    room.current_turn = last_color
                    room.undo_count[player.color] += 1
                    room.last_move = None
                    if room.move_history:
                        room.move_history.pop()
                    room.turn_start_time = time.time()
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
                    update_score(player.nickname, "loss")
                    opp = room.players[1 - player_index]
                    if opp:
                        update_score(opp.nickname, "win")
                broadcast_state(room)

            # ── 再来一局 ──
            elif cmd == CMD_REMATCH:
                if player_index is None:
                    continue
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
                        # 双方都准备了 → 开始新局
                        if room.rematch_ready == {1, 2}:
                            room.reset()
                            time.sleep(0.3)  # 给客户端动画留时间
                            broadcast_game_start(room)
                    elif choice == "no":
                        # 一方拒绝
                        opp = room.players[1 - player_index]
                        if opp:
                            send_to_player(opp, pack_message(CMD_REMATCH_ACK, "reject"))
                        send_to_player(player, pack_message(CMD_REMATCH_ACK, "reject_self"))

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
            else:
                with room.lock:
                    try:
                        room.spectators.remove(player)
                    except ValueError:
                        pass
            cleanup_room(room)
        try:
            conn.close()
        except Exception:
            pass


# ── 超时检测线程 ──────────────────────────────────────────
def timeout_monitor():
    """定时检查所有房间的落子超时。"""
    while True:
        time.sleep(5)
        with rooms_lock:
            for room in list(rooms.values()):
                with room.lock:
                    if room.game_over or None in room.players:
                        continue
                    elapsed = time.time() - room.turn_start_time
                    if elapsed > TIMEOUT_SECONDS:
                        timeout_player = room.players[1 - room.current_turn] if room.current_turn == 1 else room.players[0]
                        # 更准确：找到当前回合对应的玩家
                        current_color = room.current_turn
                        current_idx = 0 if room.players[0] and room.players[0].color == current_color else 1
                        timeout_player = room.players[current_idx]
                        if timeout_player:
                            print(f"[超时] {timeout_player.nickname} 落子超时")
                            room.game_over = True
                            room.winner = 3 - current_color
                            update_score(timeout_player.nickname, "loss")
                            opp = room.players[1 - current_idx]
                            if opp:
                                update_score(opp.nickname, "win")
                            broadcast_state(room)


# ── 主函数 ────────────────────────────────────────────────
def main():
    server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server_socket.bind((HOST, PORT))
    server_socket.listen(10)
    print(f"[服务器] 监听 {HOST}:{PORT}")

    # 启动超时监控线程
    threading.Thread(target=timeout_monitor, daemon=True, name="TimeoutMonitor").start()

    try:
        while True:
            conn, addr = server_socket.accept()
            t = threading.Thread(target=handle_client, args=(conn, addr), daemon=True)
            t.start()
    except KeyboardInterrupt:
        print("\n[服务器] 正在关闭...")
    finally:
        server_socket.close()


if __name__ == "__main__":
    main()
