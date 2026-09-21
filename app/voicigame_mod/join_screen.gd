extends CanvasLayer
## „Lobby beitreten": dieser PC spielt bei einem anderen Host mit, wie ein Handy.
##   Eingabe   Raumcode und Name
##   Raum      Mitspieler, je Runde: Clip anhören, Los, Countdown, Aufnahme, Senden, Punkte, Rangliste

signal closed

const UI = preload("ui.gd")
const I18n = preload("i18n.gd")
const JoinClient = preload("join_client.gd")
const Players = preload("players.gd")
const CONFIG := "user://voicigame.cfg"
const COUNTDOWN := 3

var client: Node
var _bg: ColorRect
var _page: Control
# Eingabe
var _code_edit: LineEdit
var _name_edit: LineEdit
var _entry_status: Label
# Raum
var _room_code: Label
var _title: Label
var _sub: Label
var _big: Label
var _bar: ProgressBar
var _meter: ProgressBar
var _btn_listen: Control
var _btn_go: Control
var _btn_stop: Control
var _btn_browser: Control
var _join_started := 0           # Zeitpunkt des Beitreten-Versuchs (für das Zeitlimit)
var _master_was_muted := false
var _players: VBoxContainer
var _result: Label
var _player: AudioStreamPlayer
# Live-Bild und Ton vom Host
var _live: TextureRect
var _live_wait: Label
var _btn_sound: Control
var _sound := true
var _gen_player: AudioStreamPlayer
var _gen: AudioStreamGeneratorPlayback
var _gen_rate := 0

var _round: Dictionary = {}
var _phase := ""                 # "" | countdown | recording | sending | sent | error
var _rec_start := 0
var _rec_seconds := 0.0
var _error := ""
var _scores: Array = []
var _ranking: Array = []


static func tr_(s: String, args: Array = []) -> String:
	return I18n.t(s, args)


## Nur im Testlauf: Schritte ins Spielprotokoll.
static func _dbg(msg: String) -> void:
	if OS.get_environment("VOICIGAME_TEST") != "":
		print("Voicigame | Beitreten: " + msg)


func setup(server_url: String) -> void:
	layer = 90
	client = JoinClient.new()
	client.name = "JoinClient"
	client.server_url = server_url
	add_child(client)
	client.joined.connect(func(_id): _show_room())
	client.state_changed.connect(func(_s): _refresh())
	client.round_started.connect(_on_round)
	client.scores_received.connect(func(s): _scores = s; _refresh())
	client.show_ended.connect(func(r): _ranking = r; _refresh())
	client.connection_changed.connect(func(_c): _refresh())
	client.error_received.connect(_on_error)
	client.kicked.connect(func(): _show_entry(tr_("Du wurdest aus dem Raum entfernt.")))
	client.clip_ready.connect(func(_id, _s): _refresh())
	client.upload_done.connect(_on_uploaded)
	client.live_frame.connect(_on_live_frame)
	_player = AudioStreamPlayer.new()
	add_child(_player)
	_bg = UI.backdrop(self)
	_show_entry("")


func _clear_page() -> void:
	if is_instance_valid(_page):
		_page.queue_free()
	_page = Control.new()
	_page.set_anchors_preset(Control.PRESET_FULL_RECT)
	_bg.add_child(_page)


# ------------------------------------------------------------------
# Eingabe
# ------------------------------------------------------------------

