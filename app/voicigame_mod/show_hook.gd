extends Node
## Gameshow mit Web-Spielern.
##
## Das Spiel nimmt jeden Mitspieler über den Tonkanal „Plmic" auf und bewertet dabei live.
## Ist ein Web-Spieler dran, legt dieser Knoten statt des PC-Mikrofons die Aufnahme vom Handy
## in genau diesen Kanal. Aufnahme, Wellenform, Jury und Punkte laufen dadurch unverändert
## über das Spiel selbst.
##
## Ablauf je Runde:
##   1. neue Runde erkannt -> Clip ist schon hochgeladen -> Handys bekommen „jetzt aufnehmen"
##   2. Handy spielt den Clip, der Spieler nimmt auf, das Handy schickt die WAV
##   3. Zug beginnt (das Spiel schaltet das Mikro ein, 0,25 s vor der Aufnahme):
##      Web-Spieler -> PC-Mikro stumm, bei Aufnahmebeginn Handy-WAV abspielen.
##      Ist seine Aufnahme noch nicht da, hält das Spiel genau hier an, bis sie ankommt
##      (höchstens WAIT_MAX_S, bei getrenntem Handy höchstens OFFLINE_GRACE_S)
##   4. Jury hat bewertet -> Punkte an alle Handys
##
## „Zug beginnt" wird am Einschalten des Mikro-Players erkannt: das passiert nur in der
## Aufnahmephase, nicht beim Rundenwechsel und nicht beim Bewerten (in beiden Spielfassungen gleich).

const Players = preload("players.gd")
const I18n = preload("i18n.gd")
const LEAD_IN_FRAMES := 25       # das Spiel startet den Clip 25 Physik-Frames nach Aufnahmebeginn
const EXTRA_SECONDS := 1.5
const WAIT_MAX_S := 60.0         # so lange wartet das Spiel höchstens auf eine Handy-Aufnahme
const OFFLINE_GRACE_S := 10.0    # so lange, wenn das Handy getrennt ist
const MAX_TRIES := 5             # Wiederholungen für Clip-Uploads und Aufnahme-Downloads
const FONT_BOLD := "res://graphic/font/Waukegan LDO Extended Bold.ttf"
const FONT_TEXT := "res://graphic/font/DuruSans-Regular.ttf"

var bridge: Node
var mm: Node
var web_by_slot := {}            # Mitspieler-Index -> Web-Spieler-ID
var name_by_slot := {}           # Mitspieler-Index -> Anzeigename
var takes := {}                  # Web-Spieler-ID -> {round_id, stream} der laufenden Runde

var _prefix := ""                # Clip-Kennungen je Spiel eindeutig, sonst bekommen Handys alte Clips
var _round := -1
var _uploaded := {}
var _clip_info := {}             # Clip-ID -> [Pfad, Titel, Länge] für Wiederholungen
var _upload_tries := {}
var _pending_round := -1
var _mantle: Node
var _mic_stream: AudioStream
var _mic_was_on := false
var _turn_slot := -1
var _web_turn := ""
var _injected := false
var _was_recording := false
var _scores_sent := -1
var _silence: AudioStreamWAV
var _fetching := {}              # Web-Spieler-ID -> true, solange die Aufnahme geladen wird
var _fetch_tries := {}
var _room_lost := false
var _waiting := ""               # Web-Spieler, auf dessen Aufnahme das Spiel gerade wartet
var _wait_since := 0
var _offline_since := 0
var _wait_before := {}           # Zustand vor dem Anhalten: time_scale, paused
var _wait_layer: CanvasLayer
var _wait_info: Label


func attach(match_master: Node, b: Node, slots: Dictionary, names: Dictionary) -> void:
	mm = match_master
	bridge = b
	web_by_slot = slots
	name_by_slot = names
	_prefix = "m%d_" % (Time.get_ticks_msec() % 100000000)
	_silence = _make_silence(2.0)
	bridge.recording_received.connect(_on_recording)
	bridge.recording_failed.connect(_on_recording_failed)
	bridge.show_recording.connect(func(_r, _p): _sync_takes())
	bridge.state_changed.connect(func(_s): _sync_takes())
	bridge.clip_uploaded.connect(_on_clip_uploaded)
	bridge.room_lost.connect(_on_room_lost)
	mm.tree_exiting.connect(_on_match_left)
	process_physics_priority = -100
	_upload_clips.call_deferred()


