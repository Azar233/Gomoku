"""
五子棋联机 — GUI 渲染模块。所有 tkinter 操作集中在此，不含游戏逻辑与网络通信。
设计语言：iOS 玻璃拟态 (Glassmorphism) — 浅色 frosted surfaces、柔和边框、圆角、通透感。
"""

import tkinter as tk

# ── UI 可调常量 ──────────────────────────────────────
CELL_SIZE = 40
PADDING = 30
PIECE_RADIUS = 17
TURN_TIME_LIMIT = 30
BOARD_SIZE = 15
BLACK = 1
WHITE = 2

# ── 设计令牌 (Design Tokens) ─────────────────────────
# 背景
SURFACE_BG    = "#EEF1F5"   # 窗口底色 (冷灰)
CARD_BG       = "#FFFFFF"   # 卡片/面板底色
CARD_BORDER   = "#DEE2E8"   # 卡片边框

# 强调色
PRIMARY       = "#6366F1"   # 靛蓝 — 主按钮
PRIMARY_HOVER = "#4F46E5"
SUCCESS       = "#10B981"   # 翠绿 — 再来一局
SUCCESS_HOVER = "#059669"
DANGER        = "#EF4444"   # 玫红 — 认输/拒绝
DANGER_HOVER  = "#DC2626"
WARNING       = "#F59E0B"   # 琥珀 — 悔棋
WARNING_HOVER = "#D97706"

# 文字
TEXT_PRIMARY  = "#1E293B"   # 深石板色 — 标题
TEXT_SECONDARY= "#64748B"   # 石板灰 — 辅助文字
TEXT_ON_COLOR = "#FFFFFF"   # 彩色底上的白字

# 棋盘
BOARD_BG      = "#E8D5B7"   # 暖木色
BOARD_LINE    = "#B0A090"   # 网格线
STAR_DOT      = "#8B7355"   # 星位点
BLACK_STONE   = "#1E1E1E"
BLACK_RING    = "#333333"
WHITE_STONE   = "#F5F5F5"
WHITE_RING    = "#C0C0C0"

# 遮罩
SCRIM         = "#0F172A"   # 深色半透明模拟
OVERLAY_BG    = "#1E293B"
OVERLAY_BORDER= "#334155"
OVERLAY_TEXT  = "#F1F5F9"
OVERLAY_GOLD  = "#FBBF24"

