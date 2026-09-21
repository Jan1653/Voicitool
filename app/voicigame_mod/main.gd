extends Node
## voicigame: Mitspielen am Handy oder im Browser für The Choicer Voicer.
##
## Wird über override.cfg neben der Spiel-exe als Autoload geladen, nach allen Modulen des
## Spiels. Läuft mit dem Originalspiel 0.5.3 und mit dem Steam-Mehrspieler-Mod.
##
##   Menü „Solo / Gruppe"   bekommt eine Kachel „Voicigame"
##   Auswahl                Lobby erstellen (Host) oder Lobby beitreten (join_screen.gd)
##   Lobby                  Raumcode, QR, Link, Web-Spieler
##   Weiter                 Web-Spieler werden Gruppenmitglieder, danach normaler Spielablauf
##   Gameshow               show_hook.gd legt die Handy-Aufnahmen in die Aufnahme des Spiels
##   Synchronisieren        dub_hook.gd: Pack im Spiel wählen, Web-Spieler sprechen ihre Figuren im Browser

const VERSION := "0.4.0"   # bei jeder Mod-Änderung erhöhen (Voicitool zeigt sie an): Fehler 0.2.x, Neues 0.x.0
const CONFIG_PATH := "user://voicigame.cfg"
const MEMBER_SCENE := "res://scenes/nav_specific/play_flow/select_member_count.tscn"
const DUB_SELECT_SCENE := "res://scenes/nav_specific/clip_selector_menus/clip_selection_dub.tscn"
const DUB_SCENE := "res://scenes/gameplay/dub_mode/main/dub_mode.tscn"
const GROUP_MODE_SCENE := "res://scenes/nav_specific/play_flow/select_game_mode_group.tscn"
## Wer im Spielmenü Solo, Gruppe oder Multiplayer wählt, spielt danach nicht mehr in der Voicigame-Runde
const OTHER_PLAY_SCENES := ["res://scenes/nav_specific/play_flow/select_game_mode_solo.tscn",
	"res://scenes/nav_specific/play_flow/select_members.tscn",
	"res://scenes/nav_specific/multiplayer/multiplayer_lobby_screen.tscn"]
const MAX_PLAYERS := 4

const Bridge = preload("bridge.gd")
const Lobby = preload("lobby.gd")
const ShowHook = preload("show_hook.gd")
const Players = preload("players.gd")
const JoinScreen = preload("join_screen.gd")
const I18n = preload("i18n.gd")
const Stream = preload("stream.gd")
const DubHook = preload("dub_hook.gd")
const Updater = preload("updater.gd")

var bridge: Node
var _lobby: CanvasLayer
var _join: CanvasLayer
var _menu: Node                  # der Solo/Gruppe-Bildschirm, über den wir weiterschalten
var _dub_active := false         # Raum ist im Dub-Modus (Synchronisieren)
var _dub_host_plays := true
var dub_hook: Node
var updater: Node


func _ready() -> void:
	process_mode = Node.PROCESS_MODE_ALWAYS
	I18n.base_dir = get_script().resource_path.get_base_dir()
	bridge = Bridge.new()
	bridge.name = "Bridge"
	bridge.server_url = _load_server_url()
	add_child(bridge)
	var stream := Stream.new()
	stream.name = "Stream"
	stream.bridge = bridge
	bridge.add_child(stream)
	get_tree().node_added.connect(_on_node_added)
	Players.remove_all()   # Reste einer abgestürzten Sitzung
	DubHook.restore_parked()
	# Neuere Mod-Version vom Server holen (nicht bei Voicitool-Installation, die aktualisiert Voicitool)
	updater = Updater.new()
	updater.name = "Updater"
	add_child(updater)
	updater.start(bridge.server_url, I18n.base_dir, VERSION)
	print("Voicigame %s geladen, Server %s" % [VERSION, bridge.server_url])


func _load_server_url() -> String:
	var cfg := ConfigFile.new()
	if cfg.load(CONFIG_PATH) == OK:
		return str(cfg.get_value("server", "url", Bridge.DEFAULT_SERVER)).trim_suffix("/")
	# Nur anlegen, wenn es die Datei nicht gibt. Ist sie da, aber nicht lesbar (Tippfehler), nicht überschreiben.
	if FileAccess.file_exists(CONFIG_PATH):
		push_warning("Voicigame: %s ist nicht lesbar, nehme den Standardserver" % CONFIG_PATH)
		return Bridge.DEFAULT_SERVER
	cfg.set_value("server", "url", Bridge.DEFAULT_SERVER)
	cfg.save(CONFIG_PATH)
	return Bridge.DEFAULT_SERVER