func _show_entry(message: String) -> void:
	_clear_page()
	var cfg := ConfigFile.new()
	cfg.load(CONFIG)
	var box := UI.center_column(_page, 16)
	box.add_child(UI.label("VOICIGAME", 34, true, UI.ACCENT))
	box.add_child(UI.label(tr_("Lobby beitreten"), 30, true))
	box.add_child(UI.text(tr_("Gib den Raumcode vom Host ein. Aufgenommen wird mit dem Mikrofon aus den Spieleinstellungen."), 22, 620, UI.MUTED))
	box.add_child(UI.label(tr_("Raumcode"), 20, false, UI.MUTED))
	_code_edit = UI.line_edit("ABCD", 44, 300)
	_code_edit.max_length = 4
	_code_edit.alignment = HORIZONTAL_ALIGNMENT_CENTER
	_code_edit.text = str(cfg.get_value("join", "code", ""))
	_code_edit.text_changed.connect(func(t): _code_edit.text = t.to_upper(); _code_edit.caret_column = _code_edit.text.length())
	box.add_child(_code_edit)
	box.add_child(UI.label(tr_("Dein Name"), 20, false, UI.MUTED))
	_name_edit = UI.line_edit("", 28, 420)
	_name_edit.max_length = 24
	_name_edit.text = str(cfg.get_value("join", "name", _profile_name()))
	box.add_child(_name_edit)
	_entry_status = UI.text(message, 20, 620, UI.WARN)
	box.add_child(_entry_status)
	var row := HBoxContainer.new()
	row.add_theme_constant_override("separation", 24)
	row.alignment = BoxContainer.ALIGNMENT_CENTER
	row.add_child(UI.button(tr_("Beitreten"), _on_join))
	row.add_child(UI.button(tr_("Zurück"), _close))
	box.add_child(row)
	_code_edit.text_submitted.connect(func(_t): _on_join())
	_name_edit.text_submitted.connect(func(_t): _on_join())
	_code_edit.grab_focus.call_deferred()


## Name aus dem Spielerprofil des Spiels als Vorschlag.
func _profile_name() -> String:
	return Players.profile_name(get_node_or_null("/root/Profile"))


func join(code: String, name: String) -> void:
	_code_edit.text = code
	_name_edit.text = name
	_on_join()


func _on_join() -> void:
	var code := _code_edit.text.strip_edges().to_upper()
	var name := _name_edit.text.strip_edges()
	if code.length() != 4 or name == "":
		_entry_status.text = tr_("Bitte Raumcode (4 Buchstaben) und Namen eingeben.")
		return
	_entry_status.text = tr_("Verbinde …")
	_join_started = Time.get_ticks_msec()
	client.join(code, name)


func _on_error(code: String, message: String) -> void:
	if code == "room_not_found":
		_show_entry(tr_("Diesen Raumcode gibt es nicht. Schau nochmal auf den PC."))
	elif code == "room_full":
		_show_entry(tr_(message))
	elif is_instance_valid(_entry_status) and _entry_status.is_inside_tree():
		_entry_status.text = tr_(message)
	elif is_instance_valid(_sub):
		_sub.text = tr_(message)


# ------------------------------------------------------------------
# Raum
# ------------------------------------------------------------------