# ------------------------------------------------------------------
# Clips und Runden
# ------------------------------------------------------------------

func _clips() -> Array:
	var st = mm.get("statsteen")
	return st.get("vamba", []) if st is Dictionary else []


func _clip_id(i: int) -> String:
	return _prefix + str(i)


func _upload_clips() -> void:
	var clips := _clips()
	var meta: Array = []
	for i in clips.size():
		var c = clips[i]
		meta.append({"id": _clip_id(i), "title": str(c.get("file_name_agnostic")) if c else str(i), "character": "", "order": i})
	bridge.set_clips(meta)
	for i in clips.size():
		var c = clips[i]
		var path := str(c.get("self_global_path")) if c else ""
		if path != "" and FileAccess.file_exists(path):
			var length := 0.0
			var audio = c.get("clip_audio")
			if audio is AudioStream:
				length = audio.get_length()
			_clip_info[_clip_id(i)] = [path, str(c.get("file_name_agnostic")), length]
			bridge.upload_clip(_clip_id(i), path, str(c.get("file_name_agnostic")), length)
		else:
			push_warning("Voicigame: Clip %d hat keine Datei (%s)" % [i, path])


func _on_clip_uploaded(clip_id: String, ok: bool) -> void:
	if not clip_id.begins_with(_prefix):
		return
	if ok:
		_uploaded[clip_id] = true
		_try_start_round()
		return
	# Fehlgeschlagen (Netz, Server kurz weg): nach einer Pause nochmal, sonst startet die Runde nie
	_upload_tries[clip_id] = int(_upload_tries.get(clip_id, 0)) + 1
	var info = _clip_info.get(clip_id)
	if info and _upload_tries[clip_id] < MAX_TRIES:
		await get_tree().create_timer(2.0, true, false, true).timeout
		if is_instance_valid(bridge) and bridge.has_room():
			bridge.upload_clip(clip_id, info[0], info[1], info[2])


func _try_start_round() -> void:
	if _pending_round < 0 or not _uploaded.has(_clip_id(_pending_round)):
		return
	var clips := _clips()
	var i := _pending_round
	_pending_round = -1
	var length := 5.0
	if i < clips.size() and clips[i] and clips[i].get("clip_audio") is AudioStream:
		length = clips[i].clip_audio.get_length()
	var lead_in := float(LEAD_IN_FRAMES) / float(Engine.physics_ticks_per_second)
	takes.clear()
	_fetching.clear()
	_fetch_tries.clear()
	bridge.start_round(i, clips.size(), _clip_id(i), length + lead_in + EXTRA_SECONDS, lead_in, web_by_slot.values())


# ------------------------------------------------------------------
# Jede Physik-Runde: Runde, Zugbeginn, Aufnahme läuft?
# ------------------------------------------------------------------

func _physics_process(_delta: float) -> void:
	if not is_instance_valid(mm):
		return
	var dyn = mm.get("dynasteen")
	if not dyn is Dictionary:
		return

	var r := int(dyn.get("round", 0))
	if r != _round:
		_round = r
		_pending_round = r
		takes.clear()
		_try_start_round()

	# Zugbeginn: das Spiel schaltet das Mikro nur direkt vor einer Aufnahme ein
	var mic := _mic_player()
	var mic_on := mic != null and mic.playing
	if mic_on and not _mic_was_on:
		_on_turn_started(int(dyn.get("on_contestant", 0)))
	_mic_was_on = mic_on

	var rec := _is_recording()
	if rec and not _was_recording:
		_on_recording_started()
	elif not rec and _was_recording:
		_on_recording_stopped()
	_was_recording = rec

	_check_scores()


func _mantle_node() -> Node:
	if is_instance_valid(_mantle):
		return _mantle
	var rm = mm.get("record_master")
	if rm:
		_mantle = rm.get("wave_control")
	return _mantle


func _mic_player() -> AudioStreamPlayer:
	var m := _mantle_node()
	var p = m.get("plmic_stream") if m else null
	return p if p is AudioStreamPlayer else null


