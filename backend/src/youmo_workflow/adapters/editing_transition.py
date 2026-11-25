"""Approved 0.6-second dip-to-black with frozen handles and no source-content loss."""

from youmo_workflow.adapters.packaging_validation import require
from youmo_workflow.domain.editing import TimelinePart
from youmo_workflow.domain.editing_presentation import TRANSITION_SECONDS


def transition_filter(timeline: list[TimelinePart], *, burn: bool) -> str:
    require(len(timeline) == 2, "转场需要速览和正片两个区间。")
    recap, main = timeline
    require(recap.part == "quick_recap" and main.part == "main", "转场顺序不符。")
    require(
        abs(main.output_start - recap.output_end - TRANSITION_SECONDS) < 1e-6,
        "转场与字幕时间轴不一致。",
    )
    filters: list[str] = []
    half = TRANSITION_SECONDS / 2
    for index, part in enumerate(timeline):
        length = part.source_end - part.source_start
        video = (
            f"[{index}:v:0]trim=start=0:end={length:.6f},setpts=PTS-STARTPTS,fps=30,"
            f"tpad=stop_mode=clone:stop_duration=0.25,trim=duration={length:.6f},"
            "setsar=1,format=yuv420p"
        )
        audio = (
            f"[{index}:a:0]atrim=start=0:end={length:.6f},asetpts=PTS-STARTPTS,"
            f"aresample=48000,apad=whole_dur={length:.6f},atrim=duration={length:.6f},"
            "aformat=sample_fmts=fltp:channel_layouts=stereo"
        )
        if index == 0:
            video += (
                f",tpad=stop_mode=clone:stop_duration={half},fade=t=out:st={length:.6f}:d={half}"
            )
            audio += f",afade=t=out:st={length - 0.06:.6f}:d=0.06,apad=pad_dur={half}"
        else:
            video += f",tpad=start_mode=clone:start_duration={half},fade=t=in:st=0:d={half}"
            audio += f",afade=t=in:st=0:d=0.06,adelay={round(half * 1000)}:all=1"
        filters.extend([video + f"[v{index}]", audio + f"[a{index}]"])
    joined = "joinedv" if burn else "outv"
    filters.append(f"[v0][a0][v1][a1]concat=n=2:v=1:a=1[{joined}][outa]")
    if burn:
        filters.append("[joinedv]ass=filename=burn-in.ass:fontsdir=subtitle-fonts[outv]")
    return ";".join(filters)
