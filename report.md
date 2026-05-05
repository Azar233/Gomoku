## 基本信息
* 姓名: [占位符1]、[占位符2]
* 班级: [占位符1]、[占位符2]
* 报告名称：计算机网络专题实验实验七报告

## 一、实验名称
双人联机五子棋网络游戏的设计与实现

## 二、实验原理

### 2.1 C/S 架构与 TCP 长连接

本系统采用经典的客户端/服务器（Client/Server）模式。服务器端绑定固定 IP 与端口（默认 `0.0.0.0:9527`），侦听客户端连接请求。每有一个客户端通过 TCP 三次握手建立连接，服务器即创建一个独立的守护线程（daemon thread）处理该客户端的完整生命周期。客户端与服务器之间维持一条 TCP 长连接，游戏全程通过该连接双向通信，避免了短连接频繁握手/挥手的开销。

TCP 提供可靠的字节流传输保障（顺序交付、无丢失、无重复），这是选择 TCP 而非 UDP 的核心原因——棋盘状态的每一次更新都必须准确到达对端，不能容忍丢包或乱序。

### 2.2 自定义应用层协议：长度前缀法解决 TCP 粘包

TCP 是面向字节流的协议，发送方多次 `send()` 的数据可能在接收方缓冲区合并（粘包），也可能被 TCP 分片传输。为解决消息边界问题，本系统设计了自定义应用层报文协议，采用**长度前缀法（Length-Prefixed Framing）**：

**报文格式**（固定 7 字节头部 + 可变长度数据段）：

```
┌──────────┬──────────┬──────────────┬──────────────────┐
│ 魔数 2B  │ 指令 1B  │ 数据长度 4B   │   数据段 (UTF-8)  │
│ 0x58 0x51│ 0x01~0x0B│ 大端序 uint32 │   可变长度        │
└──────────┴──────────┴──────────────┴──────────────────┘
```

- **魔数 (Magic Number)**：`\x58\x51`（即 ASCII 字符 "XQ"），用于快速识别合法报文，过滤垃圾数据或协议错位。
- **指令类型 (Command)**：1 字节无符号整数，标识报文语义（连接、落子、悔棋、认输、心跳等共 11 种）。
- **数据长度 (Payload Length)**：4 字节大端序无符号整数，声明数据段的字节数，接收方可据此精确切割报文边界。
- **数据段 (Payload)**：UTF-8 编码的字符串，承载具体业务数据（如坐标 `"7,7"`、棋盘状态序列化字符串）。

接收方工作流程：先调用 `recv_exact(7)` 精确读取头部 → 解析魔数校验 → 提取 data_length → 再调用 `recv_exact(data_length)` 精确读取数据段。这种"固定头 + 变长体"的设计从根本上杜绝了粘包/半包导致的解析错误。

### 2.3 多线程并发控制

服务器端为每个客户端连接分配一个独立线程（`handle_client`），同时还有一个全局守护线程 `timeout_monitor` 每 5 秒巡检所有房间的落子超时。各线程共享全局房间字典 `rooms` 及每个 `GameRoom` 对象的棋盘状态。为避免竞态条件（race condition），采用 `threading.Lock()` 互斥锁保护所有对共享数据的读写操作。

**关键约束**：Python 的 `threading.Lock` 是非重入锁（non-reentrant），同一线程不可重复获取已持有的锁。因此所有广播函数（`broadcast_state`、`broadcast_game_start`）必须在释放锁之后调用，否则会死锁。

客户端同样采用双线程架构：网络接收子线程（`RecvThread`）持续阻塞读取 socket，收到完整报文后通过回调将消息入队（`deque` + `threading.Lock` 保护）；主线程通过 `root.after(50, ...)` 定时消费队列，在 tkinter 主线程上下文中安全更新 UI，避免了"非主线程操作 GUI 组件导致崩溃"的经典问题。

## 三、实验目的

1. 掌握 TCP Socket 编程：理解 `socket()` → `bind()` → `listen()` → `accept()` 的服务器端完整流程，以及客户端的 `connect()` → `sendall()` → `recv()` 模式。
2. 掌握自定义应用层协议设计：通过魔数校验、长度前缀法解决 TCP 粘包/半包问题，设计清晰的消息类型枚举体系。
3. 掌握多线程编程与并发控制：理解守护线程的生命周期管理，正确使用互斥锁保护共享数据，避免死锁与竞态条件。
4. 掌握网络游戏状态同步机制：服务器权威（Server-Authoritative）模式下，客户端仅发送操作指令，服务器校验并广播权威状态。
5. 掌握基础 GUI 编程：使用 tkinter 构建棋盘渲染、事件响应、动画效果，理解主线程事件循环与子线程通信的隔离机制。
6. 培养系统健壮性设计思维：心跳检测、断线重连、异常数据容错、边界条件防御。

