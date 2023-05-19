import json
import random
import os
import re
import gc
import time

import numpy as np
from . import vtt, srt, sphinx, fcpxml
from pathlib import Path
from typing import Optional, List, Union, Iterator

from moviepy.editor import VideoFileClip, concatenate_videoclips, VideoClip, ColorClip

BATCH_SIZE = 20
SUB_EXTS = [".json", ".vtt", ".srt", ".transcript"]


def find_transcript(videoname: str, prefer: Optional[str] = None) -> Optional[str]:
    """
    Takes a video file path and finds a matching subtitle file.

    :param videoname str: Video file path
    :param prefer Optiona[str]: Transcript file type preference. Can be vtt, srt, or json
    :rtype Optional[str]: Subtitle file path
    """

    subfile = None

    _sub_exts = SUB_EXTS

    if prefer is not None:
        _sub_exts = [prefer] + SUB_EXTS

    all_files = [str(f) for f in Path(videoname).parent.iterdir() if f.is_file()]

    for ext in _sub_exts:
        pattern = (
            re.escape(os.path.splitext(videoname)[0])
            + r"\..*?\.?"
            + ext.replace(".", "")
        )

        print("Looking for", pattern)

        for f in all_files:
            if re.search(pattern, f):
                subfile = f
                break
        if subfile:
            break

    return subfile


def parse_transcript(
    videoname: str, prefer: Optional[str] = None
) -> Optional[List[dict]]:
    """
    Helper function to parse a subtitle file and returns timestamps.

    :param videoname str: Video file path
    :param prefer Optiona[str]: Transcript file type preference. Can be vtt, srt, or json
    :rtype Optional[List[dict]]: List of timestamps or None
    """

    subfile = find_transcript(videoname, prefer)

    if subfile is None:
        print("No subtitle file found for", videoname)
        return None

    transcript = None

    with open(subfile, "r", encoding="utf8") as infile:
        if subfile.endswith(".srt"):
            transcript = srt.parse(infile)
        elif subfile.endswith(".vtt"):
            transcript = vtt.parse(infile)
        elif subfile.endswith(".json"):
            transcript = json.load(infile)
        elif subfile.endswith(".transcript"):
            transcript = sphinx.parse(infile)

    return transcript


def get_ngrams(files: Union[str, list], n: int = 1) -> Iterator[tuple]:
    """
    Get n-grams from video file(s)
    Sourced from: https://gist.github.com/dannguyen/93c2c43f4e65328b85af

    :param files Union[str, list]: Path or paths to video files
    :param n int: N-gram size
    :rtype Iterator[tuple]: List of (n-gram, occurrences)
    """

    if not isinstance(files, list):
        files = [files]

    words = []

    for file in files:
        transcript = parse_transcript(file)
        if transcript is None:
            continue
        for line in transcript:
            if "words" in line:
                words += [w["word"] for w in line["words"]]
            else:
                words += re.split(r"[.?!,:\"]+\s*|\s+", line["content"])

    ngrams = zip(*[words[i:] for i in range(n)])
    return ngrams


def remove_overlaps(segments: List[dict]) -> List[dict]:
    """
    Removes any time overlaps from clips

    :param segments List[dict]: Segments to clean up
    :rtype List[dict]: Cleaned output
    """

    if len(segments) == 0:
        return []

    segments = sorted(segments, key=lambda k: k["start"])
    out = [segments[0]]
    for segment in segments[1:]:
        prev_end = out[-1]["end"]
        start = segment["start"]
        end = segment["end"]
        if prev_end >= start:
            out[-1]["end"] = end
        else:
            out.append(segment)

    return out


def pad_and_sync(
    segments: List[dict], padding: float = 0, resync: float = 0
) -> List[dict]:
    """
    Adds padding and resyncs

    :param segments List[dict]: Segments
    :param padding float: Time in seconds to pad each clip
    :param resync float: Time in seconds to shift subtitle timestamps
    :rtype List[dict]: Padded and cleaned output
    """

    if len(segments) == 0:
        return []

    for s in segments:
        if padding != 0:
            s["start"] -= padding
            s["end"] += padding
        if resync != 0:
            s["start"] += resync
            s["end"] += resync

        if s["start"] < 0:
            s["start"] = 0
        if s["end"] < 0:
            s["end"] = 0

    out = [segments[0]]
    for segment in segments[1:]:
        prev_file = out[-1]["file"]
        current_file = segment["file"]
        if current_file != prev_file:
            out.append(segment)
            continue
        prev_end = out[-1]["end"]
        start = segment["start"]
        end = segment["end"]
        if prev_end >= start:
            out[-1]["end"] = end
        else:
            out.append(segment)

    return out

