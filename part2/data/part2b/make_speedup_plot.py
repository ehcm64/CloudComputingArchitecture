import matplotlib.pyplot as plt

threads = [1, 2, 4, 8]

# real-times in seconds parsed from logs_part2b/*.txt
runtimes = {
    "barnes":        [108.135,  55.232,  28.702,  22.535],
    "blackscholes":  [ 64.427,  37.890,  24.677,  21.023],
    "canneal":       [220.950, 139.466,  96.129,  71.652],
    "freqmine":      [337.162, 169.362,  86.765,  66.770],
    "radix":         [ 33.600,  17.075,   8.913,   5.402],
    "streamcluster": [428.720, 244.012, 145.646, 105.652],
    "vips":          [ 60.137,  34.978,  17.250,  14.203],
}

speedups = {b: [r[0] / t for t in r] for b, r in runtimes.items()}

print(f"{'benchmark':<15} {'n=1':>6} {'n=2':>6} {'n=4':>6} {'n=8':>6}")
for b, s in speedups.items():
    print(f"{b:<15} " + " ".join(f"{x:6.3f}" for x in s))

markers = {
    "barnes": "o", "blackscholes": "s", "canneal": "^", "freqmine": "D",
    "radix": "v", "streamcluster": "P", "vips": "X",
}

fig, ax = plt.subplots(figsize=(8, 5.5))
for b, s in speedups.items():
    ax.plot(threads, s, marker=markers[b], linewidth=2, markersize=8, label=b)

ax.plot(threads, threads, linestyle="--", color="gray", alpha=0.7, label="ideal (linear)")

ax.set_xlabel("Number of threads", fontsize=12)
ax.set_ylabel(r"Speedup  ($T_{1}/T_{n}$)", fontsize=12)
ax.set_title("PARSEC / SPLASH-2x multi-thread speedup", fontsize=13)
ax.set_xticks(threads)
ax.set_yticks(range(1, 9))
ax.grid(True, linestyle=":", alpha=0.6)
ax.legend(loc="upper left", ncol=2, fontsize=10)
ax.set_xlim(0.8, 8.5)
ax.set_ylim(0.8, 8.5)

fig.tight_layout()
out = "/Users/gianlo_personal/Develop/CCA/part2/data/part2b/speedup_plot.png"
fig.savefig(out, dpi=160)
print(f"\nSaved: {out}")