## 四、实验内容

### 4.1 基本功能

1. **TCP 双人联机通信**：两个客户端通过服务器中转，实现实时对弈。
2. **棋盘状态广播同步**：服务器接收落子指令后，校验合法性，更新棋盘，将完整状态序列化后广播给双方客户端。
3. **五连胜负判断**：以落子位置为中心，沿水平、垂直、两条对角线四个方向计数连续同色棋子，达到 5 颗即判胜；棋盘满子无五连则判平局。
4. **回合控制**：服务器维护 `current_turn` 状态（1=黑棋, 2=白棋），拒绝非当前回合玩家的落子请求。

### 4.2 高级功能

1. **悔棋机制**：每局每方最多 3 次悔棋机会；仅上一回合被动方（即当前回合方）可请求悔棋；悔棋后撤销对方上一颗棋子，切换回对方回合。
2. **认输功能**：任意回合可认输，服务器判定对手获胜并更新计分。
3. **心跳检测与超时断线处理**：客户端每 5 秒发送心跳包，服务器原样回复；若 45 秒未收到心跳应答，客户端判定连接断开。服务器每 5 秒巡检落子超时（30 秒），超时方判负。任一方断线，服务器通知幸存方"对手已断开，游戏终止"。
4. **断线自动重连**：客户端检测到断线后自动尝试重连（最多 3 次，间隔 2 秒），保留原服务器地址与昵称信息；同时提供"手动重连"按钮。
5. **多房间系统**：服务器使用字典 `rooms: dict[int, GameRoom]` 管理多个并发房间，新玩家优先加入未满房间，玩家全离开后自动清理空房间。
6. **再来一局（Rematch）**：游戏结束后，任一方可发起再来一局请求；对方收到后可选"接受"或"拒绝"；双方均同意则服务器重置棋盘，广播新游戏开始；一方拒绝则通知对方。
7. **落子超时判负**：服务器端 30 秒倒计时，超时未落子自动判负并更新积分。
8. **计分持久化**：每局结束将胜负平结果写入 `score.txt`，格式为 `昵称,胜场,平局,负场`，启动时加载历史战绩。
9. **庆祝粒子动画**：游戏结束显示 30 颗随机彩色粒子，带重力和生命周期模拟。
10. **iOS 玻璃拟态 UI**：模拟毛玻璃效果（frosted glass），采用冷灰色调基底、白色卡片面板、柔和边框、圆角按钮、暗色半透明遮罩，棋子增加高光环模拟玻璃反光质感。
11. **棋盘/棋子尺寸可调**：提供小/中/大三档切换，以及 30s/60s/90s 三档限时时长。
12. **落子防抖**：客户端 500ms 内禁止重复落子，避免网络延迟下的误操作。

## 五、实验实现

### 5.1 人员分工

| 职责 | 负责人 | 具体内容 |
|------|--------|----------|
| 服务端架构设计 | [占位符1] | 自定义协议报文格式定义、魔数校验机制、`GameRoom` 状态机设计、多房间匹配逻辑 |
| 协议封装模块 | [占位符1] | `protocol.py` — 打包/解包函数、`recv_exact` 粘包安全接收、11 种指令类型枚举 |
| 并发控制与多线程调试 | [占位符1] | 互斥锁粒度优化、死锁排查（rematch/timeout 广播死锁修复）、计分持久化线程安全 |
| 客户端 UI 设计 | [占位符2] | tkinter 界面布局、玻璃拟态视觉系统、Canvas 棋盘渲染、粒子动画、通知浮层 |
| 客户端网络同步逻辑 | [占位符2] | `NetworkManager` 网络层封装、消息队列消费机制、`board_diff` 增量更新算法 |
| 边界异常处理与测试 | [占位符2] | 断线重连流程测试、粘包模拟测试、非法输入防御、跨平台兼容性验证 |

### 5.2 实验设计

#### （一）协议设计

**传输层**：TCP，服务器默认监听 `0.0.0.0:9527`。

**消息格式**：固定 7 字节头部 + 可变长度 UTF-8 数据段（详见第二章 2.2 节）。

**指令集**（共 11 种）：