def content_aware_segments(segments: List[dict], transcript: List[dict], file: str, idx: int) -> List[dict]:

    # Check if transcript[idx - 1]["start"] exist in any of the segments
    if not any([s["start"] == transcript[idx - 1]["start"] for s in segments]):
        insert_idx = idx
        if idx == 0:
            insert_idx = 0
        else:
            insert_idx = idx - 1
        
        segments.insert(
            insert_idx,
            {
                "file": file,
                "start": transcript[idx]["start"],
                "end": transcript[idx]["end"],
                "content": transcript[idx]["content"],
                "content_aware": True,
                "position": "before",
            }
        )

    if not any([s["start"] == transcript[idx + 1]["start"] for s in segments]):
        segments.insert(
            idx + 1,
            {
                "file": file,
                "start": transcript[idx + 1]["start"],
                "end": transcript[idx + 1]["end"],
                "content": transcript[idx + 1]["content"],
                "content_aware": True,
                "position": "after",
            }
        )
    return segments

def search(
    files: Union[str, list],
    query: Union[str, list],
    search_type: str = "sentence",
    prefer: Optional[str] = None,
    word_file: Optional[str] = None,
    context_aware: bool = False
) -> List[dict]:
    """
    Searches for a query in a video file or files and returns a list of timestamps in the format [{file, start, end, content}]

    :param files Union[str, list]: List of files or file
    :param query str: Query as a regular expression, or a list of queries
    :param search_type str: Return timestamps for "sentence" or "fragment"
    :param prefer str: Transcript file type preference. Can be vtt, srt, or json
    :param word_file str: Path to a wordlist. Can be csv or txt
    :param context_aware bool: Include context information
    :rtype List[dict]: A list of timestamps that match the query
    """
    if not isinstance(files, list):
        files = [files]

    if not isinstance(query, list):
        if not isinstance(query, str):
            query: List[str] = list()
        else:
            query = [query]


    all_segments = []
    word_content = []
    if word_file:
        word_content = open(word_file, "r", encoding="utf8").read().splitlines()
        
    for file in files:
        segments = []
        transcript = parse_transcript(file, prefer=prefer)
        if transcript is None:
            continue

        if search_type == "sentence":
            for idx, line in enumerate(transcript):
                for _query in query:
                    if re.search(_query.lower().strip(), line["content"]):
                        segments.append(
                            {
                                "file": file,
                                "start": line["start"],
                                "end": line["end"],
                                "content": line["content"],
                            }
                        )
                        if context_aware:
                            segments = content_aware_segments(
                                segments, transcript, file, idx
                            )

                for _query in word_content:
                    if re.search(_query.lower().strip(), line["content"]):
                        segments.append(
                            {
                                "file": file,
                                "start": line["start"],
                                "end": line["end"],
                                "content": line["content"],
                            }
                        )
                        if context_aware:
                            segments = content_aware_segments(
                                segments, transcript, file, idx
                            )

        elif search_type == "fragment":
            if "words" not in transcript[0]:
                print("Could not find word-level timestamps for", file)
                continue

            words = []
            for line in transcript:
                words += line["words"]

            for _query in query:
                queries = _query.split(" ")
                queries = [q.strip() for q in queries if q.strip() != ""]
                fragments = zip(*[words[i:] for i in range(len(queries))])
                for fragment in fragments:
                    found = all(
                        re.search(q, w["word"]) for q, w in zip(queries, fragment)
                    )
                    if found:
                        phrase = " ".join([w["word"] for w in fragment])
                        segments.append(
                            {
                                "file": file,
                                "start": fragment[0]["start"],
                                "end": fragment[-1]["end"],
                                "content": phrase,
                            }
                        )

        elif search_type == "mash":
            if "words" not in transcript[0]:
                print("Could not find word-level timestamps for", file)
                continue

            words = []
            for line in transcript:
                words += line["words"]

            for _query in query:
                queries = _query.split(" ")

                for q in queries:
                    matches = [w for w in words if w["word"].lower() == q.lower()]
                    if len(matches) == 0:
                        print("Could not find", q, "in transcript")
                        return []
                    random.shuffle(matches)
                    word = matches[0]
                    segments.append(
                        {
                            "file": file,
                            "start": word["start"],
                            "end": word["end"],
                            "content": word["word"],
                        }
                    )

        segments = sorted(segments, key=lambda k: k["start"])

        all_segments += segments

    return all_segments

