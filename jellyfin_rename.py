#!/usr/bin/env python3
"""Rename files in a Jellyfin Media library to the 2026 naming convention.

Movies:  Movie Name (Year)/Movie Name (Year).mkv
TV/Anime: Show/Season 01/Show - S01E01.mkv
Subtitles travel with the video. Dry-run unless --apply.

# ponytail: filename parser only (no TMDB/TVDB). Add FileBot/Radarr if titles stay ambiguous.
"""
from __future__ import annotations

import argparse
import fcntl
import html as html_lib
import json
import os
import re
import shutil
import sys
import tempfile
import threading
import time
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, urlparse

VIDEO_EXT = {".mkv", ".mp4", ".avi", ".m4v", ".ts", ".m2ts", ".webm", ".mpg", ".mpeg", ".wmv", ".mov"}
SUB_EXT = {".srt", ".ass", ".ssa", ".sub", ".idx", ".vtt"}
SIDECAR_EXT = SUB_EXT | {".nfo"}
JUNK_NAME_RE = re.compile(r"(?i)^(www\.yts|yts\b.*official|rarbg\.com|sample[-_.]|____padding)")
EXTRA_DIRS = {
    "trailers",
    "featurettes",
    "behind the scenes",
    "scenes",
    "shorts",
    "interviews",
    "deleted scenes",
    "extras",
    "other",
    "sample",
    "samples",
}
SKIP_DIRS = EXTRA_DIRS | {".____padding_file", "@eadir", ".ds_store", "#recycle", ".recycle"}
ID_TAG_RE = re.compile(r"\{(?:tmdb|tvdb|imdb)-[^}]+\}", re.I)
YEAR_RE = re.compile(r"(?:19|20)\d{2}")
SE_RE = re.compile(
    r"(?i)(?:^|[\s._\-\[(])S(\d{1,2})E(\d{1,3})(?:\s*-\s*E?(\d{1,3}))?(?:[\s._\-\])|]|$)"
)
XE_RE = re.compile(r"(?i)(?:^|[\s._\-])(\d{1,2})x(\d{1,3})(?:[\s._\-]|$)")
ANIME_EP_RE = re.compile(
    r"(?i)^(?:\[[^\]]+\]\s*)*(.+?)\s+-\s+(\d{1,3})(?:\s*v\d+)?(?:\s*(?:\[|$|\.))"
)
BARE_EP_RE = re.compile(r"^(?P<ep>\d{1,3})(?:-(?P<end>\d{1,3}))?$")
SEASON_DIR_RE = re.compile(r"(?i)^season\s+(\d{1,2})$")
SHOW_SEASON_RE = re.compile(r"(?i)^(.+?)\s+S(\d{1,2})$")
PACK_RE = re.compile(
    r"(?i)(2160p|1080p|720p|480p|4k|uhd|web-?dl|webrip|bluray|x264|x265|h264|hevc|"
    r"complete|hdtv|dvdrip|bdrip|\[eztv|yts\.|web-dl)"
)
QUALITY_RE = re.compile(
    r"(?i)[\s._\-]+(2160p|1080p|720p|480p|4k|uhd|bluray|blu-ray|webrip|web-?dl|"
    r"hdtv|dvdrip|bdrip|x264|x265|h\.?264|h\.?265|hevc)\b"
)
RES_RE = re.compile(r"(?i)\b(2160p|1080p|720p|480p)\b")
UHD_RE = re.compile(r"(?i)\b(2160p|4k|uhd)\b")
ISO_LANG = {
    "eng", "en", "spa", "es", "fre", "fr", "ger", "de", "ita", "it", "por", "pt",
    "jpn", "ja", "kor", "ko", "chi", "zho", "zh", "rus", "ru", "dut", "nld", "nl",
    "may", "msa", "ms", "cat", "ca", "slo", "slk", "sk", "glg", "gl", "tel", "te",
    "rum", "ron", "ro", "mac", "mkd", "mk", "alb", "sqi", "sq", "kan", "kn",
    "swe", "sv", "kir", "ky", "baq", "eus", "eu", "ice", "isl", "is", "tha", "th",
    "aze", "az", "nob", "nb", "lav", "lv", "mal", "ml", "heb", "he", "fin", "fi",
    "pol", "pl", "lit", "lt", "dan", "da", "arm", "hye", "hy", "hrv", "hr", "hun",
    "hu", "kaz", "kk", "est", "et", "cze", "ces", "cs", "bul", "bg", "srp", "sr",
    "tur", "tr", "hin", "tam", "ta", "gre", "ell", "el", "geo", "kat", "ka",
    "ukr", "uk", "ind", "id", "ara", "ar", "vie", "vi", "khm", "km", "slv", "sl",
    "und", "nor", "no",
}
LANG3 = {
    "en": "eng", "es": "spa", "fr": "fre", "de": "ger", "ja": "jpn", "ko": "kor",
    "zh": "chi", "pt": "por", "nl": "dut", "ru": "rus", "it": "ita",
}
TAG_TOKS = {"hi", "sdh", "cc", "forced"}
REGION_TOKS = {"hant", "hans", "ca", "fr", "es", "nl", "be", "pt", "br", "mx"}
JUNK_TOKENS = {
    "1080p", "720p", "2160p", "480p", "4k", "uhd", "hdr", "hdr10", "dv",
    "bluray", "blu-ray", "webrip", "web-dl", "webdl", "web", "hdtv", "dvdrip", "bdrip",
    "x264", "x265", "h264", "h.264", "h265", "h.265", "hevc", "avc", "10bit", "10-bit",
    "aac", "aac2.0", "aac5.1", "ddp", "ddp5.1", "ddp2.0", "dd+", "ac3", "dts", "truehd",
    "atmos", "eac3", "6ch", "5.1", "2.0", "7.1", "10bit",
    "nf", "amzn", "dsnp", "hulu", "atvp", "hbo", "max",
    "multi", "multisub", "multi-audio", "internal", "repack", "proper",
    "hq", "pre", "eng", "subs", "english", "complete", "season",
    "sci-fi", "scifi", "sci", "fi",
}
SMALL_WORDS = {"a", "an", "and", "as", "at", "for", "from", "in", "of", "on", "or", "the", "to", "vs", "with"}
ILLEGAL_FS = re.compile(r'[<>:"/\\|?*]')


@dataclass
class Movie:
    title: str
    year: str | None = None
    res: str | None = None
    id_tags: str = ""


@dataclass
class Episode:
    show: str
    season: int
    episode: int
    episode_end: int | None = None
    title: str = ""
    year: str | None = None


@dataclass
class Plan:
    src: Path
    dst: Path
    kind: str
    note: str = ""


@dataclass
class Row:
    rel: str
    name: str
    is_dir: bool
    status: str
    would: str
    n_rename: int


def safe_name(name: str) -> str:
    name = name.replace(":", " -")
    name = ILLEGAL_FS.sub("", name)
    return re.sub(r"\s+", " ", name).strip(" .")


def smart_title(name: str, dotted: bool) -> str:
    name = re.sub(r"\s+", " ", name.replace("_", " ")).strip(" -.")
    name = re.sub(r"(?<=\w)-\s+(?=\w)", " - ", name)
    if not dotted:
        return name
    words = name.split()
    out = []
    for i, w in enumerate(words):
        lw = w.lower()
        if i != 0 and lw in SMALL_WORDS:
            out.append(lw)
        elif w.isupper() and 1 < len(w) <= 3:
            out.append(w)
        else:
            out.append(w[:1].upper() + w[1:] if w else w)
    return " ".join(out)


def strip_brackets(text: str) -> str:
    keep = ID_TAG_RE.findall(text)
    text = ID_TAG_RE.sub(" ", text)
    text = re.sub(r"\[[^\]]*\]", " ", text)
    if keep:
        text = text.rstrip() + " " + " ".join(keep)
    return text


