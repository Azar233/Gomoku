"""
五子棋联机游戏 — 客户端 (tkinter GUI)。
包含连接界面、游戏棋盘、网络通信、心跳、快捷键等功能。
绝不在子线程操作 UI，统一通过 root.after() 调度。
"""

import socket
import threading
import time
import tkinter as tk
from tkinter import messagebox
from collections import deque
from protocol import (
    recv_message, pack_message,
    CMD_CONNECT, CMD_PLACE, CMD_UNDO, CMD_RESIGN,
    CMD_BROADCAST, CMD_HEARTBEAT, CMD_ERROR, CMD_GAME_START,
)

BOARD_SIZE = 15

# ── 可调设置 ──
CELL_SIZE = 40
PADDING = 30
PIECE_RADIUS = 17
TURN_TIME_LIMIT = 30

# 颜色映射常量
BLACK = 1
WHITE = 2


def str_to_board(s: str):
    """从字符串反序列化棋盘（与服务器端保持一致）。"""
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
    """比较新旧棋盘，返回 (action, row, col, color)。
    action: 'place' 落子, 'undo' 悔棋, None 无变化。"""
    # 检测落子
    for r in range(BOARD_SIZE):
        for c in range(BOARD_SIZE):
            if old_board[r][c] == 0 and new_board[r][c] != 0:
                return ('place', r, c, new_board[r][c])
    # 检测悔棋（棋子消失）
    for r in range(BOARD_SIZE):
        for c in range(BOARD_SIZE):
            if old_board[r][c] != 0 and new_board[r][c] == 0:
                return ('undo', r, c, old_board[r][c])
    return (None, 0, 0, 0)


