"""
五子棋联机游戏 — 客户端 (tkinter GUI)。
连接界面、游戏棋盘、网络通信、心跳、胜利结算动画、再来一局。
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
    CMD_REMATCH, CMD_REMATCH_ACK,
)

BOARD_SIZE = 15

# ── 可调设置 ──
CELL_SIZE = 40
PADDING = 30
PIECE_RADIUS = 17
TURN_TIME_LIMIT = 30

BLACK = 1
WHITE = 2


def str_to_board(s: str):
    """从字符串反序列化棋盘。"""
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
    """比较新旧棋盘，返回 (action, row, col, color)。"""
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
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("五子棋联机对战")
        self.root.resizable(False, False)

        self.sock: socket.socket | None = None
        self.running = False
        self.my_color = 0
        self.my_nickname = ""
        self.opponent_nickname = ""
        self.board = [[0] * BOARD_SIZE for _ in range(BOARD_SIZE)]
        self.prev_board = [[0] * BOARD_SIZE for _ in range(BOARD_SIZE)]
        self.current_turn = 1
        self.game_over = False
        self.game_result = "未分胜负"

        # 消息队列（子线程 → 主线程）
        self.msg_queue = deque()
        self.queue_lock = threading.Lock()

        self.last_heartbeat_ack = time.time()
        self._last_place_time = 0.0

        # 重连
        self.reconnect_server = ("127.0.0.1", 9527)
        self.reconnect_attempts = 0
        self.max_reconnect = 3

        # 落子记录
        self.move_history: list[tuple[int, int, int]] = []

        # 倒计时
        self._countdown_value = TURN_TIME_LIMIT
        self._countdown_job = None
        self.countdown_running = False

        # 胜利动画
        self._celebration_job = None
        self._celebration_dots: list[int] = []

        # 再来一局
        self._rematch_status = ""  # "" / "waiting_self" / "waiting_opp"

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
            elif cmd == CMD_REMATCH_ACK:
                self._handle_rematch_ack(data)
            elif cmd == CMD_ERROR:
                messagebox.showinfo("提示", data)
                if "断开" in data or "终止" in data:
                    self._show_game_terminated(data)

    # ═══════════════════════════════════════════════
    #  棋盘广播处理
    # ═══════════════════════════════════════════════
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
            self._reset_countdown()
            print(f"[DEBUG] 棋盘更新：放置 ({r}, {c})，颜色 {color}，结果 {result}")
        elif action == 'undo':
            for i in range(len(self.move_history) - 1, -1, -1):
                if self.move_history[i][1] == r and self.move_history[i][2] == c:
                    del self.move_history[i]
                    break
            self._reset_countdown()
            print(f"[DEBUG] 棋盘更新：悔棋 ({r}, {c})")

        self._draw_board()
        self._update_move_log()

        if result != "未分胜负":
            if not self.game_over:
                self.game_over = True
                self._stop_countdown()
                print(f"[DEBUG] 游戏结束：{result}，设置 game_over=True")
                self._show_game_over_overlay(result)
        else:
            self.game_over = False
            self._hide_game_over_overlay()
            self._start_countdown()
            turn_display = {1: "●黑棋", 2: "○白棋"}.get(self.current_turn, "?")
            self._update_status(f"当前回合: {turn_display}")

        if self.game_over:
            self._update_status(f"游戏结束: {result}")
        elif not self.game_over:
            turn_display = {1: "●黑棋", 2: "○白棋"}.get(self.current_turn, "?")
            self._update_status(f"当前回合: {turn_display}")

    def _handle_game_start(self, data: str):
        parts = data.split("|")
        if len(parts) >= 3:
            self.my_color = int(parts[0])
            self.opponent_nickname = parts[2]
            color_symbol = {BLACK: "●", WHITE: "○"}.get(self.my_color, "◎")
            color_name = {BLACK: "黑棋", WHITE: "白棋", 0: "旁观"}.get(self.my_color, "?")
            title = f"我方: {color_symbol}{self.my_nickname}({color_name})   VS  对手: {self.opponent_nickname}"
            self._update_players_display(title)
        self.game_over = False
        self._hide_game_over_overlay()
        self._rematch_status = ""
        self.move_history.clear()
        self._update_move_log()
        self._start_countdown()

    # ═══════════════════════════════════════════════
    #  再来一局
    # ═══════════════════════════════════════════════
    def _handle_rematch_ack(self, data: str):
        if data == "waiting_self":
            self._rematch_status = "waiting_self"
            self._update_rematch_panel("已发送请求，等待对方回应...", False)
        elif data.startswith("waiting|"):
            self._rematch_status = "waiting_opp"
            self._update_rematch_panel("对方想要再来一局！", True)
        elif data == "reject":
            self._rematch_status = ""
            self._update_rematch_panel("对方拒绝了再来一局", False)
        elif data == "reject_self":
            self._rematch_status = ""
            self._update_rematch_panel("已取消", False)

    def _request_rematch(self):
        if not self.sock:
            return
        self._send_raw(pack_message(CMD_REMATCH, "yes"))

    def _reject_rematch(self):
        if not self.sock:
            return
        self._send_raw(pack_message(CMD_REMATCH, "no"))

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
        if hasattr(self, '_countdown_job') and self._countdown_job:
            self.root.after_cancel(self._countdown_job)
        self._tick_countdown()

    def _reset_countdown(self):
        self._countdown_value = TURN_TIME_LIMIT
        self.timer_label.config(text=f"⏱ {self._countdown_value}s")

    def _stop_countdown(self):
        self.countdown_running = False
        if hasattr(self, '_countdown_job') and self._countdown_job:
            self.root.after_cancel(self._countdown_job)
            self._countdown_job = None

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
    #  胜利结算动画
    # ═══════════════════════════════════════════════
    def _show_game_over_overlay(self, result: str):
        """在棋盘上画出结算遮罩和动画。"""
        board_px = BOARD_SIZE * CELL_SIZE + PADDING * 2

        # 半透明遮罩
        self.canvas.create_rectangle(
            0, 0, board_px, board_px,
            fill="", outline="", tags="overlay"
        )

        # 深色半透明背景条
        bar_top = board_px // 2 - 55
        self.canvas.create_rectangle(
            20, bar_top, board_px - 20, bar_top + 110,
            fill="#2d2d2d", outline="#555555", tags="overlay"
        )

        # 结果文字
        result_text = result
        if "黑" in result:
            emoji = "🖤"
        elif "白" in result:
            emoji = "🤍"
        else:
            emoji = "🤝"

        self.canvas.create_text(
            board_px // 2, bar_top + 35,
            text=f"{emoji}  {result_text}  {emoji}",
            font=("", 20, "bold"), fill="#FFD700", tags="overlay",
        )

        # 再来一局按钮
        btn_y = bar_top + 72
        self.canvas.create_rectangle(
            board_px // 2 - 90, btn_y - 13, board_px // 2 - 10, btn_y + 13,
            fill="#4CAF50", outline="", tags=("overlay", "rematch_yes_btn")
        )
        self.canvas.create_text(
            board_px // 2 - 50, btn_y,
            text="再来一局", font=("", 11, "bold"), fill="white",
            tags=("overlay", "rematch_yes_btn")
        )

        self.canvas.create_rectangle(
            board_px // 2 + 10, btn_y - 13, board_px // 2 + 90, btn_y + 13,
            fill="#888", outline="", tags=("overlay", "rematch_no_btn")
        )
        self.canvas.create_text(
            board_px // 2 + 50, btn_y,
            text="离开", font=("", 11, "bold"), fill="white",
            tags=("overlay", "rematch_no_btn")
        )

        # 点击事件绑定到 overlay 上的按钮区域
        self.canvas.tag_bind("rematch_yes_btn", "<Button-1>", lambda e: self._request_rematch())
        self.canvas.tag_bind("rematch_no_btn", "<Button-1>", lambda e: self._on_close())

        # 小字提示
        status_y = btn_y + 28
        self.canvas.create_text(
            board_px // 2, status_y,
            text="", font=("", 9), fill="#CCC",
            tags=("overlay", "rematch_status_text")
        )

        # 启动庆祝粒子
        self._start_celebration()

    def _hide_game_over_overlay(self):
        self._stop_celebration()
        self.canvas.delete("overlay")

    def _update_rematch_panel(self, status_text: str, show_accept: bool):
        """更新结算叠加层上的状态文字。"""
        board_px = BOARD_SIZE * CELL_SIZE + PADDING * 2

        # 清除旧的状态和按钮
        self.canvas.delete("rematch_extra")

        status_y = board_px // 2 + 100
        self.canvas.create_text(
            board_px // 2, status_y,
            text=status_text, font=("", 9), fill="#CCC",
            tags=("overlay", "rematch_extra")
        )

        if show_accept:
            btn_y = status_y + 25
            self.canvas.create_rectangle(
                board_px // 2 + 10, btn_y - 13, board_px // 2 + 90, btn_y + 13,
                fill="#4CAF50", outline="", tags=("overlay", "rematch_extra", "accept_btn")
            )
            self.canvas.create_text(
                board_px // 2 + 50, btn_y,
                text="接受", font=("", 11, "bold"), fill="white",
                tags=("overlay", "rematch_extra", "accept_btn")
            )
            self.canvas.tag_bind("accept_btn", "<Button-1>", lambda e: self._request_rematch())

    def _start_celebration(self):
        """落子闪烁 + 随机彩色粒子动画。"""
        self._celebration_dots = []
        board_px = BOARD_SIZE * CELL_SIZE + PADDING * 2
        import random
        for _ in range(30):
            x = random.randint(30, board_px - 30)
            y = random.randint(30, board_px - 30)
            r = random.randint(3, 7)
            color = random.choice(["#FFD700", "#FF6B6B", "#4FC3F7", "#81C784",
                                    "#FFB74D", "#BA68C8", "#FFF176"])
            dot_id = self.canvas.create_oval(
                x - r, y - r, x + r, y + r,
                fill=color, outline="", tags=("overlay", "confetti")
            )
            self._celebration_dots.append({
                "id": dot_id, "x": x, "y": y, "r": r,
                "dx": random.uniform(-3, 3),
                "dy": random.uniform(-5, -1),
                "life": random.randint(20, 50),
            })
        self._animate_celebration()

    def _animate_celebration(self):
        board_px = BOARD_SIZE * CELL_SIZE + PADDING * 2
        to_remove = []
        for dot in self._celebration_dots:
            dot["life"] -= 1
            dot["x"] += dot["dx"]
            dot["y"] += dot["dy"]
            dot["dy"] += 0.2  # 重力
            r = dot["r"]
            if dot["life"] <= 0:
                to_remove.append(dot)
                self.canvas.delete(dot["id"])
            elif 0 < dot["x"] < board_px and 0 < dot["y"] < board_px:
                self.canvas.coords(dot["id"], dot["x"] - r, dot["y"] - r, dot["x"] + r, dot["y"] + r)

        for d in to_remove:
            self._celebration_dots.remove(d)

        if self._celebration_dots:
            self._celebration_job = self.root.after(40, self._animate_celebration)

    def _stop_celebration(self):
        if self._celebration_job:
            self.root.after_cancel(self._celebration_job)
            self._celebration_job = None
        self.canvas.delete("confetti")

    # ═══════════════════════════════════════════════
    #  游戏界面
    # ═══════════════════════════════════════════════
    def _setup_game_screen(self):
        self._clear_root()
        board_px = BOARD_SIZE * CELL_SIZE + PADDING * 2
        self.root.geometry(f"{board_px + 220}x{board_px + 60}")

        info_frame = tk.Frame(self.root)
        info_frame.pack(side="top", fill="x", padx=10, pady=5)

        self.players_label = tk.Label(info_frame, text="", font=("", 11, "bold"), fg="#333")
        self.players_label.pack(side="left")

        self.status_label = tk.Label(info_frame, text="等待游戏开始...", font=("", 10), fg="#555")
        self.status_label.pack(side="left", padx=20)

        self.timer_label = tk.Label(info_frame, text="", font=("", 12, "bold"), fg="#D32F2F")
        self.timer_label.pack(side="right")

        self.reconnect_btn = tk.Button(
            info_frame, text="手动重连", command=self._manual_reconnect,
            bg="#2196F3", fg="white")

        main_frame = tk.Frame(self.root)
        main_frame.pack(side="top")

        self.canvas = tk.Canvas(main_frame, width=board_px, height=board_px,
                                bg="#DEB887", cursor="hand2")
        self.canvas.pack(side="left")
        self.canvas.bind("<Button-1>", self._on_click)
        self._draw_board()

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
        self.canvas.delete("board")
        board_width = (BOARD_SIZE - 1) * CELL_SIZE

        for i in range(BOARD_SIZE):
            coord = PADDING + i * CELL_SIZE
            self.canvas.create_line(PADDING, coord, PADDING + board_width, coord,
                                    fill="#555", tags="board")
            self.canvas.create_line(coord, PADDING, coord, PADDING + board_width,
                                    fill="#555", tags="board")

        star_points = [(3, 3), (3, 7), (3, 11), (7, 3), (7, 7), (7, 11),
                       (11, 3), (11, 7), (11, 11)]
        for r, c in star_points:
            cx = PADDING + c * CELL_SIZE
            cy = PADDING + r * CELL_SIZE
            self.canvas.create_oval(cx - 3, cy - 3, cx + 3, cy + 3,
                                    fill="#333", outline="", tags="board")

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
            fill=fill, outline=outline, width=2, tags="board"
        )

    # ═══════════════════════════════════════════════
    #  点击交互
    # ═══════════════════════════════════════════════
    def _on_click(self, event):
        print(f"[DEBUG] Canvas 点击事件：({event.x}, {event.y})，game_over={self.game_over}")
        # 检查点击是否在遮罩按钮上（遮罩显示时的点击由 tag_bind 处理）
        if self.game_over:
            print(f"[DEBUG] 游戏已结束 (game_over={self.game_over})，忽略点击")
            return
        if self.my_color == 0:
            messagebox.showinfo("提示", "您正在观战，无法落子")
            return
        if self.current_turn != self.my_color:
            messagebox.showinfo("提示", f"现在不是您的回合（当前回合：{self.current_turn}，您的颜色：{self.my_color}）")
            return

        now = time.time()
        if now - self._last_place_time < 0.5:
            print(f"[DEBUG] 点击频率过快，忽略")
            return
        self._last_place_time = now

        col = round((event.x - PADDING) / CELL_SIZE)
        row = round((event.y - PADDING) / CELL_SIZE)

        if not (0 <= row < BOARD_SIZE and 0 <= col < BOARD_SIZE):
            print(f"[DEBUG] 点击越界：({row}, {col})")
            return
        if self.board[row][col] != 0:
            messagebox.showinfo("提示", f"该位置已有棋子 ({row}, {col})")
            return

        print(f"[DEBUG] 放置棋子: ({row}, {col}), 我的颜色: {self.my_color}, 当前回合: {self.current_turn}")
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
    #  UI 更新
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
        self._stop_celebration()
        if self.sock:
            try:
                self.sock.close()
            except Exception:
                pass
        self.root.destroy()


if __name__ == "__main__":
    GomokuClient().run()