def is_pack_name(name: str) -> bool:
    return bool(PACK_RE.search(name.replace(" ", ".")))


def resolution_of(text: str) -> str | None:
    if UHD_RE.search(text):
        return "2160p"
    m = RES_RE.search(text)
    return m.group(1).lower() if m else None


def parse_movie(stem: str) -> Movie:
    id_tags = " ".join(ID_TAG_RE.findall(stem))
    dotted = stem.count(".") >= 2
    work = strip_brackets(stem)
    work = work.replace("_", " ")
    year = None
    title = work
    m = re.search(r"\(((?:19|20)\d{2})\)", work)
    if m:
        year = m.group(1)
        title = work[: m.start()]
    else:
        m = re.search(r"(?:^|[\s.])((?:19|20)\d{2})(?:[\s.]|$)", work)
        if m:
            year = m.group(1)
            title = work[: m.start()]
        else:
            q = QUALITY_RE.search(work if " " in work else work.replace(".", " "))
            dotted_work = work.replace(".", " ")
            q = QUALITY_RE.search(" " + dotted_work)
            if q:
                title = dotted_work[: q.start()]
            else:
                title = work
    title = title.replace(".", " ")
    title = re.sub(r"^\d{1,2}\s+", "", title.strip(" -._"))
    title = re.sub(r"(?i)\s+-\s+sci-?fi\s*$", "", title)
    title = smart_title(title, dotted)
    title = safe_name(title)
    return Movie(title=title, year=year, res=resolution_of(stem), id_tags=id_tags)


def cut_junk_title(text: str) -> str:
    text = strip_brackets(text).replace(".", " ").replace("_", " ")
    text = re.sub(r"\s+", " ", text).strip(" -")
    tokens = []
    for tok in text.split():
        raw = tok.strip("-")
        key = raw.lower()
        if key in JUNK_TOKENS or re.fullmatch(r"\d{3,4}p", key):
            break
        if re.fullmatch(r"(?i)eztvx?|yts.*|rarbg|telly|ntb|elite|megusta|flux|grace|hodl|shaanig|neonoire?", key):
            break
        tokens.append(tok)
    title = " ".join(tokens).strip(" -")
    if re.fullmatch(r"(?i)episode\s+\d+", title):
        return ""
    return title


def parse_episode(stem: str, hint_show: str | None, hint_season: int | None, hint_year: str | None) -> Episode | None:
    dotted = stem.count(".") >= 2 and " " not in stem.split("S")[0] if "S" in stem or "s" in stem else stem.count(".") >= 2
    work = stem
    m = SE_RE.search(work)
    if m:
        show = strip_brackets(work[: m.start()]).replace(".", " ").replace("_", " ")
        show = smart_title(show.strip(" -._"), dotted or "." in work[: m.start()])
        ep_title = cut_junk_title(work[m.end() :])
        season, ep, end = int(m.group(1)), int(m.group(2)), int(m.group(3)) if m.group(3) else None
        if hint_show:
            hint_base = re.sub(r"\s+\((?:19|20)\d{2}\)$", "", hint_show).strip()
            if not show or show.lower() == hint_show.lower() or show.lower() == hint_base.lower():
                show = hint_base
        show = safe_name(show or (re.sub(r"\s+\((?:19|20)\d{2}\)$", "", hint_show).strip() if hint_show else ""))
        if not show:
            return None
        return Episode(show, season, ep, end, ep_title, hint_year)
    m = XE_RE.search(work)
    if m:
        show = hint_show or ""
        if not show:
            pre = strip_brackets(work[: m.start()]).replace(".", " ")
            show = smart_title(pre.strip(" -._"), True)
        ep_title = cut_junk_title(work[m.end() :])
        show = safe_name(show)
        if not show:
            return None
        return Episode(show, int(m.group(1)), int(m.group(2)), None, ep_title, hint_year)
    m = ANIME_EP_RE.match(strip_brackets(work).strip())
    if m and hint_show:
        return Episode(safe_name(hint_show), hint_season or 1, int(m.group(2)), None, "", hint_year)
    if m and not hint_show:
        show = smart_title(m.group(1).strip(" -._"), False)
        show = re.sub(r"(?i)\s+part\s+\d+$", "", show).strip()
        show = re.sub(r"(?i)\s+final season$", "", show).strip()
        return Episode(safe_name(show), hint_season or 1, int(m.group(2)), None, "", hint_year)
    m = BARE_EP_RE.fullmatch(work.strip())
    if m and hint_show:
        epn = int(m.group("ep"))
        end = int(m.group("end")) if m.group("end") else None
        title = ""
        if end is not None and end <= 9 and end != epn:
            title = f"part {end}"
            end = None
        return Episode(safe_name(hint_show), hint_season or 1, epn, end, title, hint_year)
    return None


def folder_hints(file_path: Path, library_root: Path) -> tuple[str | None, int | None, str | None]:
    rel = file_path.relative_to(library_root)
    show = None
    season = None
    year = None
    for part in rel.parts[:-1]:
        if part.lower() in SKIP_DIRS or part.lower() in {"subs", "subtitles"}:
            continue
        sm = SEASON_DIR_RE.fullmatch(part)
        if sm:
            season = int(sm.group(1))
            continue
        titled = re.fullmatch(r"(.+?)\s+\(((?:19|20)\d{2})\)", part)
        if titled and not is_pack_name(part):
            show = safe_name(titled.group(1))
            year = titled.group(2)
            continue
        ym = YEAR_RE.search(part)
        if ym:
            year = ym.group(0)
        pack_s = re.search(r"(?i)\.S(\d{1,2})\.", part + ".")
        if is_pack_name(part):
            if pack_s:
                season = season or int(pack_s.group(1))
            continue
        s2 = SHOW_SEASON_RE.fullmatch(part)
        if s2:
            show = safe_name(s2.group(1))
            season = season or int(s2.group(2))
            continue
        if show is None:
            show = part
    return show, season, year


def movie_dest(library: Path, movie: Movie, ext: str, use_res: bool) -> Path:
    tag = f" {movie.id_tags}" if movie.id_tags else ""
    folder = safe_name(f"{movie.title} ({movie.year}){tag}" if movie.year else f"{movie.title}{tag}")
    fname = folder
    if use_res and movie.res:
        fname = f"{folder} - {movie.res}"
    return library / folder / f"{fname}{ext.lower()}"


def episode_dest(library: Path, ep: Episode, ext: str) -> Path:
    show = ep.show
    folder = f"{show} ({ep.year})" if ep.year and ep.year not in show else show
    season_dir = f"Season {ep.season:02d}"
    se = f"S{ep.season:02d}E{ep.episode:02d}"
    if ep.episode_end:
        se += f"-E{ep.episode_end:02d}"
    fname = f"{show} - {se}"
    if ep.title:
        fname += f" - {safe_name(ep.title)}"
    return library / folder / season_dir / f"{fname}{ext.lower()}"