| 指令 | 值 | 方向 | 数据内容 | 说明 |
|------|-----|------|----------|------|
| `CMD_CONNECT` | 0x01 | C→S | 昵称 | 建立连接后的身份注册 |
| `CMD_PLACE` | 0x02 | C→S | `"行,列"` | 落子请求 |
| `CMD_UNDO` | 0x03 | C→S | 空 | 悔棋请求 |
| `CMD_RESIGN` | 0x04 | C→S | 空 | 认输请求 |
| `CMD_BROADCAST` | 0x05 | S→C | 棋盘状态\|回合\|结果\|当前回合方 | 服务器推送权威状态 |
| `CMD_HEARTBEAT` | 0x06 | 双向 | 空 | 心跳保活 |
| `CMD_ERROR` | 0x07 | S→C | 错误消息 | 操作拒绝/异常通知 |
| `CMD_GAME_START` | 0x08 | S→C | 己方颜色\|颜色名\|对手昵称 | 游戏开始通知 |
| `CMD_UNDO_RESULT` | 0x09 | S→C | 更新后的棋盘状态 | 悔棋结果 |
| `CMD_REMATCH` | 0x0A | C→S | `"yes"` 或 `"no"` | 再来一局请求/应答 |
| `CMD_REMATCH_ACK` | 0x0B | S→C | `"waiting_self"` / `"waiting|颜色"` / `"reject"` / `"reject_self"` | 再来一局状态通知 |

**状态转换流程**：

```
Client A                    Server                    Client B
   |                          |                          |
   |── CONNECT("Alice") ─────>|                          |
   |                          | [等待对手]               |
   |                          |<──── CONNECT("Bob") ─────|
   |                          |                          |
   |<── GAME_START(黑, Bob) ──|── GAME_START(白, Alice) ─>|
   |<── BROADCAST(board) ─────|──── BROADCAST(board) ────>|
   |                          |                          |
   |── PLACE("7,7") ─────────>|                          |
   |                          | [校验回合+位置]          |
   |<── BROADCAST(新board) ───|──── BROADCAST(新board) ──>|
   |                          |                          |
   ...                     ...                        ...
```

**错误处理**：服务器在以下情况通过 `CMD_ERROR` 拒绝操作并保持游戏状态不变：落子位置越界、该位置已有棋子、非己方回合、游戏已结束、悔棋次数耗尽、非上一回合被动方请求悔棋、再来一局请求在游戏未结束时发送。客户端对无法解析的报文（魔数非法、UTF-8 解码失败）静默丢弃，避免崩溃。

#### （二）UI 设计

采用 tkinter 构建，分为连接界面与游戏界面两大视图。

**连接界面**：居中白色玻璃卡片，包含 IP、端口、昵称三个输入框及连接按钮。输入框使用浅灰底色（`#F8FAFC`）配合实线边框，主按钮采用靛蓝色（`#6366F1`）扁平风格。按回车键等效点击连接按钮。连接过程中显示状态反馈（连接中/成功/失败）。

**游戏界面**：三区域水平布局。
- **顶部信息栏**：白色玻璃面板，显示双方信息、回合状态、倒计时及重连按钮。
- **左侧棋盘区**：Canvas 组件绘制 15×15 网格线、9 个星位点、棋子。棋子增加高光环（黑子用 `#555` 环、白子用 `#E0E0E0` 环）模拟玻璃反光。点击事件通过 Canvas 的 `<Button-1>` 绑定，计算最近交叉点坐标，经过遮罩层穿透检测、回合校验、防抖处理后发送 `CMD_PLACE`。
- **右侧面板**：白色玻璃卡片，包含操作按钮（悔棋/认输）、尺寸切换（小/中/大三档）、限时切换（30s/60s/90s）、落子记录列表。

**浮层系统**：
- **通知浮层**：Canvas 居中圆角矩形 + 文字，按 info/warn/error 三个级别区分底色（浅灰/浅黄/浅红），3 秒自动消失，不改变布局。
- **确认遮罩**：深色半透明 scrim（`stipple="gray25"` 模拟）+ 居中 frosted card + 圆角操作按钮。用于认输确认。
- **结算遮罩**：同上结构 + 结果文字（金色）+ "再来一局"/"离开"按钮 + 30 颗彩色粒子庆祝动画。

**落子防抖**：客户端记录 `_last_place_time`，500ms 内重复点击直接忽略，避免网络延迟导致的连续误触。

#### （三）框架结构

项目共 5 个源文件，按职责清晰分层：

