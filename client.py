"""
五子棋联机游戏 — 客户端编排器。
持有游戏状态，桥接 NetworkManager（网络）与 GameUI（界面）。
"""

import time
import tkinter as tk
import threading
from collections import deque
from protocol import (
    pack_message,
    CMD_PLACE, CMD_UNDO, CMD_RESIGN,
    CMD_BROADCAST, CMD_GAME_START,
    CMD_REMATCH, CMD_REMATCH_ACK, CMD_ERROR,
)
from client_net import NetworkManager
from client_ui import GameUI

BOARD_SIZE = 15
BLACK = 1
WHITE = 2
MAX_RECONNECT = 3


# ── 工具函数 ────────────────────────────────────────────
def str_to_board(s: str):
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


def board_diff(old_board, new_board):
    for r in range(BOARD_SIZE):
        for c in range(BOARD_SIZE):
            if old_board[r][c] == 0 and new_board[r][c] != 0:
                return ('place', r, c, new_board[r][c])
    for r in range(BOARD_SIZE):
        for c in range(BOARD_SIZE):
            if old_board[r][c] != 0 and new_board[r][c] == 0:
                return ('undo', r, c, old_board[r][c])
    return (None, 0, 0, 0)


class GomokuClient:
    """客户端编排器：持有游戏状态，连接网络层与 UI 层。"""

    def __init__(self):
        self.root = tk.Tk()
        self.root.title("五子棋联机对战")
        self.root.resizable(False, False)
        self._alive = True

        # ── 游戏状态 ──
        self.board = [[0] * BOARD_SIZE for _ in range(BOARD_SIZE)]
        self.prev_board = [[0] * BOARD_SIZE for _ in range(BOARD_SIZE)]
        self.my_color = 0
        self.my_nickname = ""
        self.opponent_nickname = ""
        self.current_turn = 1
        self.game_over = False
        self.game_result = "未分胜负"
        self.move_history: list[tuple[int, int, int]] = []
        self._rematch_status = ""  # "" / "waiting_self" / "waiting_opp"
        self._last_place_time = 0.0

        # ── 消息队列 ──
        self.msg_queue: deque = deque()
        self.queue_lock = threading.Lock()

        # ── 网络层 ──
        self.net = NetworkManager(
            on_message=self._on_net_message,
            on_disconnected=self._on_net_disconnected,
        )

        # ── UI 层 ──
        self.ui = GameUI(self.root, callbacks={
            'on_connect': self._on_connect,
            'on_click': self._on_click,
            'on_undo': self._on_undo,
            'on_resign': self._on_resign,
            'on_resign_confirm': self._on_resign_confirm,
            'on_rematch_yes': self._on_rematch_yes,
            'on_rematch_no': self._on_close,
            'on_reconnect': self._on_manual_reconnect,
            'on_time_change': self._on_time_change,
            'on_size_change': self._on_size_change,
        })
        self.ui.set_on_close(self._on_close)
        self.ui.show_connect_screen()

    # ═══════════════════════════════════════════════════
    #  网络回调（子线程 → 入队）
    # ═══════════════════════════════════════════════════
    def _on_net_message(self, cmd: int, data: str):
        self._queue_msg(("message", cmd, data))

    def _on_net_disconnected(self, reason: str):
        self._queue_msg(("disconnected", reason))

    def _queue_msg(self, msg):
        with self.queue_lock:
            self.msg_queue.append(msg)

    # ═══════════════════════════════════════════════════
    #  主线程消息消费
    # ═══════════════════════════════════════════════════
    def _process_queue(self):
        try:
            with self.queue_lock:
                while self.msg_queue:
                    msg = self.msg_queue.popleft()
                    self._handle_msg(msg)
        finally:
            if self._alive:
                self.root.after(50, self._process_queue)

    def _handle_msg(self, msg):
        msg_type = msg[0]
        if msg_type == "disconnected":
            reason = msg[1]
            if self.net.reconnect_attempts < MAX_RECONNECT:
                self._try_reconnect()
            else:
                self._show_game_terminated(reason)
        elif msg_type == "message":
            _, cmd, data = msg
            if cmd == CMD_BROADCAST:
                self._handle_broadcast(data)
            elif cmd == CMD_GAME_START:
                self._handle_game_start(data)
            elif cmd == CMD_REMATCH_ACK:
                self._handle_rematch_ack(data)
            elif cmd == CMD_ERROR:
                if "断开" in data or "终止" in data:
                    self._show_game_terminated(data)
                else:
                    self.ui.notify(data, "warn")

    # ═══════════════════════════════════════════════════
    #  广播处理（核心游戏逻辑）
    # ═══════════════════════════════════════════════════
    def _handle_broadcast(self, data: str):
        parts = data.split("|")
        if len(parts) < 4:
            return
        *board_parts, turn_str, result, current_turn_str = parts
        board_str = "|".join(board_parts)
        self.prev_board = [row[:] for row in self.board]
        self.board = str_to_board(board_str)
        self.current_turn = int(current_turn_str)
        self.game_result = result

        action, r, c, color = board_diff(self.prev_board, self.board)
        if action == 'place':
            self.move_history.append((color, r, c))
            self.ui.reset_countdown()
            print(f"[DEBUG] 棋盘更新：放置 ({r}, {c})，颜色 {color}，结果 {result}")
        elif action == 'undo':
            for i in range(len(self.move_history) - 1, -1, -1):
                if self.move_history[i][1] == r and self.move_history[i][2] == c:
                    del self.move_history[i]
                    break
            self.ui.reset_countdown()
            print(f"[DEBUG] 棋盘更新：悔棋 ({r}, {c})")

        self.ui.draw_board(self.board)
        self.ui.update_move_log(self.move_history)

        if result != "未分胜负":
            if not self.game_over:
                self.game_over = True
                self.ui.stop_countdown()
                print(f"[DEBUG] 游戏结束：{result}，设置 game_over=True")
                self.ui.show_game_over_overlay(result)
        else:
            self.game_over = False
            self.ui.hide_game_over_overlay()
            self.ui.start_countdown()
            turn_display = {1: "●黑棋", 2: "○白棋"}.get(self.current_turn, "?")
            self.ui.update_status(f"当前回合: {turn_display}")

        if self.game_over:
            self.ui.update_status(f"游戏结束: {result}")
        else:
            turn_display = {1: "●黑棋", 2: "○白棋"}.get(self.current_turn, "?")
            self.ui.update_status(f"当前回合: {turn_display}")

    def _handle_game_start(self, data: str):
        parts = data.split("|")
        if len(parts) >= 3:
            self.my_color = int(parts[0])
            self.opponent_nickname = parts[2]
            color_symbol = {BLACK: "●", WHITE: "○"}.get(self.my_color, "◎")
            color_name = {BLACK: "黑棋", WHITE: "白棋", 0: "旁观"}.get(self.my_color, "?")
            title = f"我方: {color_symbol}{self.my_nickname}({color_name})   VS  对手: {self.opponent_nickname}"
            self.ui.update_players_display(title)
        self.game_over = False
        self.ui.hide_game_over_overlay()
        self._rematch_status = ""
        self.move_history.clear()
        self.ui.update_move_log(self.move_history)
        self.ui.start_countdown()

    # ═══════════════════════════════════════════════════
    #  再来一局
    # ═══════════════════════════════════════════════════
    def _handle_rematch_ack(self, data: str):
        if data == "waiting_self":
            self._rematch_status = "waiting_self"
            self.ui.update_rematch_panel("已发送请求，等待对方回应...", False)
        elif data.startswith("waiting|"):
            self._rematch_status = "waiting_opp"
            self.ui.update_rematch_panel("对方想要再来一局！", True)
        elif data == "reject":
            self._rematch_status = ""
            self.ui.update_rematch_panel("对方拒绝了再来一局", False)
        elif data == "reject_self":
            self._rematch_status = ""
            self.ui.update_rematch_panel("已取消", False)

    # ═══════════════════════════════════════════════════
    #  用户操作回调
    # ═══════════════════════════════════════════════════
    def _on_connect(self, ip: str, port: int, nickname: str):
        self.my_nickname = nickname
        if not self.net.connect(ip, port, nickname):
            self.ui.set_connect_status("连接失败", "red")
            return
        self.ui.set_connect_status("连接成功，等待对手...", "green")
        self.ui.show_game_screen()
        self.ui.draw_board(self.board)
        self._alive = True
        self.net.start_threads()
        self._process_queue()

    def _on_click(self, row: int, col: int):
        print(f"[DEBUG] Canvas 点击事件：({row}, {col})，game_over={self.game_over}")
        if self.game_over:
            print(f"[DEBUG] 游戏已结束，忽略点击")
            return
        if not (0 <= row < BOARD_SIZE and 0 <= col < BOARD_SIZE):
            print(f"[DEBUG] 点击越界：({row}, {col})")
            return
        if self.my_color == 0:
            self.ui.notify("您正在观战，无法落子", "warn")
            return
        if self.current_turn != self.my_color:
            self.ui.notify("现在不是您的回合", "warn")
            return

        now = time.time()
        if now - self._last_place_time < 0.5:
            print(f"[DEBUG] 点击频率过快，忽略")
            return
        self._last_place_time = now

        if self.board[row][col] != 0:
            self.ui.notify("该位置已有棋子", "warn")
            return

        print(f"[DEBUG] 放置棋子: ({row}, {col}), 我的颜色: {self.my_color}, 当前回合: {self.current_turn}")
        self.net.send_raw(pack_message(CMD_PLACE, f"{row},{col}"))

    def _on_undo(self):
        if self.game_over or not self.net.is_connected:
            return
        self.net.send_raw(pack_message(CMD_UNDO))

    def _on_resign(self):
        if self.game_over or not self.net.is_connected:
            return
        if self.my_color == 0:
            self.ui.notify("旁观者不能认输", "warn")
            return
        self.ui.show_confirm_overlay()

    def _on_resign_confirm(self):
        self.net.send_raw(pack_message(CMD_RESIGN))

    def _on_rematch_yes(self):
        if not self.net.is_connected:
            return
        self.net.send_raw(pack_message(CMD_REMATCH, "yes"))

    def _on_time_change(self, seconds: int):
        import client_ui
        client_ui.TURN_TIME_LIMIT = seconds

    def _on_size_change(self, cell_size: int, piece_radius: int):
        self.ui.resize_board(cell_size, piece_radius, self.board)

    # ═══════════════════════════════════════════════════
    #  重连
    # ═══════════════════════════════════════════════════
    def _try_reconnect(self):
        self.net.reconnect_attempts += 1
        self.ui.update_status(
            f"正在重连 ({self.net.reconnect_attempts}/{MAX_RECONNECT})...")
        ip, port = self.net._server
        if self.net.connect(ip, port, self.my_nickname):
            self.ui.update_status("重连成功")
            self.net.start_threads()
        else:
            if self.net.reconnect_attempts < MAX_RECONNECT:
                self.root.after(2000, self._try_reconnect)
            else:
                self._show_game_terminated("重连失败，游戏终止")

    def _on_manual_reconnect(self):
        self.net.reconnect_attempts = 0
        ip, port = self.net._server
        if self.net.connect(ip, port, self.my_nickname):
            self.ui.notify("重连成功！", "info")
            self.net.start_threads()
        else:
            self.ui.notify("重连失败", "error")

    # ═══════════════════════════════════════════════════
    #  终止
    # ═══════════════════════════════════════════════════
    def _show_game_terminated(self, reason: str):
        self.game_over = True
        self.ui.stop_countdown()
        self.ui.update_status(f"连接中断: {reason}")
        self.ui.show_terminated(reason)

    # ═══════════════════════════════════════════════════
    #  生命周期
    # ═══════════════════════════════════════════════════
    def run(self):
        self.root.mainloop()

    def _on_close(self):
        self._alive = False
        self.ui.stop_countdown()
        self.ui._stop_celebration()
        self.net.disconnect()
        self.ui.destroy()


if __name__ == "__main__":
    GomokuClient().run()