def sub_lang_suffix(stem: str) -> str:
    low = stem.lower()
    parts = [p.strip("[]()") for p in re.split(r"[.\s]+", stem.strip()) if p.strip("[]()")]
    tags: list[str] = []
    regions: list[str] = []
    lang = None
    while parts:
        p = parts[-1].lower()
        if p in TAG_TOKS:
            tags.append("forced" if p == "forced" else "hi")
            parts.pop()
            continue
        if p in REGION_TOKS:
            regions.append(p)
            parts.pop()
            continue
        if p in ISO_LANG:
            lang = LANG3.get(p, p)
            parts.pop()
            break
        break
    if not lang:
        if "latin" in low or "español" in low or "espanol" in low or "spanish" in low:
            lang = "spa"
        elif "english" in low or "forced" in low:
            lang = "eng"
    if not lang:
        return ""
    if "forced" in low and "forced" not in tags:
        tags.append("forced")
    if not regions:
        if lang == "dut":
            if "belgi" in low:
                lang = "dut.be"
            elif "nederland" in low:
                lang = "dut.nl"
        elif lang == "spa":
            if "españa" in low or "espana" in low or "spain" in low:
                lang = "spa.es"
            elif "latino" in low or "latin american" in low:
                lang = "spa"
        elif lang == "por":
            if "brasil" in low or "brazil" in low:
                lang = "por.br"
            elif "portugal" in low:
                lang = "por.pt"
        elif lang == "fre":
            if "canada" in low:
                lang = "fre.ca"
            elif "france" in low:
                lang = "fre.fr"
        elif lang == "chi":
            if "繁" in stem or "traditional" in low:
                lang = "chi.hant"
            elif "简" in stem or "simplified" in low:
                lang = "chi.hans"
    if regions:
        lang = lang + "." + ".".join(reversed(regions))
    seen = []
    for t in tags:
        if t not in seen:
            seen.append(t)
    extra = "".join(f".{t}" for t in seen)
    return f".{lang}{extra}"


def sidecar_lang(video_stem: str, side: Path) -> str | None:
    if side.suffix.lower() not in SIDECAR_EXT:
        return None
    if side.stem == video_stem:
        return ""
    if not side.stem.startswith(video_stem):
        return None
    rest = side.stem[len(video_stem) :]
    if not rest.startswith("."):
        return None
    extra = sub_lang_suffix(rest)
    return extra if extra else None


def is_junk_file(path: Path) -> bool:
    name = path.name
    if name.startswith("."):
        return True
    if JUNK_NAME_RE.search(name):
        return True
    if path.suffix.lower() in {".jpg", ".jpeg", ".png", ".gif", ".txt", ".exe", ".url"}:
        return name.lower() not in {"cover.jpg", "poster.jpg", "folder.jpg", "fanart.jpg", "banner.jpg", "backdrop.jpg", "logo.png"}
    return False


def iter_files(root: Path):
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d.lower() not in SKIP_DIRS and not d.startswith(".")]
        for name in filenames:
            yield Path(dirpath) / name


def classify_libraries(media_root: Path) -> dict[str, Path]:
    found = {}
    for child in sorted(media_root.iterdir() if media_root.is_dir() else []):
        if not child.is_dir() or child.name.startswith("."):
            continue
        key = child.name.lower().replace(" ", "")
        if key in {"movies", "films", "peliculas", "películas"}:
            found["movies"] = child
        elif key in {"tvshows", "tv", "series"}:
            found["tv"] = child
        elif key in {"anime"}:
            found["anime"] = child
        elif key in {"music", "musica", "música"}:
            found["music"] = child
        elif key in {"audiobooks", "audiolibros"}:
            found["audiobooks"] = child
    return found


def plan_movies(library: Path) -> list[Plan]:
    videos = [p for p in iter_files(library) if p.suffix.lower() in VIDEO_EXT and not is_junk_file(p)]
    parsed: list[tuple[Path, Movie]] = [(p, parse_movie(p.stem)) for p in videos]
    counts: dict[tuple[str, str | None], set[str | None]] = {}
    for _, m in parsed:
        counts.setdefault((m.title.lower(), m.year), set()).add(m.res)
    plans = []
    for src, movie in parsed:
        if not movie.title:
            continue
        use_res = len(counts[(movie.title.lower(), movie.year)]) > 1
        dst = movie_dest(library, movie, src.suffix, use_res)
        plans.append(Plan(src, dst, "movie"))
        for side in src.parent.glob(src.stem + ".*"):
            extra = sidecar_lang(src.stem, side)
            if extra is None:
                continue
            plans.append(Plan(side, dst.with_name(dst.stem + extra + side.suffix.lower()), "sidecar"))
        subs_dir = src.parent / "Subs"
        if not subs_dir.is_dir():
            subs_dir = src.parent / "subs"
        if subs_dir.is_dir():
            for sub in subs_dir.iterdir():
                if sub.suffix.lower() not in SUB_EXT:
                    continue
                extra = sub_lang_suffix(sub.stem) or ""
                plans.append(Plan(sub, dst.with_name(dst.stem + extra + sub.suffix.lower()), "sub"))
    return plans


def match_sub_to_episode(sub: Path, episodes: list[tuple[Path, Episode]]) -> Episode | None:
    same_dir = [(src, ep) for src, ep in episodes if src.parent == sub.parent]
    parsed = parse_episode(sub.stem, None, None, None)

    def pick(pool: list[tuple[Path, Episode]], season: int, episode: int, show: str | None) -> Episode | None:
        hits = [ep for _, ep in pool if ep.season == season and ep.episode == episode]
        if show:
            named = [ep for ep in hits if ep.show.lower() == show.lower()]
            if named:
                return named[0]
            return None
        return hits[0] if len(hits) == 1 else None

    if parsed:
        found = pick(same_dir, parsed.season, parsed.episode, parsed.show or None)
        if found:
            return found
        return pick(episodes, parsed.season, parsed.episode, parsed.show or None)
    for src, ep in same_dir:
        if sub.stem.startswith(src.stem) or src.stem.startswith(sub.stem[:20]):
            return ep
    return None


def plan_series(library: Path) -> list[Plan]:
    videos = [p for p in iter_files(library) if p.suffix.lower() in VIDEO_EXT and not is_junk_file(p)]
    episodes: list[tuple[Path, Episode]] = []
    movies: list[tuple[Path, Movie]] = []
    for src in videos:
        hint_show, hint_season, hint_year = folder_hints(src, library)
        ep = parse_episode(src.stem, hint_show, hint_season, None)
        if ep:
            # year only from pack folders like "Renegade 1992 Season 1 Complete..."
            ep.year = hint_year
            if ep.year:
                ep.show = re.sub(r"\s+\((?:19|20)\d{2}\)$", "", ep.show).strip()
                ep.show = re.sub(r"\s+\d{4}$", "", ep.show).strip()
            episodes.append((src, ep))
        else:
            movies.append((src, parse_movie(src.stem)))
    plans: list[Plan] = []
    for src, ep in episodes:
        dst = episode_dest(library, ep, src.suffix)
        plans.append(Plan(src, dst, "episode"))
        for side in src.parent.glob(src.stem + ".*"):
            extra = sidecar_lang(src.stem, side)
            if extra is None:
                continue
            plans.append(Plan(side, dst.with_name(dst.stem + extra + side.suffix.lower()), "sidecar"))
    subs = [p for p in iter_files(library) if p.suffix.lower() in SUB_EXT and not is_junk_file(p)]
    planned_src = {p.src for p in plans}
    for sub in subs:
        if sub in planned_src:
            continue
        ep = match_sub_to_episode(sub, episodes)
        if not ep:
            continue
        dst_video = episode_dest(library, ep, ".mkv")
        extra = sub_lang_suffix(sub.stem)
        plans.append(Plan(sub, dst_video.with_name(dst_video.stem + extra + sub.suffix.lower()), "sub"))
    for src, movie in movies:
        if not movie.title:
            continue
        dst = movie_dest(library, movie, src.suffix, False)
        plans.append(Plan(src, dst, "movie"))
        for side in src.parent.glob(src.stem + ".*"):
            extra = sidecar_lang(src.stem, side)
            if extra is None:
                continue
            plans.append(Plan(side, dst.with_name(dst.stem + extra + side.suffix.lower()), "sidecar"))
    return plans