```
Gomoku/
├── protocol.py      # 协议层：报文打包/解包、粘包安全接收
├── server.py        # 服务器：多房间管理、游戏逻辑、并发控制、计分持久化
├── client.py        # 客户端编排器：游戏状态持有、网络↔UI 桥接
├── client_net.py    # 客户端网络层：纯 socket 通信、心跳、重连（零 tkinter 依赖）
└── client_ui.py     # 客户端 UI 层：所有 tkinter 渲染（零游戏逻辑）
```

**服务器端架构**：

```
main()
  ├── socket() → bind() → listen()
  ├── timeout_monitor (daemon thread) — 每 5s 巡检落子超时
  └── accept() 循环
       └── handle_client(conn, addr) (daemon thread per client)
            ├── 等待 CMD_CONNECT → 分配房间
            ├── 主消息循环 (recv_message → 解析 cmd → 处理)
            │    ├── CMD_PLACE   → 校验回合/位置 → 更新棋盘 → 判定胜负 → broadcast_state
            │    ├── CMD_UNDO    → 校验悔棋资格 → 撤销 → broadcast_state
            │    ├── CMD_RESIGN  → 标记对手获胜 → broadcast_state
            │    ├── CMD_REMATCH → 协商再来一局 → 双方同意则 reset + broadcast_game_start
            │    └── CMD_HEARTBEAT → 回复心跳
            └── finally: 清理房间、关闭连接
```

**共享数据保护**：
- `rooms_lock`（全局）：保护 `rooms` 字典和 `next_room_id` 的增删操作。
- `room.lock`（每个房间）：保护棋盘 `board`、`current_turn`、`game_over`、`move_history`、`undo_count`、`rematch_ready` 等状态的读写。
- **关键原则**：`broadcast_state()` 和 `broadcast_game_start()` 内部会尝试获取 `room.lock`（通过 `board_to_str`），因此绝对不能在持有 `room.lock` 时调用这些广播函数，否则同一线程将因 Python 非重入锁而永久阻塞（死锁）。正确做法是：在锁内设置标志位或准备数据，退出锁后再调用广播。

**客户端端架构**：

```
GomokuClient (client.py)
  ├── NetworkManager (client_net.py)
  │    ├── connect() → sock.connect() + send CMD_CONNECT
  │    ├── RecvThread → recv_message() 循环 → on_message 回调
  │    └── HeartbeatThread → 每 5s 发 CMD_HEARTBEAT，45s 无 ACK 则 on_disconnected
  ├── GameUI (client_ui.py)
  │    ├── Connect Screen → 表单输入 → on_connect 回调
  │    ├── Game Screen → Canvas 棋盘 + 右侧面板
  │    ├── Overlays → 确认遮罩 / 结算遮罩 / 通知浮层
  │    └── Animations → 庆祝粒子 / 倒计时 tick
  └── Message Queue (deque + Lock)
       └── _process_queue() root.after(50) 主线程消费
```

**子线程→主线程通信保护**：网络回调 `_on_net_message` 和 `_on_net_disconnected` 在子线程中执行，仅做一件事——将消息入队（`queue_lock` 保护 `deque.append`）。主线程通过 `root.after(50, self._process_queue)` 每 50ms 消费队列，在 tkinter 主线程上下文中安全调用 UI 方法。这是经典的 **Producer-Consumer + Main Thread Dispatch** 模式，避免了 tkinter 的线程不安全问题。

### 5.3 关键代码描述

#### 代码段 1：协议层粘包安全接收（`protocol.py`）

**功能**：从 TCP 字节流中精确提取一个完整报文，利用长度前缀法解决粘包/半包问题。

**编写人**：[占位符1]

**核心逻辑**：

```python
def recv_exact(sock, n: int) -> Optional[bytes]:
    """从 socket 精确接收 n 字节数据。"""
    buf = b""
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            return None  # 连接关闭
        buf += chunk
    return buf


def recv_message(sock) -> Optional[Tuple[int, str]]:
    """先读 7 字节头部，解析魔数和数据长度，再按长度读数据段。"""
    header = recv_exact(sock, HEADER_SIZE)  # 精确 7 字节
    if header is None:
        return None
    cmd, length = parse_header(header)      # 校验魔数 + 提取长度
    if length > 0:
        payload_bytes = recv_exact(sock, length)  # 精确 N 字节
    # ...
    return cmd, payload
```