func _notification(what: int) -> void:
	if what == NOTIFICATION_WM_CLOSE_REQUEST:
		bridge.close_room()
		Players.remove_all()


# ------------------------------------------------------------------
# Szenen erkennen
# ------------------------------------------------------------------

func _on_node_added(node: Node) -> void:
	if node.scene_file_path == MEMBER_SCENE:
		# Nicht hier zurücksetzen: das Spiel lädt dieses Menü auch zwischendurch (etwa zwischen zwei Dub-Runden)
		node.ready.connect(_add_tile.bind(node), CONNECT_ONE_SHOT)
	elif node.scene_file_path in OTHER_PLAY_SCENES:
		_dub_active = false   # ein späteres Solo-Dub gehört nicht mehr zur Voicigame-Runde
	elif node.scene_file_path == DUB_SCENE and _dub_active and bridge.has_room():
		node.ready.connect(_attach_dub.bind(node), CONNECT_ONE_SHOT)
	elif bridge.has_room() and node.has_method("GENERIC_RF_RecordContestants") and not _web_slots().is_empty():
		node.ready.connect(_attach_show.bind(node), CONNECT_ONE_SHOT)


## Neue Kachel neben Solo / Gruppe (/ Multiplayer vom Steam-Mod), geklont aus einer vorhandenen.
func _add_tile(menu: Node) -> void:
	var row := menu.get_node_or_null("MarginContainer/HBoxContainer")
	if row == null or row.has_node("VoicigameTile"):
		return
	# Vorlage: die letzte sichtbare Kachel. Im Originalspiel ist die letzte ein versteckter Platzhalter.
	var template: Node = null
	for c in row.get_children():
		if c is CanvasItem and c.visible:
			template = c
	if template == null:
		return
	var tile: Node = template.duplicate(Node.DUPLICATE_GROUPS | Node.DUPLICATE_SCRIPTS | Node.DUPLICATE_USE_INSTANTIATION)
	tile.name = "VoicigameTile"
	_own_materials(tile)
	row.add_child(tile)
	tile.visible = true
	var button: Node = tile.get_child(0)
	var labels: Array = button.get_children().filter(func(n): return n is Label)
	if labels.size() >= 1:
		labels[0].text = _same_layout(labels[0].text, "Voicigame")
	if labels.size() >= 2:
		labels[1].text = _same_layout(labels[1].text, I18n.t("Handy & Browser"))
	if OS.get_environment("VOICIGAME_TEST") != "":
		print("Voicigame | Kachel-Vorlage: %s" % [labels.map(func(l): return l.text.c_escape())])
		var own: bool = button.material == null or button.material != template.get_child(0).material
		print("Voicigame | Kachel mit eigenem Material: %s" % own)
	if button.has_signal("button_clicked"):
		for con in button.get_signal_connection_list("button_clicked"):
			button.disconnect("button_clicked", con.callable)
		button.button_clicked.connect(_open_lobby.bind(menu))
	_fit_row.call_deferred(row)


## Jede Kachel des Spiels leuchtet über ein Shader-Material auf, das sich alle Kopien teilen. Ohne eigene Kopie
## leuchtet die Voicigame-Kachel mit, wenn man über die Vorlage fährt (und umgekehrt).
static func _own_materials(node: Node) -> void:
	if node is CanvasItem and node.material:
		node.material = node.material.duplicate()
	for c in node.get_children():
		_own_materials(c)


## Neuer Text mit den Leerzeichen und Zeilenumbrüchen des alten davor und danach
## (die Kacheln des Spiels richten ihre Unterzeile damit aus).
static func _same_layout(old: String, text: String) -> String:
	var lead := old.length() - old.lstrip(" \n\t").length()
	var trail := old.length() - old.rstrip(" \n\t").length()
	return old.substr(0, lead) + text + old.substr(old.length() - trail)


## Mit einer Kachel mehr wird die Reihe breiter als der Bildschirm: dann die ganze Reihe verkleinern.
func _fit_row(row: Control) -> void:
	await get_tree().process_frame
	if not is_instance_valid(row):
		return
	var avail := row.get_viewport_rect().size.x * 0.96
	var need := row.get_combined_minimum_size().x
	if need > avail and need > 0.0:
		var k := avail / need
		row.pivot_offset = Vector2(row.size.x / 2.0, row.size.y / 2.0)
		row.scale = Vector2(k, k)


# ------------------------------------------------------------------
# Lobby
# ------------------------------------------------------------------

func _open_lobby(menu: Node) -> void:
	_menu = menu
	if is_instance_valid(_lobby) or is_instance_valid(_join):
		return
	_lobby = Lobby.new()
	_lobby.name = "VoicigameLobby"
	_lobby.update_note = _update_note()
	get_tree().root.add_child(_lobby)
	_lobby.setup(bridge)
	_lobby.start_requested.connect(_start_session)
	_lobby.join_requested.connect(_open_join)
	_lobby.dub_requested.connect(_start_dub)
	_lobby.closed.connect(_on_lobby_closed)