# ═══════════════════════════════════════════════════════
class GameUI:
    """游戏界面管理器，负责所有 tkinter 组件与渲染。"""

    def __init__(self, root: tk.Tk, callbacks: dict):
        """
        callbacks 字典必须包含：
          on_connect(ip, port, nick)   — 连接按钮点击
          on_click(row, col)           — 棋盘点击
          on_undo()                    — 悔棋
          on_resign()                  — 认输确认
          on_rematch_yes()             — 再来一局
          on_rematch_no()              — 离开
          on_reconnect()               — 手动重连
          on_time_change(seconds)      — 限时变更
          on_size_change(cell, radius) — 棋盘尺寸变更
        """
        self.root = root
        self.cb = callbacks
        self.root.configure(bg=SURFACE_BG)

        # 控件引用（在 show_xxx_screen 中赋值）
        self._connect_status: tk.Label | None = None
        self.ip_entry: tk.Entry | None = None
        self.port_entry: tk.Entry | None = None
        self.nick_entry: tk.Entry | None = None

        self.players_label: tk.Label | None = None
        self.status_label: tk.Label | None = None
        self.timer_label: tk.Label | None = None
        self.reconnect_btn: tk.Button | None = None
        self.canvas: tk.Canvas | None = None
        self.move_listbox: tk.Listbox | None = None

        # 倒计时
        self._countdown_value = TURN_TIME_LIMIT
        self._countdown_job = None
        self.countdown_running = False

        # 通知
        self._note_job = None

        # 庆祝动画
        self._celebration_job = None
        self._celebration_dots: list[dict] = []

    # ═══════════════════════════════════════════════════
    #  辅助：创建圆角效果的 Pill 按钮 (Canvas)
    # ═══════════════════════════════════════════════════
    def _pill_button(self, canvas, x1, y1, x2, y2, text, fill, hover_fill,
                     on_click, tags=()):
        """在 Canvas 上绘制一个圆角矩形按钮，返回其 tag。"""
        tag = f"pill_{id(on_click)}"
        r = 6  # 圆角半径
        # 用多个形状模拟圆角矩形（tkinter 不支持 border-radius）
        items = []
        items.append(canvas.create_rectangle(
            x1 + r, y1, x2 - r, y2, fill=fill, outline="", tags=(tag, *tags)))
        items.append(canvas.create_rectangle(
            x1, y1 + r, x2, y2 - r, fill=fill, outline="", tags=(tag, *tags)))
        items.append(canvas.create_oval(
            x1, y1, x1 + 2*r, y1 + 2*r, fill=fill, outline="", tags=(tag, *tags)))
        items.append(canvas.create_oval(
            x2 - 2*r, y1, x2, y1 + 2*r, fill=fill, outline="", tags=(tag, *tags)))
        items.append(canvas.create_oval(
            x1, y2 - 2*r, x1 + 2*r, y2, fill=fill, outline="", tags=(tag, *tags)))
        items.append(canvas.create_oval(
            x2 - 2*r, y2 - 2*r, x2, y2, fill=fill, outline="", tags=(tag, *tags)))
        canvas.create_text((x1 + x2) // 2, (y1 + y2) // 2,
                           text=text, fill=TEXT_ON_COLOR,
                           font=("", 11, "bold"), tags=(tag, *tags))

        def on_enter(e):
            for i in items:
                canvas.itemconfig(i, fill=hover_fill)
        def on_leave(e):
            for i in items:
                canvas.itemconfig(i, fill=fill)

        canvas.tag_bind(tag, "<Button-1>", lambda e: on_click())
        canvas.tag_bind(tag, "<Enter>", on_enter)
        canvas.tag_bind(tag, "<Leave>", on_leave)
        return tag

    # ═══════════════════════════════════════════════════
    #  连接界面
    # ═══════════════════════════════════════════════════
    def _clear_root(self):
        for w in self.root.winfo_children():
            w.destroy()
        self.canvas = None

    def show_connect_screen(self):
        self._clear_root()
        self.root.geometry("420x380")
        self.root.configure(bg=SURFACE_BG)

        # 居中容器
        container = tk.Frame(self.root, bg=SURFACE_BG)
        container.place(relx=0.5, rely=0.5, anchor="center")

        # 毛玻璃卡片
        card = tk.Frame(container, bg=CARD_BG,
                        highlightbackground=CARD_BORDER, highlightthickness=1,
                        padx=40, pady=36)
        card.pack()

        tk.Label(card, text="五子棋", font=("", 26, "bold"),
                 fg=TEXT_PRIMARY, bg=CARD_BG).pack()
        tk.Label(card, text="联机对战", font=("", 13),
                 fg=TEXT_SECONDARY, bg=CARD_BG).pack(pady=(2, 24))

        # 字段样式
        field_font = ("", 11)
        label_font = ("", 9)

        for i, (label, _, default, width) in enumerate([
            ("IP 地址", "ip", "127.0.0.1", 22),
            ("端口", "port", "9527", 22),
            ("昵称", "nick", "", 22),
        ]):
            tk.Label(card, text=label, font=label_font,
                     fg=TEXT_SECONDARY, bg=CARD_BG, anchor="w").pack(
                fill="x", pady=(10 if i > 0 else 0, 2))

            entry = tk.Entry(card, font=field_font,
                             bg="#F8FAFC",
                             fg=TEXT_PRIMARY,
                             insertbackground=TEXT_PRIMARY,
                             relief="solid",
                             highlightbackground=CARD_BORDER,
                             highlightthickness=1,
                             width=width)
            entry.insert(0, default)
            entry.pack(ipady=6)

            if label == "IP 地址":
                self.ip_entry = entry
            elif label == "端口":
                self.port_entry = entry
            else:
                self.nick_entry = entry

        # 连接按钮
        btn_frame = tk.Frame(card, bg=CARD_BG)
        btn_frame.pack(pady=(20, 8))
        btn = tk.Button(btn_frame, text="连接服务器",
                        command=self._on_connect_btn,
                        font=("", 12, "bold"),
                        bg=PRIMARY, fg=TEXT_ON_COLOR,
                        activebackground=PRIMARY_HOVER,
                        activeforeground=TEXT_ON_COLOR,
                        relief="flat", padx=32, pady=8,
                        cursor="hand2", borderwidth=0)
        btn.pack()

        self._connect_status = tk.Label(card, text="", font=("", 9),
                                        fg=TEXT_SECONDARY, bg=CARD_BG)
        self._connect_status.pack(pady=(4, 0))

        self.root.bind("<Return>", lambda e: self._on_connect_btn())

    def set_connect_status(self, text: str, color: str):
        if self._connect_status:
            self._connect_status.config(text=text, fg=color)

    def _on_connect_btn(self):
        ip = self.ip_entry.get().strip() if self.ip_entry else ""
        try:
            port = int(self.port_entry.get().strip()) if self.port_entry else 0
        except ValueError:
            self.set_connect_status("端口必须为整数", DANGER)
            return
        nickname = self.nick_entry.get().strip() if self.nick_entry else ""
        if not nickname:
            self.set_connect_status("请输入昵称", DANGER)
            return
        self.set_connect_status("正在连接...", PRIMARY)
        self.root.update()
        self.cb['on_connect'](ip, port, nickname)

    # ═══════════════════════════════════════════════════
    #  游戏界面
    # ═══════════════════════════════════════════════════
    def show_game_screen(self):
        self._clear_root()
        board_px = BOARD_SIZE * CELL_SIZE + PADDING * 2
        panel_w = 220
        self.root.geometry(f"{board_px + panel_w + 30}x{board_px + 80}")
        self.root.configure(bg=SURFACE_BG)

        # ── 顶部信息栏 ──
        info_frame = tk.Frame(self.root, bg=CARD_BG,
                              highlightbackground=CARD_BORDER,
                              highlightthickness=1)
        info_frame.pack(side="top", fill="x", padx=12, pady=(10, 6), ipady=6)

        self.players_label = tk.Label(info_frame, text="",
                                      font=("", 11, "bold"),
                                      fg=TEXT_PRIMARY, bg=CARD_BG)
        self.players_label.pack(side="left", padx=12)

        self.status_label = tk.Label(info_frame, text="等待游戏开始...",
                                     font=("", 10),
                                     fg=TEXT_SECONDARY, bg=CARD_BG)
        self.status_label.pack(side="left", padx=8)

        self.timer_label = tk.Label(info_frame, text="",
                                    font=("", 13, "bold"),
                                    fg=DANGER, bg=CARD_BG)
        self.timer_label.pack(side="right", padx=12)

        self.reconnect_btn = tk.Button(
            info_frame, text="重连", command=self.cb['on_reconnect'],
            font=("", 9), bg=PRIMARY, fg=TEXT_ON_COLOR,
            activebackground=PRIMARY_HOVER, relief="flat",
            cursor="hand2", padx=8, borderwidth=0)

        # ── 主区域 ──
        main_frame = tk.Frame(self.root, bg=SURFACE_BG)
        main_frame.pack(side="top", padx=12, pady=(0, 10))

        self._note_job = None

        self.canvas = tk.Canvas(main_frame, width=board_px, height=board_px,
                                bg=BOARD_BG, cursor="hand2",
                                highlightthickness=0)
        self.canvas.pack(side="left")
        self.canvas.bind("<Button-1>", self._on_canvas_click)

        # ── 右侧面板 ──
        panel = tk.Frame(main_frame, width=panel_w, bg=CARD_BG,
                         highlightbackground=CARD_BORDER,
                         highlightthickness=1)
        panel.pack(side="right", fill="y", padx=(10, 0))
        panel.pack_propagate(False)

        # 操作区
        tk.Label(panel, text="操作", font=("", 11, "bold"),
                 fg=TEXT_PRIMARY, bg=CARD_BG).pack(pady=(16, 10))

        btn_opts = {"font": ("", 10), "relief": "flat",
                    "activeforeground": TEXT_ON_COLOR, "cursor": "hand2",
                    "borderwidth": 0, "padx": 20, "pady": 6, "width": 14}

        tk.Button(panel, text="悔棋 (Ctrl+R)", command=self.cb['on_undo'],
                  bg=WARNING, fg=TEXT_ON_COLOR,
                  activebackground=WARNING_HOVER, **btn_opts).pack(pady=4)

        tk.Button(panel, text="认输 (Ctrl+G)", command=self.cb['on_resign'],
                  bg=DANGER, fg=TEXT_ON_COLOR,
                  activebackground=DANGER_HOVER, **btn_opts).pack(pady=4)

        # 分隔
        tk.Frame(panel, height=1, bg=CARD_BORDER).pack(fill="x", padx=20, pady=12)

        # 尺寸
        tk.Label(panel, text="棋盘/棋子大小", font=("", 9),
                 fg=TEXT_SECONDARY, bg=CARD_BG).pack()
        size_frame = tk.Frame(panel, bg=CARD_BG)
        size_frame.pack(pady=4)
        size_btn = {"font": ("", 9), "relief": "flat", "borderwidth": 0,
                    "cursor": "hand2", "padx": 8, "pady": 4, "width": 4}
        for label, cell, radius in [("小", 30, 13), ("中", 40, 17), ("大", 50, 22)]:
            is_default = (cell == 40)
            tk.Button(size_frame, text=label,
                      command=lambda c=cell, r=radius: self.cb['on_size_change'](c, r),
                      bg=PRIMARY if is_default else "#CBD5E1",
                      fg=TEXT_ON_COLOR if is_default else TEXT_PRIMARY,
                      activebackground=PRIMARY_HOVER if is_default else "#A8B4C4",
                      **size_btn).pack(side="left", padx=3)

        # 限时
        tk.Label(panel, text="限时时长", font=("", 9),
                 fg=TEXT_SECONDARY, bg=CARD_BG).pack(pady=(12, 0))
        time_frame = tk.Frame(panel, bg=CARD_BG)
        time_frame.pack(pady=4)
        for secs in [30, 60, 90]:
            is_default = (secs == 30)
            tk.Button(time_frame, text=f"{secs}s",
                      command=lambda s=secs: self.cb['on_time_change'](s),
                      bg=PRIMARY if is_default else "#CBD5E1",
                      fg=TEXT_ON_COLOR if is_default else TEXT_PRIMARY,
                      activebackground=PRIMARY_HOVER if is_default else "#A8B4C4",
                      **size_btn).pack(side="left", padx=3)

        # 分隔
        tk.Frame(panel, height=1, bg=CARD_BORDER).pack(fill="x", padx=20, pady=12)

        # 落子记录
        tk.Label(panel, text="落子记录", font=("", 9),
                 fg=TEXT_SECONDARY, bg=CARD_BG).pack()
        list_frame = tk.Frame(panel, bg=CARD_BORDER)
        list_frame.pack(pady=4, padx=14, fill="both", expand=True)
        self.move_listbox = tk.Listbox(list_frame, height=12, width=20,
                                       font=("Consolas", 9),
                                       bg="#F8FAFC", fg=TEXT_PRIMARY,
                                       selectbackground=PRIMARY,
                                       selectforeground=TEXT_ON_COLOR,
                                       relief="flat",
                                       highlightthickness=0,
                                       borderwidth=1)
        self.move_listbox.pack(fill="both", expand=True)

        # 键盘绑定
        self.root.bind("<Control-r>", lambda e: self.cb['on_undo']())
        self.root.bind("<Control-R>", lambda e: self.cb['on_undo']())
        self.root.bind("<Control-g>", lambda e: self.cb['on_resign']())
        self.root.bind("<Control-G>", lambda e: self.cb['on_resign']())

    # ═══════════════════════════════════════════════════
    #  棋盘绘制
    # ═══════════════════════════════════════════════════
    def draw_board(self, board: list[list[int]]):
        if not self.canvas:
            return
        self.canvas.delete("board")
        board_width = (BOARD_SIZE - 1) * CELL_SIZE

        # 网格线
        for i in range(BOARD_SIZE):
            coord = PADDING + i * CELL_SIZE
            self.canvas.create_line(PADDING, coord, PADDING + board_width, coord,
                                    fill=BOARD_LINE, width=1, tags="board")
            self.canvas.create_line(coord, PADDING, coord, PADDING + board_width,
                                    fill=BOARD_LINE, width=1, tags="board")

        # 星位点
        star_points = [(3, 3), (3, 7), (3, 11), (7, 3), (7, 7), (7, 11),
                       (11, 3), (11, 7), (11, 11)]
        for r, c in star_points:
            cx = PADDING + c * CELL_SIZE
            cy = PADDING + r * CELL_SIZE
            self.canvas.create_oval(cx - 3, cy - 3, cx + 3, cy + 3,
                                    fill=STAR_DOT, outline="", tags="board")

        # 棋子
        for r in range(BOARD_SIZE):
            for c in range(BOARD_SIZE):
                if board[r][c] != 0:
                    self._draw_piece(r, c, board[r][c])

    def _draw_piece(self, row: int, col: int, color: int):
        if not self.canvas:
            return
        cx = PADDING + col * CELL_SIZE
        cy = PADDING + row * CELL_SIZE
        r = PIECE_RADIUS

        if color == BLACK:
            # 玻璃质感黑子：深色底 + 高光点
            self.canvas.create_oval(
                cx - r, cy - r, cx + r, cy + r,
                fill=BLACK_STONE, outline=BLACK_RING, width=1, tags="board")
            # 高光
            hl_r = r * 0.3
            self.canvas.create_oval(
                cx - hl_r - 2, cy - hl_r - 2,
                cx + hl_r - 2, cy + hl_r - 2,
                fill="", outline="#555555", width=1, tags="board")
        else:
            # 玻璃质感白子：浅色底 + 高光点 + 浅灰环
            self.canvas.create_oval(
                cx - r, cy - r, cx + r, cy + r,
                fill=WHITE_STONE, outline=WHITE_RING, width=1, tags="board")
            hl_r = r * 0.3
            self.canvas.create_oval(
                cx - hl_r - 2, cy - hl_r - 2,
                cx + hl_r - 2, cy + hl_r - 2,
                fill="", outline="#E0E0E0", width=1, tags="board")

    # ═══════════════════════════════════════════════════
    #  棋盘点击
    # ═══════════════════════════════════════════════════
    def _on_canvas_click(self, event):
        overlapping = self.canvas.find_overlapping(
            event.x - 1, event.y - 1, event.x + 1, event.y + 1)
        for item in overlapping:
            tags = self.canvas.gettags(item)
            if "confirm_overlay" in tags or "overlay" in tags:
                return
        col = round((event.x - PADDING) / CELL_SIZE)
        row = round((event.y - PADDING) / CELL_SIZE)
        self.cb['on_click'](row, col)

    # ═══════════════════════════════════════════════════
    #  毛玻璃遮罩辅助
    # ═══════════════════════════════════════════════════
    def _glass_overlay_bg(self, tag, alpha_steps=5):
        """模拟毛玻璃暗色遮罩：绘制多层半透明条纹。"""
        if not self.canvas:
            return
        board_px = BOARD_SIZE * CELL_SIZE + PADDING * 2
        # 使用交叉的细线模拟磨砂质感
        for i in range(0, board_px, alpha_steps):
            shade = f"#{int(15*(1 - i/board_px)):02x}{int(23*(1 - i/board_px)):02x}{int(42*(1 - i/board_px)):02x}"
            self.canvas.create_line(i, 0, i, board_px, fill=shade, tags=tag)
        # 主 scrim 矩形
        self.canvas.create_rectangle(0, 0, board_px, board_px,
                                     fill="", outline="", tags=tag)

    # ═══════════════════════════════════════════════════
    #  确认遮罩（认输）
    # ═══════════════════════════════════════════════════
    def show_confirm_overlay(self):
        if not self.canvas:
            return
        board_px = BOARD_SIZE * CELL_SIZE + PADDING * 2

        # scrim
        self.canvas.create_rectangle(0, 0, board_px, board_px,
                                     fill=SCRIM, stipple="gray25",
                                     outline="", tags="confirm_overlay")

        # frosted card
        card_w, card_h = 300, 110
        cx, cy = board_px // 2, board_px // 2
        x1, y1 = cx - card_w // 2, cy - card_h // 2
        x2, y2 = cx + card_w // 2, cy + card_h // 2

        self.canvas.create_rectangle(x1, y1, x2, y2,
                                     fill=OVERLAY_BG, outline=OVERLAY_BORDER,
                                     width=1, tags="confirm_overlay")

        self.canvas.create_text(cx, y1 + 28,
                                text="确定要认输吗？", font=("", 14, "bold"),
                                fill=OVERLAY_TEXT, tags="confirm_overlay")

        # 确认按钮
        btn_w = 96
        self._pill_button(self.canvas,
                          cx - btn_w - 12, y1 + 52, cx - 12, y1 + 80,
                          "确认认输", DANGER, DANGER_HOVER,
                          self._on_confirm_yes, ("confirm_overlay",))
        # 取消按钮
        self._pill_button(self.canvas,
                          cx + 12, y1 + 52, cx + btn_w + 12, y1 + 80,
                          "取消", "#64748B", "#475569",
                          self.hide_confirm_overlay, ("confirm_overlay",))

        # 点击 scrim 也可取消
        self.canvas.tag_bind("confirm_overlay", "<Button-1>",
                             lambda e: None)  # 阻止穿透

    def _on_confirm_yes(self):
        self.hide_confirm_overlay()
        self.cb['on_resign_confirm']()

    def hide_confirm_overlay(self):
        if self.canvas:
            self.canvas.delete("confirm_overlay")

    # ═══════════════════════════════════════════════════
    #  胜利结算遮罩
    # ═══════════════════════════════════════════════════
    def show_game_over_overlay(self, result: str):
        if not self.canvas:
            return
        board_px = BOARD_SIZE * CELL_SIZE + PADDING * 2

        # scrim
        self.canvas.create_rectangle(0, 0, board_px, board_px,
                                     fill=SCRIM, stipple="gray25",
                                     outline="", tags="overlay")

        card_w, card_h = 320, 150
        cx, cy = board_px // 2, board_px // 2
        x1, y1 = cx - card_w // 2, cy - card_h // 2
        x2, y2 = cx + card_w // 2, cy + card_h // 2

        self.canvas.create_rectangle(x1, y1, x2, y2,
                                     fill=OVERLAY_BG, outline=OVERLAY_BORDER,
                                     width=1, tags="overlay")

        if "黑" in result:
            emoji = "⚫"
        elif "白" in result:
            emoji = "⚪"
        else:
            emoji = "🤝"
        self.canvas.create_text(cx, y1 + 32,
                                text=f"{emoji}  {result}  {emoji}",
                                font=("", 20, "bold"), fill=OVERLAY_GOLD,
                                tags="overlay")

        btn_y = y1 + 76
        btn_w = 100
        # 再来一局
        self._pill_button(self.canvas,
                          cx - btn_w - 14, btn_y - 14, cx - 14, btn_y + 14,
                          "再来一局", SUCCESS, SUCCESS_HOVER,
                          lambda: self.cb['on_rematch_yes'](), ("overlay",))
        # 离开
        self._pill_button(self.canvas,
                          cx + 14, btn_y - 14, cx + btn_w + 14, btn_y + 14,
                          "离开", "#64748B", "#475569",
                          lambda: self.cb['on_rematch_no'](), ("overlay",))

        # 状态文字区域占位
        self.canvas.create_text(cx, y2 + 20, text="",
                                font=("", 9), fill=OVERLAY_TEXT,
                                tags=("overlay", "rematch_status_text"))

        self._start_celebration()

    def hide_game_over_overlay(self):
        self._stop_celebration()
        if self.canvas:
            self.canvas.delete("overlay")
            self.canvas.delete("confirm_overlay")

    def update_rematch_panel(self, status_text: str, show_accept: bool = False,
                             show_reject: bool = False):
        if not self.canvas:
            return
        self.canvas.delete("rematch_extra")

        board_px = BOARD_SIZE * CELL_SIZE + PADDING * 2
        card_h = 150
        y2 = board_px // 2 + card_h // 2
        status_y = y2 + 34

        self.canvas.create_text(board_px // 2, status_y,
                                text=status_text, font=("", 9),
                                fill="#94A3B8", tags=("overlay", "rematch_extra"))

        if show_accept or show_reject:
            btn_y = status_y + 26
            btn_w = 90
            if show_accept and show_reject:
                self._pill_button(self.canvas,
                                  board_px // 2 - btn_w - 12, btn_y - 13,
                                  board_px // 2 - 12, btn_y + 13,
                                  "接受", SUCCESS, SUCCESS_HOVER,
                                  lambda: self.cb['on_rematch_yes'](),
                                  ("overlay", "rematch_extra"))
                self._pill_button(self.canvas,
                                  board_px // 2 + 12, btn_y - 13,
                                  board_px // 2 + btn_w + 12, btn_y + 13,
                                  "拒绝", DANGER, DANGER_HOVER,
                                  lambda: self.cb['on_rematch_reject'](),
                                  ("overlay", "rematch_extra"))
            elif show_accept:
                self._pill_button(self.canvas,
                                  board_px // 2 - btn_w // 2, btn_y - 13,
                                  board_px // 2 + btn_w // 2, btn_y + 13,
                                  "接受", SUCCESS, SUCCESS_HOVER,
                                  lambda: self.cb['on_rematch_yes'](),
                                  ("overlay", "rematch_extra"))
            elif show_reject:
                self._pill_button(self.canvas,
                                  board_px // 2 - btn_w // 2, btn_y - 13,
                                  board_px // 2 + btn_w // 2, btn_y + 13,
                                  "取消", "#64748B", "#475569",
                                  lambda: self.cb['on_rematch_reject'](),
                                  ("overlay", "rematch_extra"))

    # ═══════════════════════════════════════════════════
    #  庆祝粒子动画
    # ═══════════════════════════════════════════════════
    def _start_celebration(self):
        if not self.canvas:
            return
        import random
        self._celebration_dots = []
        board_px = BOARD_SIZE * CELL_SIZE + PADDING * 2
        colors = ["#FBBF24", "#F472B6", "#38BDF8", "#34D399",
                  "#FB923C", "#A78BFA", "#FDE047"]
        for _ in range(30):
            x = random.randint(30, board_px - 30)
            y = random.randint(30, board_px - 30)
            r = random.randint(3, 7)
            color = random.choice(colors)
            dot_id = self.canvas.create_oval(
                x - r, y - r, x + r, y + r,
                fill=color, outline="", tags=("overlay", "confetti"))
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
            dot["dy"] += 0.2
            r = dot["r"]
            if dot["life"] <= 0:
                to_remove.append(dot)
                self.canvas.delete(dot["id"])
            elif 0 < dot["x"] < board_px and 0 < dot["y"] < board_px:
                self.canvas.coords(dot["id"],
                                   dot["x"] - r, dot["y"] - r,
                                   dot["x"] + r, dot["y"] + r)
        for d in to_remove:
            self._celebration_dots.remove(d)
        if self._celebration_dots:
            self._celebration_job = self.root.after(40, self._animate_celebration)

    def _stop_celebration(self):
        if self._celebration_job:
            self.root.after_cancel(self._celebration_job)
            self._celebration_job = None
        if self.canvas:
            self.canvas.delete("confetti")

    # ═══════════════════════════════════════════════════
    #  倒计时
    # ═══════════════════════════════════════════════════
    def start_countdown(self):
        self._countdown_value = TURN_TIME_LIMIT
        self.countdown_running = True
        if self.timer_label:
            self.timer_label.config(text=f"⏱ {self._countdown_value}s")
        if self._countdown_job:
            self.root.after_cancel(self._countdown_job)
        self._tick_countdown()

    def reset_countdown(self):
        self._countdown_value = TURN_TIME_LIMIT
        if self.timer_label:
            self.timer_label.config(text=f"⏱ {self._countdown_value}s")

    def stop_countdown(self):
        self.countdown_running = False
        if self._countdown_job:
            self.root.after_cancel(self._countdown_job)
            self._countdown_job = None

    def _tick_countdown(self):
        if not self.countdown_running:
            if self.timer_label:
                self.timer_label.config(text="")
            return
        if self.timer_label:
            self.timer_label.config(text=f"⏱ {self._countdown_value}s")
        if self._countdown_value <= 0:
            self.countdown_running = False
            return
        self._countdown_value -= 1
        self._countdown_job = self.root.after(1000, self._tick_countdown)

    # ═══════════════════════════════════════════════════
    #  通知浮层（Canvas 居中弹出，不改变布局）
    # ═══════════════════════════════════════════════════
    def notify(self, text: str, level: str = "info"):
        """level: info / warn / error — 在棋盘中央显示浮层，3 秒后消失。"""
        if not self.canvas:
            return
        color_map = {
            "info": ("#1E293B", "#F1F5F9"),
            "warn": ("#78350F", "#FEF3C7"),
            "error": ("#991B1B", "#FEE2E2"),
        }
        fg, bg = color_map.get(level, color_map["info"])

        self.canvas.delete("notify")
        board_px = BOARD_SIZE * CELL_SIZE + PADDING * 2
        cx, cy = board_px // 2, board_px // 2

        font = ("", 12, "bold")
        text_id = self.canvas.create_text(cx, cy, text=text, font=font, fill=fg,
                                          tags="notify")
        bbox = self.canvas.bbox(text_id)
        if bbox:
            pad_x, pad_y = 28, 14
            x1, y1, x2, y2 = bbox
            # 圆角背景
            r = 8
            self.canvas.create_rectangle(
                x1 - pad_x + r, y1 - pad_y, x2 + pad_x - r, y2 + pad_y,
                fill=bg, outline="#E2E8F0", tags="notify")
            self.canvas.create_rectangle(
                x1 - pad_x, y1 - pad_y + r, x2 + pad_x, y2 + pad_y - r,
                fill=bg, outline="", tags="notify")
            for (ox, oy) in [(x1 - pad_x, y1 - pad_y), (x2 + pad_x - 2*r, y1 - pad_y),
                              (x1 - pad_x, y2 + pad_y - 2*r), (x2 + pad_x - 2*r, y2 + pad_y - 2*r)]:
                self.canvas.create_oval(ox, oy, ox + 2*r, oy + 2*r,
                                        fill=bg, outline="", tags="notify")
            self.canvas.lift(text_id)

        if self._note_job:
            self.root.after_cancel(self._note_job)
        self._note_job = self.root.after(3000, self._hide_notification)

    def _hide_notification(self):
        if self.canvas:
            self.canvas.delete("notify")

    # ═══════════════════════════════════════════════════
    #  状态更新
    # ═══════════════════════════════════════════════════
    def update_status(self, text: str):
        if self.status_label:
            try:
                self.status_label.config(text=text)
            except Exception:
                pass

    def update_players_display(self, text: str):
        if self.players_label:
            try:
                self.players_label.config(text=text)
            except Exception:
                pass

    def update_move_log(self, moves: list[tuple[int, int, int]]):
        if not self.move_listbox:
            return
        self.move_listbox.delete(0, tk.END)
        color_names = {BLACK: "●黑", WHITE: "○白"}
        for i, (color, r, c) in enumerate(moves, 1):
            self.move_listbox.insert(tk.END,
                                     f"{i:02d}. {color_names.get(color,'?')} ({r},{c})")
        self.move_listbox.see(tk.END)

    def show_terminated(self, reason: str):
        """显示断线状态：重连按钮 + 错误通知。"""
        if self.reconnect_btn:
            self.reconnect_btn.pack(side="right", padx=10)
        self.notify(reason, "error")

    # ═══════════════════════════════════════════════════
    #  棋盘尺寸调整
    # ═══════════════════════════════════════════════════
    def resize_board(self, cell_size: int, piece_radius: int,
                     board: list[list[int]]):
        global CELL_SIZE, PIECE_RADIUS
        CELL_SIZE = cell_size
        PIECE_RADIUS = piece_radius
        board_px = BOARD_SIZE * CELL_SIZE + PADDING * 2
        if self.canvas:
            self.canvas.config(width=board_px, height=board_px)
        self.root.geometry(f"{board_px + 250}x{board_px + 80}")
        self.draw_board(board)

    # ═══════════════════════════════════════════════════
    #  生命周期
    # ═══════════════════════════════════════════════════
    def set_on_close(self, handler):
        self.root.protocol("WM_DELETE_WINDOW", handler)

    def run(self):
        self.root.mainloop()

    def destroy(self):
        try:
            self.root.destroy()
        except Exception:
            pass