`recv_exact` 的核心价值在于：即使 `sock.recv()` 单次返回不足 n 字节（TCP 分片），或一次返回了多条消息的数据（TCP 粘包），函数内部通过 `while` 循环持续接收直到凑足 n 字节，剩余数据留待下一次 `recv_message` 调用处理。这从根本上避免了手动拼接/切割字节流的繁琐与出错。

#### 代码段 2：服务端落子并发锁控制（`server.py`）

**功能**：在互斥锁保护下完成落子校验、棋盘更新、胜负判定，并安全释放锁后再广播。

**编写人**：[占位符1]

**核心逻辑**：

```python
# 在 handle_client 的消息循环中：
elif cmd == CMD_PLACE:
    with room.lock:
        if room.game_over:
            send_error(player, "游戏已结束")
            continue
        if room.current_turn != player.color:
            send_error(player, "现在不是您的回合")
            continue
        # ... 坐标解析、越界检测、重复检测 ...
        room.board[row][col] = player.color
        room.last_move = (row, col)
        is_win = check_win(room.board, row, col, player.color)
        if is_win:
            room.game_over = True
            room.winner = player.color
            update_score(winner_name, "wins")
            update_score(loser_name, "losses")
        elif is_board_full(room.board):
            room.game_over = True
            room.winner = 0
        else:
            room.current_turn = 3 - room.current_turn  # 切换回合
    # ⚠️ 锁在此处释放，broadcast_state 在锁外调用
    broadcast_state(room)
```

所有状态读写（棋盘、回合、胜负标记）在 `with room.lock` 临界区内原子完成；广播函数在锁外调用，防止同一线程重复获取非重入锁导致死锁。

#### 代码段 3：客户端消息队列消费机制（`client.py`）

**功能**：子线程安全入队，主线程定时消费，确保 UI 更新只发生在 tkinter 主线程。

**编写人**：[占位符2]

**核心逻辑**：

```python
def _on_net_message(self, cmd: int, data: str):
    """子线程回调——仅入队，不做任何 UI 操作。"""
    self._queue_msg(("message", cmd, data))

def _queue_msg(self, msg):
    with self.queue_lock:
        self.msg_queue.append(msg)

def _process_queue(self):
    """主线程消费循环，每 50ms 执行一次。"""
    try:
        with self.queue_lock:
            while self.msg_queue:
                msg = self.msg_queue.popleft()
                self._handle_msg(msg)
    finally:
        if self._alive:
            self.root.after(50, self._process_queue)
```

这种设计将"网络数据到达"与"UI 状态更新"解耦：子线程只负责入队，不接触任何 tkinter 组件；`root.after()` 确保 `_handle_msg` 总是在主线程的 tkinter 事件循环中执行，符合 tkinter 的线程安全约束。

## 六、测试及结果分析

### 6.1 测试 1：基础联机与落子同步

**测试过程**：
1. 启动服务器 `python server.py`，确认输出 `[服务器] 监听 0.0.0.0:9527`。
2. 启动客户端 A，输入昵称 "Alice"，点击连接。
3. 启动客户端 B，输入昵称 "Bob"，点击连接。
4. Alice（黑棋先行）点击棋盘交叉点 (7,7)，观察双方界面是否同步显示黑子。
5. Bob（白棋）点击 (7,8)，观察同步。
6. 交替落子直至一方五连，观察结算遮罩是否弹出。

**结果与分析**：
- 双方棋盘实时同步，延迟 < 50ms（局域网环境）。
- 五连判定准确：服务器在落子后沿四个方向计数，确认达到 5 颗后立即标记 `game_over=True` 并广播结果。
- 结算遮罩正常弹出，显示获胜方，庆祝粒子动画播放流畅。
- 非当前回合方点击棋盘时，客户端拦截（`current_turn != my_color`）并弹出 "现在不是您的回合" 通知；同时服务器端也有相同校验作为双重保险，防止客户端绕过（如直接发 `CMD_PLACE`）。
- 点击已有棋子的位置时，服务器校验 `board[row][col] != 0`，返回错误提示；客户端也做了同样的前置检查。

### 6.2 测试 2：TCP 粘包与异常数据测试

**测试过程**：
1. 模拟高频落子：编写脚本连续快速发送多个 `CMD_PLACE` 报文（间隔 < 10ms），模拟网络波动下的粘包场景。
2. 模拟魔数非法：向服务器发送随机字节流。
3. 模拟数据长度不匹配：发送声明长度为 100 但实际仅 10 字节数据的畸形报文。
4. 模拟 UTF-8 解码失败：在数据段中插入非法字节序列（如单字节 `\xFF`）。