## Der Aufnahme-Effekt sitzt je nach Spielfassung an anderer Stelle (Original 7, Steam-Mod 10).
## Deshalb die Referenz nehmen, die das Spiel selbst benutzt, sonst den Kanal danach absuchen.
func _record_effect() -> AudioEffectRecord:
	var m := _mantle_node()
	if m:
		var fx = m.get("plmic_record_effect")
		if fx is AudioEffectRecord:
			return fx
	var bus := AudioServer.get_bus_index("Plmic")
	for i in AudioServer.get_bus_effect_count(bus) if bus >= 0 else 0:
		var e = AudioServer.get_bus_effect(bus, i)
		if e is AudioEffectRecord:
			return e
	return null


func _is_recording() -> bool:
	var fx := _record_effect()
	return fx != null and fx.is_recording_active()


func _name_of(slot: int) -> String:
	var who := str(name_by_slot.get(slot, ""))
	var slots = mm.get("conte_slots")
	if who == "" and slots is Array and slot >= 0 and slot < slots.size():
		who = str(slots[slot].name)
	return who


func _on_turn_started(slot: int) -> void:
	_restore_mic()
	_turn_slot = slot
	_web_turn = str(web_by_slot.get(slot, ""))
	var who := _name_of(slot)
	if who != "":
		bridge.set_status("%s ist dran" % who)
	if _web_turn != "":
		_mute_mic()
		if _take(_web_turn) == null and not _room_lost and _player_state(_web_turn) != "left":
			_start_wait(who)


## Solange ein Web-Spieler dran ist, soll kein Raumgeräusch vom PC-Mikro in seine Aufnahme.
func _mute_mic() -> void:
	var player := _mic_player()
	if player == null:
		return
	if _mic_stream == null and not (player.stream is AudioStreamWAV):
		_mic_stream = player.stream
	var was_playing := player.playing
	player.stream = _silence
	if was_playing:
		player.play()
	_injected = false


func _on_recording_started() -> void:
	if _web_turn == "":
		return
	var player := _mic_player()
	if player == null:
		return
	var take := _take(_web_turn)
	if take == null:
		push_warning("Voicigame: Aufnahme von %s ist noch nicht da" % _web_turn)
		bridge.set_status("Die Aufnahme ist nicht rechtzeitig angekommen.")
		take = _silence
	player.stream = take
	player.play(0.0)
	_injected = true
	print("Voicigame | Aufnahme von %s eingespielt (%.2f s)" % [_web_turn, take.get_length()])


func _on_recording_stopped() -> void:
	if _injected:
		_dump_for_test()
		_restore_mic()


## Nur im Testlauf: was das Spiel aufgenommen hat, neben die eingespielte Handy-Aufnahme legen.
func _dump_for_test() -> void:
	if OS.get_environment("VOICIGAME_TEST") == "":
		return
	var fx := _record_effect()
	var got: AudioStreamWAV = fx.get_recording() if fx else null
	var take := _take(_web_turn)
	DirAccess.make_dir_recursive_absolute("user://voicigame_test")
	if got:
		got.save_to_wav("user://voicigame_test/plmic_r%d.wav" % _round)
	if take:
		take.save_to_wav("user://voicigame_test/take_r%d.wav" % _round)
	print("Voicigame | Test: Aufnahme Runde %d gespeichert (%.2f s)" % [_round, got.get_length() if got else 0.0])


## Nur zurücktauschen, wenn gerade nicht das Mikro läuft. Ein unnötiges Stoppen und Starten des Mikros
## direkt nach dem Gerätewechsel des Spiels kann den Windows-Audiotreiber (WASAPI) aufhängen.
func _restore_mic() -> void:
	var player := _mic_player()
	if player and _mic_stream and player.stream != _mic_stream:
		var was_playing := player.playing
		player.stop()
		player.stream = _mic_stream
		if was_playing:
			player.play()
	_injected = false


# ------------------------------------------------------------------
# Aufnahmen der Handys holen
# Der Server meldet neue Aufnahmen (show.recording) und führt sie im Zustand (round.done).
# Aus beidem nachladen: so geht keine verloren, auch wenn die Verbindung kurz weg war.
# ------------------------------------------------------------------

