#!/usr/bin/env python3
"""Play the original bundled games through the actual BMO Desktop.

Only the device boundary of navigator.getGamepads is replaced. The production
engines, host admission, menu, input routing, rendering, scores and cleanup run.
No microphone, camera, hardware controller or provider API is used.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import time

SOFTWARE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SOFTWARE))
from checks.check_desktop_app_browser import DesktopFixture, FIXTURE
from bmo_ai.screen_profiles import SCREEN_PROFILES


PAD_BOUNDARY = """window.__audioCalls=0;HTMLMediaElement.prototype.play=function(){
 window.__audioCalls++;throw Error('Browser audio forbidden')};
 window.__padCalls=0;window.__padButtons=[];window.__padConnected=true;
 Object.defineProperty(navigator,'getGamepads',{value:()=>{
  window.__padCalls++;return window.__padConnected?[{index:0,connected:true,mapping:'standard',
   buttons:Array.from({length:16},(_,i)=>({pressed:window.__padButtons.includes(i)})),axes:[0,0]}]:[];
 }});"""


ENGINE_SCENARIOS = r"""() => {
 const checks=[];const assert=(ok,message)=>{if(!ok)throw Error(message);};
 const tap=(e,key)=>{e.input(key,true);e.input(key,false);};
 const blocks=BMOOfflineGames.create('bmo_blocks');
 try {
  let before=blocks.snapshot();tap(blocks,'LEFT');let after=blocks.snapshot();
  assert(after.piece.x<before.piece.x,'Blocks left input must move the real piece');
  tap(blocks,'B');after=blocks.snapshot();
  assert(after.score>0&&after.drops===1&&after.board.some(row=>row.some(Boolean)),'Blocks drop must stack and score');
  const rotation=after.piece.rotation;tap(blocks,'A');assert(blocks.snapshot().piece.rotation!==rotation,'Blocks next I-piece rotation');
  tap(blocks,'START');before=blocks.snapshot();blocks.update(.25);after=blocks.snapshot();
  assert(before.status==='paused'&&JSON.stringify(before)===JSON.stringify(after),'Blocks pause stops physics');
  tap(blocks,'START');assert(blocks.snapshot().status==='playing','Blocks resume');
  for(let i=0;i<100&&blocks.snapshot().status==='playing';i++)tap(blocks,'B');
  assert(blocks.snapshot().status==='gameover','Blocks real stack collision must end the game');
  tap(blocks,'A');after=blocks.snapshot();assert(after.score===0&&after.status==='playing'&&after.drops===0,'Blocks terminal restart');
  const placements=[['O',0,0],['I',0,3],['T',2,3],['O',0,0],['L',3,2],['S',1,4],['J',0,7],['I',1,9]];
  for(const [kind,turns,x] of placements){
   assert(blocks.snapshot().piece.kind===kind,'Deterministic original piece bag');
   for(let i=0;i<turns;i++)tap(blocks,'A');
   let moves=0;while(blocks.snapshot().piece.x!==x&&moves++<15)tap(blocks,blocks.snapshot().piece.x<x?'RIGHT':'LEFT');
   assert(blocks.snapshot().piece.x===x,'Legal input placement');tap(blocks,'B');
  }
  after=blocks.snapshot();assert(after.lines===1&&after.drops===8&&after.score===330,'Eight real input placements clear a row and award the line bonus');
  checks.push('actual falling-block engine moves, rotates, stacks, clears a genuine row for score, pauses, collides and restarts from controls');
 } finally {blocks.close();assert(blocks.snapshot().closed===true,'Blocks close');}
 const snake=BMOOfflineGames.create('bmo_snake');
 try {
  let before=snake.snapshot();tap(snake,'RIGHT');
  for(let i=0;i<8&&snake.snapshot().score===0;i++)snake.update(.2);
  let after=snake.snapshot();assert(after.score>=10&&after.length>before.length,'Snake food collision must grow and score');
  tap(snake,'START');before=snake.snapshot();snake.update(.25);after=snake.snapshot();
  assert(before.status==='paused'&&JSON.stringify(before)===JSON.stringify(after),'Snake paused state must not advance');
  tap(snake,'START');
  for(let i=0;i<150&&snake.snapshot().status==='playing';i++)snake.update(.25);
  assert(snake.snapshot().status==='gameover'&&snake.snapshot().collision,'Snake wall/body collision');
  snake.restart();after=snake.snapshot();assert(after.status==='playing'&&after.score===0&&after.foods===0,'Snake restart');
  const first=after.body[0];tap(snake,'RIGHT');tap(snake,'LEFT');snake.update(.2);
  assert(snake.snapshot().body[0].x>first.x,'Snake cannot instantly reverse into itself');
  checks.push('actual snake engine eats food, grows, scores, pauses, rejects reverse, collides and restarts');
 } finally {snake.close();assert(snake.snapshot().closed===true,'Snake close');}
 const maze=BMOOfflineGames.create('bmo_maze');
 try {
  let s=maze.snapshot();const dirs=[['LEFT',-1,0],['RIGHT',1,0],['UP',0,-1],['DOWN',0,1]];
  const wall=dirs.find(([,dx,dy])=>!s.map[s.player.y+dy]||s.map[s.player.y+dy][s.player.x+dx]==='#');
  assert(wall,'Maze initial wall exists');tap(maze,wall[0]);maze.update(.2);
  let after=maze.snapshot();assert(after.player.x===s.player.x&&after.player.y===s.player.y,'Maze walls block movement');
  const floor=dirs.find(([,dx,dy])=>s.map[s.player.y+dy]?.[s.player.x+dx]!==undefined&&s.map[s.player.y+dy][s.player.x+dx]!=='#');
  assert(floor,'Maze reachable floor exists');tap(maze,floor[0]);
  for(let i=0;i<4&&maze.snapshot().score===0;i++)maze.update(.2);
  after=maze.snapshot();assert(after.score>0&&after.collected>0&&after.remaining<s.remaining,'Maze pellet collision scores');
  tap(maze,'START');s=maze.snapshot();maze.update(.25);
  assert(s.status==='paused'&&JSON.stringify(s)===JSON.stringify(maze.snapshot()),'Maze pause freezes ghost/player physics');
  tap(maze,'START');maze.releaseInputs();
  for(let i=0;i<4000&&maze.snapshot().status==='playing';i++)maze.update(.25);
  after=maze.snapshot();assert(after.status==='gameover'&&after.lives===0,'Maze pursuing ghosts cause actual collisions/gameover');
  maze.restart();after=maze.snapshot();assert(after.score===0&&after.lives===3&&after.status==='playing','Maze restart');
  checks.push('actual maze engine blocks walls, consumes pellets, scores, freezes on pause, loses lives to ghosts and restarts');
 } finally {maze.close();assert(maze.snapshot().closed===true,'Maze close');}
 return checks;
}"""


def run(profile="ci-synthetic", chromium="/usr/bin/chromium", *, artifacts=None):
    from playwright.sync_api import sync_playwright
    face = FIXTURE if profile == "ci-synthetic" else Path(SCREEN_PROFILES[profile]["default_path"])
    before = hashlib.sha256(face.read_bytes()).hexdigest()
    if profile != "ci-synthetic":
        assert before == SCREEN_PROFILES[profile]["sha256"]
    output = Path(artifacts) if artifacts is not None else None
    if output is not None:
        output.mkdir(parents=True, exist_ok=True)
    checks, screenshots, errors, game_events = [], [], [], []
    with tempfile.TemporaryDirectory(prefix="bmo-playable-games-") as directory:
        fixture = DesktopFixture(directory, profile=profile, face=face, scenario="playable")
        try:
            with sync_playwright() as pw:
                browser = pw.chromium.launch(executable_path=chromium, headless=True,
                    env={"PATH": os.defpath, "HOME": directory, "LC_ALL": "C.UTF-8"},
                    args=["--no-sandbox", "--disable-gpu", "--mute-audio", "--disable-audio-output",
                          "--disable-background-networking", "--disable-component-update", "--disable-sync", "--no-first-run"])
                try:
                    page = browser.new_page(viewport={"width": 800, "height": 480})
                    page.set_default_timeout(8000)
                    page.on("pageerror", lambda error: errors.append(str(error)))
                    def observe_request(request):
                        if request.method == 'POST' and request.url.endswith('/api/control'):
                            payload = request.post_data_json.get('payload', {})
                            if payload.get('action') == 'game_event':
                                game_events.append(payload['value'])
                    page.on('request', observe_request)
                    page.route("**/*", lambda r: r.continue_() if r.request.url.startswith(fixture.url) else r.abort())
                    page.add_init_script(PAD_BOUNDARY)

                    def wait_features(predicate, timeout=8):
                        deadline = time.monotonic() + timeout
                        while time.monotonic() < deadline:
                            value = fixture.features()
                            if predicate(value):
                                return value
                            page.wait_for_timeout(25)
                        raise AssertionError("Playable host state timeout: " + json.dumps({
                            name: value.get(name) for name in ("mode", "games", "last_result")}))

                    def game():
                        return page.evaluate("()=>BMOPlayableGames.snapshot()")

                    def wait_game(predicate, timeout=8):
                        deadline = time.monotonic() + timeout
                        while time.monotonic() < deadline:
                            value = game()
                            if value is not None and predicate(value):
                                return value
                            page.wait_for_timeout(25)
                        raise AssertionError("Playable engine state timeout: " + json.dumps(value))

                    def shot(name):
                        if output is not None:
                            path = output / (profile + "-" + name + ".png")
                            page.screenshot(path=str(path))
                            screenshots.append(str(path))

                    def open_menu():
                        page.evaluate("()=>BMODesktop.menu()")
                        page.locator("#games").click()
                        page.locator("#game-menu").wait_for(state="visible")

                    def launch(game_id):
                        page.locator('#game-list button[data-game-id="' + game_id + '"]').click()
                        wait_features(lambda s: s["mode"] == "game" and s["games"]["active_id"] == game_id)
                        state = wait_game(lambda s: s["game_id"] == game_id and s["animation_running"])
                        page.locator("#game-canvas").focus()
                        return state

                    def back_to_face(*, keyboard=False):
                        if keyboard:
                            page.keyboard.press("Escape")
                        else:
                            page.locator("#game-exit").click()
                        wait_features(lambda s: s["mode"] == "game_menu")
                        page.wait_for_function("()=>BMOPlayableGames.snapshot()===null")
                        page.locator("#game-menu").wait_for(state="visible")
                        page.locator("#game-back").click()
                        wait_features(lambda s: s["mode"] == "conversation" and s["audio_owner"] is None)
                        page.wait_for_function("()=>BMODesktop.features?.mode==='conversation'&&BMOPlayableGames.snapshot()===null")
                        assert not page.locator("#game-surface").is_visible()
                        assert page.locator("#face").bounding_box() == dict(x=0, y=0, width=800, height=480)

                    page.goto(fixture.url, wait_until="domcontentloaded")
                    page.wait_for_function("()=>BMODesktop.state?.code==='greeting_completed'&&BMODesktop.features")
                    initial = fixture.features()
                    catalog = initial["games"]["catalog"]
                    assert {item["id"] for item in catalog} == {"bmo_blocks", "bmo_maze", "bmo_snake"}
                    assert all(item["readiness"] == "READY" and item["simulated"] is False for item in catalog)
                    assert initial["usb_gamepad"]["enabled"]
                    assert page.evaluate("()=>window.__padCalls") == 0
                    assert game() is None
                    assert fixture.request("/face.html?kiosk=1")[1] == face.read_bytes()
                    checks.append("actual hash-verified bundled catalog contains three nonsimulated original web games; idle preserves the unchanged face with zero gamepad polling")

                    open_menu()
                    s = launch("bmo_blocks")
                    bounds = page.locator("#game-canvas").bounding_box()
                    assert bounds and bounds['width'] > 0 and bounds['height'] > 0
                    assert bounds['x'] >= 0 and bounds['y'] >= 0
                    assert bounds['x'] + bounds['width'] <= 801 and bounds['y'] + bounds['height'] <= 481
                    assert page.evaluate("()=>{const c=document.getElementById('game-canvas');"
                        "return c.width===800&&c.height===480&&c.getContext('2d').getImageData(0,0,800,480).data.some(v=>v>0);}")
                    x = s["piece"]["x"]
                    page.keyboard.press("ArrowLeft")
                    wait_game(lambda v: v["piece"]["x"] < x)
                    page.keyboard.press("b")
                    scored = wait_game(lambda v: v["score"] > 0 and v["drops"] == 1)
                    rotation = scored["piece"]["rotation"]
                    page.keyboard.press("ArrowUp")
                    wait_game(lambda v: v["piece"]["rotation"] != rotation)
                    shot("blocks-scored")
                    page.keyboard.press("Enter")
                    paused = wait_game(lambda v: v["status"] == "paused" and not v["animation_running"])
                    page.wait_for_timeout(180)
                    assert game()["frames"] == paused["frames"] and not game()["animation_running"]
                    page.keyboard.press("Enter")
                    wait_game(lambda v: v["status"] == "playing" and v["animation_running"])
                    old_launch = game()["launch_id"]
                    page.keyboard.down("b")
                    page.locator("#game-restart").click()
                    page.keyboard.up("b")
                    wait_game(lambda v: v["launch_id"] != old_launch and v["score"] == 0
                              and v["drops"] == 0 and v["animation_running"])
                    page.wait_for_timeout(250)
                    assert game()["score"] == game()["drops"] == 0
                    page.evaluate("()=>{window.__padButtons=[1];}")
                    wait_game(lambda v: v["score"] > 0 and v["drops"] == 1)
                    page.evaluate("()=>{window.__padButtons=[];}")
                    back_to_face(keyboard=True)
                    checks.append("real 800x480 blocks receives keyboard movement/rotation and keyboard/gamepad hard-drop score, pauses its RAF, restarts and returns to the original face")

                    open_menu()
                    s = launch("bmo_maze")
                    directions = (("ArrowRight", 1, 0), ("ArrowDown", 0, 1), ("ArrowLeft", -1, 0), ("ArrowUp", 0, -1))
                    key = next(k for k, dx, dy in directions
                               if 0 <= s["player"]["y"] + dy < len(s["map"])
                               and s["map"][s["player"]["y"] + dy][s["player"]["x"] + dx] != "#")
                    page.keyboard.down(key)
                    scored = wait_game(lambda v: v["score"] > 0 and v["collected"] > 0)
                    page.keyboard.up(key)
                    assert scored["player"] != s["player"]
                    shot("maze-scored")
                    page.locator("#game-pause").click()
                    wait_game(lambda v: v["status"] == "paused")
                    page.locator("#game-restart").click()
                    wait_game(lambda v: v["score"] == 0 and v["lives"] == 3 and not v["started"] and v["animation_running"])
                    page.wait_for_timeout(180)
                    pad_button = {"ArrowRight": 15, "ArrowDown": 13, "ArrowLeft": 14, "ArrowUp": 12}[key]
                    page.evaluate("button=>{window.__padButtons=[button];}", pad_button)
                    wait_game(lambda v: v["score"] > 0 and v["collected"] > 0)
                    page.evaluate("()=>{window.__padButtons=[];}")
                    back_to_face()
                    checks.append("real maze moves and consumes pellets for score from keyboard and gamepad, pauses/restarts and returns to the same face/audio owner")

                    open_menu()
                    s = launch("bmo_snake")
                    page.wait_for_function("()=>BMODesktop.features?.mode==='game'")
                    page.wait_for_timeout(180)  # Poll neutral at mode activation before a new pad edge.
                    page.evaluate("()=>{window.__padButtons=[15];}")
                    scored = wait_game(lambda v: v["score"] >= 10 and v["length"] > s["length"])
                    page.evaluate("()=>{window.__padButtons=[];}")
                    shot("snake-scored")
                    assert fixture.features()["usb_gamepad"]["accepted_edges"] > 0
                    terminal_launch = game()['launch_id']
                    wait_game(lambda v: v["status"] == "gameover", timeout=8)
                    deadline = time.monotonic() + 3
                    while not any(e['launch_id'] == terminal_launch for e in game_events):
                        assert time.monotonic() < deadline, 'Actual engine terminal event missing'
                        page.wait_for_timeout(25)
                    terminal = [e for e in game_events if e['launch_id'] == terminal_launch]
                    assert len(terminal) == 1 and terminal[0]['outcome'] == 'gameover'
                    assert terminal[0]['event_id'] == terminal_launch + ':outcome'
                    page.locator("#game-restart").click()
                    wait_game(lambda v: v["score"] == 0 and not v["started"] and v["animation_running"])
                    page.keyboard.press("ArrowRight")
                    wait_game(lambda v: v["started"])
                    page.evaluate("()=>{window.__padConnected=false;}")
                    wait_features(lambda v: v["mode"] == "game_menu" and v["audio_owner"] is None)
                    page.wait_for_function("()=>BMOPlayableGames.snapshot()===null")
                    assert fixture.features()["usb_gamepad"]["held"] == []
                    page.locator("#game-back").click()
                    wait_features(lambda v: v["mode"] == "conversation")
                    checks.append("standard gamepad boundary drives real Snake food/growth/score and wall gameover; actual terminal edge reports once and disconnect releases input/game/audio ownership")

                    # Use real factory/inputs only; no board setter or test cheat
                    # exists. These bounded deterministic physics steps add
                    # collision coverage without making the app run at fake speed.
                    checks.extend(page.evaluate(ENGINE_SCENARIOS))

                    # Keyboard-only blur pauses the canvas. A connected pad's
                    # blur intentionally exits via PAD_LOST (covered above),
                    # so leave that boundary disconnected for this case.
                    page.evaluate("()=>{window.__padConnected=false;window.__padButtons=[];}")
                    open_menu()
                    launch("bmo_snake")
                    page.keyboard.press("ArrowRight")
                    wait_game(lambda v: v["started"])
                    page.evaluate("()=>window.dispatchEvent(new Event('blur'))")
                    paused = wait_game(lambda v: v["paused_by_focus"] and v["status"] == "paused")
                    page.wait_for_timeout(200)
                    assert game()["frames"] == paused["frames"] and not game()["animation_running"]
                    page.locator("#game-restart").click()
                    wait_game(lambda v: v["score"] == 0 and not v["started"] and v["animation_running"])
                    back_to_face()
                    checks.append("blur releases inputs and stops the actual game RAF; explicit restart and exit leave no stale game state")

                    open_menu()
                    launch("bmo_blocks")
                    page.reload(wait_until="domcontentloaded")
                    page.wait_for_function("()=>BMODesktop.state?.phase==='ready'")
                    # Browser departure/reload cannot leave an inaccessible game
                    # holding audio indefinitely. A fresh game may be mounted or
                    # the host may safely return to the menu; both must be owned.
                    wait_features(lambda v: v["mode"] in ("game", "game_menu", "conversation"))
                    if fixture.features()["mode"] == "game":
                        wait_game(lambda v: v["game_id"] == "bmo_blocks")
                        back_to_face()
                    else:
                        page.evaluate("()=>BMODesktop.intent('return_to_face')")
                        wait_features(lambda v: v["mode"] == "conversation")
                    page.wait_for_function("()=>BMOPlayableGames.snapshot()===null")
                    audit = fixture.features()["test_fixture"]
                    assert audit["brain"] == audit["stt"] == audit["synthesis"] == audit["mic_sources"] == 0
                    assert audit["greeting_calls"] == 1
                    assert not fixture.features()["camera"]["on"]
                    assert not fixture.features()["motion"]["active"]
                    assert page.evaluate("()=>window.__audioCalls") == 0
                    assert not errors, errors
                    checks.append("reload/repeated launches preserve one greeting and return ownership; game Enter/keys never call PTT/brain/Fish/camera/arms or browser audio")

                    code, counts, error = fixture.finish()
                    assert code == 0, error
                    assert counts["players"] == counts["reaped"] == 1
                    assert counts["browsers"] == counts["browser_reaped"] == 1
                    assert counts["forbidden"] == 0
                    checks.append("normal Desktop exit reaps the owned browser/greeting child and closes the actual packaged-game owner")
                except Exception:
                    shot("failure")
                    raise
                finally:
                    browser.close()
        finally:
            fixture.close()
    assert hashlib.sha256(face.read_bytes()).hexdigest() == before
    result = {"status": "PASS", "screen_profile": profile, "checks": checks,
              "counts": counts, "screenshots": screenshots,
              "sha256_before": before, "sha256_after": before,
              "actual_browser_gameplay": True,
              "not_run": ["actual Pi USB gamepad", "native Qt display", "physical devices", "paid APIs"]}
    if output is not None:
        (output / (profile + "-result.json")).write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--screen-profile", choices=("ci-synthetic", "r10", "v13.2"), default="ci-synthetic")
    parser.add_argument("--chromium", nargs="?", const=None, default="/usr/bin/chromium")
    parser.add_argument("--artifacts", type=Path)
    args = parser.parse_args()
    print(json.dumps(run(args.screen_profile, args.chromium, artifacts=args.artifacts), ensure_ascii=False, indent=2))
