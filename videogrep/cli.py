import argparse
import os

from . import get_ngrams, sphinx, videogrep, __version__


def main():
    """
    Run the command line version of Videogrep
    """

    parser = argparse.ArgumentParser(
        description='Generate a "supercut" of one or more video files by searching through subtitle tracks.'
    )
    parser.add_argument(
        "--input",
        "-i",
        dest="inputfile",
        nargs="*",
        required=False,
        help="video file or files, leave this empty to search all files in the current directory",
    )
    parser.add_argument(
        "--search", "-s", dest="search", action="append", help="search term"
    )
    parser.add_argument(
        "--input-words",
        "-iw",
        default=None,
        dest="inputwordfile",
        help="file with words to search for",
    )
    parser.add_argument(
        "--search-type",
        "-st",
        dest="searchtype",
        default="sentence",
        choices=["sentence", "fragment", "mash"],
        help="type of search - can either be 'sentence', 'fragment' or 'mash'",
    )
    parser.add_argument(
        "--max-clips",
        "-m",
        dest="maxclips",
        type=int,
        default=0,
        help="maximum number of clips to use for the supercut",
    )
    parser.add_argument(
        "--output",
        "-o",
        dest="outputfile",
        # default="supercut.mp4",
        default="supercut.mp4",
        help="name of output file",
    )
    parser.add_argument(
        "--outfolder",
        "-of",
        dest="outfolder",
        default="output",
        help="name of output folder",
    )
    parser.add_argument(
        "--export-clips",
        "-ec",
        dest="export_clips",
        action="store_true",
        help="Export individual clips",
    )
    parser.add_argument(
        "--demo",
        "-d",
        action="store_true",
        help="show results without making the supercut",
    )
    parser.add_argument(
        "--randomize", "-r", action="store_true", help="randomize the clips"
    )
    parser.add_argument(
        "--padding",
        "-p",
        dest="padding",
        default=0,
        type=float,
        help="padding in seconds to add to the start and end of each clip",
    )
    parser.add_argument(
        "--pause",
        "-pa",
        dest="pause",
        default=0,
        type=float,
        help="pause in seconds after each clip",
    )
    parser.add_argument(
        "--resyncsubs",
        "-rs",
        dest="sync",
        default=0,
        type=float,
        help="subtitle re-synch delay +/- in seconds",
    )
    parser.add_argument(
        "--sphinx-transcribe",
        "-str",
        dest="sphinxtranscribe",
        action="store_true",
        help="transcribe the video using pocketsphinx (must be installed)",
    )
    parser.add_argument(
        "--transcribe",
        "-tr",
        dest="transcribe",
        default=True,
        action="store_true",
        help="transcribe the video using vosk (built in)",
    )
    parser.add_argument(
        "--model",
        "-mo",
        dest="model",
        help="model folder for transcription",
    )
    parser.add_argument(
        "--ngrams",
        "-n",
        dest="ngrams",
        type=int,
        default=0,
        help="return ngrams for videos",
    )
    parser.add_argument(
        "--context-aware",
        "-ca",
        dest="context_aware",
        action="store_false",
        help="include sentences surrounding the query in the supercut",
    )
    parser.add_argument(
        "--merge",
        "-me",
        dest="merge",
        action="store_true",
        help="merge the supercut into a single video",
    )
    parser.add_argument(
        "--version",
        "-v",
        help="show version",
        action="version",
        version=__version__,
    )
    args = parser.parse_args()

    if args.ngrams > 0:
        from collections import Counter

        grams = get_ngrams(args.inputfile, args.ngrams)
        most_common = Counter(grams).most_common(100)
        for ngram, count in most_common:
            print(" ".join(ngram), count)

        return True

    if args.sphinxtranscribe:
        for f in args.inputfile:
            sphinx.transcribe(f)
        return True
    
    if not args.inputfile:
        args.inputfile = os.listdir(".")

    media_extensions = [".mp4", ".mkv", ".webm", ".mov", ".mp3", ".wav"]
    if args.transcribe:
        try:
            from . import transcribe
        except ModuleNotFoundError:
            print("You must install vosk to transcribe files: \n\npip install vosk\n")
            return False

        for f in args.inputfile[:]:
            
            # ignore if not media file
            if not any(f.endswith(ext) for ext in media_extensions):
                args.inputfile.remove(f)
                continue

            # Check if transcribe file exists
            # Avoid transcribing multiple times
            if not os.path.exists(os.path.splitext(f)[0] + ".json"):
                transcribe.transcribe(f, args.model)

            # continue

        # return True

    if not any([args.search, args.inputwordfile]):
        parser.error("argument --search/-s is required with --input-words/-iw")

    if args.inputwordfile is not None and str(args.inputwordfile).strip() != "":
        # Check if the file exists
        if not os.path.exists(args.inputwordfile):
            parser.error(repr(f"file {args.inputwordfile} does not exist"))

    if not os.path.exists(args.outfolder):
        try:
            os.mkdir(args.outfolder)
        except:
            pass

    for f in args.inputfile:
        videogrep(
            files=f,
            query=args.search,
            search_type=args.searchtype,
            # output=args.outputfile,
            output=os.path.join(str(args.outfolder), "output_" + f),
            maxclips=args.maxclips,
            padding=args.padding,
            demo=args.demo,
            random_order=args.randomize,
            resync=args.sync,
            export_clips=args.export_clips,
            pause=args.pause,
            word_file=args.inputwordfile,
            context_aware=args.context_aware
        )

    if args.merge:
        if not args.outputfile:
            args.outputfile = "merged.mp4"
        def merge():
            """
                Merge the supercut into a single video
            """

            from moviepy.editor import VideoFileClip, concatenate_videoclips

            files = os.listdir(args.outfolder)
            clips = []
            for f in files:
                try:
                    clips.append(VideoFileClip(os.path.join(args.outfolder, f)))
                except:
                    pass

            merged_clip = concatenate_videoclips(clips, method='compose')
            merged_clip.write_videofile(os.path.join(args.outfolder, args.outputfile))

        merge()