func _current_round() -> Dictionary:
	var show = bridge.state.get("show")
	var rnd = show.get("round") if show is Dictionary else null
	return rnd if rnd is Dictionary else {}


func _sync_takes() -> void:
	var rnd := _current_round()
	var rid := str(rnd.get("roundId", ""))
	if rid == "":
		return
	for pid in rnd.get("done", []):
		pid = str(pid)
		if _take(pid) != null or _fetching.has(pid) or int(_fetch_tries.get(pid, 0)) >= MAX_TRIES:
			continue
		_fetching[pid] = true
		bridge.download_show_recording(rid, pid)


func _on_recording(round_id: String, player_id: String, stream: AudioStreamWAV) -> void:
	_fetching.erase(player_id)
	takes[player_id] = {"round_id": round_id, "stream": stream}
	if _waiting == player_id and _take(player_id) != null:
		_end_wait()


func _on_recording_failed(_round_id: String, player_id: String) -> void:
	_fetching.erase(player_id)
	_fetch_tries[player_id] = int(_fetch_tries.get(player_id, 0)) + 1
	await get_tree().create_timer(1.5, true, false, true).timeout
	if is_instance_valid(bridge):
		_sync_takes()


## Aufnahme eines Web-Spielers, aber nur, wenn sie zur laufenden Runde gehört.
func _take(player_id: String) -> AudioStreamWAV:
	var t = takes.get(player_id)
	if not t is Dictionary:
		return null
	var rnd := _current_round()
	if not rnd.is_empty() and str(rnd.get("roundId", "")) != t.round_id:
		return null
	return t.stream


## "online", "offline" (getrennt, kann wiederkommen) oder "left" (nicht mehr im Raum)
func _player_state(player_id: String) -> String:
	for p in bridge.state.get("players", []):
		if str(p.get("id", "")) == player_id:
			return "online" if p.get("connected", false) else "offline"
	return "left"


func _on_room_lost() -> void:
	# Server neu gestartet oder Raum entfernt: auf Handys kann nichts mehr kommen
	_room_lost = true
	_end_wait()


# ------------------------------------------------------------------
# Warten auf eine Handy-Aufnahme
# Spielzeit anhalten (Timer, Tweens, Animationen) und den Baum pausieren. Dieser Knoten,
# die Verbindung und die Anzeige laufen weiter (PROCESS_MODE_ALWAYS vom Mod-Knoten).
# ------------------------------------------------------------------

func _start_wait(who: String) -> void:
	if _waiting != "":
		return
	_waiting = _web_turn
	_wait_since = Time.get_ticks_msec()
	_offline_since = 0
	_wait_before = {"time_scale": Engine.time_scale, "paused": get_tree().paused}
	Engine.time_scale = 0.0
	get_tree().paused = true
	bridge.set_status("Alle warten auf die Aufnahme von %s" % who)
	print("Voicigame | Warte auf die Aufnahme von %s" % who)
	_show_wait(who)
	_sync_takes()


func _end_wait() -> void:
	if _waiting == "":
		return
	var waited := (Time.get_ticks_msec() - _wait_since) / 1000.0
	print("Voicigame | Warten beendet nach %.1f s, Aufnahme %s" % [waited, "da" if _take(_waiting) else "fehlt"])
	_waiting = ""
	Engine.time_scale = float(_wait_before.get("time_scale", 1.0))
	get_tree().paused = bool(_wait_before.get("paused", false))
	var who := _name_of(_turn_slot)
	bridge.set_status("%s ist dran" % who if who != "" else "")
	if is_instance_valid(_wait_layer):
		_wait_layer.queue_free()
	_wait_layer = null


func _process(_delta: float) -> void:
	if _waiting == "":
		return
	var now := Time.get_ticks_msec()
	var left := WAIT_MAX_S - (now - _wait_since) / 1000.0
	# Getrenntes Handy: nur kurz warten (Bildschirmsperre), Spieler weg: sofort weiter
	var st := _player_state(_waiting)
	if st == "left" or _room_lost:
		left = 0.0
	elif st == "offline":
		if _offline_since == 0:
			_offline_since = now
		left = minf(left, OFFLINE_GRACE_S - (now - _offline_since) / 1000.0)
	else:
		_offline_since = 0
	if is_instance_valid(_wait_info):
		_wait_info.text = I18n.t("Das Spiel geht spätestens in {} s ohne sie weiter.", [maxi(0, ceili(left))])
	if left <= 0.0:
		_end_wait()