**Bug 重现与调试**：

早期版本中，当客户端快速连续点击棋盘时，偶发出现"棋盘状态错乱"——某次落子未显示、或显示在错误位置。经过日志排查发现：服务器端确实收到了每次落子，但客户端某次 `recv()` 调用一次性接收了两次 `BROADCAST` 的数据，而旧版接收代码仅处理了第一条消息。

**调试过程**：
1. 在 `recv_message` 中添加调试日志，打印每次读取的 `length` 值。
2. 对比发送端日志与接收端日志，发现某次 `BROADCAST` 的 `length` 值被解析为异常大的数值（约 4GB）。
3. 追踪到根因：当两条消息粘在一起时，旧代码使用了 `sock.recv(4096)` 固定缓冲读取，未实现"先读头部、再按长度读数据段"的两步精确读取流程。
4. **解决方案**：重构为 `recv_exact(n)` 函数，先精确读 7 字节头部，解析出 `data_length` 后，再精确读取 `data_length` 字节。剩余数据自然留在 socket 缓冲区等待下一次 `recv_message` 调用。这一改动彻底消灭了粘包/半包问题。

**结果验证**：
- 改造后的 `recv_message` 经 500 次连续高频落子测试，无一次消息错位或状态不同步。
- 非法魔数的报文被 `parse_header` 抛出 `ValueError` 后直接丢弃（`return None`），连接不中断。
- 数据长度不匹配时，`recv_exact(length)` 要么读满指定长度，要么因连接关闭返回 `None`，不会出现"读到半截当成完整消息"的情况。
- UTF-8 非法字节被 `try/except UnicodeDecodeError` 捕获，返回空字符串，避免了服务端崩溃。

### 6.3 测试 3：并发一致性与断线异常测试

**测试过程**：
1. **锁机制验证**：启动两个客户端正常对弈，同时在服务器代码中插入断言日志，监控共享变量在不同线程中的读写时序。
2. **悔棋并发测试**：双方同时点击悔棋按钮，验证服务器互斥处理。
3. **落子超时测试**：Alice 回合故意不落子，等待 30 秒，验证服务器 `timeout_monitor` 是否正确判 Alice 负。
4. **断线恢复测试**：在 Bob 回合中强制关闭 Bob 的客户端进程（模拟崩溃），观察服务器和 Alice 客户端的反应。
5. **死锁回归测试**：在游戏结束后点击"再来一局"，双方均同意，验证新游戏是否正确开始、棋盘是否清空。

**Bug 重现与调试**：

**Bug 1：计分更新导致断线（KeyError `'losss'`）**

在早期版本中，每次游戏结束后，胜方的客户端会立即断开连接。日志显示服务器端抛出 `KeyError: 'losss'` 异常，导致 `handle_client` 线程崩溃退出。

**调试过程**：
1. 在 `update_score` 函数入口打印调用栈，发现传入的 key 参数为 `"losss"` 而非预期的 `"losses"`。
2. 追踪到旧代码：`update_score(player, f"{result}s")`——当 `result = "loss"` 时拼接得到 `"losss"`，而非字典键 `"losses"`。
3. **解决方案**：改为由调用方直接传递精确的键名（如 `update_score(winner_name, "wins")`、`update_score(loser_name, "losses")`），消除字符串拼写的歧义。

**Bug 2：再来一局死锁（线程阻塞）**

游戏结束后双方均同意再来一局，但新游戏永远不会开始——两个客户端界面都卡在结算遮罩。

**调试过程**：
1. 在服务器 `CMD_REMATCH` 处理分支添加日志，发现代码执行到 `room.reset()` 后即停止，后续的 `broadcast_game_start(room)` 从未执行。
2. 通过 `threading` 模块的 `active_count()` 和线程名排查，发现处理 rematch 的工作线程状态为 "blocked on lock acquire"。
3. 根因确认：`broadcast_game_start(room)` 被错误放置在 `with room.lock:` 代码块内，而 `broadcast_game_start` → `broadcast_state` → `board_to_str` 内部会尝试获取同一把 `room.lock`。Python 的 `threading.Lock` 是非重入锁，同一线程无法重复获取，导致永久阻塞。
4. 同样的问题也存在于 `timeout_monitor` 线程：`broadcast_state(room)` 放在 `with room.lock:` 内。但由于超时线程与落子线程不是同一线程，表现不同——超时线程在锁内调用广播时，广播内部试图再次获取锁，同样阻塞。
5. **解决方案**：
   - Rematch 处理：在锁内设置 `start_new_game = True` 标志，退出锁后调用 `broadcast_game_start(room)`。
   - Timeout 处理：在锁内设置 `should_broadcast = True` 标志，退出锁后调用 `broadcast_state(room)`。

