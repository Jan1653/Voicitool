extends RefCounted
## Übersetzung der Mod-Texte. Deutsch ist die Quelle im Code, die Übersetzungen stehen in lang.json.
## Sprache: [ui] lang in user://voicigame.cfg, sonst die von Windows, sonst Englisch.
## Texte, die an den Server gehen (Statuszeilen), bleiben deutsch: die Handys übersetzen sie selbst.

const CONFIG := "user://voicigame.cfg"

static var base_dir := ""        # Ordner des Mods, setzt main.gd beim Start
static var lang := ""
static var _dict := {}
static var _patterns: Array = []   # [RegEx, Übersetzung] für Schlüssel mit {}
static var _loaded := false


## Übersetzt s. Jedes {} wird der Reihe nach durch args ersetzt.
## Ohne args werden auch fertige Sätze erkannt, z. B. „Jan ist dran" über den Schlüssel „{} ist dran".
static func t(s: String, args: Array = []) -> String:
	if not _loaded:
		_load()
	var out: String = _dict.get(s, "")
	if out == "" and args.is_empty():
		for p in _patterns:
			var m: RegExMatch = p[0].search(s)
			if m:
				out = p[1]
				args = m.strings.slice(1)
				break
	if out == "":
		out = s
	for a in args:
		var i := out.find("{}")
		if i < 0:
			break
		out = out.substr(0, i) + str(a) + out.substr(i + 2)
	return out


static func _load() -> void:
	_loaded = true
	var cfg := ConfigFile.new()
	var chosen := ""
	if cfg.load(CONFIG) == OK:
		chosen = str(cfg.get_value("ui", "lang", ""))
	lang = chosen if chosen != "" else OS.get_locale_language()
	if lang == "de":
		return
	var path := base_dir.path_join("lang.json")
	var data = JSON.parse_string(FileAccess.get_file_as_string(path)) if FileAccess.file_exists(path) else null
	if not data is Dictionary:
		push_warning("Voicigame: Übersetzungen fehlen (%s)" % path)
		return
	if not data.has(lang):
		lang = "en"
	if data.get(lang) is Dictionary:
		_dict = data[lang]
	for key in _dict:
		if not "{}" in key:
			continue
		var parts: Array = []
		for part in str(key).split("{}"):
			parts.append(_escape(part))
		var rx := RegEx.new()
		if rx.compile("^" + "(.*?)".join(PackedStringArray(parts)) + "$") == OK:
			_patterns.append([rx, _dict[key]])


static func _escape(s: String) -> String:
	var out := ""
	for ch in s:
		out += ("\\" + ch) if ch in ".*+?^$()[]{}|\\/" else ch
	return out