def make_frame(t):
    """
          A function t-> frame at time t where frame is a w*h*3 RGB array.
    """
    # create a black image
    return np.zeros((640, 480, 3), dtype=np.uint8)

def create_supercut(composition: List[dict], outputfile: str, pause: float = 0):
    """
    Concatenate video clips together.

    :param composition List[dict]: List of timestamps in the format [{start, end, file}]
    :param outputfile str: Path to save the video to
    :param pause float: Time in seconds to pause between clips
    """
    print("[+] Creating clips.")

    all_filenames = set([c["file"] for c in composition])
    videofileclips = dict([(f, VideoFileClip(f)) for f in all_filenames])

    # Create an clip that has same size as the video
    empty_clip = ColorClip(videofileclips[composition[0]["file"]].size, color=(0,0,0), duration=pause)

    cut_clips = []

    for c in composition:
        if c["start"] < 0:
            c["start"] = 0
        if c["end"] > videofileclips[c["file"]].duration:
            c["end"] = videofileclips[c["file"]].duration
        cut_clips.append(videofileclips[c["file"]].subclip(c["start"], c["end"]))

        if "content_aware" in c and c["position"] == "before":
            continue
        elif composition.index(c) == len(composition) - 1:
            continue # skip pause at the end
        cut_clips.append(empty_clip)


    print("[+] Concatenating clips.")
    final_clip = concatenate_videoclips(cut_clips, method="compose")

    print("[+] Writing ouput file.")
    final_clip.write_videofile(
        outputfile,
        codec="libx264",
        temp_audiofile=f"{outputfile}_temp-audio{time.time()}.m4a",
        remove_temp=True,
        audio_codec="aac",
    )


def create_supercut_in_batches(composition: List[dict], outputfile: str, pause):
    """
    Concatenate video clips together in groups of size BATCH_SIZE.

    :param composition List[dict]: List of timestamps in the format [{start, end, file}]
    :param outputfile str: Path to save the video to
    """
    total_clips = len(composition)
    start_index = 0
    end_index = BATCH_SIZE
    batch_comp = []
    while start_index < total_clips:
        filename = outputfile + ".tmp" + str(start_index) + ".mp4"
        try:
            create_supercut(composition[start_index:end_index], filename, pause)
            batch_comp.append(filename)
            gc.collect()
            start_index += BATCH_SIZE
            end_index += BATCH_SIZE
        except Exception as e:
            start_index += BATCH_SIZE
            end_index += BATCH_SIZE
            next

    clips = [VideoFileClip(filename) for filename in batch_comp]
    video = concatenate_videoclips(clips, method="compose")
    video.write_videofile(
        outputfile,
        codec="libx264",
        temp_audiofile=f"{outputfile}_temp-audio{time.time()}.m4a",
        remove_temp=True,
        audio_codec="aac",
    )

    # remove partial video files
    for filename in batch_comp:
        os.remove(filename)

    cleanup_log_files(outputfile)


def export_individual_clips(composition: List[dict], outputfile: str, pause: float = 0.0):
    """
    Exports videogrep composition to individual clips.

    :param composition List[dict]: List of timestamps in the format [{start, end, file}]
    :param outputfile str: Path to save the videos to
    :param pause float: Time to pause between clips
    """

    all_filenames = set([c["file"] for c in composition])
    videofileclips = dict([(f, VideoFileClip(f)) for f in all_filenames])
    cut_clips = []
    empty_clip = ColorClip(videofileclips[composition[0]["file"]].size, color=(0,0,0), duration=pause)

    for c in composition:
        if c["start"] < 0:
            c["start"] = 0
        if c["end"] > videofileclips[c["file"]].duration:
            c["end"] = videofileclips[c["file"]].duration
        cut_clips.append(videofileclips[c["file"]].subclip(c["start"], c["end"]))

    cut_clips_with_pause = []

    for i, clip in enumerate(cut_clips):
        if i > 0:
            cut_clips_with_pause.append(empty_clip)
        cut_clips_with_pause.append(clip)

    basename, ext = os.path.splitext(outputfile)
    print("[+] Writing ouput files.")
    for i, clip in enumerate(cut_clips_with_pause):
        clipfilename = basename + "_" + str(i).zfill(5) + ext
        clip.write_videofile(
            clipfilename,
            codec="libx264",
            temp_audiofile="{clipfilename}_temp-audio.m4a",
            remove_temp=True,
            audio_codec="aac",
        )