def dedupe_plans(plans: list[Plan]) -> list[Plan]:
    seen_src = set()
    dest_count: dict[Path, list[Plan]] = {}
    out = []
    for p in plans:
        if p.src.resolve() in seen_src:
            continue
        seen_src.add(p.src.resolve())
        dest_count.setdefault(p.dst, []).append(p)
        out.append(p)
    for dst, group in dest_count.items():
        if len(group) < 2:
            continue
        # same dest from same logical file is ok; different sources collide
        srcs = {g.src.resolve() for g in group}
        if len(srcs) == 1:
            continue
        for i, g in enumerate(group):
            if i == 0:
                continue
            g.dst = g.dst.with_name(f"{g.dst.stem}.{i}{g.dst.suffix}")
            g.note = "collision"
    return out


def needs_move(plan: Plan) -> bool:
    try:
        return plan.src.resolve() != plan.dst.resolve()
    except FileNotFoundError:
        return True


def apply_plans(plans: list[Plan], log_path: Path | None) -> tuple[int, int]:
    moved = skipped = 0
    log_fh = log_path.open("a", encoding="utf-8") if log_path else None
    try:
        for plan in plans:
            if not needs_move(plan):
                skipped += 1
                continue
            if not plan.src.exists():
                skipped += 1
                continue
            if plan.dst.exists() and plan.dst.resolve() != plan.src.resolve():
                print(f"SKIP exists: {plan.dst}", file=sys.stderr)
                skipped += 1
                continue
            plan.dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(plan.src), str(plan.dst))
            moved += 1
            if log_fh:
                log_fh.write(json.dumps({"src": str(plan.src), "dst": str(plan.dst), "ts": int(time.time())}) + "\n")
    finally:
        if log_fh:
            log_fh.close()
    return moved, skipped


def prune_empty(root: Path, keep: set[Path]) -> int:
    removed = 0
    for dirpath, dirnames, filenames in os.walk(root, topdown=False):
        p = Path(dirpath)
        if p in keep or p == root:
            continue
        try:
            if not any(p.iterdir()):
                p.rmdir()
                removed += 1
        except OSError:
            pass
    return removed


def undo(log_path: Path, apply: bool) -> int:
    if not log_path.exists():
        print(f"No log: {log_path}", file=sys.stderr)
        return 1
    rows = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    n = 0
    for row in reversed(rows):
        src, dst = Path(row["src"]), Path(row["dst"])
        print(f"{'UNDO' if apply else 'WOULD UNDO'} {dst} -> {src}")
        if apply:
            if not dst.exists():
                continue
            src.parent.mkdir(parents=True, exist_ok=True)
            if src.exists():
                print(f"SKIP exists: {src}", file=sys.stderr)
                continue
            shutil.move(str(dst), str(src))
            n += 1
    return 0 if n or not apply else 0


def with_lock(lock_path: Path):
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fh = lock_path.open("w")
    try:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        fh.close()
        raise
    return fh


def print_report(plans: list[Plan]) -> None:
    moves = [p for p in plans if needs_move(p)]
    ok = [p for p in plans if not needs_move(p)]
    print(f"Already Jellyfin-named: {len(ok)}")
    print(f"To rename: {len(moves)}")
    print()
    for p in moves:
        print(f"{p.kind:8} {p.src}")
        print(f"      -> {p.dst}" + (f"  ({p.note})" if p.note else ""))


def build_plans(media_root: Path, only: str | None = None) -> list[Plan]:
    libs = classify_libraries(media_root)
    if only:
        libs = {k: v for k, v in libs.items() if k == only}
    plans: list[Plan] = []
    for kind, path in libs.items():
        if kind in {"tv", "anime"}:
            plans.extend(plan_series(path))
        elif kind == "movies":
            plans.extend(plan_movies(path))
    return [p for p in dedupe_plans(plans) if p.src.exists()]


def confined(media_root: Path, rel: str) -> Path | None:
    rel = (rel or "").replace("\\", "/").strip("/")
    root = media_root.resolve()
    if not rel:
        return root
    if ".." in Path(rel).parts:
        return None
    target = (root / rel).resolve()
    try:
        target.relative_to(root)
    except ValueError:
        return None
    return target


def _would_for(child: Path, under: list[Plan]) -> str:
    if child.is_file():
        for p in under:
            if needs_move(p):
                return p.dst.name
        return "—"
    names = []
    parent = child.parent
    for p in under:
        if not needs_move(p):
            continue
        try:
            names.append(p.dst.resolve().relative_to(parent).parts[0])
        except ValueError:
            names.append(p.dst.name)
    uniq = list(dict.fromkeys(names))
    if not uniq:
        return "—"
    if len(uniq) == 1:
        return uniq[0] if uniq[0] != child.name else "—"
    return f"{len(uniq)} destinos"


def browse_rows(media_root: Path, here: Path, plans: list[Plan]) -> list[Row]:
    root = media_root.resolve()
    here = here.resolve()
    rows: list[Row] = []
    if not here.is_dir():
        return rows
    try:
        kids = list(here.iterdir())
    except OSError:
        return rows
    kids.sort(key=lambda p: (not p.is_dir(), p.name.lower()))
    for child in kids:
        if child.name.startswith(".") or child.name.lower() in SKIP_DIRS:
            continue
        if child.is_file() and (
            is_junk_file(child) or child.suffix.lower() not in VIDEO_EXT | SIDECAR_EXT
        ):
            continue
        child = child.resolve()
        under = [p for p in plans if p.src.resolve() == child or child in p.src.resolve().parents]
        n_ren = sum(1 for p in under if needs_move(p))
        try:
            rel = child.relative_to(root).as_posix()
        except ValueError:
            continue
        rows.append(
            Row(rel, child.name, child.is_dir(), "rename" if n_ren else "ok", _would_for(child, under), n_ren)
        )
    return rows


def crumb_html(rel: str) -> str:
    parts = [p for p in rel.replace("\\", "/").split("/") if p]
    bits = ['<a href="/">Media</a>']
    acc: list[str] = []
    for part in parts:
        acc.append(part)
        href = "/?p=" + quote("/".join(acc), safe="/")
        bits.append(f'<a href="{href}">{html_lib.escape(part)}</a>')
    return " / ".join(bits)


def table_html(rows: list[Row], need_only: bool) -> str:
    out = [
        '<table><thead><tr><th scope="col"><span class="vh">Select</span></th><th scope="col">Name</th><th scope="col">Status</th><th scope="col">Would become</th></tr></thead><tbody>'
    ]
    shown = 0
    for r in rows:
        if need_only and r.status != "rename":
            continue
        shown += 1
        name = html_lib.escape(r.name)
        label = f'<a href="/?p={quote(r.rel, safe="/")}">{name}</a>' if r.is_dir else name
        if r.status == "ok":
            st = "OK"
        else:
            st = f"Rename ({r.n_rename})" if r.n_rename else "Rename"
        val = html_lib.escape(r.rel, quote=True)
        would = html_lib.escape(r.would)
        need = " data-need" if r.status == "rename" else ""
        out.append(
            f'<tr class="{r.status}"{need}><td><input type="checkbox" name="sel" value="{val}"'
            f'{" disabled" if r.status != "rename" else ""}></td>'
            f"<td>{label}</td><td class=\"st\">{st}</td><td>{would}</td></tr>"
        )
    if not shown:
        out.append('<tr><td colspan="4" class="empty">Nothing in this folder.</td></tr>')
    out.append("</tbody></table>")
    return "".join(out)


def toast_html(kind: str, msg: str) -> str:
    if kind not in {"ok", "err"} or not msg:
        return ""
    role = "alert" if kind == "err" else "status"
    return (
        f'<div class="toast {kind}" role="{role}">'
        f"<p>{html_lib.escape(msg)}</p>"
        f'<button type="button" class="toast-x" aria-label="Dismiss">Dismiss</button>'
        f"</div>"
    )