**Bug 3：Tkinter 非法颜色值崩溃**

游戏结束弹出结算遮罩时，客户端直接抛出 `TclError: invalid color name "#1a1a1acc"`，遮罩未渲染。

**调试过程**：
1. 错误信息定位到遮罩背景色 `#1a1a1acc`（8 位十六进制，末尾 `cc` 为 alpha 通道 80%）。
2. 问题明确：tkinter 不支持 CSS 的 8 位 hex 颜色格式（`#RRGGBBAA`），仅支持 6 位 `#RRGGBB`。
3. **解决方案**：改为使用纯色 `#2d2d2d` 配合边框 `#555555` 实现相近的视觉效果。在后续玻璃拟态改版中，进一步使用 `stipple="gray25"` 模拟半透明 scrim。

**Bug 4：通知提示导致布局抖动**

早期通知使用 `tk.Label` 组件 pack 到棋盘上方，每次弹出通知时，棋盘整体向下推移，消失时又弹回——视觉效果极差。

**调试过程**：
1. 分析 tkinter 的 pack 布局管理器：新增 `Label` pack 到 `info_frame` 下方会触发所有后续组件的重新布局计算。
2. **解决方案**：将通知改为 Canvas 居中浮层（`create_rectangle` + `create_text`），使用绝对坐标定位，完全脱离布局管理器。通知的显示/隐藏不影响任何已 pack 的组件。

**结果验证**：
- 计分字典键名修复后，`score.txt` 正确记录胜负平数据，无崩溃。
- 死锁修复后，连续 10 轮"再来一局"测试全部通过，新棋盘正确清空。
- 颜色修复后，所有遮罩正常渲染。
- 双侧同时点击悔棋时，服务器仅处理先到达的请求，另一请求合法返回错误提示（"只有上一回合的玩家才能请求悔棋"），无状态不一致。
- 落子超时 30 秒后，`timeout_monitor` 正确判超时方负，对方客户端显示胜利。
- 强制杀死 Bob 客户端后，服务器 45 秒内心跳超时检测到断线，通知 Alice "对手已断开连接，游戏终止"；Alice 客户端显示终止状态，提供重连按钮。
- Bob 重启客户端后可成功重连（保留原昵称和服务器地址），恢复对弈。

## 七、实验结论

本项目成功实现了一个功能完善、稳定可靠的双人联机五子棋系统。各模块运行情况总结如下：

1. **自定义协议层**：7 字节固定头 + 变长数据段的长度前缀法设计，经过粘包模拟测试与高频落子压力测试，证明能可靠地在 TCP 字节流上划分消息边界。魔数校验机制有效过滤了非法数据，UTF-8 解码异常保护避免了因错误数据导致的线程崩溃。

2. **服务器多线程并发**：通过 `rooms_lock` + `room.lock` 两层互斥锁机制，保障了多客户端同时操作下的数据一致性。明确了"广播在锁外"的铁律，消除了两次死锁事故的隐患。超时监控线程与客户端处理线程协同工作，正确覆盖了落子超时和断线异常两种边界情况。

3. **客户端网络-UI 隔离**：`deque` + `queue_lock` + `root.after(50)` 的三段式消息传递机制，使得网络子线程与 tkinter 主线程彻底解耦，彻底规避了非主线程操作 GUI 的崩溃风险。

4. **用户体验**：玻璃拟态界面视觉统一，通知浮层、确认遮罩、结算动画三个浮层系统互不干扰，倒计时与防抖提升了操作容错性。悔棋、认输、再来一局、断线重连等进阶功能均通过完整交互流程测试。

系统整体达到预期稳定性要求，可作为计算机网络课程中 Socket 编程、自定义协议设计、多线程并发控制三个核心知识点的综合实践案例。

## 八、总结及心得体会

### [占位符1] 的心得体会

负责服务端架构和协议层的开发，让我对计算机网络课本上的"TCP 字节流""粘包"等概念有了切身体会。

