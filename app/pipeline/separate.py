"""Stimmen vom Hintergrund trennen (audio-separator, BS-RoFormer)."""
import gc
import shutil
from pathlib import Path

from app import config


def _patch_tqdm(on_progress):
    """audio-separator meldet Fortschritt nur über tqdm, hier abgreifen."""
    import tqdm as tqdm_mod
    import importlib

    original = tqdm_mod.tqdm

    class ReportingTqdm(original):
        def update(self, n=1):
            r = super().update(n)
            if self.total:
                on_progress(min(1.0, self.n / self.total))
            return r

    patched = []
    for mod_name in (
        "audio_separator.separator.architectures.mdxc_separator",
        "audio_separator.separator.architectures.mdx_separator",
        "audio_separator.separator.architectures.demucs_separator",
        "audio_separator.separator.architectures.vr_separator",
    ):
        try:
            mod = importlib.import_module(mod_name)
        except Exception:
            continue
        if getattr(mod, "tqdm", None) is not None:
            patched.append((mod, mod.tqdm))
            mod.tqdm = ReportingTqdm
    return patched


def separate(audio_path, out_dir, on_progress, overlap=4):
    """Erzeugt stimmen.wav (nur Stimmen) und hintergrund.wav (alles ohne Stimmen)."""
    import torch
    from audio_separator.separator import Separator

    out_dir = Path(out_dir)
    tmp = out_dir / "_trennung"
    tmp.mkdir(exist_ok=True)
    patched = _patch_tqdm(on_progress)
    try:
        sep = Separator(
            output_dir=str(tmp),
            model_file_dir=str(config.MODELS_DIR / "separator"),
            output_format="WAV",
            sample_rate=44100,
            use_autocast=torch.cuda.is_available(),
            mdxc_params={"segment_size": 256, "override_model_segment_size": False, "batch_size": 1,
                         "overlap": int(overlap), "pitch_shift": 0},
        )
        sep.load_model(model_filename=config.SEPARATOR_MODEL)
        files = sep.separate(str(audio_path), custom_output_names={"Vocals": "stimmen", "Instrumental": "hintergrund"})
        for f in files:
            p = Path(f)
            if not p.is_absolute():
                p = tmp / p
            name = "stimmen.wav" if "stimmen" in p.name.lower() else "hintergrund.wav"
            shutil.move(str(p), out_dir / name)
    finally:
        for mod, orig in patched:
            mod.tqdm = orig
        shutil.rmtree(tmp, ignore_errors=True)
        try:
            del sep
        except UnboundLocalError:
            pass
        gc.collect()
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
    if not (out_dir / "stimmen.wav").exists() or not (out_dir / "hintergrund.wav").exists():
        raise RuntimeError("Stimmen-Trennung hat keine Ausgabedateien erzeugt.")
