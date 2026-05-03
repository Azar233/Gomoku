"""
五子棋联机游戏 — 客户端编排器。
持有游戏状态，桥接 NetworkManager（网络）与 GameUI（界面）。
"""

import time
import tkinter as tk
import threading
import json
import os
import socket
from collections import deque
from protocol import (
    pack_message,
    CMD_PLACE, CMD_UNDO, CMD_RESIGN,
    CMD_BROADCAST, CMD_GAME_START,
    CMD_REMATCH, CMD_REMATCH_ACK, CMD_ERROR, CMD_TIME_LIMIT,
)
from client_net import NetworkManager
from client_ui import GameUI

BOARD_SIZE = 15
BLACK = 1
WHITE = 2
MAX_RECONNECT = 3
CLIENT_PREFS_FILE = "client_prefs.json"


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


def load_client_prefs() -> dict:
    """加载客户端连接偏好。"""
    if not os.path.exists(CLIENT_PREFS_FILE):
        return {}
    try:
        with open(CLIENT_PREFS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return {
            "ip": str(data.get("ip", "")).strip(),
            "port": int(data.get("port", 9527)),
            "nickname": str(data.get("nickname", "")).strip(),
            "enable_animation": bool(data.get("enable_animation", True)),
            "enable_sound": bool(data.get("enable_sound", False)),
        }
    except Exception:
        return {}


def save_client_prefs(ip: str, port: int, nickname: str,
                      enable_animation: bool = True,
                      enable_sound: bool = False):
    """保存客户端连接偏好。"""
    try:
        with open(CLIENT_PREFS_FILE, "w", encoding="utf-8") as f:
            json.dump(
                {
                    "ip": ip.strip(),
                    "port": int(port),
                    "nickname": nickname.strip(),
                    "enable_animation": bool(enable_animation),
                    "enable_sound": bool(enable_sound),
                },
                f, ensure_ascii=False, indent=2
            )
    except Exception:
        pass


def detect_local_ip() -> str:
    """探测本机局域网 IP（用于跨设备连接提示）。"""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
        if ip and not ip.startswith("127."):
            return ip
    except Exception:
        pass
    return ""


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
        self.turn_time_limit = 30
        self.prefs = load_client_prefs()
        self.local_ip = detect_local_ip()

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
            'on_undo_accept': self._on_undo_accept,
            'on_undo_reject': self._on_undo_reject,
            'on_resign': self._on_resign,
            'on_resign_confirm': self._on_resign_confirm,
            'on_rematch_yes': self._on_rematch_yes,
            'on_rematch_no': self._on_close,
            'on_rematch_reject': self._on_rematch_reject,
            'on_reconnect': self._on_manual_reconnect,
            'on_time_change': self._on_time_change,
            'on_size_change': self._on_size_change,
            'on_toggle_animation': self._on_toggle_animation,
            'on_toggle_sound': self._on_toggle_sound,
        })
        self.ui.enable_animation = bool(self.prefs.get("enable_animation", True))
        self.ui.enable_sound = bool(self.prefs.get("enable_sound", False))
        self.ui.set_on_close(self._on_close)
        self.ui.show_connect_screen(self.prefs, self.local_ip)

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
                self._refresh_net_status()

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
            elif cmd == CMD_UNDO:
                self._handle_undo_signal(data)
            elif cmd == CMD_TIME_LIMIT:
                self._handle_time_limit(data)

    # ═══════════════════════════════════════════════════
    #  广播处理（核心游戏逻辑）
    # ═══════════════════════════════════════════════════
    def _handle_broadcast(self, data: str):
        parts = data.split("|")
        if len(parts) < 4:
            return
        self.ui.hide_undo_request_overlay()
        if len(parts) >= 5:
            *board_parts, turn_str, result, current_turn_str, limit_str = parts
            try:
                self.turn_time_limit = int(limit_str)
            except ValueError:
                self.turn_time_limit = 30
        else:
            *board_parts, turn_str, result, current_turn_str = parts
        board_str = "|".join(board_parts)
        self.prev_board = [row[:] for row in self.board]
        self.board = str_to_board(board_str)
        self.current_turn = int(current_turn_str)
        self.game_result = result

        action, r, c, color = board_diff(self.prev_board, self.board)
        if action == 'place':
            self.move_history.append((color, r, c))
            self.ui.set_last_move(r, c)
            self.ui.reset_countdown(self.turn_time_limit)
            print(f"[DEBUG] 棋盘更新：放置 ({r}, {c})，颜色 {color}，结果 {result}")
        elif action == 'undo':
            for i in range(len(self.move_history) - 1, -1, -1):
                if self.move_history[i][1] == r and self.move_history[i][2] == c:
                    del self.move_history[i]
                    break
            if self.move_history:
                _, lr, lc = self.move_history[-1]
                self.ui.set_last_move(lr, lc)
            else:
                self.ui.clear_last_move()
            self.ui.reset_countdown(self.turn_time_limit)
            self.ui.play_action_sound("undo")
            print(f"[DEBUG] 棋盘更新：悔棋 ({r}, {c})")

        self.ui.draw_board(self.board)
        self.ui.update_move_log(self.move_history)

        if result != "未分胜负":
            if not self.game_over:
                self.game_over = True
                self.ui.stop_countdown()
                print(f"[DEBUG] 游戏结束：{result}，设置 game_over=True")
                self.ui.show_game_over_overlay(result)
                self.ui.set_action_buttons_enabled(False)
        else:
            self.game_over = False
            self.ui.hide_game_over_overlay()
            self.ui.start_countdown(self.turn_time_limit)
            turn_display = {1: "●黑棋", 2: "○白棋"}.get(self.current_turn, "?")
            self.ui.update_status(f"当前回合: {turn_display}")
            self.ui.set_action_buttons_enabled(True)

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
            color_name = {BLACK: "黑棋", WHITE: "白棋"}.get(self.my_color, "?")
            title = f"我方: {color_symbol}{self.my_nickname}({color_name})   VS  对手: {self.opponent_nickname}"
            self.ui.update_players_display(title)
        self.game_over = False
        self.ui.hide_game_over_overlay()
        self._rematch_status = ""
        self.move_history.clear()
        self.ui.clear_last_move()
        self.ui.update_move_log(self.move_history)
        self.ui.start_countdown(self.turn_time_limit)
        self.ui.set_action_buttons_enabled(True)

    def _handle_time_limit(self, data: str):
        parts = data.split("|")
        if len(parts) < 2:
            return
        try:
            seconds = int(parts[0])
        except ValueError:
            return
        operator = parts[1].strip() or "对手"
        self.turn_time_limit = seconds
        self.ui.reset_countdown(self.turn_time_limit)
        self.ui.notify(f"回合限时已设为 {seconds}s（设置者：{operator}）", "info")

    def _handle_undo_signal(self, data: str):
        token = (data or "").strip()
        if token.startswith("request|"):
            requester = token.split("|", 1)[1].strip() or "对手"
            self.ui.show_undo_request_overlay(requester)
            self.ui.notify(f"{requester} 请求悔棋", "warn")
            return

        low = token.lower()
        if low == "waiting_self":
            self.ui.notify("悔棋请求已发送，等待对手确认", "info")
        elif low == "accepted":
            self.ui.hide_undo_request_overlay()
            self.ui.notify("悔棋请求已同意", "info")
        elif low == "reject":
            self.ui.hide_undo_request_overlay()
            self.ui.notify("对手拒绝了悔棋请求", "warn")
        elif low == "rejected":
            self.ui.hide_undo_request_overlay()
            self.ui.notify("您已拒绝对手的悔棋请求", "info")
        elif low == "expired":
            self.ui.hide_undo_request_overlay()
            self.ui.notify("悔棋请求已失效（对手已落子）", "warn")
        elif low == "timeout":
            self.ui.hide_undo_request_overlay()
            self.ui.notify("悔棋请求已超时自动取消", "warn")

    # ═══════════════════════════════════════════════════
    #  再来一局
    # ═══════════════════════════════════════════════════
    def _handle_rematch_ack(self, data: str):
        if data == "waiting_self":
            self._rematch_status = "waiting_self"
            self.ui.update_rematch_panel("已发送请求，等待对方回应...",
                                         show_reject=True)
        elif data.startswith("waiting|"):
            self._rematch_status = "waiting_opp"
            self.ui.update_rematch_panel("对方想要再来一局！",
                                         show_accept=True, show_reject=True)
        elif data == "reject":
            self._rematch_status = ""
            self.ui.update_rematch_panel("对方拒绝了再来一局")
        elif data == "reject_self":
            self._rematch_status = ""
            self.ui.update_rematch_panel("已取消")

    def _resolve_pref_context(self) -> tuple[str, int, str]:
        """优先使用当前会话信息，回退到缓存偏好。"""
        if self.net._server:
            ip, port = self.net._server
        else:
            ip = str(self.prefs.get("ip", ""))
            try:
                port = int(self.prefs.get("port", 9527))
            except (TypeError, ValueError):
                port = 9527
        nick = self.my_nickname or str(self.prefs.get("nickname", ""))
        return ip, port, nick

    def _persist_prefs(self, ip: str, port: int, nickname: str):
        save_client_prefs(
            ip, port, nickname,
            enable_animation=self.ui.enable_animation,
            enable_sound=self.ui.enable_sound,
        )
        self.prefs = {
            "ip": str(ip).strip(),
            "port": int(port),
            "nickname": str(nickname).strip(),
            "enable_animation": bool(self.ui.enable_animation),
            "enable_sound": bool(self.ui.enable_sound),
        }

    # ═══════════════════════════════════════════════════
    #  用户操作回调
    # ═══════════════════════════════════════════════════
    def _on_connect(self, ip: str, port: int, nickname: str):
        self.my_nickname = nickname
        if not self.net.connect(ip, port, nickname):
            self.ui.set_connect_status("连接失败", "red")
            return
        self._persist_prefs(ip, port, nickname)
        self.ui.set_connect_status("连接成功，等待对手...", "green")
        self.ui.show_game_screen()
        self.ui.draw_board(self.board)
        self.ui.update_net_quality(-1)
        self._alive = True
        self.net.start_threads()
        self._refresh_net_status()
        self._process_queue()

    def _on_click(self, row: int, col: int):
        print(f"[DEBUG] Canvas 点击事件：({row}, {col})，game_over={self.game_over}")
        if self.game_over:
            print(f"[DEBUG] 游戏已结束，忽略点击")
            return
        if not (0 <= row < BOARD_SIZE and 0 <= col < BOARD_SIZE):
            print(f"[DEBUG] 点击越界：({row}, {col})")
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
        self.net.send_raw(pack_message(CMD_UNDO, "request"))

    def _on_undo_accept(self):
        if self.game_over or not self.net.is_connected:
            return
        self.net.send_raw(pack_message(CMD_UNDO, "yes"))

    def _on_undo_reject(self):
        if self.game_over or not self.net.is_connected:
            return
        self.net.send_raw(pack_message(CMD_UNDO, "no"))

    def _on_resign(self):
        if self.game_over or not self.net.is_connected:
            return
        self.ui.show_confirm_overlay()

    def _on_resign_confirm(self):
        self.net.send_raw(pack_message(CMD_RESIGN))

    def _on_rematch_yes(self):
        if not self.net.is_connected:
            return
        self.net.send_raw(pack_message(CMD_REMATCH, "yes"))

    def _on_rematch_reject(self):
        if not self.net.is_connected:
            return
        self.net.send_raw(pack_message(CMD_REMATCH, "no"))

    def _on_time_change(self, seconds: int):
        if not self.net.is_connected:
            self.ui.notify("未连接服务器，无法修改限时", "warn")
            return
        self.net.send_raw(pack_message(CMD_TIME_LIMIT, str(seconds)))

    def _on_size_change(self, cell_size: int, piece_radius: int):
        self.ui.resize_board(cell_size, piece_radius, self.board)

    def _on_toggle_animation(self, enabled: bool):
        self.ui.enable_animation = enabled
        ip, port, nick = self._resolve_pref_context()
        self._persist_prefs(ip, port, nick)
        self.ui.notify("落子动画已开启" if enabled else "落子动画已关闭", "info")

    def _on_toggle_sound(self, enabled: bool):
        self.ui.enable_sound = enabled
        ip, port, nick = self._resolve_pref_context()
        self._persist_prefs(ip, port, nick)
        self.ui.notify("音效已开启" if enabled else "音效已关闭", "info")

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
        self.ui.hide_undo_request_overlay()
        self.ui.update_status(f"连接中断: {reason}")
        self.ui.show_terminated(reason)
        self.ui.set_action_buttons_enabled(False)
        self.ui.update_net_quality(-1)

    def _refresh_net_status(self):
        if not self._alive:
            return
        if not self.net.is_connected:
            self.ui.update_net_quality(-1)
            return
        self.ui.update_net_quality(self.net.rtt_ms)

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