def self_test() -> int:
    cases_movie = [
        ("01 Westworld - Sci-Fi 1973 Eng Subs 1080p [H264-mp4]", "Westworld", "1973"),
        ("02 Futureworld - Sci-Fi 1976 Eng Subs 1080p [H264-mp4]", "Futureworld", "1976"),
        ("Spider-Man- Brand New Day 2026.1080p.HQ Pre.Multi.AAC 2.0.x264", "Spider-Man - Brand New Day", "2026"),
        ("Bugonia.2025.1080p.WEBRip.x265.10bit.AAC5.1-[YTS.LT]", "Bugonia", "2025"),
        ("The Hidden (1987) [1080p] [BluRay] [5.1] [YTS.MX]", "The Hidden", "1987"),
        ("Swiss.Army.Man.2016.1080p.BluRay.6CH.ShAaNiG", "Swiss Army Man", "2016"),
        ("Spaceballs.1987.2160p.4K.BluRay.x265.10bit.AAC5.1-[YTS.MX]", "Spaceballs", "1987"),
        ("[AnimeSalt] Jujutsu Kaisen 0 (2021) [1080p] [Multi-Audio]", "Jujutsu Kaisen 0", "2021"),
        ("Three Bags Full A Sheep Detective Movie (2026) [1080p] [WEBRip]", "Three Bags Full A Sheep Detective Movie", "2026"),
    ]
    for stem, title, year in cases_movie:
        m = parse_movie(stem)
        assert m.title == title, f"{stem!r} title {m.title!r} != {title!r}"
        assert m.year == year, f"{stem!r} year {m.year!r} != {year!r}"

    ep = parse_episode("From.S04E10.1080p.HEVC.x265-MeGusta[EZTVx.to]", "From", 4, None)
    assert ep and ep.show == "From" and ep.season == 4 and ep.episode == 10 and ep.title == ""

    ep = parse_episode("FROM.S04E06.The.Heart.Is.A.Lonely.Hunter.1080p.HEVC.x265-MeGusta[EZTVx.to]", "From", 4, None)
    assert ep and ep.show == "From" and ep.episode == 6 and ep.title == "The Heart Is A Lonely Hunter", ep

    ep = parse_episode("FROM S04E07 Best Laid Plans 1080p AMZN WEB-DL DDP5 1 H 264-FLUX[EZTVx.to]", "From", 4, None)
    assert ep and ep.episode == 7 and ep.title == "Best Laid Plans", ep

    ep = parse_episode("Fleabag.S01E01.Episode.1.1080p.WEB-DL.AAC2.0.H.264-NTb", None, None, None)
    assert ep and ep.show == "Fleabag" and ep.season == 1 and ep.episode == 1 and ep.title == ""

    ep = parse_episode("Widows.Bay.S01E01.Welcome.to.Widows.Bay.1080p.WEBRip.10Bit.DDP5.1.x265-NeoNoir", None, None, None)
    assert ep and ep.show == "Widows Bay" and ep.title == "Welcome to Widows Bay", ep

    ep = parse_episode("Renegade S01E08 Payback", "Renegade", 1, "1992")
    assert ep and ep.show == "Renegade" and ep.episode == 8 and ep.title == "Payback"

    ep = parse_episode("Twin Peaks - S01E03 - Zen, or the Skill to Catch a Killer", "Twin Peaks", 1, None)
    assert ep and ep.title == "Zen, or the Skill to Catch a Killer"

    ep = parse_episode("1x04. And Fan Boy Is His Name-Lawn Gnomes Chapter I", "Freakazoid", 1, None)
    assert ep and ep.season == 1 and ep.episode == 4 and "Fan Boy" in ep.title

    ep = parse_episode("Yellowjackets - 3x02 - Dislocation.WEB.AMZN.en", "Yellowjackets", 3, None)
    assert ep and ep.season == 3 and ep.episode == 2

    ep = parse_episode("[Erai-raws] Hikaru ga Shinda Natsu - 01 [1080p NF WEBRip HEVC EAC3][MultiSub][EBAEB1E0]", "Hikaru ga Shinda Natsu", None, None)
    assert ep and ep.show == "Hikaru ga Shinda Natsu" and ep.season == 1 and ep.episode == 1

    ep = parse_episode("[Erai-raws] Sakamoto Days Part 2 - 10", "Sakamoto Days", 2, None)
    assert ep and ep.show == "Sakamoto Days" and ep.season == 2 and ep.episode == 10

    ep = parse_episode("[Erai-raws] Beastars Final Season - 07 [1080p NF WEBRip HEVC EAC3][MultiSub][8E019A97]", "Beastars", 3, None)
    assert ep and ep.season == 3 and ep.episode == 7

    ep = parse_episode("Dorohedoro.S01E11.1080p.NF.WEB-DL.DDP2.0.x264-Telly", None, None, None)
    assert ep and ep.show == "Dorohedoro" and ep.season == 1 and ep.episode == 11

    ep = parse_episode("01", "Sexy Commando Gaiden Sugoiyo!! Masaru-san", None, None)
    assert ep and ep.episode == 1 and ep.season == 1
    ep = parse_episode("01-2", "Sexy Commando Gaiden Sugoiyo!! Masaru-san", None, None)
    assert ep and ep.episode == 1 and ep.title == "part 2"

    ep = parse_episode("Reality.Check.Inside.Americas.Next.Top.Model.S01E03.1080p.WEB.h264-GRACE[EZTVx.to]", None, None, None)
    assert ep and ep.show == "Reality Check Inside Americas Next Top Model" and ep.episode == 3

    dest = episode_dest(Path("/media/tv"), Episode("From", 4, 10), ".mkv")
    assert dest.as_posix().endswith("From/Season 04/From - S04E10.mkv"), dest

    dest = movie_dest(Path("/media/movies"), Movie("Westworld", "1973"), ".mp4", False)
    assert dest.as_posix().endswith("Westworld (1973)/Westworld (1973).mp4"), dest

    dest = movie_dest(Path("/media/movies"), Movie("Spaceballs", "1987", "2160p"), ".mkv", True)
    assert dest.as_posix().endswith("Spaceballs (1987)/Spaceballs (1987) - 2160p.mkv"), dest

    assert sub_lang_suffix("Latin American.spa") == ".spa"
    assert sub_lang_suffix("SDH.eng.HI") == ".eng.hi"
    assert sub_lang_suffix("Forced.eng") == ".eng.forced"
    assert sub_lang_suffix("Yellowjackets - 3x02 - Dislocation.WEB.AMZN.en") == ".eng"
    assert sub_lang_suffix("Nederlands (Nederland).dut") == ".dut.nl"
    assert sub_lang_suffix("Español (España).spa") == ".spa.es"
    assert sub_lang_suffix("Français (Canada).fre") == ".fre.ca"
    assert sub_lang_suffix(".chi.hant") == ".chi.hant"
    assert sub_lang_suffix(".fre.ca") == ".fre.ca"
    dest = episode_dest(Path("/media/tv"), Episode("Renegade", 1, 14, title="Samurai", year="1992"), ".mkv")
    assert dest.as_posix().endswith("Renegade (1992)/Season 01/Renegade - S01E14 - Samurai.mkv"), dest
    assert not is_junk_file(Path("From.S04E10.1080p.HEVC.x265-MeGusta[EZTVx.to].mkv"))
    assert is_junk_file(Path("www.YTS.MX.jpg"))

    tmp = Path(tempfile.mkdtemp())
    try:
        bad = tmp / "Movies" / "Bugonia.2025.1080p.WEBRip"
        bad.mkdir(parents=True)
        (bad / "Bugonia.2025.1080p.WEBRip.x265.mkv").write_bytes(b"x")
        good = tmp / "Movies" / "Westworld (1973)"
        good.mkdir()
        (good / "Westworld (1973).mp4").write_bytes(b"x")
        plans = build_plans(tmp)
        by_name = {r.name: r for r in browse_rows(tmp, tmp / "Movies", plans)}
        assert by_name["Westworld (1973)"].status == "ok", by_name["Westworld (1973)"]
        assert by_name["Bugonia.2025.1080p.WEBRip"].status == "rename"
        assert by_name["Bugonia.2025.1080p.WEBRip"].would == "Bugonia (2025)"
        assert confined(tmp, "../etc") is None
        assert confined(tmp, "Movies").resolve() == (tmp / "Movies").resolve()
        logp = tmp / "log.jsonl"
        assert schedule_enabled(logp) is False
        set_schedule(logp, True)
        assert schedule_enabled(logp) is True
    finally:
        shutil.rmtree(tmp)

    assert 'role="status"' in toast_html("ok", "Renamed 1")
    assert 'role="alert"' in toast_html("err", "fail")
    assert toast_html("", "x") == ""
    assert "&lt;" in toast_html("ok", "<x>")

    print("self-test ok")
    return 0


