# Memory on a first visit

Cold Firefox runs of the candidate (CLIP q4f16, MiniLM q8) peaked at 1.67 to
1.70 GB in three of eight runs, over the 1.5 GB budget. These runs looked for
the cause. Same laptop as the main results (Intel Iris Xe, 15 GB RAM, Linux),
2026-10-07, WASM path, Firefox 155 and Chrome 154.

Peak is the largest proportional set size over the browser's process tree,
sampled every 500 ms. Each directory is one browser session: a cold load from
a fresh profile, then a warm load in the same session.

## Is it the model cache?

`SPIKE_NO_CACHE=1` turns off the Cache API, so models are downloaded on every
load and never written to browser storage.

| Runs | Cold peak | Warm peak |
| --- | --- | --- |
| `firefox-cache-r1` to `r5` | 1704, 1693, 1693, 1283, 1701 MB | 1280 to 1357 MB |
| `firefox-nocache-r1` to `r5` | 1330, 1734, 1737, 1737, 1357 MB | 1269 to 1446 MB |

No. The peak happens with and without the cache. It happens on the first load
in a fresh browser session, and not on the second load in the same session,
even when the second load downloads everything again.

In the time series, memory climbs while the models download and load, then
about 700 MB is released roughly five seconds in.

## Can the browser give it back under pressure?

Each session ran inside a cgroup with a hard memory limit and no swap
(`systemd-run --user --scope -p MemoryMax=... -p MemorySwapMax=0`). The limit
covers the browser, the Playwright test runner (Node.js) and any page cache
the browser writes, so it is stricter than the peak above. The Vite server ran
outside it. A run killed by the kernel's OOM killer leaves no result file, so
the outcomes are recorded here.

| Browser | Limit | Sessions | Cold load | Warm load |
| --- | --- | --- | --- | --- |
| Firefox | 1600 MB | 5 | killed in 5 of 5 | - |
| Firefox | 2000 MB | 3 | killed in 2 of 3; one finished at 1459 MB | finished, 1276 MB |
| Chrome | 1600 MB | 3 | finished in 3 of 3, 1022 to 1162 MB | killed in 2 of 3; one finished, 1163 MB |

Under these limits the sessions failed: whether the excess is live memory or
garbage the browser did not collect in time, it was not freed before the
kernel stepped in. With the test runner and page cache inside the same limit,
Firefox's first load did not complete within 1.6 GB and often not within
2 GB. Chrome's first load completed within 1.6 GB, but a second load in the
same session sometimes did not. No allocation or GC tracing was done, so the
split between live memory and uncollected garbage is not known.

These runs filled memory on the machine they ran on, which is a reason not to
repeat them on a laptop in use.

## Outcome

The app loads each model the first time it is needed instead of all three up
front, so adding images never loads the text models. Its first visit, adding
50 images and 30 notes, peaks at 0.69 to 0.84 GB in Chrome and 1.36 to 1.44 GB
in Firefox over five cold runs each (`web/results/memory/`), inside the 1.5 GB
budget.
