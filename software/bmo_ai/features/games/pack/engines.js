/* Original BMO offline games. MIT; Copyright (c) 2026 BGH.
 * Deterministic mechanics and canvas artwork written for this project.
 * No timers, DOM listeners, audio, storage, network, or device access here.
 * The Desktop owns the one animation loop and supplies normalized inputs.
 */
(() => {
  'use strict';
  const DIRECTIONS = Object.freeze({
    UP: {name: 'UP', dx: 0, dy: -1}, DOWN: {name: 'DOWN', dx: 0, dy: 1},
    LEFT: {name: 'LEFT', dx: -1, dy: 0}, RIGHT: {name: 'RIGHT', dx: 1, dy: 0}
  });
  const COLORS = ['#162c3a', '#75e3c0', '#eabe72', '#e897ac', '#8ac2ee', '#b7a0eb', '#e5e788', '#f0a576'];
  const clone = value => JSON.parse(JSON.stringify(value));
  const delta = value => typeof value === 'number' && Number.isFinite(value) ? Math.max(0, Math.min(.25, value)) : 0;
  const cellKey = (x, y) => x + ',' + y;
  function random(seed) {
    let state = seed >>> 0;
    return () => { state ^= state << 13; state ^= state >>> 17; state ^= state << 5; return (state >>> 0) / 4294967296; };
  }
  function rectangle(ctx, x, y, w, h, color, radius = 0) {
    ctx.fillStyle = color;
    if (radius && typeof ctx.roundRect === 'function') { ctx.beginPath(); ctx.roundRect(x, y, w, h, radius); ctx.fill(); }
    else ctx.fillRect(x, y, w, h);
  }
  function text(ctx, value, x, y, size = 20, color = '#e8fff3', align = 'left') {
    ctx.fillStyle = color; ctx.font = `600 ${size}px system-ui,sans-serif`; ctx.textAlign = align; ctx.fillText(String(value), x, y);
  }
  function shell(ctx, width, height, title, subtitle, score, best) {
    ctx.save(); ctx.scale(width / 800, height / 480);
    rectangle(ctx, 0, 0, 800, 480, '#081d27');
    rectangle(ctx, 12, 12, 776, 456, '#102a36', 18);
    text(ctx, title, 30, 43, 25); text(ctx, subtitle, 30, 62, 11, '#91b6bb');
    rectangle(ctx, 532, 78, 235, 326, '#173844', 13);
    text(ctx, 'SCORE', 553, 108, 12, '#a4c7c9'); text(ctx, score, 553, 145, 34, '#f6d889');
    text(ctx, 'BEST THIS SESSION', 553, 172, 10, '#a4c7c9'); text(ctx, best, 553, 197, 20);
    ctx.restore();
  }
  function overlay(ctx, width, height, status, title, details) {
    if (status === 'playing') return;
    ctx.save(); ctx.scale(width / 800, height / 480);
    rectangle(ctx, 25, 170, 485, 140, '#071923ee', 13);
    text(ctx, status === 'paused' ? 'PAUSED' : status === 'won' ? 'GARDEN COMPLETE!' : title, 267, 219, 28, '#f6d889', 'center');
    text(ctx, status === 'paused' ? 'START or pause button to continue' : details, 267, 254, 14, '#d6f4e9', 'center');
    ctx.restore();
  }
  class Game {
    constructor(id) { this.id = id; this.closed = false; this.best = 0; this.held = new Set(); }
    common(key, pressed) {
      if (this.closed || typeof pressed !== 'boolean') return true;
      if (!pressed) return false;
      if (['gameover', 'won'].includes(this.status) && ['A', 'B', 'START'].includes(key)) { this.restart(); return true; }
      if (key === 'START') { this.status === 'paused' ? this.resume() : this.pause(); return true; }
      return this.status !== 'playing';
    }
    pause() { if (!this.closed && this.status === 'playing') { this.status = 'paused'; this.releaseInputs(); } }
    resume() { if (!this.closed && this.status === 'paused') this.status = 'playing'; }
    releaseInputs() { this.held.clear(); }
    close() { this.releaseInputs(); this.closed = true; if (this.status === 'playing') this.status = 'paused'; }
    scored(points) { this.score += points; this.best = Math.max(this.best, this.score); }
    commonState() { return {game_id: this.id, status: this.status, score: this.score, best: this.best, closed: this.closed}; }
  }

  // STACK SPROUT: original falling-polyomino implementation.
  const SHAPES = Object.freeze({
    I: [[-1, 0], [0, 0], [1, 0], [2, 0]], O: [[0, 0], [1, 0], [0, 1], [1, 1]],
    T: [[-1, 0], [0, 0], [1, 0], [0, 1]], L: [[-1, 0], [0, 0], [1, 0], [1, -1]],
    J: [[-1, -1], [-1, 0], [0, 0], [1, 0]], S: [[-1, 1], [0, 1], [0, 0], [1, 0]],
    Z: [[-1, 0], [0, 0], [0, 1], [1, 1]]
  });
  const PIECE_NAMES = Object.keys(SHAPES);
  class Blocks extends Game {
    constructor() { super('bmo_blocks'); this.restart(); }
    restart() {
      if (this.closed) return;
      this.status = 'playing'; this.score = this.lines = this.drops = 0; this.level = 1;
      this.board = Array.from({length: 18}, () => Array(10).fill(0)); this.queue = ['O', 'I', 'T'];
      this.rng = random(0x5370726f); this.gravity = this.repeat = this.soft = 0;
      this.held.clear(); this.horizontal = null; this.last_clear = 0; this._spawn();
    }
    _fill() {
      while (this.queue.length < 8) {
        const bag = PIECE_NAMES.slice();
        for (let i = bag.length - 1; i > 0; i--) { const j = Math.floor(this.rng() * (i + 1)); [bag[i], bag[j]] = [bag[j], bag[i]]; }
        this.queue.push(...bag);
      }
    }
    _spawn() {
      this._fill(); const kind = this.queue.shift();
      this.piece = {kind, x: 4, y: 1, rotation: 0, cells: clone(SHAPES[kind])}; this.gravity = 0;
      if (!this._fits(this.piece.x, this.piece.y, this.piece.cells)) { this.status = 'gameover'; this.releaseInputs(); }
    }
    _fits(x, y, cells) { return cells.every(([dx, dy]) => x + dx >= 0 && x + dx < 10 && y + dy < 18 && (y + dy < 0 || this.board[y + dy][x + dx] === 0)); }
    _move(dx, dy, points = 0) {
      if (!this._fits(this.piece.x + dx, this.piece.y + dy, this.piece.cells)) return false;
      this.piece.x += dx; this.piece.y += dy; if (points) this.scored(points); return true;
    }
    _rotate() {
      if (this.piece.kind === 'O') return;
      const cells = this.piece.cells.map(([x, y]) => [-y, x]);
      for (const [dx, dy] of [[0, 0], [-1, 0], [1, 0], [-2, 0], [2, 0], [0, -1]]) {
        if (this._fits(this.piece.x + dx, this.piece.y + dy, cells)) {
          this.piece.x += dx; this.piece.y += dy; this.piece.cells = cells; this.piece.rotation = (this.piece.rotation + 1) % 4; return;
        }
      }
    }
    _lock() {
      for (const [dx, dy] of this.piece.cells) {
        const x = this.piece.x + dx, y = this.piece.y + dy;
        if (y < 0) { this.status = 'gameover'; this.releaseInputs(); return; }
        this.board[y][x] = PIECE_NAMES.indexOf(this.piece.kind) + 1;
      }
      const remaining = this.board.filter(row => row.some(value => value === 0));
      this.last_clear = 18 - remaining.length;
      if (this.last_clear) {
        this.lines += this.last_clear; this.scored([0, 100, 260, 480, 800][this.last_clear] * this.level);
        while (remaining.length < 18) remaining.unshift(Array(10).fill(0)); this.board = remaining;
        this.level = 1 + Math.floor(this.lines / 6);
      }
      this.drops++; this._spawn();
    }
    input(key, pressed) {
      if (this.common(key, pressed)) return;
      if (!pressed) { this.held.delete(key); if (key === this.horizontal) this.horizontal = null; return; }
      if (this.status !== 'playing' || this.held.has(key)) return;
      this.held.add(key);
      if (key === 'LEFT' || key === 'RIGHT') { this._move(key === 'LEFT' ? -1 : 1, 0); this.horizontal = key; this.repeat = .18; }
      else if (key === 'UP' || key === 'A') this._rotate();
      else if (key === 'DOWN') { if (!this._move(0, 1, 1)) this._lock(); this.soft = .06; }
      else if (key === 'B') { while (this._move(0, 1, 2)) {} this._lock(); }
    }
    update(value) {
      if (this.closed || this.status !== 'playing') return;
      const dt = delta(value);
      if (this.horizontal && this.held.has(this.horizontal)) {
        this.repeat -= dt;
        for (let i = 0; i < 3 && this.repeat <= 0; i++, this.repeat += .10) this._move(this.horizontal === 'LEFT' ? -1 : 1, 0);
      }
      if (this.held.has('DOWN')) {
        this.soft -= dt;
        for (let i = 0; i < 5 && this.soft <= 0 && this.status === 'playing'; i++, this.soft += .06) if (!this._move(0, 1, 1)) this._lock();
      } else {
        this.gravity += dt; const interval = Math.max(.11, .65 - (this.level - 1) * .06);
        if (this.gravity >= interval) { this.gravity -= interval; if (!this._move(0, 1)) this._lock(); }
      }
    }
    releaseInputs() { super.releaseInputs(); this.horizontal = null; }
    snapshot() {
      return clone({...this.commonState(), cols: 10, rows: 18, board: this.board, piece: this.piece,
        next: this.queue.slice(0, 3), lines: this.lines, drops: this.drops, level: this.level, last_clear: this.last_clear});
    }
    draw(ctx, width = 800, height = 480) {
      shell(ctx, width, height, 'STACK SPROUT', 'Build tidy rows. Make room for the next little shape.', this.score, this.best);
      ctx.save(); ctx.scale(width / 800, height / 480); const size = 20, ox = 148, oy = 77;
      rectangle(ctx, ox - 5, oy - 5, 210, 370, '#071b27', 7);
      const block = (x, y, color, ghost = false) => {
        if (y < 0) return; rectangle(ctx, ox + x * size + 1, oy + y * size + 1, size - 2, size - 2, color, 3);
        if (!ghost) rectangle(ctx, ox + x * size + 4, oy + y * size + 3, size - 8, 3, '#ffffff3b', 1);
      };
      for (let y = 0; y < 18; y++) for (let x = 0; x < 10; x++) block(x, y, COLORS[this.board[y][x]]);
      let drop = 0; while (this._fits(this.piece.x, this.piece.y + drop + 1, this.piece.cells)) drop++;
      for (const [x, y] of this.piece.cells) block(this.piece.x + x, this.piece.y + y + drop, '#527780', true);
      for (const [x, y] of this.piece.cells) block(this.piece.x + x, this.piece.y + y, COLORS[PIECE_NAMES.indexOf(this.piece.kind) + 1]);
      text(ctx, `ROWS ${this.lines}   LEVEL ${this.level}`, 553, 230, 15);
      text(ctx, 'NEXT', 553, 258, 11, '#a4c7c9');
      for (let i = 0; i < 3; i++) for (const [x, y] of SHAPES[this.queue[i]]) rectangle(ctx, 572 + i * 61 + x * 11, 282 + y * 11, 9, 9, COLORS[PIECE_NAMES.indexOf(this.queue[i]) + 1], 2);
      for (const [line, y] of [['← →  Move', 322], ['↑ / A  Rotate', 346], ['↓  Soft drop', 370], ['B  Hard drop', 394]]) text(ctx, line, 553, y, 14, '#d0ede1');
      text(ctx, 'START pause · START+SELECT hold exits', 30, 457, 11, '#91b6bb'); ctx.restore();
      overlay(ctx, width, height, this.status, 'STACK FULL', 'A / B to plant a fresh stack');
    }
  }

  // LANTERN LABYRINTH: a deterministic original maze and square sentinels.
  class Maze extends Game {
    constructor() { super('bmo_maze'); this.restart(); }
    restart() {
      if (this.closed) return;
      this.status = 'playing'; this.score = this.collected = 0; this.lives = 3; this.power = this.invulnerable = 0;
      this.rng = random(0x4c616e74); this.cols = 21; this.rows = 15;
      const map = Array.from({length: this.rows}, () => Array(this.cols).fill('#'));
      const stack = [[1, 1]]; map[1][1] = '.';
      while (stack.length) {
        const [x, y] = stack[stack.length - 1];
        const options = Object.values(DIRECTIONS).map(d => [x + d.dx * 2, y + d.dy * 2, d.dx, d.dy]).filter(([nx, ny]) => nx > 0 && ny > 0 && nx < this.cols - 1 && ny < this.rows - 1 && map[ny][nx] === '#');
        if (!options.length) { stack.pop(); continue; }
        const [nx, ny, dx, dy] = options[Math.floor(this.rng() * options.length)];
        map[y + dy][x + dx] = map[ny][nx] = '.'; stack.push([nx, ny]);
      }
      // A few original loop openings let players evade approaching sentinels.
      for (const [x, y] of [[6, 3], [10, 7], [14, 11], [4, 9]]) if (map[y][x - 1] === '.' && map[y][x + 1] === '.') map[y][x] = '.';
      this.map = map.map(row => row.join('')); this.pellets = new Set();
      for (let y = 1; y < this.rows - 1; y++) for (let x = 1; x < this.cols - 1; x++) if (map[y][x] === '.' && (x !== 1 || y !== 1)) this.pellets.add(cellKey(x, y));
      this.capsules = new Set([cellKey(1, this.rows - 2), cellKey(this.cols - 2, 1)]);
      this.total = this.pellets.size; this.started = false; this.direction = this.next_direction = null;
      this.player = {x: 1, y: 1}; this.ghosts = [{id: 'amber', x: 19, y: 13, home: {x: 19, y: 13}}, {id: 'violet', x: 19, y: 1, home: {x: 19, y: 1}}];
      this.move_clock = this.ghost_clock = 0; this.steps = 0; this.held.clear(); this.collision = null;
    }
    _open(x, y) { return y >= 0 && y < this.rows && x >= 0 && x < this.cols && this.map[y][x] !== '#'; }
    _collide() {
      if (this.invulnerable > 0) return;
      for (const ghost of this.ghosts) if (ghost.x === this.player.x && ghost.y === this.player.y) {
        if (this.power > 0) { ghost.x = ghost.home.x; ghost.y = ghost.home.y; this.scored(100); }
        else {
          this.lives--; this.collision = 'sentinel';
          if (!this.lives) { this.status = 'gameover'; this.releaseInputs(); return; }
          this.player = {x: 1, y: 1}; this.direction = this.next_direction = null; this.invulnerable = 1.3;
          for (const sentinel of this.ghosts) { sentinel.x = sentinel.home.x; sentinel.y = sentinel.home.y; }
          return;
        }
      }
    }
    _step() {
      const wanted = DIRECTIONS[this.next_direction], current = DIRECTIONS[this.direction];
      if (wanted && this._open(this.player.x + wanted.dx, this.player.y + wanted.dy)) this.direction = wanted.name;
      const d = DIRECTIONS[this.direction];
      if (!d || !this._open(this.player.x + d.dx, this.player.y + d.dy)) return;
      this.player.x += d.dx; this.player.y += d.dy; this.started = true; this.steps++;
      const key = cellKey(this.player.x, this.player.y);
      if (this.pellets.delete(key)) { this.collected++; this.scored(this.capsules.has(key) ? 40 : 10); }
      if (this.capsules.delete(key)) this.power = 6;
      this._collide();
      if (this.status === 'playing' && !this.pellets.size) { this.status = 'won'; this.scored(250); this.releaseInputs(); }
    }
    _ghostStep() {
      for (const ghost of this.ghosts) {
        const queue = [{x: ghost.x, y: ghost.y, first: null}], seen = new Set([cellKey(ghost.x, ghost.y)]);
        let choice = null;
        while (queue.length) {
          const cell = queue.shift();
          if (cell.x === this.player.x && cell.y === this.player.y) { choice = cell.first; break; }
          for (const d of Object.values(DIRECTIONS)) {
            const nx = cell.x + d.dx, ny = cell.y + d.dy, key = cellKey(nx, ny);
            if (!this._open(nx, ny) || seen.has(key)) continue;
            seen.add(key); queue.push({x: nx, y: ny, first: cell.first || d});
          }
        }
        if (this.power > 0) {
          const options = Object.values(DIRECTIONS).filter(d => this._open(ghost.x + d.dx, ghost.y + d.dy));
          options.sort((a, b) => Math.abs(ghost.x + b.dx - this.player.x) + Math.abs(ghost.y + b.dy - this.player.y) - Math.abs(ghost.x + a.dx - this.player.x) - Math.abs(ghost.y + a.dy - this.player.y));
          choice = options[0];
        }
        if (choice) { ghost.x += choice.dx; ghost.y += choice.dy; }
        this._collide(); if (this.status !== 'playing') break;
      }
    }
    input(key, pressed) {
      if (this.common(key, pressed)) return;
      if (!pressed) { this.held.delete(key); return; }
      if (this.status !== 'playing' || this.held.has(key)) return;
      this.held.add(key);
      if (DIRECTIONS[key]) { this.next_direction = key; this._step(); this.move_clock = 0; }
    }
    update(value) {
      if (this.closed || this.status !== 'playing') return;
      const dt = delta(value); this.power = Math.max(0, this.power - dt); this.invulnerable = Math.max(0, this.invulnerable - dt);
      if (!this.started) return;
      this.move_clock += dt; this.ghost_clock += dt;
      for (let i = 0; i < 3 && this.move_clock >= .14 && this.status === 'playing'; i++, this.move_clock -= .14) this._step();
      if (this.ghost_clock >= .46) { this.ghost_clock -= .46; this._ghostStep(); }
    }
    releaseInputs() { super.releaseInputs(); this.direction = this.next_direction = null; }
    snapshot() {
      return clone({...this.commonState(), cols: this.cols, rows: this.rows, map: this.map, player: this.player,
        pellets: [...this.pellets].map(key => { const [x, y] = key.split(',').map(Number); return {x, y}; }),
        capsules: [...this.capsules].map(key => { const [x, y] = key.split(',').map(Number); return {x, y}; }),
        ghosts: this.ghosts, lives: this.lives, power: this.power, invulnerable: this.invulnerable,
        direction: this.direction, next_direction: this.next_direction, started: this.started, collected: this.collected,
        remaining: this.pellets.size, steps: this.steps, collision: this.collision, move_interval: .14, ghost_interval: .46});
    }
    draw(ctx, width = 800, height = 480) {
      shell(ctx, width, height, 'LANTERN LABYRINTH', 'Collect garden lights. Stay clear of the wandering sentinels.', this.score, this.best);
      ctx.save(); ctx.scale(width / 800, height / 480); const size = 23, ox = 28, oy = 84;
      rectangle(ctx, ox - 3, oy - 3, this.cols * size + 6, this.rows * size + 6, '#071b27', 5);
      for (let y = 0; y < this.rows; y++) for (let x = 0; x < this.cols; x++) {
        if (this.map[y][x] === '#') { rectangle(ctx, ox + x * size + 1, oy + y * size + 1, size - 2, size - 2, '#254c5c', 4); rectangle(ctx, ox + x * size + 4, oy + y * size + 3, size - 8, 2, '#3a6f78', 1); }
        else if (this.pellets.has(cellKey(x, y))) { const big = this.capsules.has(cellKey(x, y)); rectangle(ctx, ox + x * size + (big ? 7 : 10), oy + y * size + (big ? 7 : 10), big ? 9 : 4, big ? 9 : 4, big ? '#f4d579' : '#9ddab0', 2); }
      }
      for (const ghost of this.ghosts) {
        rectangle(ctx, ox + ghost.x * size + 3, oy + ghost.y * size + 3, 17, 17, this.power ? '#6da0b3' : ghost.id === 'amber' ? '#e8b867' : '#bf97db', 4);
        rectangle(ctx, ox + ghost.x * size + 6, oy + ghost.y * size + 7, 4, 4, '#152d36'); rectangle(ctx, ox + ghost.x * size + 13, oy + ghost.y * size + 7, 4, 4, '#152d36');
      }
      const px = ox + this.player.x * size, py = oy + this.player.y * size;
      rectangle(ctx, px + 3, py + 4, 17, 17, this.invulnerable > 0 ? '#d8fff1' : '#71e4ba', 5);
      rectangle(ctx, px + 7, py + 8, 3, 4, '#102b35'); rectangle(ctx, px + 14, py + 8, 3, 4, '#102b35');
      rectangle(ctx, px + 10, py + 1, 3, 4, '#71e4ba', 1);
      text(ctx, `LIGHTS ${this.collected}/${this.total}`, 553, 231, 15); text(ctx, `LIVES ${this.lives}`, 553, 259, 18, '#f2b1a4');
      text(ctx, this.power > 0 ? `LANTERN POWER ${Math.ceil(this.power)}s` : 'Big lights repel sentinels', 553, 291, 12, '#f6d889');
      text(ctx, 'Arrows / D-pad  Move', 553, 329, 14); text(ctx, 'START  Pause', 553, 356, 14);
      text(ctx, this.started ? 'Find every little light.' : 'Press a direction to begin.', 553, 388, 12, '#a4c7c9');
      text(ctx, 'START pause · START+SELECT hold exits', 30, 457, 11, '#91b6bb'); ctx.restore();
      overlay(ctx, width, height, this.status, 'LIGHTS OUT', 'A / B to explore again');
    }
  }

  // VINE RUN: original grid snake mechanics and plant artwork.
  class Snake extends Game {
    constructor() { super('bmo_snake'); this.restart(); }
    restart() {
      if (this.closed) return;
      this.status = 'playing'; this.score = this.foods = 0; this.cols = 22; this.rows = 16;
      this.body = [{x: 5, y: 8}, {x: 4, y: 8}, {x: 3, y: 8}]; this.direction = 'RIGHT'; this.queued_direction = 'RIGHT';
      this.food = {x: 9, y: 8}; this.rng = random(0x56696e65); this.elapsed = 0; this.started = false;
      this.collision = null; this.held.clear();
    }
    _food() {
      const occupied = new Set(this.body.map(p => cellKey(p.x, p.y))), free = [];
      for (let y = 0; y < this.rows; y++) for (let x = 0; x < this.cols; x++) if (!occupied.has(cellKey(x, y))) free.push({x, y});
      if (!free.length) { this.food = null; this.status = 'won'; this.scored(250); this.releaseInputs(); }
      else this.food = free[Math.floor(this.rng() * free.length)];
    }
    input(key, pressed) {
      if (this.common(key, pressed)) return;
      if (!pressed) { this.held.delete(key); return; }
      if (this.status !== 'playing') return;
      this.held.add(key);
      if (DIRECTIONS[key]) {
        const wanted = DIRECTIONS[key], current = DIRECTIONS[this.direction];
        if (wanted.dx !== -current.dx || wanted.dy !== -current.dy) this.queued_direction = key;
        this.started = true;
      } else if (key === 'A') this.started = true;
    }
    _step() {
      this.direction = this.queued_direction; const d = DIRECTIONS[this.direction];
      const next = {x: this.body[0].x + d.dx, y: this.body[0].y + d.dy};
      const grows = this.food && next.x === this.food.x && next.y === this.food.y;
      const occupied = grows ? this.body : this.body.slice(0, -1);
      if (next.x < 0 || next.x >= this.cols || next.y < 0 || next.y >= this.rows || occupied.some(p => p.x === next.x && p.y === next.y)) {
        this.collision = next.x < 0 || next.x >= this.cols || next.y < 0 || next.y >= this.rows ? 'wall' : 'self';
        this.status = 'gameover'; this.releaseInputs(); return;
      }
      this.body.unshift(next);
      if (grows) { this.foods++; this.scored(10); this._food(); } else this.body.pop();
    }
    interval() { return Math.max(.075, .18 - this.foods * .004); }
    update(value) {
      if (this.closed || this.status !== 'playing' || !this.started) return;
      this.elapsed += delta(value);
      for (let i = 0; i < 4 && this.elapsed >= this.interval() && this.status === 'playing'; i++) { this.elapsed -= this.interval(); this._step(); }
    }
    snapshot() { return clone({...this.commonState(), cols: this.cols, rows: this.rows, body: this.body,
      food: this.food, direction: DIRECTIONS[this.direction], queued_direction: DIRECTIONS[this.queued_direction],
      started: this.started, length: this.body.length, foods: this.foods, collision: this.collision, move_interval: this.interval()}); }
    draw(ctx, width = 800, height = 480) {
      shell(ctx, width, height, 'VINE RUN', 'Gather fruit. Leave enough room for your growing garden vine.', this.score, this.best);
      ctx.save(); ctx.scale(width / 800, height / 480); const size = 22, ox = 28, oy = 80;
      rectangle(ctx, ox - 3, oy - 3, this.cols * size + 6, this.rows * size + 6, '#071b27', 5);
      for (let y = 0; y < this.rows; y++) for (let x = 0; x < this.cols; x++) rectangle(ctx, ox + x * size + 1, oy + y * size + 1, size - 2, size - 2, (x + y) % 2 ? '#142f37' : '#17343c', 2);
      if (this.food) {
        const x = ox + this.food.x * size, y = oy + this.food.y * size;
        rectangle(ctx, x + 5, y + 7, 13, 12, '#efae82', 6); rectangle(ctx, x + 11, y + 2, 3, 7, '#8bd3a1', 2); rectangle(ctx, x + 13, y + 3, 6, 3, '#acd991', 2);
      }
      for (let i = this.body.length - 1; i >= 0; i--) {
        const p = this.body[i], x = ox + p.x * size, y = oy + p.y * size;
        rectangle(ctx, x + 2, y + 2, size - 4, size - 4, i ? '#68bc91' : '#a6e7ad', 6);
        if (!i) { const d = DIRECTIONS[this.direction]; rectangle(ctx, x + 6 + d.dx * 3, y + 6 + d.dy * 3, 3, 3, '#17383b'); rectangle(ctx, x + 13 + d.dx * 3, y + 12 + d.dy * 3, 3, 3, '#17383b'); }
      }
      text(ctx, `VINE LENGTH ${this.body.length}`, 553, 231, 15); text(ctx, `FRUIT ${this.foods}`, 553, 260, 18, '#f2b1a4');
      text(ctx, 'Arrows / D-pad  Steer', 553, 307, 14); text(ctx, 'A  Begin', 553, 334, 14); text(ctx, 'START  Pause', 553, 361, 14);
      text(ctx, this.started ? 'Avoid the walls and your tail.' : 'Press a direction to begin.', 553, 389, 12, '#a4c7c9');
      text(ctx, 'START pause · START+SELECT hold exits', 30, 457, 11, '#91b6bb'); ctx.restore();
      overlay(ctx, width, height, this.status, 'VINE TANGLED', 'A / B to grow another vine');
    }
  }
  const factories = Object.freeze({bmo_blocks: () => new Blocks(), bmo_maze: () => new Maze(), bmo_snake: () => new Snake()});
  const api = Object.freeze({version: '1.0.0', ids: Object.freeze(Object.keys(factories)), create(id) {
    if (typeof id !== 'string' || !Object.prototype.hasOwnProperty.call(factories, id)) throw new Error('unknown_packaged_game');
    return factories[id]();
  }});
  Object.defineProperty(window, 'BMOOfflineGames', {value: api, writable: false, configurable: false});
})();