func _show_room() -> void:
	_join_started = 0
	if is_instance_valid(_title) and _title.is_inside_tree():
		_refresh()
		return
	_clear_page()
	# Das Spiel rechnet mit 1152 x 648: links das Live-Bild, rechts Runde, Aufnahme und Mitspieler
	var root := UI.margin(_page, 32)
	var cols := HBoxContainer.new()
	cols.add_theme_constant_override("separation", 24)
	root.add_child(cols)

	var left := VBoxContainer.new()
	left.add_theme_constant_override("separation", 10)
	cols.add_child(left)
	var head := HBoxContainer.new()
	head.add_theme_constant_override("separation", 16)
	head.add_child(UI.label("VOICIGAME", 26, true, UI.ACCENT))
	_room_code = UI.label(client.code, 26, true)
	head.add_child(_room_code)
	left.add_child(head)
	var frame := Panel.new()
	frame.custom_minimum_size = Vector2(640, 360)
	var sb := StyleBoxFlat.new()
	sb.bg_color = Color(0.02, 0.05, 0.09)
	sb.set_corner_radius_all(10)
	frame.add_theme_stylebox_override("panel", sb)
	frame.clip_contents = true
	left.add_child(frame)
	_live = TextureRect.new()
	_live.set_anchors_preset(Control.PRESET_FULL_RECT)
	_live.expand_mode = TextureRect.EXPAND_IGNORE_SIZE
	_live.stretch_mode = TextureRect.STRETCH_KEEP_ASPECT_CENTERED
	frame.add_child(_live)
	_live_wait = UI.label(tr_("Warte auf das Bild vom PC …"), 18, false, UI.MUTED)
	_live_wait.set_anchors_preset(Control.PRESET_FULL_RECT)
	_live_wait.horizontal_alignment = HORIZONTAL_ALIGNMENT_CENTER
	_live_wait.vertical_alignment = VERTICAL_ALIGNMENT_CENTER
	frame.add_child(_live_wait)
	var tools := HBoxContainer.new()
	tools.add_theme_constant_override("separation", 16)
	_btn_sound = UI.button(tr_("Ton aus"), _toggle_sound, 200, 52)
	tools.add_child(_btn_sound)
	tools.add_child(UI.button(tr_("Verlassen"), _close, 200, 52))
	left.add_child(tools)

	var right := VBoxContainer.new()
	right.custom_minimum_size.x = 424
	right.add_theme_constant_override("separation", 8)
	cols.add_child(right)
	_title = UI.text("", 26, 424)
	var f := UI.font(UI.FONT_BOLD)
	if f:
		_title.add_theme_font_override("font", f)
	right.add_child(_title)
	_sub = UI.text("", 17, 424, UI.MUTED)
	right.add_child(_sub)
	_big = UI.label("", 48, true, UI.ACCENT)
	_big.horizontal_alignment = HORIZONTAL_ALIGNMENT_CENTER
	right.add_child(_big)
	_bar = ProgressBar.new()
	_bar.custom_minimum_size = Vector2(424, 12)
	_bar.show_percentage = false
	right.add_child(_bar)
	_meter = ProgressBar.new()
	_meter.custom_minimum_size = Vector2(424, 6)
	_meter.show_percentage = false
	_meter.modulate = Color(0.45, 1.0, 0.55)
	right.add_child(_meter)
	var buttons := HBoxContainer.new()
	buttons.add_theme_constant_override("separation", 12)
	_btn_listen = UI.button(tr_("Original anhören"), _on_listen, 230, 52)
	_btn_go = UI.button(tr_("Los"), _on_go, 150, 52)
	_btn_stop = UI.button(tr_("Aufnahme beenden"), _on_stop, 260, 52)
	_btn_browser = UI.button(tr_("Im Browser öffnen"), _open_browser, 260, 52)
	buttons.add_child(_btn_listen)
	buttons.add_child(_btn_go)
	buttons.add_child(_btn_stop)
	buttons.add_child(_btn_browser)
	right.add_child(buttons)
	_result = UI.text("", 18, 424, UI.WARN)
	right.add_child(_result)
	var gap := Control.new()
	gap.custom_minimum_size.y = 8
	right.add_child(gap)
	right.add_child(UI.label(tr_("Mitspieler"), 22, true))
	_players = VBoxContainer.new()
	_players.add_theme_constant_override("separation", 4)
	right.add_child(_players)
	client.mic_on()
	_refresh()


func _on_round(r: Dictionary) -> void:
	if str(r.get("roundId", "")) == str(_round.get("roundId", "")):
		return
	_dbg("neue Runde %s" % r.get("roundId", ""))
	_round = r
	_phase = ""
	_error = ""
	if _player.playing:
		_player.stop()
	_dbg("Wiedergabe gestoppt")
	_refresh()
	_dbg("Anzeige aktualisiert")


func _mine() -> bool:
	return not _round.is_empty() and client.player_id in _round.get("recorders", [])


func _done_on_server() -> bool:
	var show = client.state.get("show")
	var r = show.get("round") if show is Dictionary else null
	return r is Dictionary and str(r.get("roundId", "")) == str(_round.get("roundId", "")) and client.player_id in r.get("done", [])


