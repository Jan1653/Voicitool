# Voicitool

Voicitool turns a video into a dub pack for **The Choicer Voicer**.

You drop in a clip or paste a YouTube link. Voicitool separates the voices from the music, writes down what is said, works out who says what and cuts every line into its own clip. Then you go through it in the editor, fix what it got wrong and export the pack, or put it straight into the game.

It all runs on your own PC. Your videos are not uploaded anywhere, unless you choose **Process online** for a video or create a share link.

## Download and install

1. Download `Voicitool.exe` from the [latest release](https://github.com/Jan1653/Voicitool/releases/latest).
2. Double-click it. Windows will most likely show a blue box saying "Windows protected your PC". That happens because the exe is not signed (a signing certificate costs money every year). Click **More info**, then **Run anyway**.

   Windows Defender may also block the download as "Trojan:Win32/Wacatac.B!ml". The "!ml" means it was guessed by Defender's automatic detection, not matched against a known virus. This happens a lot with new programs that nobody has signed, and it is a false alarm that gets reported to Microsoft. If you don't want to trust the exe, all the code is here and `Exe bauen.bat` builds the same exe on your own PC.
3. The setup checks your PC. You choose where to install, which language packs you want and whether you want shortcuts. The setup language follows Windows and can be changed at the top right.
4. It then downloads Python, the AI packages and the AI models, about 10 GB in total. Depending on your internet connection this takes 10 to 40 minutes. You don't need admin rights.

After that, the same exe simply starts Voicitool. When a newer version is out here on GitHub, a note shows up at the top of the app. Click **Update now**, or just start Voicitool the next time, and it updates itself. If an update fails, Voicitool starts the version you already have. Updates never touch your projects, exports, models or settings.

**Uninstall:** Windows Settings → Apps, or in Voicitool under Settings → About Voicitool. You can choose to keep your projects and models.

### Requirements

- Windows 10 (version 1809 or newer) or Windows 11, 64-bit
- 8 GB RAM (16 GB recommended)
- about 15 GB of free disk space
- an internet connection for the setup

| Graphics card | What you get |
|---|---|
| NVIDIA GTX 16xx or RTX, driver 570 or newer | Everything runs on the graphics card (CUDA 12.8) |
| Older NVIDIA (GTX 9xx/10xx), driver 560 or newer | Graphics card with CUDA 12.6 |
| AMD, Intel, very old NVIDIA or no graphics card | Runs on the processor. It works, but it takes much longer. Use the quality "Fast" or [process online](#process-online). |

## How to use it

1. Drag a video into the window, drop it into the `eingang` folder (the inbox) or paste a link.
2. Pick a name, the language, the quality and, if you know it, the number of speakers. Click **Process**.
3. Open the project. In **Characters**, name the speakers and merge the ones that are actually the same person.
4. Watch it through and fix the lines: text, speaker, start and end.
5. Go to **Export**. Save the pack as a ZIP, or click **Install into the game**.

Each quality level shows how long it will probably take. The estimate learns from every run, so it gets more accurate on your PC over time. Downloads don't wait for a running export, and the progress also shows on the Voicitool icon in the taskbar.

Projects can be sorted into categories (for example one category for 30 Family Guy clips) and exported together.

### Provide the text

For songs and episodes it helps a lot to give Voicitool the actual words. Before you click **Process**, click **Provide text**. Voicitool searches:

- subtitles inside the video file itself (for example in an MKV episode)
- the uploader's subtitles if the video came from YouTube
- lyrics from [LRCLIB](https://lrclib.net) and lyrics.ovh
- episode transcripts from the Fandom wikis (SpongeBob, The Simpsons and many more)

When you paste a YouTube link, tick **Also get subtitles** and the uploader's own subtitles are added to the video right away. You can also paste any text yourself. The text lands in a field first, so you can shorten it (for example verses that are not in the clip). Voicitool then takes the spelling from your text, fills in words it missed and starts a new line for every line of the lyrics. The timing still comes from the video. On our test videos this roughly halved the word errors when the right text was found.

### Process online

Without a suitable graphics card, separating the voices and recognizing the text take a long time. Voicitool can hand these two steps to free online services:

| Step | Service | Free |
|---|---|---|
| Separate the voices | [MVSEP](https://mvsep.com) | 50 videos a day, up to 10 minutes each |
| Recognize the text | [Groq](https://console.groq.com) (recommended) | 8 hours of audio a day |
| | [Cloudflare Workers AI](https://developers.cloudflare.com/workers-ai/) | about 3.5 hours of audio a day |
| | [Gemini](https://aistudio.google.com) (optional) | limit not published |

You need your own free accounts. Settings → Process online explains step by step where to get the keys, and Voicitool asks once at the first start whether you want to set it up. Then click **Process online** on a video in the inbox. If you set up more than one service, you can pick one for each video, and the list shows the model and which one is recommended. Only the audio is uploaded, and only for videos where you click that button. The keys stay on your PC. Speakers, laughs and everything else still run locally.

On the free Gemini tier, Google may use uploaded audio to improve its products and people may listen to it. According to Google's terms this does not apply in the EU, the UK and Switzerland. Voicitool shows this warning before Gemini can be used.

### Quality levels

| Level | Speech recognition | Voice separation | Video memory |
|---|---|---|---|
| Fast | Whisper large-v3-turbo | light | about 3 GB |
| Standard | Whisper large-v3 | normal | about 5 GB |
| Maximum | Whisper large-v3, wider search | thorough | about 5 GB |
| Extreme | Whisper large-v3, widest search | very thorough | about 5 GB, very slow |

Standard is fine for most videos. Extreme mostly helps with telling speakers apart, not with the text, and it takes about three times as long.

### Languages

- The app is available in 26 languages and starts in your Windows language. You can switch at the top right at any time.
- English speech recognition is always included. For every other language (about 100 in total) you need one of the "All languages" packs. You can pick one during setup or download it later under Settings → AI models.

## Editor

| Action | What it does |
|---|---|
| Drag in an empty speaker row | New line |
| Double-click in an empty row | New line, fitted to the voice |
| Drag a block | Move it, also to another speaker |
| Drag the edge of a block | Change start or end |
| Right-click | Split, merge, fit to voice, recognize text again, change speaker, delete |
| Ctrl + mouse wheel | Zoom |

| Key | Action |
|---|---|
| Space | Play / pause |
| ← / → (with Shift) | 1 s (5 s) back / forward |
| ↑ / ↓ | Previous / next line |
| Enter | Play line |
| N | New line at the playhead |
| I / O | Set start / end to the playhead |
| S / M | Split / merge |
| 1 to 9 | Assign speaker |
| Ctrl+C / X / V | Copy / cut / paste at the playhead |
| Delete | Delete line |
| Ctrl+Z / Ctrl+Y | Undo / redo |

The audio switch **Original / Voices only / Background** lets you check how well the voices were separated. If you set your own instrumental, **Background** plays that one and **Background + voices** plays it together with the separated voices. The slider next to it sets how loud the background is, and the export uses the same setting. **Find laughs** adds "(laughs)" lines where someone laughs.

**Cut video** removes parts of the video from the pack: drag across the part in the timeline. The original stays untouched, the cut part is only skipped while playing and left out when exporting, and everything after it moves forward. Ctrl+Z undoes a cut, a right-click on it removes it again.

Where two lines of the same speaker touch, the middle of the shared edge moves both lines, a little to the left only the end of the left line and a little to the right only the start of the right one. The mouse pointer shows which one you grab.

Lines that say the same thing can share one recording: right-click a line and pick **Link as a repeat of …**. You can unlink them the same way.

## Export

A pack contains the video, one clip per line with its text and timestamps, a background track without voices and the pack info (title, icon, authors). You can choose between voices only and original sound for the clips, and adjust volume and video quality.

If you have the instrumental version of the song, you can load it under Export → Background. Voicitool lines it up with the video on its own. If that doesn't work, click **Align by hand**: you see the separated background and your instrumental on top of each other, can drag yours until they match and listen to one of them or both at once. Your instrumental gets the same loudness as the music in the video, and **Background volume** turns it up or down from there.

**Create link (72 h)** uploads the exported ZIP to Litterbox (catbox.moe) so you can send it to friends. No account is needed, and the link expires after 72 hours. Keep in mind that the clips usually belong to someone else.

"Made with Voicitool" is added to the pack credits by default. You can turn it off per project under Export → More options.

## Known limits

- If two voices sound very similar, Voicitool sometimes hears only one speaker. In that case set the number of speakers and use **Reassign speakers automatically**. That usually separates them.
- Too many speakers is easier to fix than too few. Extra ones can be merged in **Characters**, and the most similar voice is shown first.
- Singing, heavy music and cartoon laughs are harder for the speech recognition. Expect to fix more lines there.
- When two people talk at the same time, both end up in the same clip.
- The game does not load clips longer than 60 seconds, so these get cut (you get a warning).

## Usage count

I'd like to know roughly how many people use Voicitool. So once a day, while the app is open, it tells [GoatCounter](https://www.goatcounter.com) "active" together with the build number. Nothing else is sent: no names, no files, no IDs. GoatCounter doesn't store IP addresses and doesn't use cookies. You can switch it off under Settings → Updates → **Count anonymously**.

## Made with AI

I built this project with a lot of help from AI (Claude). If that bothers you, that's completely fine, you don't have to use it.

## For developers

- `app/version.json` holds the version and a build number. The exe compares the build number with the `main` branch here and updates if it is higher, so the build number goes up with every change.
- `app/changelog.json` lists what changed in each build. The app shows it when an update is available and once after updating.
- `launcher` in `app/version.json` is the version of the exe itself. It goes up when `app/setup/launcher/Voicitool.cs` changes, and the new exe has to be attached to a release. Installed copies then swap their exe on the next start.
- `Exe bauen.bat` (or `app/setup/build_exe.py`) builds `Voicitool.exe`. The app is embedded as a ZIP, and the exe is compiled with the C# compiler that comes with Windows. The exe goes into the release, not into the repository.
- Voicitool is a local web app (FastAPI + plain JavaScript) in a pywebview window. The processing uses faster-whisper, BS-RoFormer (audio-separator), SpeechBrain (ECAPA, ResNet), an AST model for laughs, yt-dlp and ffmpeg.
- UI translations are in `app/static/lang/`. The texts of the setup and the start window are in `app/setup/sprachen.json`.

## License

Voicitool © 2026 Jan1653, licensed under the **GNU General Public License v3.0** (see `LICENSE`).

You may use, change and share it, also commercially. Changed versions have to be released under the GPL-3.0 as well, with source code. The AI models and tools that the setup downloads (Whisper, BS-RoFormer, SpeechBrain, ffmpeg and others) come with their own licenses.