class GomokuClient:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("五子棋联机对战")
        self.root.resizable(False, False)

        self.sock: socket.socket | None = None
        self.running = False
        self.my_color = 0           # 1=黑, 2=白, 0=旁观
        self.my_nickname = ""
        self.opponent_nickname = ""
        self.board = [[0] * BOARD_SIZE for _ in range(BOARD_SIZE)]
        self.prev_board = [[0] * BOARD_SIZE for _ in range(BOARD_SIZE)]
        self.current_turn = 1
        self.game_over = False
        self.game_result = "未分胜负"

        # 线程安全消息队列（接收线程 → 主线程）
        self.msg_queue = deque()
        self.queue_lock = threading.Lock()

        self.last_heartbeat_ack = time.time()

        # 防抖
        self._last_place_time = 0.0

        # 重连
        self.reconnect_server = ("127.0.0.1", 9527)
        self.reconnect_attempts = 0
        self.max_reconnect = 3

        # 落子记录
        self.move_history: list[tuple[int, int, int]] = []  # (color, row, col)

        # 倒计时
        self._countdown_value = TURN_TIME_LIMIT
        self._countdown_job = None
        self.countdown_running = False

        self._setup_connect_screen()

    # ═══════════════════════════════════════════════
    #  连接界面
    # ═══════════════════════════════════════════════
    def _clear_root(self):
        for w in self.root.winfo_children():
            w.destroy()

    def _setup_connect_screen(self):
        self._clear_root()
        self.root.geometry("360x260")

        frame = tk.Frame(self.root)
        frame.pack(expand=True)

        tk.Label(frame, text="五子棋联机对战", font=("", 18, "bold")).grid(
            row=0, column=0, columnspan=2, pady=(0, 15))

        tk.Label(frame, text="IP 地址:").grid(row=1, column=0, sticky="e", padx=5, pady=4)
        self.ip_entry = tk.Entry(frame, width=20)
        self.ip_entry.insert(0, "127.0.0.1")
        self.ip_entry.grid(row=1, column=1, sticky="w", pady=4)

        tk.Label(frame, text="端口:").grid(row=2, column=0, sticky="e", padx=5, pady=4)
        self.port_entry = tk.Entry(frame, width=20)
        self.port_entry.insert(0, "9527")
        self.port_entry.grid(row=2, column=1, sticky="w", pady=4)

        tk.Label(frame, text="昵称:").grid(row=3, column=0, sticky="e", padx=5, pady=4)
        self.nick_entry = tk.Entry(frame, width=20)
        self.nick_entry.grid(row=3, column=1, sticky="w", pady=4)

        tk.Button(frame, text="连接服务器", command=self._do_connect,
                  width=16, bg="#4CAF50", fg="white").grid(
            row=4, column=0, columnspan=2, pady=(15, 0))

        self._connect_status = tk.Label(frame, text="", fg="gray")
        self._connect_status.grid(row=5, column=0, columnspan=2)

        self.root.bind("<Return>", lambda e: self._do_connect())

    # ═══════════════════════════════════════════════
    #  网络连接
    # ═══════════════════════════════════════════════
    def _do_connect(self):
        ip = self.ip_entry.get().strip()
        try:
            port = int(self.port_entry.get().strip())
        except ValueError:
            messagebox.showerror("输入错误", "端口必须为整数")
            return
        nickname = self.nick_entry.get().strip()
        if not nickname:
            messagebox.showerror("输入错误", "请输入昵称")
            return
        self.my_nickname = nickname
        self.reconnect_server = (ip, port)
        self._connect_status.config(text="正在连接...", fg="blue")
        self.root.update()

        if not self._connect(ip, port, nickname):
            self._connect_status.config(text="连接失败", fg="red")
            return

        self._connect_status.config(text="连接成功，等待对手...", fg="green")
        self.running = True
        self._setup_game_screen()

        threading.Thread(target=self._recv_loop, daemon=True, name="RecvThread").start()
        threading.Thread(target=self._heartbeat_loop, daemon=True, name="Heartbeat").start()
        self._process_queue()

    def _connect(self, ip: str, port: int, nickname: str) -> bool:
        try:
            self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.sock.settimeout(5)
            self.sock.connect((ip, port))
            self.sock.settimeout(None)
            self.sock.sendall(pack_message(CMD_CONNECT, nickname))
            self.last_heartbeat_ack = time.time()
            self.reconnect_attempts = 0
            return True
        except Exception as e:
            print(f"[连接失败] {e}")
            return False

    def _heartbeat_loop(self):
        while self.running and self.sock:
            try:
                self.sock.sendall(pack_message(CMD_HEARTBEAT))
            except Exception:
                break
            if time.time() - self.last_heartbeat_ack > 45:
                self._queue_msg(("disconnected", "心跳超时，连接已断开"))
                break
            time.sleep(5)

    # ═══════════════════════════════════════════════
    #  消息收发与调度
    # ═══════════════════════════════════════════════
    def _recv_loop(self):
        while self.running and self.sock:
            result = recv_message(self.sock)
            if result is None:
                self._queue_msg(("disconnected", "与服务器的连接已断开"))
                break
            cmd, data = result
            self._queue_msg(("message", cmd, data))
            if cmd == CMD_HEARTBEAT:
                self.last_heartbeat_ack = time.time()

    def _queue_msg(self, msg):
        with self.queue_lock:
            self.msg_queue.append(msg)

    def _process_queue(self):
        """主线程定时消费消息队列。"""
        try:
            with self.queue_lock:
                while self.msg_queue:
                    msg = self.msg_queue.popleft()
                    self._handle_msg(msg)
        finally:
            if self.running:
                self.root.after(50, self._process_queue)

    def _handle_msg(self, msg):
        msg_type = msg[0]
        if msg_type == "disconnected":
            reason = msg[1]
            if self.reconnect_attempts < self.max_reconnect:
                self._try_reconnect()
            else:
                self._show_game_terminated(reason)
        elif msg_type == "message":
            _, cmd, data = msg
            if cmd == CMD_BROADCAST:
                self._handle_broadcast(data)
            elif cmd == CMD_GAME_START:
                self._handle_game_start(data)
            elif cmd == CMD_ERROR:
                messagebox.showinfo("提示", data)
                if "断开" in data or "终止" in data:
                    self._show_game_terminated(data)

    # ═══════════════════════════════════════════════
    #  消息处理
    # ═══════════════════════════════════════════════
    def _handle_broadcast(self, data: str):
        # 数据格式: board_str|turn_str|result|current_turn
        parts = data.split("|")
        if len(parts) < 4:
            return
        # 最后三个字段是 turn_str, result, current_turn；前面的是 board_str
        *board_parts, turn_str, result, current_turn_str = parts
        board_str = "|".join(board_parts)  # 如果 board_str 本身含 | 也能正确还原
        self.prev_board = [row[:] for row in self.board]
        self.board = str_to_board(board_str)
        self.current_turn = int(current_turn_str)
        self.game_result = result

        # 检测变化并记录历史
        action, r, c, color = board_diff(self.prev_board, self.board)
        if action == 'place':
            self.move_history.append((color, r, c))
            self._reset_countdown()
        elif action == 'undo':
            for i in range(len(self.move_history) - 1, -1, -1):
                if self.move_history[i][1] == r and self.move_history[i][2] == c:
                    del self.move_history[i]
                    break
            self._reset_countdown()
        # 无变化时不动倒计时

        if result != "未分胜负":
            self.game_over = True
            self._stop_countdown()
        else:
            self._start_countdown()
        # 注: _start_countdown 内部有幂等处理，不会重复启动

        self._draw_board()
        self._update_move_log()

        if not self.game_over:
            turn_display = {1: "●黑棋", 2: "○白棋"}.get(self.current_turn, "?")
            self._update_status(f"当前回合: {turn_display}")
        else:
            self._update_status(f"游戏结束: {result}")

    def _handle_game_start(self, data: str):
        parts = data.split("|")
        if len(parts) >= 3:
            self.my_color = int(parts[0])
            color_name = parts[1]
            self.opponent_nickname = parts[2]
            color_symbol = "●" if self.my_color == BLACK else "○" if self.my_color == WHITE else "◎"
            title = f"我方: {color_symbol}{self.my_nickname}   VS  对手: {self.opponent_nickname}"
            self._update_players_display(title)
        self.game_over = False
        self.move_history.clear()
        self._start_countdown()

    # ═══════════════════════════════════════════════
    #  重连
    # ═══════════════════════════════════════════════
    def _try_reconnect(self):
        self.reconnect_attempts += 1
        self._update_status(f"正在重连 ({self.reconnect_attempts}/{self.max_reconnect})...")
        ip, port = self.reconnect_server
        if self._connect(ip, port, self.my_nickname):
            self._update_status("重连成功")
        else:
            if self.reconnect_attempts < self.max_reconnect:
                self.root.after(2000, self._try_reconnect)
            else:
                self._show_game_terminated("重连失败，游戏终止")

    def _manual_reconnect(self):
        self.reconnect_attempts = 0
        ip, port = self.reconnect_server
        if self._connect(ip, port, self.my_nickname):
            messagebox.showinfo("重连", "重连成功！")
        else:
            messagebox.showerror("重连", "重连失败")

    # ═══════════════════════════════════════════════
    #  倒计时
    # ═══════════════════════════════════════════════
    def _start_countdown(self):
        self._countdown_value = TURN_TIME_LIMIT
        self.countdown_running = True
        self.timer_label.config(text=f"⏱ {self._countdown_value}s")
        # 取消旧定时器，启动新定时器
        if hasattr(self, '_countdown_job') and self._countdown_job:
            self.root.after_cancel(self._countdown_job)
        self._tick_countdown()

    def _reset_countdown(self):
        self._countdown_value = TURN_TIME_LIMIT
        self.timer_label.config(text=f"⏱ {self._countdown_value}s")

    def _stop_countdown(self):
        self.countdown_running = False

    def _tick_countdown(self):
        if not self.countdown_running or self.game_over:
            self.countdown_running = False
            self.timer_label.config(text="")
            return
        self.timer_label.config(text=f"⏱ {self._countdown_value}s")
        if self._countdown_value <= 0:
            self.countdown_running = False
            return
        self._countdown_value -= 1
        self._countdown_job = self.root.after(1000, self._tick_countdown)

    # ═══════════════════════════════════════════════
    #  游戏界面
    # ═══════════════════════════════════════════════
    def _setup_game_screen(self):
        self._clear_root()
        board_px = BOARD_SIZE * CELL_SIZE + PADDING * 2
        self.root.geometry(f"{board_px + 220}x{board_px + 60}")

        # 顶部信息栏
        info_frame = tk.Frame(self.root)
        info_frame.pack(side="top", fill="x", padx=10, pady=5)

        self.players_label = tk.Label(info_frame, text="", font=("", 11, "bold"), fg="#333")
        self.players_label.pack(side="left")

        self.status_label = tk.Label(info_frame, text="等待游戏开始...", font=("", 10), fg="#555")
        self.status_label.pack(side="left", padx=20)

        self.timer_label = tk.Label(info_frame, text="", font=("", 12, "bold"), fg="#D32F2F")
        self.timer_label.pack(side="right")

        # 主区域
        main_frame = tk.Frame(self.root)
        main_frame.pack(side="top")

        self.canvas = tk.Canvas(main_frame, width=board_px, height=board_px,
                                bg="#DEB887", cursor="hand2")
        self.canvas.pack(side="left")
        self.canvas.bind("<Button-1>", self._on_click)
        self._draw_board()

        # 右侧面板
        panel = tk.Frame(main_frame, width=200)
        panel.pack(side="right", fill="y", padx=(10, 0))

        tk.Label(panel, text="操作", font=("", 11, "bold")).pack(pady=(0, 10))

        tk.Button(panel, text="悔棋 (Ctrl+R)", command=lambda: self._send_cmd(CMD_UNDO),
                  width=16, bg="#FF9800", fg="white").pack(pady=3)
        tk.Button(panel, text="认输 (Ctrl+G)", command=self._confirm_resign,
                  width=16, bg="#f44336", fg="white").pack(pady=3)

        tk.Label(panel, text="").pack()

        tk.Label(panel, text="棋盘/棋子大小", font=("", 9)).pack()
        size_frame = tk.Frame(panel)
        size_frame.pack(pady=3)
        tk.Button(size_frame, text="小", command=self._set_board_small, width=4).pack(side="left", padx=2)
        tk.Button(size_frame, text="中", command=self._set_board_medium, width=4).pack(side="left", padx=2)
        tk.Button(size_frame, text="大", command=self._set_board_large, width=4).pack(side="left", padx=2)

        tk.Label(panel, text="限时时长", font=("", 9)).pack(pady=(10, 0))
        time_frame = tk.Frame(panel)
        time_frame.pack(pady=3)
        tk.Button(time_frame, text="30s", command=lambda: self._set_time_limit(30), width=4).pack(side="left", padx=2)
        tk.Button(time_frame, text="60s", command=lambda: self._set_time_limit(60), width=4).pack(side="left", padx=2)
        tk.Button(time_frame, text="90s", command=lambda: self._set_time_limit(90), width=4).pack(side="left", padx=2)

        tk.Label(panel, text="落子记录", font=("", 9)).pack(pady=(10, 0))
        self.move_listbox = tk.Listbox(panel, height=14, width=22, font=("", 8))
        self.move_listbox.pack(pady=3, fill="both", expand=True)

        # 底部重连按钮（默认隐藏）
        self.reconnect_btn = tk.Button(
            info_frame, text="手动重连", command=self._manual_reconnect,
            bg="#2196F3", fg="white")

        # 快捷键
        self.root.bind("<Control-r>", lambda e: self._send_cmd(CMD_UNDO))
        self.root.bind("<Control-R>", lambda e: self._send_cmd(CMD_UNDO))
        self.root.bind("<Control-g>", lambda e: self._confirm_resign())
        self.root.bind("<Control-G>", lambda e: self._confirm_resign())

        self.countdown_running = False
        self._start_countdown()

    # ═══════════════════════════════════════════════
    #  棋盘绘制
    # ═══════════════════════════════════════════════
    def _draw_board(self):
        self.canvas.delete("all")
        board_width = (BOARD_SIZE - 1) * CELL_SIZE

        for i in range(BOARD_SIZE):
            coord = PADDING + i * CELL_SIZE
            self.canvas.create_line(PADDING, coord, PADDING + board_width, coord, fill="#555")
            self.canvas.create_line(coord, PADDING, coord, PADDING + board_width, fill="#555")

        # 星位
        star_points = [(3, 3), (3, 7), (3, 11), (7, 3), (7, 7), (7, 11),
                       (11, 3), (11, 7), (11, 11)]
        for r, c in star_points:
            cx = PADDING + c * CELL_SIZE
            cy = PADDING + r * CELL_SIZE
            self.canvas.create_oval(cx - 3, cy - 3, cx + 3, cy + 3, fill="#333", outline="")

        for r in range(BOARD_SIZE):
            for c in range(BOARD_SIZE):
                if self.board[r][c] != 0:
                    self._draw_piece(r, c, self.board[r][c])

    def _draw_piece(self, row: int, col: int, color: int):
        cx = PADDING + col * CELL_SIZE
        cy = PADDING + row * CELL_SIZE
        fill = "#1a1a1a" if color == BLACK else "#FAFAFA"
        outline = "#555" if color == BLACK else "#333"
        self.canvas.create_oval(
            cx - PIECE_RADIUS, cy - PIECE_RADIUS,
            cx + PIECE_RADIUS, cy + PIECE_RADIUS,
            fill=fill, outline=outline, width=2
        )

    # ═══════════════════════════════════════════════
    #  点击交互
    # ═══════════════════════════════════════════════
    def _on_click(self, event):
        if self.game_over:
            messagebox.showinfo("提示", "游戏已结束")
            return
        if self.my_color == 0:
            messagebox.showinfo("提示", "您正在观战，无法落子")
            return
        if self.current_turn != self.my_color:
            messagebox.showinfo("提示", "现在不是您的回合")
            return

        now = time.time()
        if now - self._last_place_time < 0.5:
            return
        self._last_place_time = now

        col = round((event.x - PADDING) / CELL_SIZE)
        row = round((event.y - PADDING) / CELL_SIZE)

        if not (0 <= row < BOARD_SIZE and 0 <= col < BOARD_SIZE):
            return
        if self.board[row][col] != 0:
            messagebox.showinfo("提示", "该位置已有棋子")
            return

        self._send_raw(pack_message(CMD_PLACE, f"{row},{col}"))

    def _send_cmd(self, cmd: int):
        if self.game_over or not self.sock:
            return
        self._send_raw(pack_message(cmd))

    def _send_raw(self, data: bytes):
        if not self.sock:
            return
        try:
            self.sock.sendall(data)
        except Exception:
            messagebox.showerror("错误", "发送失败，连接可能已断开")
            self._show_game_terminated("发送失败")

    def _confirm_resign(self):
        if self.game_over or not self.sock:
            return
        if self.my_color == 0:
            messagebox.showinfo("提示", "旁观者不能认输")
            return
        if messagebox.askyesno("确认认输", "确定要认输吗？"):
            self._send_cmd(CMD_RESIGN)

    # ═══════════════════════════════════════════════
    #  UI 更新辅助
    # ═══════════════════════════════════════════════
    def _update_status(self, text: str):
        try:
            self.status_label.config(text=text)
        except Exception:
            pass

    def _update_players_display(self, text: str):
        try:
            self.players_label.config(text=text)
        except Exception:
            pass

    def _update_move_log(self):
        self.move_listbox.delete(0, tk.END)
        color_names = {BLACK: "●黑", WHITE: "○白"}
        for i, (color, r, c) in enumerate(self.move_history, 1):
            self.move_listbox.insert(tk.END, f"{i:02d}. {color_names.get(color,'?')} ({r},{c})")
        self.move_listbox.see(tk.END)

    def _show_game_terminated(self, reason: str):
        self.game_over = True
        self._stop_countdown()
        self._update_status(f"连接中断: {reason}")
        if hasattr(self, "reconnect_btn"):
            self.reconnect_btn.pack(side="right", padx=10)
        messagebox.showwarning("连接断开", reason)

    # ═══════════════════════════════════════════════
    #  设置
    # ═══════════════════════════════════════════════
    def _set_board_small(self):
        global CELL_SIZE, PIECE_RADIUS
        CELL_SIZE, PIECE_RADIUS = 30, 13
        self._resize_board()

    def _set_board_medium(self):
        global CELL_SIZE, PIECE_RADIUS
        CELL_SIZE, PIECE_RADIUS = 40, 17
        self._resize_board()

    def _set_board_large(self):
        global CELL_SIZE, PIECE_RADIUS
        CELL_SIZE, PIECE_RADIUS = 50, 22
        self._resize_board()

    def _resize_board(self):
        board_px = BOARD_SIZE * CELL_SIZE + PADDING * 2
        self.canvas.config(width=board_px, height=board_px)
        self.root.geometry(f"{board_px + 220}x{board_px + 60}")
        self._draw_board()

    def _set_time_limit(self, seconds: int):
        global TURN_TIME_LIMIT
        TURN_TIME_LIMIT = seconds

    def run(self):
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self.root.mainloop()

    def _on_close(self):
        self.running = False
        self._stop_countdown()
        if self.sock:
            try:
                self.sock.close()
            except Exception:
                pass
        self.root.destroy()


if __name__ == "__main__":
    GomokuClient().run()