func _refresh() -> void:
	if not is_instance_valid(_title) or not _title.is_inside_tree():
		return
	var st: Dictionary = client.state
	var show = st.get("show", {})
	if not show is Dictionary:
		show = {}
	# Mitspieler bzw. Punkte
	for c in _players.get_children():
		c.queue_free()
	var totals := {}
	for s in _scores:
		totals[str(s.get("playerId", s.get("name")))] = s.get("total")
	for p in st.get("players", []):
		var line := ("● " if p.get("connected", false) else "○ ") + str(p.get("name", "?"))
		if p.get("id") == client.player_id:
			line += " " + tr_("(du)")
		if totals.has(str(p.get("id"))) and totals[str(p.get("id"))] != null:
			line += "   %s" % _pts(totals[str(p.get("id"))])
		_players.add_child(UI.label(line, 18))
	# Wer am Host-PC spielt, steht nur in den Punkten
	for sc in _scores:
		if str(sc.get("playerId", "")) == "" or sc.get("playerId") == null:
			_players.add_child(UI.label("● %s   %s" % [sc.get("name", "?"), _pts(sc.get("total"))], 18))

	var phase := str(st.get("phase", "lobby"))
	var show_mode := str(show.get("game", "show")) == "show"
	client.watch(show_mode and phase == "playing")
	if is_instance_valid(_live_wait):
		_live_wait.visible = _live.texture == null
	var cached: bool = not _round.is_empty() and client.clip(str(_round.get("clipId", ""))) != null
	var status := tr_(str(show.get("status", ""))) if str(show.get("status", "")) != "" else ""
	var sent := _phase == "sent" or (_phase == "" and _done_on_server())
	_big.text = ""
	_big.visible = false
	_bar.visible = false
	_meter.visible = false
	_btn_listen.visible = false
	_btn_go.visible = false
	_btn_stop.visible = false
	_btn_browser.visible = false
	_result.text = ""
	_result.visible = false

	if not client.connected:
		_title.text = tr_("Verbindung weg, verbinde neu …")
		_sub.text = ""
		return
	# Dub-Raum: Video und Zeilen gibt es nur im Browser, dieser PC ist hier Zuschauer
	if str(st.get("game", "show")) == "dub":
		_title.text = tr_("In diesem Raum wird synchronisiert.")
		_sub.text = tr_("Mach im Browser mit: dort siehst du das Video und nimmst deine Zeilen auf.")
		_btn_browser.visible = true
		return
	if phase == "playing" and not st.get("hostOnline", true):
		_title.text = tr_("Verbindung zum PC unterbrochen. Warte, bis er wieder da ist …")
		_sub.text = ""
		return
	if phase == "ended" or not _ranking.is_empty() and phase != "playing":
		var list: Array = _ranking if not _ranking.is_empty() else show.get("ranking", [])
		_title.text = tr_("Rangliste") if not list.is_empty() else tr_("Der PC hat die Runde beendet.")
		var lines := PackedStringArray()
		for x in list:
			lines.append("%d. %s   %s" % [int(x.get("place", 0)), x.get("name", ""), _pts(x.get("total"))])
		_sub.text = "\n".join(lines)
		_result.text = tr_("Der PC hat die Runde beendet.") if st.get("closed", false) else tr_("Bleib im Raum, der Host kann eine neue Runde starten.")
		_result.visible = not list.is_empty()
		return
	if phase == "lobby" or _round.is_empty():
		_title.text = tr_("Warte, bis der PC die Show startet")
		_sub.text = tr_("Jede Runde bekommst du einen Clip. Hör ihn dir an und sprich ihn so genau wie möglich nach. Das Spiel am PC vergibt die Punkte.")
		return

	_title.text = tr_("Runde {} von {}", [int(_round.get("index", 0)) + 1, int(_round.get("total", 1))])
	var me_score = null
	for s in _scores:
		if str(s.get("playerId", "")) == client.player_id:
			me_score = s.get("score")
	if not _scores.is_empty() and me_score != null:
		_result.text = tr_("Letzte Bewertung") + ": " + _pts(me_score)
		_result.visible = true

	if not _mine():
		# Wer erst während der Show beitritt, ist nicht im Spiel und schaut nur zu
		_sub.text = (status if status != "" else tr_("Die anderen sind dran")) + "\n" + tr_("Du schaust dieser Runde zu.")
		return
	match _phase:
		"countdown":
			_sub.text = tr_("Gleich geht die Aufnahme los")
		"recording":
			_sub.text = tr_("Aufnahme läuft, leg los!")
			_bar.visible = true
			_meter.visible = true
			_btn_stop.visible = true
		"sending":
			_sub.text = tr_("Deine Aufnahme geht an den PC")
			_big.text = "…"
			_big.visible = true
		"error":
			_sub.text = tr_(_error if _error != "" else "Etwas ist schiefgelaufen") + " " + tr_("Tipp nochmal, um es neu zu versuchen.")
			_btn_go.visible = cached
			_btn_listen.visible = cached
		_:
			if sent:
				_title.text = tr_("Gesendet")
				_sub.text = status if status != "" else tr_("Das Spiel bewertet dich, sobald du dran bist.")
			elif not cached:
				_sub.text = tr_("Dein Clip lädt noch. Die anderen warten kurz.")
			else:
				_sub.text = tr_("Hör dir den Clip an. Tipp auf Los, wenn du bereit bist. Nach dem Countdown sprichst du ihn nach.")
				_btn_listen.visible = true
				_btn_go.visible = true
				_meter.visible = true   # Pegel schon vorher: zeigt, ob das Mikro ankommt
	if _phase == "countdown":
		pass   # Zahl setzt _record()