func _show_wait(who: String) -> void:
	_wait_layer = CanvasLayer.new()
	_wait_layer.layer = 95
	add_child(_wait_layer)
	var bg := ColorRect.new()
	bg.color = Color(0.02, 0.05, 0.1, 0.72)
	bg.set_anchors_preset(Control.PRESET_FULL_RECT)
	_wait_layer.add_child(bg)
	var box := VBoxContainer.new()
	box.set_anchors_preset(Control.PRESET_CENTER)
	box.grow_horizontal = Control.GROW_DIRECTION_BOTH
	box.grow_vertical = Control.GROW_DIRECTION_BOTH
	box.alignment = BoxContainer.ALIGNMENT_CENTER
	box.add_theme_constant_override("separation", 14)
	bg.add_child(box)
	box.add_child(_label(I18n.t("Warte auf die Aufnahme von {}", [who]), 40, true))
	box.add_child(_label(I18n.t("Die Aufnahme kommt vom Handy. Sobald sie da ist, geht es weiter."), 22, false))
	_wait_info = _label("", 20, false)
	_wait_info.modulate = Color(1, 1, 1, 0.7)
	box.add_child(_wait_info)


func _label(text: String, size: int, bold: bool) -> Label:
	var l := Label.new()
	l.text = text
	l.horizontal_alignment = HORIZONTAL_ALIGNMENT_CENTER
	l.add_theme_font_size_override("font_size", size)
	l.add_theme_color_override("font_color", Color.WHITE)
	l.add_theme_color_override("font_outline_color", Color(0, 0, 0, 0.6))
	l.add_theme_constant_override("outline_size", 6)
	var path := FONT_BOLD if bold else FONT_TEXT
	if ResourceLoader.exists(path):
		l.add_theme_font_override("font", load(path))
	return l


# ------------------------------------------------------------------
# Punkte
# ------------------------------------------------------------------

func _check_scores() -> void:
	var slots = mm.get("conte_slots")
	if not slots is Array or slots.is_empty():
		return
	var done := true
	for c in slots:
		if int(c.round_scores.size()) <= _round:
			done = false
			break
	if not done or _scores_sent >= _round:
		return
	_scores_sent = _round
	bridge.set_status("")
	bridge.send_scores(_round, _score_list(slots))


func _score_list(slots: Array) -> Array:
	var out: Array = []
	for i in slots.size():
		var c = slots[i]
		var scores: Array = c.round_scores
		var total := 0.0
		for s in scores:
			total += float(s)
		out.append({
			"playerId": str(web_by_slot.get(i, "")), "name": str(name_by_slot.get(i, c.name)),
			"score": float(scores[-1]) if not scores.is_empty() else 0.0, "total": total,
			"web": web_by_slot.has(i),
		})
	return out


func _on_match_left() -> void:
	_end_wait()
	_restore_mic()
	# Das Spiel stellt je Zug das Eingabegerät des Spielers ein, für Web-Spieler unser Kennzeichen.
	# Bleibt das stehen, nehmen spätere Modi (Dub, Solo) mit dem Windows-Standardmikro auf.
	var profile = get_node_or_null("/root/Profile")
	if profile and str(AudioServer.input_device).begins_with(Players.DEVICE_PREFIX):
		AudioServer.input_device = str(profile.get("audio_device_in"))
	var slots = mm.get("conte_slots")
	if slots is Array and not slots.is_empty():
		var ranking := _score_list(slots)
		ranking.sort_custom(func(a, b): return a.total > b.total)
		bridge.end_show(ranking)
	queue_free()


static func _make_silence(seconds: float) -> AudioStreamWAV:
	var s := AudioStreamWAV.new()
	s.format = AudioStreamWAV.FORMAT_16_BITS
	s.mix_rate = 44100
	var data := PackedByteArray()
	data.resize(int(44100 * seconds) * 2)
	data.fill(0)
	s.data = data
	s.loop_mode = AudioStreamWAV.LOOP_FORWARD
	s.loop_end = int(44100 * seconds)
	return s