APP_DIR = Path(__file__).resolve().parent


def first_file(*paths: Path) -> Path | None:
    for p in paths:
        if p.is_file():
            return p
    return None


def schedule_file(log_path: Path) -> Path:
    return log_path.with_name("schedule.json")


def schedule_enabled(log_path: Path) -> bool:
    try:
        return json.loads(schedule_file(log_path).read_text(encoding="utf-8")).get("enabled") is True
    except (OSError, json.JSONDecodeError):
        return False


def set_schedule(log_path: Path, enabled: bool) -> None:
    path = schedule_file(log_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"enabled": enabled}), encoding="utf-8")


def gate_html(enabled: bool, interval: int) -> str:
    if not interval:
        return '<p class="muted">Scheduled renaming is off (INTERVAL=0).</p>'
    mins = interval // 60
    if enabled:
        return f'<p class="muted">Scheduled renaming is on · every {mins} min.</p>'
    return f"""<section class="gate">
<h2>Review before enabling scheduled renaming</h2>
<p>Jellyfin Rename will process the folder mapped to <code>/media</code> every {mins} minutes and may change file and folder names.</p>
<p>Check the selected folder, review the preview, and make sure you have a verified backup. Do not assume changes can be undone.</p>
<form method="post" action="/schedule">
<p class="ack"><label><input type="checkbox" name="ack" value="1" required onchange="this.form.querySelector('[type=submit]').disabled=!this.checked"> I have reviewed the directory mapping and proposed changes, have a backup, and understand the risks.</label></p>
<button class="primary" type="submit" disabled>Enable scheduled renaming</button>
</form>
</section>"""


ABOUT_HTML = """
<div class="about">
<p>Keep your media names consistent and your Jellyfin library organized.</p>
<p>Jellyfin Rename watches your media folder and renames movies, TV shows, and anime using Jellyfin naming conventions.</p>
<p>Open the web panel to preview proposed changes before letting the app run every 30 minutes.</p>
<details>
<summary>Getting started, precautions, and disclaimer</summary>
<h2>Getting started</h2>
<ol>
<li>Back up your media and test the app on a small copy of your files.</li>
<li>Map the container’s <code>/media</code> directory to the library folder you want to manage.</li>
<li>Open the web panel and carefully review the proposed changes.</li>
<li>Check your settings before leaving scheduled processing running.</li>
</ol>
<h2>Important precautions</h2>
<ul>
<li>This app changes file and folder names in the mapped directory. Incorrect settings or naming matches may cause unwanted changes, identification errors, or playback issues.</li>
<li>Only map folders you intend the app to manage. Avoid granting access to unrelated files or your entire storage volume.</li>
<li>Review proposed changes carefully, especially for ambiguous titles, anime, specials, and custom folder structures.</li>
<li>Keep a separate, verified backup. A preview is not a backup, and you should not assume changes can be undone.</li>
<li>Avoid running other tools that move or rename the same files at the same time.</li>
</ul>
<h2>Security</h2>
<p>Do not expose port 8098 to the internet. Keep the web panel accessible only through your trusted local network or a securely configured private connection.</p>
<h2>Disclaimer</h2>
<p>This software is provided “as is,” without warranties of any kind, to the extent permitted by applicable law. You are responsible for your configuration, directory mappings, permissions, and backups.</p>
<p>File operations carry risks. Incorrect use, misconfiguration, software defects, or interactions with other tools may result in unwanted renaming, overwritten or deleted files, data loss, or an unavailable media library. To the extent permitted by applicable law, the authors and contributors are not liable for losses or damage arising from use of this software.</p>
<p>Jellyfin Rename is an independent project and is not affiliated with or endorsed by Jellyfin or CasaOS.</p>
</details>
</div>
"""


PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Jellyfin Rename</title>
<link rel="icon" href="/icon.png">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;500&family=Rubik:wght@400;500;600;700&display=swap" rel="stylesheet">
<style>
:root {
  --bg: #161a20;
  --surface: #21346e;
  --text: #F5F5F5;
  --muted: #CCCCCC;
  --line: #2A4080;
  --accent: #CCCCCC;
  --primary: #21346e;
  --neutral: #1A1A1A;
  --soft: #F5F5F5;
  --ok: #5aa36a;
  --warn: #c9a227;
  --danger: #c45c5c;
  --radius: 8px;
  --space: 0.5rem;
  --pad: 1.5rem;
  --max: 1280px;
  --measure: 72ch;
  --sans: Rubik, ui-sans-serif, system-ui, sans-serif;
  --mono: "JetBrains Mono", ui-monospace, monospace;
}
* { box-sizing: border-box; }
html, body { min-height: 100dvh; }
body {
  margin: 0;
  background: var(--bg);
  color: var(--text);
  font: 400 1rem/1.6 var(--sans);
  overscroll-behavior: contain;
}
main { max-width: var(--max); margin: 0 auto; padding: var(--pad); }
.brand { max-width: var(--measure); margin: 0 0 1rem; }
.brand h1 { margin: 0; }
.brand img { height: clamp(64px, 18vw, 96px); width: auto; max-width: 100%; display: block; }
.about, .gate { max-width: var(--measure); margin: 0 0 1rem; }
.about h2, .gate h2 { font-size: 1.5rem; font-weight: 700; line-height: 1.2; margin: 1rem 0 0.5rem; }
.gate h2 { margin-top: 0; }
.about ol, .about ul { margin: 0 0 1rem; padding-left: 1.25rem; }
.about summary {
  cursor: pointer;
  color: var(--muted);
  font-size: 0.875rem;
  font-weight: 500;
  letter-spacing: 0.02em;
}
.gate {
  background: var(--surface);
  border: 1px solid var(--warn);
  border-radius: var(--radius);
  box-shadow: 0 2px 12px rgba(0,0,0,0.06);
  padding: 1rem;
}
.gate p { margin: 0 0 1rem; }
.ack { margin: 0 0 1rem; }
.ack label { display: flex; gap: var(--space); align-items: flex-start; }
.ack input { margin-top: 0.3rem; flex: 0 0 auto; }
.row { display: flex; flex-wrap: wrap; gap: var(--space); margin: 0 0 1rem; align-items: center; }
.status { color: var(--muted); font-size: 0.875rem; font-weight: 500; }
.dot { width: 8px; height: 8px; border-radius: 4px; background: var(--ok); display: inline-block; }
.dot.off { background: var(--warn); }
button {
  background: transparent;
  color: var(--text);
  border: 1.5px solid var(--accent);
  border-radius: var(--radius);
  padding: 12px 1rem;
  font: 600 1rem/1.2 var(--sans);
  cursor: pointer;
  touch-action: manipulation;
  transition: transform 200ms ease, box-shadow 200ms ease, background-color 200ms ease, border-color 200ms ease;
}
button:hover:not(:disabled) {
  transform: scale(1.03);
  box-shadow: 0 2px 12px rgba(0,0,0,0.18);
  background: rgba(42, 64, 128, 0.45);
}
button:active:not(:disabled) { transform: scale(0.95) translateY(1px); }
button.primary {
  background: var(--accent);
  color: var(--neutral);
  border-color: var(--accent);
}
button.primary:hover:not(:disabled) { background: #b8b8b8; border-color: #b8b8b8; }
button:disabled { opacity: .5; cursor: not-allowed; }
button:focus-visible, input:focus-visible, a:focus-visible, summary:focus-visible {
  outline: 2px solid var(--accent);
  outline-offset: 2px;
}
.muted { color: var(--muted); margin: 0 0 1rem; }
.crumb {
  margin: 0 0 1rem;
  font: 0.875rem/1.6 var(--mono);
  color: var(--muted);
  overflow-wrap: anywhere;
}
.crumb a { color: var(--text); text-decoration: none; border-bottom: 1px solid var(--line); }
.filters { font-size: 0.875rem; font-weight: 500; letter-spacing: 0.02em; }
.filters a { color: var(--muted); text-decoration: none; margin-right: 12px; padding-bottom: 2px; border-bottom: 2px solid transparent; }
.filters a.on { color: var(--text); border-bottom-color: var(--accent); }
.board {
  overflow: auto;
  -webkit-overflow-scrolling: touch;
  overscroll-behavior: contain;
  border: 1px solid var(--line);
  border-radius: var(--radius);
  background: var(--surface);
  box-shadow: 0 2px 12px rgba(0,0,0,0.06);
  margin: 0 0 1rem;
}
table { width: 100%; min-width: 36rem; border-collapse: collapse; font: 0.875rem/1.5 var(--mono); }
th {
  text-align: left;
  font: 500 0.875rem/1.5 var(--sans);
  letter-spacing: 0.02em;
  color: var(--muted);
  padding: var(--space) 10px;
  border-bottom: 1px solid var(--line);
}
td { padding: var(--space) 10px; border-bottom: 1px solid var(--line); vertical-align: top; overflow-wrap: anywhere; }
tr:last-child td { border-bottom: 0; }
td a { color: var(--text); }
.ok .st { color: var(--ok); }
.rename .st { color: var(--warn); }
.empty { color: var(--muted); font-family: var(--sans); }
.vh { position: absolute; width: 1px; height: 1px; overflow: hidden; clip: rect(0 0 0 0); }
input[type=checkbox] { width: 1.25rem; height: 1.25rem; accent-color: var(--accent); }
pre {
  background: var(--surface);
  border: 1px solid var(--line);
  border-radius: var(--radius);
  padding: 1rem;
  overflow: auto;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  font: 0.875rem/1.5 var(--mono);
  margin: 0;
  max-height: 30vh;
}
.toast {
  position: fixed;
  z-index: 500;
  left: 1.5rem;
  right: 1.5rem;
  bottom: max(1.5rem, env(safe-area-inset-bottom));
  max-width: 36rem;
  margin: 0 auto;
  display: flex;
  gap: 0.5rem;
  align-items: center;
  justify-content: space-between;
  padding: 12px 1rem;
  border-radius: var(--radius);
  background: var(--surface);
  color: var(--text);
  border: 1px solid var(--ok);
  box-shadow: 0 2px 12px rgba(0,0,0,0.18);
  font: 500 0.875rem/1.5 var(--sans);
  animation: toast-in 200ms ease-out;
  transition: opacity 200ms ease, transform 200ms ease;
}
.toast p { margin: 0; min-width: 0; overflow-wrap: anywhere; }
.toast.err { border-color: var(--danger); }
.toast-x {
  flex: 0 0 auto;
  padding: 8px 12px;
  min-height: 44px;
  width: auto;
  font-size: 0.875rem;
  font-weight: 500;
}
.toast.out { opacity: 0; transform: translateY(8px); }
@keyframes toast-in {
  from { opacity: 0; transform: translateY(16px); }
  to { opacity: 1; transform: translateY(0); }
}
@media (max-width: 767px) {
  .row { flex-direction: column; align-items: stretch; }
  button { width: 100%; min-height: 44px; }
  input[type=checkbox] { width: 1.5rem; height: 1.5rem; }
  .ack label { min-height: 44px; }
}
@media (prefers-reduced-motion: reduce) {
  button { transition: none; }
  button:hover:not(:disabled), button:active:not(:disabled) { transform: none; }
  .toast { animation: none; }
  .toast.out { transform: none; }
}
</style>
</head>
<body>
<main>
<div class="brand">
<h1><img src="/logo.png" alt="Jellyfin Rename"></h1>
</div>
__ABOUT__
<p class="status"><span class="dot __DOT__"></span> __STATUS__</p>
<p class="muted">__MEDIA__</p>
__GATE__
<p class="crumb">__CRUMB__</p>
<form method="post" action="/apply">
<input type="hidden" name="p" value="__P__">
<div class="row">
<button type="submit" formaction="/scan">Refresh</button>
<button type="button" onclick="document.querySelectorAll('tr[data-need] input').forEach(function(c){c.checked=true})">Select pending</button>
<button class="primary" type="submit">Rename selected</button>
</div>
<p class="filters muted">
<a class="__ALL__" href="__HREF_ALL__">All</a>
<a class="__NEED__" href="__HREF_NEED__">Pending</a>
</p>
<div class="board">__TABLE__</div>
</form>
<pre>__REPORT__</pre>
</main>
__TOAST__
<script>
(function(){
  var t=document.querySelector('.toast');
  if(!t) return;
  function hide(){
    t.classList.add('out');
    t.addEventListener('transitionend', function(){ t.remove(); }, {once:true});
    setTimeout(function(){ t.remove(); }, 400);
  }
  t.querySelector('.toast-x').addEventListener('click', hide);
  setTimeout(hide, 5000);
})();
</script>
</body>
</html>
"""


def serve(media_root: Path, log_path: Path, interval: int, port: int) -> int:
    # ponytail: no auth, LAN only. Add a token if you publish the port.
    state: dict = {"busy": False, "report": "Escaneando…", "last": "iniciando", "plans": [], "toast": None}
    interval = max(0, interval)
    lock = threading.Lock()

    def refresh() -> None:
        state["plans"] = build_plans(media_root)
        moves = [p for p in state["plans"] if needs_move(p)]
        state["report"] = f"{len(state['plans']) - len(moves)} OK · {len(moves)} to rename"
        state["last"] = time.strftime("%Y-%m-%d %H:%M:%S")

    def apply_moves(moves: list[Plan]) -> None:
        if not moves:
            state["toast"] = ("err", "Nothing selected to rename.")
            return
        log_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            fh = with_lock(log_path.with_suffix(".lock"))
        except BlockingIOError:
            state["toast"] = ("err", "Another rename is already running. Try again in a moment.")
            return
        try:
            moved, skipped = apply_plans(moves, log_path)
            pruned = 0
            for path in classify_libraries(media_root).values():
                pruned += prune_empty(path, {path})
            msg = f"Renamed {moved}, skipped {skipped}, removed {pruned} empty folders."
            state["toast"] = ("ok", msg) if moved else ("err", msg)
        finally:
            fh.close()
        refresh()

    def job(apply: bool, rels: list[str] | None = None) -> None:
        if not lock.acquire(blocking=False):
            if apply:
                state["toast"] = ("err", "Another rename is already running. Try again in a moment.")
            return
        state["busy"] = True
        state["last"] = "renaming" if apply else "scanning"
        try:
            if apply:
                if rels is None:
                    apply_moves([p for p in build_plans(media_root) if needs_move(p)])
                else:
                    roots = [p for r in rels if (p := confined(media_root, r)) is not None]
                    moves = [
                        p
                        for p in build_plans(media_root)
                        if needs_move(p)
                        and any(p.src == root or root in p.src.parents for root in roots)
                    ]
                    apply_moves(moves)
            else:
                refresh()
        except Exception as exc:
            state["report"] = str(exc)
            state["last"] = "error"
            if apply:
                state["toast"] = ("err", str(exc))
        finally:
            state["busy"] = False
            lock.release()

    def loop() -> None:
        job(False)
        while interval:
            while interval and not schedule_enabled(log_path):
                time.sleep(2)
            if not interval:
                return
            time.sleep(interval)
            if schedule_enabled(log_path):
                job(True)

    threading.Thread(target=loop, daemon=True).start()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt: str, *args) -> None:
            return

        def _page(self, rel: str, need_only: bool, code: int = 200) -> None:
            here = confined(media_root, rel) or media_root
            rows = browse_rows(media_root, here, state["plans"])
            q_all = "/?p=" + quote(rel, safe="/") if rel else "/"
            q_need = (q_all + ("&" if rel else "?")) + "need=1"
            if q_all == "/":
                q_need = "/?need=1"
            toast = state.pop("toast", None) or ("", "")
            body = (
                PAGE.replace("__ABOUT__", ABOUT_HTML)
                .replace("__GATE__", gate_html(schedule_enabled(log_path), interval))
                .replace("__DOT__", "off" if state["busy"] else "")
                .replace("__STATUS__", "working…" if state["busy"] else f"last: {state['last']}")
                .replace("__MEDIA__", html_lib.escape(str(media_root)))
                .replace("__REPORT__", html_lib.escape(state["report"]))
                .replace("__TOAST__", toast_html(toast[0], toast[1]))
                .replace("__CRUMB__", crumb_html(rel))
                .replace("__TABLE__", table_html(rows, need_only))
                .replace("__P__", html_lib.escape(rel, quote=True))
                .replace("__HREF_ALL__", q_all)
                .replace("__HREF_NEED__", q_need)
                .replace("__ALL__", "on" if not need_only else "")
                .replace("__NEED__", "on" if need_only else "")
            ).encode()
            self.send_response(code)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _qs(self, path: str) -> tuple[str, bool]:
            qs = parse_qs(urlparse(path).query)
            rel = (qs.get("p") or [""])[0]
            return rel, (qs.get("need") or [""])[0] == "1"

        def _file(self, path: Path, ctype: str, body: bool = True) -> None:
            data = path.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "public, max-age=86400")
            self.end_headers()
            if body:
                self.wfile.write(data)

        def _static(self, body: bool) -> bool:
            route = urlparse(self.path).path
            logo = first_file(APP_DIR / "logo.png", APP_DIR / "app-logo.png")
            store_png = first_file(APP_DIR / "store-icon.png", APP_DIR / "icon.png")
            store_svg = first_file(APP_DIR / "store-icon.svg", APP_DIR / "icon.svg")
            if route == "/logo.png" and logo:
                self._file(logo, "image/png", body)
                return True
            if route in {"/icon.png", "/favicon.ico"} and store_png:
                self._file(store_png, "image/png", body)
                return True
            if route == "/icon.svg" and store_svg:
                self._file(store_svg, "image/svg+xml", body)
                return True
            if route == "/favicon.ico":
                self.send_response(204)
                self.end_headers()
                return True
            return False

        def do_HEAD(self) -> None:
            if not self._static(False):
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()

        def do_GET(self) -> None:
            if self._static(True):
                return
            rel, need = self._qs(self.path)
            self._page(rel, need)

        def do_POST(self) -> None:
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length) if length else b""
            form = parse_qs(raw.decode("utf-8", "replace"))
            rel = (form.get("p") or [""])[0]
            action = urlparse(self.path).path.rstrip("/")
            if action == "/schedule":
                if (form.get("ack") or [""])[0] == "1":
                    set_schedule(log_path, True)
            elif action == "/apply":
                job(True, form.get("sel") or [])
            else:
                job(False)
            loc = "/?p=" + quote(rel, safe="/") if rel else "/"
            self.send_response(303)
            self.send_header("Location", loc)
            self.end_headers()

    print(f"Panel en http://0.0.0.0:{port}  media={media_root}", flush=True)
    ThreadingHTTPServer(("0.0.0.0", port), Handler).serve_forever()
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Review/rename a Jellyfin Media folder.")
    parser.add_argument("media_root", nargs="?", default=os.environ.get("MEDIA_ROOT", "/DATA/Media"), help="Media root (Movies, TV Shows, Anime, Music)")
    parser.add_argument("--apply", action="store_true", help="Actually rename. Default is dry-run.")
    parser.add_argument("--only", choices=["movies", "tv", "anime", "music", "audiobooks"], help="Limit to one library")
    parser.add_argument("--log", default=os.environ.get("LOG_PATH", ""), help="JSONL move log")
    parser.add_argument("--undo", action="store_true", help="Reverse moves from --log")
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--json", action="store_true", help="Print plan as JSON")
    parser.add_argument("--serve", action="store_true", help="Web panel + auto-rename loop (CasaOS app)")
    parser.add_argument("--port", type=int, default=int(os.environ.get("PORT", "8080")))
    parser.add_argument("--interval", type=int, default=int(os.environ.get("INTERVAL", "1800")), help="Seconds between auto-apply. 0 disables.")
    args = parser.parse_args(argv)

    if args.self_test:
        return self_test()

    media_root = Path(args.media_root).expanduser().resolve()
    default_log = Path("/DATA/AppData/jellyfin-rename/log.jsonl")
    config_log = Path("/config/log.jsonl")
    if args.log:
        log_path = Path(args.log).expanduser()
    elif config_log.parent.exists():
        log_path = config_log
    elif default_log.parent.exists():
        log_path = default_log
    else:
        log_path = Path("jellyfin-rename.log")

    if args.serve:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        return serve(media_root, log_path, args.interval, args.port)

    if args.undo:
        return undo(log_path, args.apply)

    if not media_root.is_dir():
        print(f"Not a directory: {media_root}", file=sys.stderr)
        return 2

    try:
        lock = with_lock(log_path.with_suffix(".lock") if args.apply else Path("/tmp/jellyfin-rename.dry.lock"))
    except BlockingIOError:
        print("Another run is in progress.", file=sys.stderr)
        return 0
    try:
        libs = classify_libraries(media_root)
        if not libs:
            print(f"No Movies/TV Shows/Anime/Music folders under {media_root}", file=sys.stderr)
            return 2
        for kind in libs:
            if kind not in {"tv", "anime", "movies"}:
                print(f"skip {kind}: not enough tags to rename safely")

        plans = build_plans(media_root, args.only)
        moves = [p for p in plans if needs_move(p)]

        if args.json:
            print(json.dumps([{"kind": p.kind, "src": str(p.src), "dst": str(p.dst)} for p in moves], indent=2, ensure_ascii=False))
        elif not args.apply or moves:
            print_report(plans)

        if not args.apply:
            print("\nDry-run. Re-run with --apply to rename.")
            return 0

        if not moves:
            return 0

        log_path.parent.mkdir(parents=True, exist_ok=True)
        moved, skipped = apply_plans(moves, log_path)
        pruned = 0
        for path in libs.values():
            pruned += prune_empty(path, {path})
        print(f"\nRenamed {moved}, skipped {skipped}, removed {pruned} empty folders.")
        print(f"Log: {log_path}")
        return 0
    finally:
        lock.close()


if __name__ == "__main__":
    sys.exit(main())