func _pts(v) -> String:
	if v == null:
		return ""
	var n := float(v)
	var shown := str(int(n)) if is_equal_approx(n, roundf(n)) else "%.1f" % n
	return tr_("1 Punkt") if shown == "1" else tr_("{} Punkte", [shown])


func _on_listen() -> void:
	var s = client.clip(str(_round.get("clipId", "")))
	if s:
		_player.stream = s
		_player.play()


func _on_go() -> void:
	if not _mine() or _phase in ["countdown", "recording", "sending", "sent"]:
		return
	if client.clip(str(_round.get("clipId", ""))) == null:
		return
	_record()


func _on_stop() -> void:
	if _phase == "recording":
		_rec_seconds = 0.0   # _process beendet die Aufnahme


func _record() -> void:
	var round_id := str(_round.get("roundId", ""))
	var lead_in := float(_round.get("leadIn", 0.0))
	_player.stop()
	_phase = "countdown"
	_refresh()
	_mute_game(true)
	for n in range(COUNTDOWN, 0, -1):
		if str(_round.get("roundId", "")) != round_id:
			_mute_game(false)
			return
		_big.text = str(n)
		_big.visible = true
		await get_tree().create_timer(1.0).timeout
	if str(_round.get("roundId", "")) != round_id:
		_mute_game(false)
		return
	_big.text = ""
	client.start_recording()
	_rec_start = Time.get_ticks_msec()
	_rec_seconds = maxf(1.0, float(_round.get("seconds", 5.0)) - lead_in)
	_phase = "recording"
	_refresh()
	while _phase == "recording" and (Time.get_ticks_msec() - _rec_start) / 1000.0 < _rec_seconds:
		await get_tree().process_frame
	var take: AudioStreamWAV = client.stop_recording()
	_mute_game(false)
	if str(_round.get("roundId", "")) != round_id:
		return
	if take == null or take.data.is_empty():
		_phase = "error"
		_error = "Etwas ist schiefgelaufen"
		_refresh()
		return
	print("Voicigame | Beitreten: Aufnahme %.2f s, Spitzenpegel %.3f" % [take.get_length(), _peak(take)])
	_phase = "sending"
	_refresh()
	client.upload_take(round_id, take, lead_in)


