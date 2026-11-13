import subprocess, sys, os
from PIL import Image
import imageio_ffmpeg
ROOT = 'D:/Github_Codes/class-hosting-platform-github/.temp/launch/out'
FPS, W, H = 24, 1080, 1350
STEPS = [4, 4, 4, 4, 5, 4, 4, 3]   # steps per slide, as printed by render.mjs
ff = imageio_ffmpeg.get_ffmpeg_exe()

def load(path):
    return Image.open(path).convert('RGB')

def encode(clips, out):
    """clips: [(png path, hold seconds, fade-in seconds)]; the fade blends the previous picture into this one."""
    cmd = [ff, '-y', '-loglevel', 'error', '-f', 'rawvideo', '-pix_fmt', 'rgb24', '-s', f'{W}x{H}', '-r', str(FPS), '-i', '-',
           '-c:v', 'libx264', '-preset', 'medium', '-crf', '20', '-pix_fmt', 'yuv420p', '-movflags', '+faststart', out]
    p = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    prev = None
    frames = 0
    for path, hold, fade in clips:
        cur = load(path)
        if cur.size != (W, H): cur = cur.resize((W, H))
        if prev is not None and fade > 0:
            n = max(1, round(fade * FPS))
            for i in range(1, n + 1):
                p.stdin.write(Image.blend(prev, cur, i / n).tobytes()); frames += 1
        raw = cur.tobytes()
        for _ in range(round(hold * FPS)): p.stdin.write(raw); frames += 1
        prev = cur
    p.stdin.close(); p.wait()
    print(f'{os.path.basename(out)}: {frames} frames, {frames / FPS:.1f} s, {os.path.getsize(out) / 1e6:.1f} MB, exit {p.returncode}')

# 1. the walkthrough: every build step of every slide
walk = []
for s, n in enumerate(STEPS, start=1):
    for k in range(n):
        last = k == n - 1
        hold = 2.4 if last else (1.3 if k == 0 else 1.2)
        fade = 0.5 if k == 0 else 0.35          # a slide change fades a little slower than a build step
        walk.append((f'{ROOT}/frames/s{s}_k{k}.png', hold, 0 if (s == 1 and k == 0) else fade))
encode(walk, f'{ROOT}/class-hosting-platform-walkthrough.mp4')

# 2. the teaser: the finished picture of five slides
tease = [(f'{ROOT}/png/slide-{i}.png', 3.6, 0 if j == 0 else 0.5) for j, i in enumerate([1, 3, 5, 6, 8])]
encode(tease, f'{ROOT}/class-hosting-platform-teaser.mp4')