def export_m3u(composition: List[dict], outputfile: str, pause: float = 0.0):
    """
    Exports supercut as an m3u file that can be played in VLC

    :param composition List[dict]: List of timestamps in the format [{start, end, file}]
    :param outputfile str: Path to save the playlist to
    """

    lines = []
    lines.append("#EXTM3U")

    for c in composition:
        lines.append(f"#EXTINF:")
        lines.append(f"#EXTVLCOPT:start-time={c['start']}")
        lines.append(f"#EXTVLCOPT:stop-time={c['end']}")
        lines.append(c["file"])

    with open(outputfile, "w") as outfile:
        outfile.write("\n".join(lines))


def export_mpv_edl(composition: List[dict], outputfile: str):
    """
    Exports supercut as an edl file that can be played in mpv. Good for previewing!

    :param composition List[dict]: List of timestamps in the format [{start, end, file}]
    :param outputfile str: Path to save the playlist to
    """
    lines = []
    lines.append("# mpv EDL v0")
    for c in composition:
        lines.append(f"{os.path.abspath(c['file'])},{c['start']},{c['end']-c['start']}")

    with open(outputfile, "w") as outfile:
        outfile.write("\n".join(lines))


def export_xml(composition: List[dict], outputfile: str):
    """
    Exports supercut as a Final Cut Pro xml file. This can be imported to Premiere or Resolve.

    :param composition List[dict]: List of timestamps in the format [{start, end, file}]
    :param outputfile str: Path to save the xml file to
    """
    fcpxml.compose(composition, outputfile)


def cleanup_log_files(outputfile: str):
    """Search for and remove temp log files found in the output directory."""
    d = os.path.dirname(os.path.abspath(outputfile))
    logfiles = [f for f in os.listdir(d) if f.endswith("ogg.log")]
    for f in logfiles:
        os.remove(f)

def make_frame(t):
    # Return black frame
    return np.zeros_like(t)

def videogrep(
    files: Union[List[str], str],
    query: Union[List[str], str],
    search_type: str = "sentence",
    output: str = "supercut.mp4",
    resync: float = 0,
    padding: float = 0,
    maxclips: int = 0,
    export_clips: bool = False,
    random_order: bool = False,
    demo: bool = False,
    pause: float = 0,
    word_file: str = None,
    context_aware: bool = True,
    transcript_type: str = None,
):
    """
    Creates a supercut of videos based on a search query

    :param files List[str]: Video file to search through
    :param query str: A query, as a regular expression
    :param search_type str: Either 'sentence' or 'fragment'
    :param output str: Filename to save to
    :param resync float: Time in seconds to shift subtitle timestamps
    :param padding float: Time in seconds to pad each clip
    :param maxclips int: Maximum clips to use (0 is unlimited)
    :param export_clips bool: Export individual clips rather than a single file (default False)
    :param random_order bool: Randomize the order of clips (default False)
    :param demo bool: Show the results of the search but don't actually make a supercut
    :param pause float: Time in seconds to pause after each clip
    :param word_file str: File contains words to search for (can be txt or csv)
    :param context_aware bool: Include sentences surrounding the query
    """

    segments = search(files=files, query=query, search_type=search_type, word_file=word_file, context_aware=context_aware, prefer=transcript_type)

    if len(segments) == 0:
        if isinstance(query, list):
            query = " ".join(query)
        print("No results found for", query if query else f"input file {word_file}")
        return False


    # padding
    segments = pad_and_sync(segments, padding=padding, resync=resync)

    # random order
    if random_order:
        random.shuffle(segments)

    # max clips
    if maxclips != 0:
        segments = segments[0:maxclips]

    # demo and exit
    if demo:
        for s in segments:
            print(s["file"], s["start"], s["end"], s["content"])
        return True

    # export individual clips
    if export_clips:
        export_individual_clips(segments, output, pause)
        return True

    # m3u
    if output.endswith(".m3u"):
        export_m3u(segments, output, pause)
        return True

    # mpv edls
    if output.endswith(".mpv.edl"):
        export_mpv_edl(segments, output, pause)
        return True

    # fcp xml (compatible with premiere/davinci)
    if output.endswith(".xml"):
        export_xml(segments, output, pause)
        return True

    # export supercut
    if len(segments) > BATCH_SIZE:
        create_supercut_in_batches(segments, output, pause)
    else:
        create_supercut(segments, output, pause)