## Ausgabe des Spiels (Menümusik, Live-Ton) stumm schalten. Das Mikro läuft über einen eigenen Kanal
## und wird davor mitgeschnitten, es ist davon nicht betroffen.
func _mute_game(on: bool) -> void:
	if on:
		_master_was_muted = AudioServer.is_bus_mute(0)
		AudioServer.set_bus_mute(0, true)
	else:
		AudioServer.set_bus_mute(0, _master_was_muted)


func _open_browser() -> void:
	OS.shell_open("%s/?r=%s" % [client.server_url, client.code])


static func _peak(s: AudioStreamWAV) -> float:
	var d := s.data
	var peak := 0
	for i in range(0, d.size() - 1, 64):
		peak = maxi(peak, absi(d.decode_s16(i)))
	return peak / 32768.0


func _on_uploaded(round_id: String, ok: bool, message: String) -> void:
	if round_id != str(_round.get("roundId", "")):
		return
	_phase = "sent" if ok else "error"
	_error = message
	_refresh()


func _process(_delta: float) -> void:
	# Server nicht erreichbar: nicht ewig „Verbinde …“ zeigen
	if _join_started > 0 and client.player_id == "" and Time.get_ticks_msec() - _join_started > 12000:
		_join_started = 0
		client.leave()
		if is_instance_valid(_entry_status) and _entry_status.is_inside_tree():
			_entry_status.text = tr_("Keine Verbindung zum Server.")
	if is_instance_valid(_meter) and _meter.visible:
		_meter.value = clampf(client.level, 0.0, 1.0) * 100.0
	if _phase == "recording" and is_instance_valid(_bar):
		var el := (Time.get_ticks_msec() - _rec_start) / 1000.0
		_bar.value = clampf(el / maxf(_rec_seconds, 0.01), 0.0, 1.0) * 100.0
		_meter.value = clampf(client.level, 0.0, 1.0) * 100.0
		_big.text = "%d s" % ceili(maxf(0.0, _rec_seconds - el))
		_big.visible = true


# ------------------------------------------------------------------
# Live-Bild und Ton
# ------------------------------------------------------------------

func _on_live_frame(data: PackedByteArray) -> void:
	if data.size() < 2 or not is_instance_valid(_live):
		return
	if data[0] == 1:
		var img := Image.new()
		if img.load_jpg_from_buffer(data.slice(1)) != OK:
			return
		var tex := _live.texture as ImageTexture
		if tex and tex.get_size() == Vector2(img.get_size()):
			tex.update(img)
		else:
			_live.texture = ImageTexture.create_from_image(img)
			_live_wait.visible = false
	elif data[0] == 2 and data.size() > 5:
		# Während der eigenen Aufnahme stumm, damit das Mikro den Spielton nicht mitnimmt
		if not _sound or _phase in ["countdown", "recording"]:
			return
		var rate := data.decode_u32(1)
		if rate < 8000 or rate > 96000:
			return
		if _gen_rate != rate:
			_start_generator(rate)
		var n := (data.size() - 5) / 2
		var free := _gen.get_frames_available()
		if free < n:
			return   # Puffer voll: Paket auslassen statt Verzögerung aufzubauen
		var frames := PackedVector2Array()
		frames.resize(n)
		for i in n:
			var v := data.decode_s16(5 + i * 2) / 32768.0
			frames[i] = Vector2(v, v)
		_gen.push_buffer(frames)


func _start_generator(rate: int) -> void:
	_gen_rate = rate
	if _gen_player == null:
		_gen_player = AudioStreamPlayer.new()
		add_child(_gen_player)
	var g := AudioStreamGenerator.new()
	g.mix_rate = rate
	g.buffer_length = 0.6
	_gen_player.stream = g
	_gen_player.play()
	_gen = _gen_player.get_stream_playback()


func _toggle_sound() -> void:
	_sound = not _sound
	UI.set_button_text(_btn_sound, tr_("Ton aus") if _sound else tr_("Ton an"))
	if not _sound and _gen_player:
		_gen_player.stop()
		_gen_rate = 0


func _close() -> void:
	if _phase in ["countdown", "recording"]:
		_mute_game(false)
	client.leave()
	closed.emit()
	queue_free()
