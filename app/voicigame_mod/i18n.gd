extends RefCounted
## Übersetzung der Mod-Texte. Deutsch ist die Quelle im Code, die Übersetzungen stehen in lang.json.
## Sprache: [ui] lang in user://voicigame.cfg (im Voicigame-Menü wählbar), sonst die von Windows, sonst Englisch.
## Texte, die an den Server gehen (Statuszeilen), bleiben deutsch: die Handys übersetzen sie selbst.

const CONFIG := "user://voicigame.cfg"
## Namen in der eigenen Sprache, in dieser Reihenfolge in der Auswahl
const LANG_NAMES := {"de": "Deutsch", "en": "English", "es": "Español", "fr": "Français", "pt": "Português",
	"it": "Italiano", "nl": "Nederlands", "pl": "Polski", "cs": "Čeština", "sk": "Slovenčina", "sr": "Srpski",
	"ro": "Română", "hu": "Magyar", "sv": "Svenska", "da": "Dansk", "el": "Ελληνικά", "tr": "Türkçe",
	"ru": "Русский", "uk": "Українська", "hi": "हिन्दी", "th": "ไทย", "vi": "Tiếng Việt", "id": "Bahasa Indonesia",
	"ja": "日本語", "zh": "中文", "ko": "한국어"}

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


## Gewählte Sprache aus den Einstellungen, "" = wie Windows.
static func chosen() -> String:
	var cfg := ConfigFile.new()
	return str(cfg.get_value("ui", "lang", "")) if cfg.load(CONFIG) == OK else ""


## Sprachen, für die es Übersetzungen gibt (Deutsch ist die Quelle).
static func available() -> Array:
	var path := base_dir.path_join("lang.json")
	var data = JSON.parse_string(FileAccess.get_file_as_string(path)) if FileAccess.file_exists(path) else null
	var have: Array = ["de"] + (data.keys() if data is Dictionary else [])
	return LANG_NAMES.keys().filter(func(c): return have.has(c))


## Sprache speichern ("" = wie Windows) und ab sofort benutzen.
static func choose(code: String) -> void:
	var cfg := ConfigFile.new()
	if cfg.load(CONFIG) != OK and FileAccess.file_exists(CONFIG):
		return   # Datei da, aber nicht lesbar: nichts überschreiben
	if code == "":
		if cfg.has_section_key("ui", "lang"):
			cfg.erase_section_key("ui", "lang")
	else:
		cfg.set_value("ui", "lang", code)
	cfg.save(CONFIG)
	_loaded = false
	_dict = {}
	_patterns = []
	lang = ""


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