func _update_note() -> String:
	if not is_instance_valid(updater):
		return ""
	match updater.state:
		"installed":
			return I18n.t("Update auf {} installiert. Es gilt ab dem nächsten Spielstart.", [updater.new_version])
		"available":
			return I18n.t("Neue Version {} verfügbar. Hol sie dir auf GameBanana oder mit Voicitool.", [updater.new_version])
	return ""


func _on_lobby_closed() -> void:
	Players.remove_all()
	_dub_active = false


## Synchronisieren: Pack in der Dub-Auswahl des Spiels wählen, danach übernimmt dub_hook.gd.
func _start_dub(host_plays: bool) -> void:
	var metro = get_node_or_null("/root/Metro")
	var m = get_node_or_null("/root/M")
	if metro == null or m == null or not is_instance_valid(_menu):
		push_warning("Voicigame: Spielmodule nicht gefunden")
		return
	_dub_active = true
	_dub_host_plays = host_plays
	bridge._send({"type": "dub.open", "source": "game"})
	m.session_type = m.SESSION_TYPE.VIDEO_DUB
	metro.clip_selection_page_back_path = MEMBER_SCENE
	_lobby.queue_free()
	_menu.call_slide(DUB_SELECT_SCENE, false)


func _attach_dub(dub_scene: Node) -> void:
	if is_instance_valid(dub_hook):
		dub_hook.queue_free()
	dub_hook = DubHook.new()
	dub_hook.name = "DubHook"
	add_child(dub_hook)
	dub_hook.attach(dub_scene, bridge, _dub_host_plays)


## Bei einem anderen Host mitspielen.
func _open_join() -> void:
	if is_instance_valid(_join):
		return
	_join = JoinScreen.new()
	_join.name = "VoicigameJoin"
	get_tree().root.add_child(_join)
	_join.setup(bridge.server_url)


func _start_session(host_plays: bool) -> void:
	var metro = get_node_or_null("/root/Metro")
	var profile = get_node_or_null("/root/Profile")
	if metro == null or profile == null or not is_instance_valid(_menu):
		push_warning("Voicigame: Spielmodule nicht gefunden")
		return
	Players.remove_all()
	# Raum war zuletzt im Dub-Modus: zurück zur Gameshow, sonst bleiben die Handys in der Dub-Ansicht
	if _dub_active or str(bridge.state.get("game", "show")) == "dub":
		bridge._send({"type": "dub.close"})
	_dub_active = false
	var members: Array[BasicPlayerPackage] = []
	if host_plays:
		members.append(BasicPlayerPackage.new(profile.contestant, profile.audio_device_in))
	# Nur wer gerade verbunden ist: ein geschlossener Tab käme sonst in jede Runde und kostet Wartezeit
	var web: Array = bridge.web_players().filter(func(p): return p.get("connected", false))
	for i in web.size():
		if members.size() >= MAX_PLAYERS:
			break
		var p: Dictionary = web[i]
		var pack := Players.make_pack(i, str(p.id), str(p.name))
		members.append(BasicPlayerPackage.new(pack, Players.device_for(str(p.id))))
	metro.current_players = members
	metro.clip_selection_page_back_path = GROUP_MODE_SCENE
	_lobby.queue_free()
	_menu.call_slide(GROUP_MODE_SCENE, false)


## Welche Mitspieler im Spiel sind Web-Spieler? Abgelesen an unserem Kennzeichen statt eines Mikrofons.
## -> {Mitspieler-Index: Web-Spieler-ID}
func _web_slots() -> Dictionary:
	var metro = get_node_or_null("/root/Metro")
	var out := {}
	if metro == null:
		return out
	var list: Array = metro.current_players
	for i in list.size():
		var id := Players.player_for_device(str(list[i].input_device_name))
		if id != "":
			out[i] = id
	return out


func _attach_show(match_master: Node) -> void:
	var slots := _web_slots()
	var names := {}
	for i in slots:
		for p in bridge.web_players():
			if str(p.id) == slots[i]:
				names[i] = str(p.name)
	# PC-Spieler: eingetragener Name statt der Figur des Spiels, damit die Handys wissen, wer das ist
	var own := Players.saved_name()
	var metro = get_node_or_null("/root/Metro")
	if own != "" and metro:
		for i in metro.current_players.size():
			if not slots.has(i):
				names[i] = own
	var hook: Node = ShowHook.new()
	hook.name = "ShowHook"
	add_child(hook)
	hook.attach(match_master, bridge, slots, names)