**协议设计的教训**：最初我过于乐观地使用了 `sock.recv(4096)` 固定缓冲读取，认为"发一次对应收一次"。直到高频落子测试中，棋盘状态出现随机错乱，我才意识到 TCP 根本不保证消息边界。排查过程非常煎熬——日志显示服务器发送正确，但客户端解析出的坐标却偏离了。最终通过在接收端逐字节打印 hex dump 才锁定了粘包根因。`recv_exact(n)` 函数的引入是本次实验最有价值的工程决策——先精确读头、再精确读体，一个循环解决了所有边界问题。

**死锁排查的血泪教训**：再来一局功能开发完成后，测试时双方都点了同意，但游戏永远不开始。服务器日志显示 `room.reset()` 执行完毕，但后续代码没有输出。我以为 `broadcast_game_start` 内部抛了异常，加了 try/except 却没有任何异常。最后通过 PyCharm 的线程转储（Thread Dump）看到工作线程状态是 "waiting for lock"，才反应过来——我在持有锁的情况下调用了需要同一把锁的函数。这让我深刻理解了 Python `threading.Lock` 的非重入特性，以及"临界区最小化"原则的重要性。

**整体感悟**：服务器开发最核心的能力不是算法，而是对并发边界条件的把控。每写一行涉及共享变量的代码，都必须问自己三个问题：此时持有哪些锁？这个函数内部会尝试获取哪些锁？异常发生时锁会正确释放吗（`with` 语句的上下文管理器救了命）？

### [占位符2] 的心得体会

负责客户端 UI 和网络同步逻辑的开发，让我对图形界面的事件驱动模型和子线程通信有了全新认识。

**tkinter 线程安全的坑**：第一次写网络回调时，我直接在子线程里调用了 `label.config(text=...)`，结果程序随机闪退，报错信息极其模糊（"TclError: expected integer but got ..."）。查阅资料才知道 tkinter 是单线程模型，所有 GUI 操作必须在主线程执行。改用 `deque` 消息队列 + `root.after()` 定时消费后，问题彻底消失。这个教训让我养成了一个习惯：任何涉及 UI 的框架，第一件事就是确认其线程模型。

**UI 布局的演进**：从最初的简易 Label pack，到通知弹出时布局跳动被测试同学吐槽，再到 Canvas 绝对定位浮层，最后到完整的玻璃拟态视觉系统——界面的打磨是一个不断迭代的过程。虽然不是前端方向的作业，但好的 UI 确实让调试效率成倍提升（比如一眼就能看出棋盘状态是否正确同步）。

**board_diff 算法的巧思**：为了在客户端实现落子记录的增量更新（而非每次全量重建），我设计了一个 `board_diff` 函数，通过比较 `prev_board` 和当前 `board` 找到变化的那一格的 `(action, row, col, color)`。这个看似简单的函数实际上解决了"服务器全量推送 vs 客户端增量展示"的矛盾——服务器不需要记录 diff，客户端自己算出来。

**整体感悟**：客户端开发的难点在于状态管理——连接中、等待对手、对弈中、已结束、断线中……每种状态下 UI 应该如何呈现、哪些按钮可点击、哪些操作要拦截。把所有这些状态收敛到 `GomokuClient` 编排器的几个核心变量（`game_over`, `_rematch_status`, `is_connected`）中，让状态机逻辑可追溯、可测试，是这次架构拆分最大的收获。

## 附件

### 1. 源码文件

| 文件 | 行数 | 功能 |
|------|------|------|
| `protocol.py` | 100 | 自定义协议编解码、粘包安全接收 |
| `server.py` | 508 | 服务器主程序：多房间、游戏逻辑、并发控制、计分持久化 |
| `client.py` | 353 | 客户端编排器：游戏状态持有、网络↔UI 消息桥接 |
| `client_net.py` | 107 | 客户端网络层：TCP 连接、心跳、自动重连 |
| `client_ui.py` | 598 | 客户端 UI 层：玻璃拟态界面、棋盘渲染、浮层系统 |

### 2. 相关文档

- `report.md` — 本实验报告
- `score.txt` — 玩家积分持久化文件

### 3. 参考资料

- Python 官方文档 — `socket` 模块: https://docs.python.org/3/library/socket.html
- Python 官方文档 — `threading` 模块: https://docs.python.org/3/library/threading.html
- Python 官方文档 — `tkinter` 模块: https://docs.python.org/3/library/tkinter.html
- Python `struct` 模块 — 二进制数据打包: https://docs.python.org/3/library/struct.html
- TCP 粘包与拆包原理: https://www.zhihu.com/question/20210025
- iOS Human Interface Guidelines — Glassmorphism: https://developer.apple.com/design/human-interface-guidelines/
